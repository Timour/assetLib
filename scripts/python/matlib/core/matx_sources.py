"""Online MaterialX material sources - one adapter per library.

Every source is reached with plain stdlib HTTP + JSON, so AssetLib keeps
ZERO third-party dependencies and this module is testable outside
Houdini (it imports no `hou`).

Two KINDS of source, which the importer treats differently:

* **package** - ships a .mtlx document plus texture maps. Downloaded into
  <library>/matX/<name>/ and turned into a real material by TRANSLATING
  the .mtlx into clean VOP nodes (core/matx_translate, on Houdini's
  MaterialX Python API).
* **values**  - ships measured shader parameters only (no textures, no
  download). Becomes a "tier A preset" material: create an
  mtlxstandard_surface, set the values, done.

Each source's categories are its own, capitalised and unsuffixed - the
source is chosen from View > Online Materials > <source>, so the category
alone identifies the group within it (see _cat()).

Verified live 2026-07-20 against each API.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.parse
import urllib.request
import zipfile

USER_AGENT = "AssetLib/1.0 (Houdini material library)"
TIMEOUT = 30

#: Resolution labels we understand, smallest first.
_RES_ORDER = ("1k", "2k", "4k", "8k", "16k")


def _res_rank(label: str) -> int:
    """Sort key for a resolution label; -1 if unrecognised."""
    m = re.search(r"(\d+)\s*k", str(label).lower())
    if not m:
        return -1
    try:
        return int(m.group(1))
    except ValueError:
        return -1


def pick_resolution(available, preferred: str) -> str | None:
    """Selection rule: exact match, else the NEXT HIGHEST available, else
    the highest available below. A preference is a floor you'd like, never
    a hard failure. Returns None only if nothing is available at all."""
    if not available:
        return None
    ranked = sorted(
        ((_res_rank(a), a) for a in available if _res_rank(a) > 0),
        key=lambda t: t[0],
    )
    if not ranked:
        return list(available)[0]
    want = _res_rank(preferred)
    if want <= 0:
        want = 2
    for rank, label in ranked:          # exact, then next highest
        if rank >= want:
            return label
    return ranked[-1][1]                # nothing higher - take the largest


def _request(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        # Some hosts need a relaxed SSL context inside Houdini's Python.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx)


def get_json(url: str):
    with _request(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def repair_mtlx_references(mtlx_path: str, dest_dir: str) -> list:
    """Make a downloaded .mtlx point at the files that were actually
    fetched, and report what had to be changed.

    PolyHaven's API is internally inconsistent: the include manifest for
    aerial_mud_1 lists `textures/aerial_mud_1_rough_2k.jpg`, while the
    .mtlx document it ships references `..._rough_2k.exr`. We download
    exactly what the manifest says, so the material ends up pointing at a
    file that was never fetched - and a missing texture reads as BLACK
    with no error Houdini would surface.

    Same map, same resolution, different container, so the fix is to
    repoint the reference at the file we have rather than guess a second
    download URL."""
    repairs = []
    try:
        with open(mtlx_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return repairs

    # Every file-ish value in the document.
    # The extension must START with a letter: a bare numeric value like
    # value="0.01" (a displacement scale) otherwise reads as a file
    # called "0" with extension "01", and gets reported as a missing
    # texture.
    referenced = set(
        re.findall(r'value="([^"]+\.[A-Za-z][A-Za-z0-9]{1,3})"', text)
    )
    if not referenced:
        return repairs

    # What we actually have, indexed by stem.
    on_disk = {}
    for root, _dirs, files in os.walk(dest_dir):
        for name in files:
            stem = os.path.splitext(name)[0]
            rel = os.path.relpath(os.path.join(root, name), dest_dir)
            on_disk.setdefault(stem, rel.replace(os.sep, "/"))

    changed = False
    for ref in referenced:
        if os.path.exists(os.path.join(dest_dir, ref)):
            continue
        stem = os.path.splitext(os.path.basename(ref))[0]
        have = on_disk.get(stem)
        if not have:
            repairs.append({"reference": ref, "fixed_to": None})
            continue
        text = text.replace('value="%s"' % ref, 'value="%s"' % have)
        repairs.append({"reference": ref, "fixed_to": have})
        changed = True

    if changed:
        try:
            with open(mtlx_path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass
    return repairs


def download(url: str, dest_path: str, on_bytes=None) -> str:
    """Stream a URL to disk. Returns dest_path.

    on_bytes(read, total) is called per 64KB chunk when given, so a caller
    on the main thread can drive a progress bar (total is 0 if the server
    sent no Content-Length)."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with _request(url) as resp, open(dest_path, "wb") as fh:
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        read = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            read += len(chunk)
            if on_bytes is not None:
                on_bytes(read, total)
    return dest_path


