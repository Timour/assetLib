"""The online MaterialX browser model.

Serves rows from the online sources (core/matx_sources.py) instead of
library.json, using the SAME role numbers as MaterialLibrary so the
existing delegate, proxy, grid, list mode and search all work untouched -
the browser IS the normal grid with a different model behind it.

Network work happens on a worker thread; the UI thread never blocks.
Preview images ride the shared thumbnail engine, so lazy loading, the RAM
budget and eviction come for free - and the preview doubles as the
imported material's thumbnail, so no shaderball render is needed.
"""

from __future__ import annotations

import math
import os
import json
import hashlib

from PySide6 import QtCore, QtGui

from matlib.core import debug, matx_icon, matx_sources, thumbnails

#: Where downloaded previews are cached (local only - never the
#: cloud-synced library folder).
PREVIEW_CACHE = os.path.expanduser(
    "~/Library/Caches/AssetLib/matx_previews"
)

#: The catalogue (all sources' records) cached to disk, so switching to
#: Online Materials shows its categories INSTANTLY instead of waiting
#: ~2-3s for the fetch (GPUOpen's API alone is ~2.2s). Refreshed in the
#: background on every open so it stays current. The _v2 suffix is the
#: cache format version - bumped when the record shape changes (v2
#: dropped the "-<Source>" category suffix and capitalised names), so a
#: stale old cache is simply ignored and re-fetched, never shown.
CATALOGUE_CACHE = os.path.expanduser(
    "~/Library/Caches/AssetLib/matx_catalogue_v2.json"
)

#: Pre-v2 cache filenames, swept once on first construction so a stale
#: old-format file (different record shape, same count - the change check
#: would not refresh it) never lingers on disk.
_LEGACY_CATALOGUE_CACHES = [
    os.path.expanduser("~/Library/Caches/AssetLib/matx_catalogue.json"),
]


def _purge_legacy_caches():
    for path in _LEGACY_CATALOGUE_CACHES:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


class _CatalogueWorker(QtCore.QThread):
    """Fetches EVERY source's full catalogue off the UI thread.

    The whole thing is only ~1300 records over 3 API calls (GPUOpen 454,
    PolyHaven 783, PhysicallyBased 86), so there is no paging: one flat
    list means typing "polyhaven" in the filter box just narrows it, with
    no source-switching machinery."""

    done = QtCore.Signal(object, object, int)   # (records, errors, generation)

    def __init__(self, sources, generation):
        super().__init__()
        self._sources = list(sources)
        self._generation = generation

    def run(self):
        records, errors = [], []
        for src in self._sources:
            try:
                records.extend(src.list_materials(limit=1000))
            except Exception as exc:
                errors.append("%s (%s)" % (src.name, type(exc).__name__))
        self.done.emit(records, errors, self._generation)


class _PreviewWorker(QtCore.QThread):
    """Downloads preview images and reports them by thumbnail-engine key."""

    ready = QtCore.Signal(object, object)   # (key, QImage)
    attempted = QtCore.Signal()             # per job, success OR failure

    def __init__(self, jobs):
        super().__init__()
        self._jobs = list(jobs)             # [(key, url, cache_path)]

    def run(self):
        for key, url, path in self._jobs:
            try:
                if not os.path.exists(path):
                    matx_sources.download(url, path)
                image = QtGui.QImage(path)
                if not image.isNull():
                    self.ready.emit(key, image)
            except Exception as exc:
                debug.exception("preview download", exc, url=url, path=path)
                print("Amaze: preview failed for %s: %s" % (url, exc))
            finally:
                # Drives the progress bar - must fire even on failure, or a
                # timed-out preview would stall the bar short of 100%.
                self.attempted.emit()