def _as_text(value) -> str:
    """Whatever a source gave us, as a display string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(str(v) for v in value.keys())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


class MatxRecord:
    """One material from any source, normalised."""

    def __init__(
        self,
        source,
        uid,
        title,
        author="",
        category="",
        tags=None,
        preview_url="",
        licence="",
        kind="package",
        payload=None,
    ):
        self.source = source
        self.uid = uid
        self.title = title
        # Sources disagree on the shape of "author": GPUOpen sends a
        # LIST, PolyHaven a dict of names, PhysicallyBased a citation
        # string. Normalise once here so nothing downstream has to care
        # (a list reaching the tooltip raised TypeError on every repaint
        # of the online grid).
        self.author = _as_text(author)
        self.category = category      # capitalised, unsuffixed (see _cat)
        self.tags = tags or []
        self.preview_url = preview_url
        self.licence = licence
        self.kind = kind              # "package" | "values"
        self.payload = payload or {}

    def __repr__(self):
        return "<MatxRecord %s/%s %r>" % (self.source, self.kind, self.title)

    def to_dict(self) -> dict:
        """Plain-dict form for the on-disk catalogue cache."""
        return {
            "source": self.source, "uid": self.uid, "title": self.title,
            "author": self.author, "category": self.category,
            "tags": self.tags, "preview_url": self.preview_url,
            "licence": self.licence, "kind": self.kind,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MatxRecord":
        return cls(
            source=d.get("source", ""), uid=d.get("uid", ""),
            title=d.get("title", "Untitled"), author=d.get("author", ""),
            category=d.get("category", ""), tags=d.get("tags") or [],
            preview_url=d.get("preview_url", ""),
            licence=d.get("licence", ""), kind=d.get("kind", "package"),
            payload=d.get("payload") or {},
        )


class MatxSource:
    """Adapter interface. Subclasses must not import hou."""

    name = ""
    licence = ""
    kind = "package"

    def page_url(self, record) -> str:
        """A link back to where this material came from, for crediting the
        creators. Subclasses return the exact material page where the URL
        scheme is known, else the library's home page."""
        return ""

    def list_materials(self, search="", offset=0, limit=60) -> list:
        raise NotImplementedError

    def resolutions(self, record) -> list:
        return []

    def fetch(self, record, resolution, dest_dir, progress=None) -> dict:
        """Package sources: download+extract, return {"mtlx": path}.
        Value sources: return {"values": {...}}.

        progress(frac) is called with a 0..1 fraction for THIS material's
        whole download when given, so the caller can show a progress bar."""
        raise NotImplementedError

    def _cat(self, name):
        """Normalise a source's category to a clean, Capitalised name.
        Sources disagree on the type: a string (GPUOpen, after UUID
        lookup), a list (PolyHaven, PhysicallyBased), or absent - and on
        case (PolyHaven's are lowercase). No "-<Source>" suffix any more:
        the source is picked from the View > Online Materials submenu, so
        it's redundant on every category."""
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
        name = (str(name).strip() if name else "") or "Uncategorised"
        return " ".join(w[:1].upper() + w[1:] for w in name.split())


class GPUOpenSource(MatxSource):
    """AMD GPUOpen MaterialX Library - 454 materials, MIT Public Domain.
    Ships true MaterialX packages (.mtlx + textures) in resolution
    variants labelled like "1k 8b"."""

    name = "GPUOpen"
    licence = "MIT Public Domain"
    API = "https://api.matlib.gpuopen.com/api"

    def page_url(self, record) -> str:
        # The GPUOpen material-library site (no confirmed per-material
        # permalink scheme, so link the library home).
        return "https://matlib.gpuopen.com/"

    def __init__(self):
        self._categories = None

    def _category_map(self):
        if self._categories is None:
            self._categories = {}
            try:
                data = get_json(self.API + "/categories/?limit=100")
                for c in data.get("results", []):
                    self._categories[c.get("id")] = c.get("title")
            except Exception:
                pass
        return self._categories

    def list_materials(self, search="", offset=0, limit=60):
        url = "%s/materials/?limit=%d&offset=%d" % (self.API, limit, offset)
        if search:
            url += "&search=" + urllib.parse.quote(search)
        data = get_json(url)
        cats = self._category_map()
        out = []
        for r in data.get("results", []):
            cat_ids = r.get("category") or []
            if not isinstance(cat_ids, list):
                cat_ids = [cat_ids]
            cat = next(
                (cats.get(c) for c in cat_ids if cats.get(c)), "Uncategorised"
            )
            renders = r.get("renders") or []
            out.append(
                MatxRecord(
                    source=self.name,
                    uid=r.get("id"),
                    title=r.get("title") or "Untitled",
                    author=r.get("author") or "",
                    category=self._cat(cat),
                    tags=[],
                    preview_url=(
                        # Verified endpoint - "/thumbnail/" 404s; the
                        # render record's own thumbnail_url is
                        # "/renders/<id>/download_thumbnail/" (302 ->
                        # the image, which urllib follows).
                        "%s/renders/%s/download_thumbnail/"
                        % (self.API, renders[0])
                        if renders else ""
                    ),
                    licence=r.get("license") or self.licence,
                    kind="package",
                    payload={"packages": r.get("packages") or []},
                )
            )
        return out

    def _packages(self, record):
        """Resolve the material's package list to (resolution, id) pairs."""
        found = []
        for pid in record.payload.get("packages", []):
            try:
                p = get_json("%s/packages/%s/" % (self.API, pid))
            except Exception:
                continue
            label = p.get("label") or ""
            if _res_rank(label) > 0:
                found.append((label.split()[0], pid, p.get("file_url")))
        return found

    def resolutions(self, record):
        # Each resolution exists twice (8-bit and 16-bit variants), so
        # dedupe - the UI offers resolutions, not bit depths.
        pkgs = record.payload.setdefault("_resolved", self._packages(record))
        seen = []
        for res, _pid, _url in pkgs:
            if res not in seen:
                seen.append(res)
        return seen

    def fetch(self, record, resolution, dest_dir, progress=None):
        pkgs = record.payload.setdefault("_resolved", self._packages(record))
        chosen = None
        for res, _pid, url in pkgs:
            if res == resolution:
                chosen = url
                break
        if chosen is None and pkgs:
            chosen = pkgs[-1][2]
        if not chosen:
            raise RuntimeError("no downloadable package for " + record.title)
        os.makedirs(dest_dir, exist_ok=True)
        zip_path = os.path.join(dest_dir, "_package.zip")

        def on_bytes(read, total):
            if progress and total:
                progress(read / total)

        download(chosen, zip_path, on_bytes=on_bytes)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
        os.remove(zip_path)
        if progress:
            progress(1.0)
        return {"mtlx": _find_mtlx(dest_dir)}