class MatxOnlineLibrary(QtCore.QAbstractListModel):
    """Rows = materials available online, from one source at a time."""

    #: (done, total) preview downloads, for the shared thin progress bar.
    #: Rolling, because previews load lazily as tiles scroll into view.
    progress_changed = QtCore.Signal(int, int)

    def __init__(self, parent=None, preferences=None):
        super().__init__(parent)
        _purge_legacy_caches()
        self.preferences = preferences
        self._sources = matx_sources.all_sources()
        self._source = self._sources[0]
        self._all = []           # every record, every source (cached)
        self._records = []       # the filtered view actually shown
        self._search = ""
        self._source_filter = None   # show only this source (View submenu)
        self._generation = 0
        self._loaded = False
        self._loading = False
        self._workers = []
        self._error = ""
        self._requested = set()      # keys already asked for
        self._preview_total = 0      # preview downloads queued this burst
        self._preview_done = 0       # ...and attempted (ok or failed)
        self._pending = []           # records awaiting the next dispatch
        self._pending_scheduled = False
        self._preview_workers = []   # bounded download pool

        # Same role numbers as MaterialLibrary - the delegate and the
        # filter proxy are shared, so they must line up exactly.
        self.IdRole = QtCore.Qt.ItemDataRole.UserRole            # 256
        self.CategoryRole = QtCore.Qt.ItemDataRole.UserRole + 1  # 257
        self.FavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 2  # 258
        self.RendererRole = QtCore.Qt.ItemDataRole.UserRole + 3  # 259
        self.TagRole = QtCore.Qt.ItemDataRole.UserRole + 4       # 260
        self.DateRole = QtCore.Qt.ItemDataRole.UserRole + 5      # 261
        self.RendererLabelRole = QtCore.Qt.ItemDataRole.UserRole + 6  # 262

        thumbnails.engine.ready.connect(self._on_preview_ready)

    # -- sources -------------------------------------------------------

    @property
    def sources(self):
        return self._sources

    @property
    def source(self):
        return self._source

    @property
    def error(self):
        """Last network error ('' when fine). The panel shows an empty
        grid when offline - no dialog; dialogs confirm actions, they
        don't announce outcomes."""
        return self._error

    def set_search(self, text):
        """Filter the current source's materials locally - no API
        round-trip. Matches title, category and tags. The source itself
        is chosen from View > Online Materials (set_source), not typed
        here - the search narrows within that source."""
        text = (text or "").strip()
        if text == self._search:
            return
        self._search = text
        self._apply_filter()

    def set_source(self, source_name):
        """Show only one source's materials (View > Online Materials >
        <source>). None shows nothing until a source is picked. Refreshes
        the sidebar to that source's categories via the model reset."""
        self._source_filter = source_name
        self._apply_filter()

    def _in_source(self, r):
        return self._source_filter is None or r.source == self._source_filter

    def _apply_filter(self):
        rows = [r for r in self._all if self._in_source(r)]
        needle = self._search.lower()
        if needle:
            def hit(r):
                hay = "%s %s %s" % (
                    r.title, r.category, " ".join(r.tags or [])
                )
                return needle in hay.lower()
            rows = [r for r in rows if hit(r)]
        debug.event("online", "filtered", needle=needle,
                    source=self._source_filter, shown=len(rows),
                    total=len(self._all))
        self.beginResetModel()
        self._records = rows
        self.endResetModel()
        # No eager queueing: data() asks for what it paints.

    def reload(self, force=False):
        """Show the catalogue, fast. If it's already in memory just
        re-filter; otherwise load the DISK CACHE instantly (categories
        appear in <100ms) and refresh from the network in the background.
        Only a cache miss waits on the ~2-3s fetch."""
        if self._loaded and not force:
            self._apply_filter()
            return

        # Instant path: the last fetch, off disk. Shows immediately; the
        # background refresh below replaces it if the remote changed.
        if not self._loaded:
            cached = self._load_cache()
            if cached:
                self._all = cached
                self._loaded = True
                self._apply_filter()

        if self._loading:
            return
        self._loading = True
        self._generation += 1
        worker = _CatalogueWorker(self._sources, self._generation)
        worker.done.connect(self._on_catalogue)
        worker.finished.connect(lambda w=worker: self._retire(w))
        self._workers.append(worker)
        worker.start()

    def _load_cache(self):
        try:
            with open(CATALOGUE_CACHE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return [matx_sources.MatxRecord.from_dict(d)
                    for d in data.get("records", [])]
        except (OSError, ValueError):
            return None

    def _save_cache(self, records) -> None:
        try:
            os.makedirs(os.path.dirname(CATALOGUE_CACHE), exist_ok=True)
            with open(CATALOGUE_CACHE, "w", encoding="utf-8") as handle:
                json.dump({"records": [r.to_dict() for r in records]}, handle)
        except OSError as exc:
            print("Amaze: could not cache the online catalogue: %s" % exc)

    def _on_catalogue(self, records, errors, generation):
        self._loading = False
        if generation != self._generation:
            return
        # A partial fetch (some source down) must not overwrite a full
        # disk cache - keep whichever has more, and only re-filter/re-save
        # when the fresh fetch actually adds something.
        if not records or (self._all and len(records) < len(self._all)):
            self._error = ", ".join(errors) if errors else ""
            if errors:
                print("Amaze: some online sources unavailable - "
                      + self._error)
            return
        changed = len(records) != len(self._all)
        self._all = records
        self._loaded = True
        self._save_cache(records)
        by_source = {}
        for r in records:
            by_source[r.source] = by_source.get(r.source, 0) + 1
        debug.event("online", "catalogue loaded", total=len(records),
                    by_source=by_source, errors=errors, changed=changed)
        self._error = ", ".join(errors) if errors else ""
        if errors:
            print("Amaze: some online sources unavailable - " + self._error)
        # Only rebuild the view if the data actually changed - a
        # no-change background refresh must not disturb what's on screen.
        if changed or not self._records:
            self._apply_filter()

    def _retire(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    # -- previews ------------------------------------------------------

    @staticmethod
    def _preview_key(record):
        return ("matx", record.source, record.uid)

    def _cache_path(self, record):
        digest = hashlib.md5(
            ("%s/%s" % (record.source, record.uid)).encode("utf-8")
        ).hexdigest()
        return os.path.join(PREVIEW_CACHE, record.source, digest + ".png")

    def _icon_size(self):
        try:
            return int(self.preferences.rendersize)
        except (AttributeError, TypeError, ValueError):
            return 256

    def _preview(self, rec):
        """Cached preview, requesting it on a miss.

        Every other model in this codebase requests lazily from data(),
        driven by what the view actually paints. This one only peeked at
        an eagerly queued slice of the first 120 rows, so rows past that
        never got an image at all, and any preview the RAM budget evicted
        never came back. One row is queued here and the batch coalesces
        on a zero-timer, mirroring the engine's own dispatch."""
        key = self._preview_key(rec)
        image = thumbnails.engine.peek(key)
        if image is not None:
            return image
        if key in self._requested:
            return None

        # Disk-cache hit: decode it here and now. A previously-seen
        # preview must not need a network worker (or even a thread) to
        # come back - browsing the same catalogue again was downloading
        # everything a second time. Same shape as the texture cache's
        # main-thread hit path: a local stat + small PNG decode.
        if rec.preview_url:
            path = self._cache_path(rec)
            if os.path.exists(path):
                cached = QtGui.QImage(path)
                if not cached.isNull():
                    thumbnails.engine.deposit(key, cached)
                    return cached

        self._requested.add(key)
        self._pending.append(rec)
        if not self._pending_scheduled:
            self._pending_scheduled = True
            QtCore.QTimer.singleShot(0, self._flush_pending)
        return None

    def _parallel(self) -> int:
        try:
            return max(1, min(16, int(
                self.preferences.matx_parallel_downloads
            )))
        except (AttributeError, TypeError, ValueError):
            return 8

    def _flush_pending(self):
        """Dispatch accumulated requests across a BOUNDED POOL.

        Previews are latency-bound, not bandwidth-bound (a 40KB GPUOpen
        thumbnail takes ~470ms), so concurrency scales close to linearly
        - measured over 32 PolyHaven previews: 1 worker 220ms each, 8
        workers 42ms, 16 workers 18ms. A single serial worker made the
        full catalogue a 5-10 minute crawl.

        Bounded because the alternative (a fresh thread per paint pass)
        is a thread explosion while scrolling, and because these are
        free public APIs worth being a polite client of."""
        self._pending_scheduled = False
        limit = self._parallel()
        while self._pending and len(self._preview_workers) < limit:
            slots = limit - len(self._preview_workers)
            size = max(1, math.ceil(len(self._pending) / slots))
            chunk = self._pending[:size]
            self._pending = self._pending[size:]
            self._queue_previews(chunk)

    def _preview_batch_done(self, worker):
        if worker in self._preview_workers:
            self._preview_workers.remove(worker)
        self._retire(worker)
        # A finished worker frees a slot - keep the pool fed.
        if self._pending and not self._pending_scheduled:
            self._pending_scheduled = True
            QtCore.QTimer.singleShot(0, self._flush_pending)

    def _queue_previews(self, records):
        jobs = []
        for rec in records:
            key = self._preview_key(rec)
            if thumbnails.engine.peek(key) is not None:
                continue
            if rec.kind == "values":
                # No render exists to download - the tile is DRAWN from
                # the material's own measured numbers. Cheap enough to do
                # on the spot (an SVG rasterise), so no worker.
                try:
                    thumbnails.engine.deposit(
                        key,
                        matx_icon.render(
                            rec.payload.get("values", {}), self._icon_size()
                        ),
                    )
                except Exception as exc:
                    print("Amaze: could not draw icon for %s: %s"
                          % (rec.title, exc))
                # Drawing is cheap, so an evicted icon can simply be
                # redrawn on the next paint - don't hold the marker.
                self._requested.discard(key)
                continue
            if not rec.preview_url:
                continue
            jobs.append((key, rec.preview_url, self._cache_path(rec)))
        if not jobs:
            return
        self._preview_total += len(jobs)
        self.progress_changed.emit(self._preview_done, self._preview_total)
        worker = _PreviewWorker(jobs)
        worker.ready.connect(self._deposit_preview)
        worker.attempted.connect(self._on_preview_attempted)
        worker.finished.connect(lambda w=worker: self._preview_batch_done(w))
        self._workers.append(worker)
        self._preview_workers.append(worker)
        worker.start()

    def _on_preview_attempted(self):
        """One preview download finished (ok or failed) - advance the bar.
        When the burst is fully drained, reset so the next scroll starts a
        fresh 0..N rather than resuming a stale total."""
        self._preview_done += 1
        self.progress_changed.emit(self._preview_done, self._preview_total)
        if self._preview_done >= self._preview_total and not self._pending:
            self._preview_done = 0
            self._preview_total = 0

    def _deposit_preview(self, key, image):
        thumbnails.engine.deposit(key, image)
        # Deposited images can still be evicted by the RAM budget; drop
        # the request marker so the next paint can ask again (from the
        # disk cache, which download() already populated).
        self._requested.discard(key)

    def _on_preview_ready(self, key):
        try:
            if not (isinstance(key, tuple) and key and key[0] == "matx"):
                return
        except Exception:
            return
        for row, rec in enumerate(self._records):
            if self._preview_key(rec) == key:
                idx = self.index(row)
                self.dataChanged.emit(
                    idx, idx, [QtCore.Qt.ItemDataRole.DecorationRole]
                )
                return

    # -- model ---------------------------------------------------------

    def rowCount(self, parent=None):
        return len(self._records)

    def record(self, row):
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def data(self, index, role=0):
        if not index.isValid() or index.row() >= len(self._records):
            return None
        rec = self._records[index.row()]

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return rec.title
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            return self._preview(rec)
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            from matlib.helpers import helpers

            bits = [rec.title]
            if rec.author:
                bits.append("by " + rec.author)
            if rec.licence:
                bits.append(rec.licence)
            if rec.kind == "values":
                bits.append("measured values - no textures")
            return helpers.tooltip_html("\n".join(bits))
        if role == self.RendererLabelRole:
            # What the Type column shows: the source, plus the fact that
            # value-sources produce a preset rather than a textured
            # material.
            return rec.source if rec.kind == "package" else rec.source + " (values)"
        if role == self.RendererRole:
            # Imported materials get their own renderer, "MtlX" - a
            # normal renderer alongside Karma/Redshift/Octane, so it
            # IS part of "All" and needs no special case.
            return "MtlX"
        if role == self.CategoryRole:
            return [rec.category]
        if role == self.TagRole:
            return rec.tags
        if role == self.FavoriteRole:
            return False
        if role == self.IdRole:
            return str(rec.uid)
        if role == self.DateRole:
            return ""
        return None

    def categories(self):
        """The distinct categories of the SELECTED source, for the sidebar
        - from _all (not the search-filtered view), so the list doesn't
        shrink as you type in the filter box. Capitalised, no source
        suffix (the source is the submenu you came in through)."""
        seen = set()
        for rec in self._all:
            if rec.category and self._in_source(rec):
                seen.add(rec.category)
        return sorted(seen, key=str.lower)


class MatxSidebarModel(QtCore.QAbstractListModel):
    """The online browser's sidebar: the categories of the SELECTED
    source (picked from View > Online Materials > <source>). Row 0 is
    "All" (all of that source); the rest are its capitalised categories,
    no source suffix - the source is already in the menu you came in
    through."""

    def __init__(self, online_model, parent=None):
        super().__init__(parent)
        self._online = online_model
        self._rows = ["All"]
        online_model.modelReset.connect(self.refresh)
        self.refresh()

    def refresh(self):
        rows = ["All"] + self._online.categories()
        if rows != self._rows:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()

    def rowCount(self, parent=None):
        return len(self._rows)

    def category_at(self, row):
        """The category for a row, or None for the "All" row."""
        if 0 <= row < len(self._rows):
            return None if row == 0 else self._rows[row]
        return None

    def data(self, index, role=0):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._rows[index.row()]
        return None