class PolyHavenSource(MatxSource):
    """Poly Haven textures - CC0. Serves a real .mtlx per resolution plus
    an explicit manifest of the textures it references, rather than a
    zip, so we fetch the document and each include."""

    name = "PolyHaven"
    licence = "CC0"
    API = "https://api.polyhaven.com"

    def page_url(self, record) -> str:
        return "https://polyhaven.com/a/%s" % record.uid

    def list_materials(self, search="", offset=0, limit=60):
        data = get_json(self.API + "/assets?type=textures")
        items = sorted(data.items(), key=lambda kv: kv[1].get("name", ""))
        if search:
            s = search.lower()
            items = [
                kv for kv in items
                if s in (kv[1].get("name", "") or "").lower()
                or any(s in t.lower() for t in (kv[1].get("tags") or []))
            ]
        out = []
        for uid, r in items[offset:offset + limit]:
            cats = r.get("categories") or []
            out.append(
                MatxRecord(
                    source=self.name,
                    uid=uid,
                    title=r.get("name") or uid,
                    author=", ".join((r.get("authors") or {}).keys()),
                    category=self._cat(cats[0] if cats else None),
                    tags=r.get("tags") or [],
                    preview_url=r.get("thumbnail_url") or "",
                    licence=self.licence,
                    kind="package",
                    payload={},
                )
            )
        return out

    def _files(self, record):
        if "_files" not in record.payload:
            record.payload["_files"] = get_json(
                "%s/files/%s" % (self.API, record.uid)
            )
        return record.payload["_files"]

    def resolutions(self, record):
        try:
            return sorted(
                (self._files(record).get("mtlx") or {}).keys(),
                key=_res_rank,
            )
        except Exception:
            return []

    def fetch(self, record, resolution, dest_dir, progress=None):
        mtlx_all = self._files(record).get("mtlx") or {}
        entry = mtlx_all.get(resolution) or next(iter(mtlx_all.values()))
        doc = entry.get("mtlx") if isinstance(entry, dict) else None
        if not doc:
            raise RuntimeError("no .mtlx for " + record.title)
        os.makedirs(dest_dir, exist_ok=True)
        mtlx_path = os.path.join(
            dest_dir, os.path.basename(urllib.parse.urlparse(doc["url"]).path)
        )
        # The .mtlx doc plus each referenced texture - many small files, so
        # progress folds the current file's bytes into "file i of n".
        files = [(doc["url"], mtlx_path)]
        for rel, info in (doc.get("include") or {}).items():
            files.append((info["url"], os.path.join(dest_dir, rel)))
        n = len(files)
        for i, (url, path) in enumerate(files):
            def on_bytes(read, total, i=i):
                if progress and total:
                    progress((i + read / total) / n)
            download(url, path, on_bytes=on_bytes)
            if progress:
                progress((i + 1) / n)
        return {"mtlx": mtlx_path}


class PhysicallyBasedSource(MatxSource):
    """PhysicallyBased - MEASURED reference values, no textures at all.

    A different kind of source: these become "tier A preset" materials -
    an mtlxstandard_surface with physically accurate constants (real
    aluminium, real gold) to build on. Nothing is downloaded; the whole
    dataset is ~69 KB of JSON."""

    name = "PhysicallyBased"
    licence = "see source reference"
    kind = "values"
    API = "https://api.physicallybased.info"

    def page_url(self, record) -> str:
        return "https://physicallybased.info/"

    def __init__(self):
        self._all = None

    def _load(self):
        if self._all is None:
            self._all = get_json(self.API + "/materials")
        return self._all

    def list_materials(self, search="", offset=0, limit=60):
        items = self._load()
        if search:
            s = search.lower()
            def _hay(m):
                cat = m.get("category")
                if isinstance(cat, (list, tuple)):
                    cat = " ".join(str(c) for c in cat)
                return "%s %s" % (m.get("name") or "", cat or "")
            items = [m for m in items if s in _hay(m).lower()]
        out = []
        for m in items[offset:offset + limit]:
            out.append(
                MatxRecord(
                    source=self.name,
                    uid=m.get("name"),
                    title=m.get("name") or "Untitled",
                    author=m.get("reference") or "",
                    category=self._cat(m.get("category")),
                    tags=m.get("tags") or [],
                    preview_url="",
                    licence=self.licence,
                    kind="values",
                    payload={"values": m},
                )
            )
        return out

    def resolutions(self, record):
        return []           # nothing to download

    def fetch(self, record, resolution, dest_dir, progress=None):
        return {"values": record.payload.get("values", {})}


def _find_mtlx(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".mtlx"):
                return os.path.join(dirpath, f)
    return None


#: Sources available to the browser, in menu order.
#: ambientCG is deliberately absent from v1: probing its API showed it
#: serves ONLY JPG/PNG texture zips, no .mtlx document (contrary to some
#: secondary sources claiming MaterialX support). Supporting it means
#: building a standard_surface from conventionally-named maps - a third
#: source KIND - which is a v2 feature, not a blocker.
SOURCES = (GPUOpenSource, PolyHavenSource, PhysicallyBasedSource)


def all_sources():
    return [cls() for cls in SOURCES]
