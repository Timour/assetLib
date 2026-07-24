"""The debug engine: a structured session log, off unless asked for.

Why this exists: several bugs this project cost multiple round-trips
because the evidence wasn't recorded anywhere. Python `print()` does NOT
reach Houdini's Log Window (only the terminal Houdini was launched from),
and PySide swallows exceptions raised inside Qt slots after printing them
to stderr - so a failure could be completely invisible in a saved log
while looking obvious live.

It writes to one file that can be read and analysed directly:

    ~/Library/Logs/AssetLib/assetlib_debug.jsonl

**Two tiers:**

* **Crash recorder - ALWAYS ON, but ONLY a real crash.** An *uncaught*
  exception (via the hook `install()` arms at panel construction) is
  always written, even with Debug Mode off, so if the app actually
  crashes there's a log to read - carrying the environment header
  (Houdini version, which renderer plugins loaded). Nothing else is
  always-on. A quiet session with Debug off writes nothing; a crash
  starts the log.
* **Verbose tier - Debug Mode gated.** `event()` / `note()` /
  `exception()` (handled) / `prefs_snapshot()` only write when Debug Mode
  is on (Preferences → Debug). Debug Off means OFF. Development
  sessions typically run with it on; turning it off silences everything
  except a crash.

**Format: JSON Lines** - one self-describing JSON object per line. Not
prose, because the point is machine analysis: filter by category
(`session` / `exception` / `note` / your own), count failures, diff two
sessions, reconstruct a sequence.

**Cost when off is one boolean test** for the verbose tier; the crash
recorder only does work if the app actually crashes.

Use:
    from matlib.core import debug

    debug.note("thumbnail missing", path=p)      # prints AND logs
    debug.event("import", "shader wired", node=n.path())
    debug.exception("import_record")             # full traceback
    with debug.timed("thumbs", "render all", count=n):
        ...
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import sys
import traceback
from contextlib import contextmanager

#: Default location. Deliberately NOT the library folder (which is
#: cloud-synced) nor the repo - a log is local-machine state, same
#: reasoning as the thumbnail caches.
DEFAULT_DIR = os.path.expanduser("~/Library/Logs/AssetLib")
DEFAULT_NAME = "assetlib_debug.jsonl"

#: Start a fresh file once the old one passes this, so a forgotten
#: Debug Mode can't fill a disk.
MAX_BYTES = 32 * 1024 * 1024

_enabled = False
_path = os.path.join(DEFAULT_DIR, DEFAULT_NAME)
_session = ""
_seq = 0
_excepthook_installed = False
_previous_excepthook = None
_installed = False


def log_path() -> str:
    """Where the log is written (shown in Preferences)."""
    return _path


def is_on() -> bool:
    """Guard expensive data-gathering at the call site with this."""
    return _enabled


def install() -> None:
    """Arm the crash recorder: capture UNCAUGHT exceptions only,
    independent of Debug Mode. Call once at panel construction.

    A genuine crash - an exception that propagates uncaught (including
    ones PySide swallows inside a Qt slot) - is always worth a record, so
    it's the one thing logged with Debug Mode off. Everything else
    (event/note/handled-exception) stays gated: Debug Off means off."""
    global _installed
    if _installed:
        return
    _installed = True
    _install_excepthook()


def _ensure_session() -> None:
    """Start a session (and write its header) if one hasn't begun. Lets an
    error land in a crash-only log - Debug Mode never turned on - still
    carrying the environment header (Houdini version, which renderer
    plugins loaded, ...) that context needs."""
    global _session
    if not _session:
        _session = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        _rotate()
        _write_session_header()


def configure(enabled: bool, path: str = "") -> None:
    """Turn VERBOSE logging on or off. Called at panel setup and whenever
    Preferences closes, so the switch takes effect without a restart.
    Crash capture (install()) is separate and always on."""
    global _enabled, _path
    was_on = _enabled
    if path:
        _path = path
    _enabled = bool(enabled)
    install()
    if _enabled and not was_on:
        _ensure_session()
        event("session", "debug mode on")
    elif was_on and not _enabled:
        event("session", "debug mode turned off")


def _rotate() -> None:
    try:
        if os.path.exists(_path) and os.path.getsize(_path) > MAX_BYTES:
            os.replace(_path, _path + ".1")
    except OSError:
        pass


def _write(record: dict) -> None:
    global _seq
    _seq += 1
    record["n"] = _seq
    record["t"] = round(
        datetime.datetime.now().timestamp(), 3
    )
    record["clock"] = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    record["session"] = _session
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a") as handle:
            handle.write(json.dumps(record, default=_stringify) + "\n")
    except Exception:
        # A logger must never be the thing that breaks the app.
        pass


def _stringify(value):
    """Anything not JSON-native (hou.Node, QImage, Sdf.Path...) becomes
    its repr rather than killing the write."""
    try:
        return str(value)
    except Exception:
        return "<unserialisable>"


def _write_session_header() -> None:
    """Everything about the environment that has ever mattered when
    diagnosing something here, recorded once per session."""
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "assetlib_env": os.environ.get("ASSETLIB", ""),
    }
    try:
        import hou

        info["houdini"] = hou.applicationVersionString()
        info["product"] = hou.applicationName()
        # Which renderer plugins actually loaded - "Invalid node type
        # name" bugs have twice turned out to be a missing plugin.
        for label, type_name in (
            ("redshift", "redshift_vopnet"),
            ("octane", "octane_vopnet"),
            ("mtlx", "mtlxstandard_surface"),
        ):
            info["has_" + label] = bool(
                hou.vopNodeTypeCategory().nodeTypes().get(type_name)
            )
    except Exception as exc:
        info["houdini_probe_failed"] = str(exc)
    # Written DIRECTLY (not via event()) so the header lands even when
    # Debug Mode is off - a crash-only log still needs its environment.
    info["debug_mode"] = _enabled
    _write({"cat": "session", "msg": "session start", "data": info})


def prefs_snapshot(preferences) -> None:
    """Record the settings that change behaviour. Paths included - they
    are local paths in a local log, and 'which library was open' is
    routinely the answer."""
    if not _enabled or preferences is None:
        return
    keys = (
        "dir", "rendersize", "rendersamples", "karma_rendersamples",
        "render_on_import", "view_mode", "last_renderer", "thumbsize",
        "ram_cache_mb", "matx_resolution", "enabled_sections",
        "hide_empty_categories", "texture_parallel_conversions",
        "texture_force_iconvert", "geometry_shading_mode", "geometry_bg",
        "scroll_speed", "accent_color",
    )
    data = {}
    for key in keys:
        try:
            data[key] = getattr(preferences, key)
        except Exception:
            pass
    event("session", "preferences", **data)


def event(category: str, message: str, /, **data) -> None:
    """Record a structured entry. Silent when Debug Mode is off.

    `category` and `message` are POSITIONAL-ONLY (the `/`): callers pass
    arbitrary keys in **data, and a key named `category`, `message` or
    `where` would otherwise collide with these parameters and raise
    TypeError. That happened immediately in real use - an import passing
    `category=record.category` raised inside a Qt slot, which PySide
    swallows, so the import silently did nothing."""
    if not _enabled:
        return
    _write({"cat": category, "msg": message, "data": data})


def note(message: str, /, **data) -> None:
    """Print to the console AND record it.

    The `Amaze: ...` prints are the project's established diagnostic
    convention; this keeps them visible live while also capturing them
    (with structure) in a file that can be read afterwards."""
    print("Amaze: " + message)
    if _enabled:
        _write({"cat": "note", "msg": message, "data": data})


def exception(where: str, exc: BaseException | None = None, /, **data) -> None:
    """Record a full traceback for a HANDLED exception. Debug-Mode gated,
    like event()/note() - it's diagnostic detail for a reproduced bug,
    and Debug Off means off.

    A genuine CRASH (an *uncaught* exception) is different: the installed
    hook always records that, Debug Mode or not - see install()."""
    if not _enabled:
        return
    text = traceback.format_exc() if exc is None else "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    _write({
        "cat": "exception",
        "msg": where,
        "data": data,
        "traceback": text,
    })


@contextmanager
def timed(category: str, message: str, /, **data):
    """Time a block and record the duration - 'why is this slow' is a
    recurring question here (iconvert, Karma thumbnails, geometry
    renders)."""
    if not _enabled:
        yield
        return
    start = datetime.datetime.now()
    failed = None
    try:
        yield
    except BaseException as exc:      # noqa: BLE001 - re-raised below
        failed = exc
        raise
    finally:
        ms = (datetime.datetime.now() - start).total_seconds() * 1000.0
        payload = dict(data)
        payload["ms"] = round(ms, 1)
        if failed is not None:
            payload["failed"] = str(failed)
        _write({"cat": category, "msg": message, "data": payload})


def image_stats(path: str) -> dict:
    """Measure a rendered image, so "it looks black" becomes a number.

    Added after a round where a thumbnail was reported black and there
    was no way to tell WHICH kind of black: an all-zero render, a
    transparent (zero-alpha) image, the missing-thumbnail placeholder, or
    a stale file from a previous attempt. Each has a different cause and
    they are indistinguishable by eye at tile size."""
    info = {"path": path}
    try:
        info["exists"] = os.path.exists(path)
        if not info["exists"]:
            return info
        info["bytes"] = os.path.getsize(path)
        info["mtime_age_s"] = round(
            datetime.datetime.now().timestamp() - os.path.getmtime(path), 1
        )
        from PySide6 import QtGui

        image = QtGui.QImage(path)
        if image.isNull():
            info["unreadable"] = True
            return info
        info["size"] = "%dx%d" % (image.width(), image.height())
        info["has_alpha"] = image.hasAlphaChannel()
        # Sample a grid rather than every pixel - cheap and plenty to
        # characterise a thumbnail.
        step = max(1, min(image.width(), image.height()) // 32)
        total = black = transparent = 0
        lum_sum = 0.0
        lum_max = 0.0
        for y in range(0, image.height(), step):
            for x in range(0, image.width(), step):
                colour = image.pixelColor(x, y)
                lum = (
                    0.2126 * colour.redF()
                    + 0.7152 * colour.greenF()
                    + 0.0722 * colour.blueF()
                )
                lum_sum += lum
                lum_max = max(lum_max, lum)
                if lum < 0.004:
                    black += 1
                if colour.alphaF() < 0.004:
                    transparent += 1
                total += 1
        if total:
            info["mean_luminance"] = round(lum_sum / total, 4)
            info["max_luminance"] = round(lum_max, 4)
            info["percent_black"] = round(100.0 * black / total, 1)
            info["percent_transparent"] = round(100.0 * transparent / total, 1)
            info["verdict"] = (
                "fully transparent" if transparent == total
                else "all black" if black == total
                else "mostly black" if black > total * 0.95
                else "has content"
            )
    except Exception as exc:
        info["stats_failed"] = str(exc)
    return info


def material_snapshot(shader, builder=None) -> dict:
    """What a shading network actually CONTAINS.

    Records the shader's effective input values, whether each is driven
    by a connection or a constant, and every texture path with whether
    the file is really on disk. Written after the imported-MaterialX
    round, where the question "is the material itself wrong, or only its
    thumbnail" could not be answered without opening it by hand."""
    if shader is None:
        return {}
    info = {}
    try:
        info["shader"] = shader.name()
        info["shader_type"] = shader.type().name()
        inputs = {}
        for name, source in zip(shader.inputNames(), shader.inputs()):
            parm = shader.parmTuple(name) or shader.parm(name)
            entry = {"driven_by": source.name() if source else None}
            if parm is not None:
                try:
                    entry["value"] = str(parm.eval())
                except Exception:
                    pass
            # A promoted parm that disagrees with the shader is the exact
            # shape of the opacity=0 bug.
            if builder is not None and source is not None and \
                    source.type().name() == "parameter":
                promoted = builder.parmTuple(name) or builder.parm(name)
                if promoted is not None:
                    try:
                        promoted_value = str(promoted.eval())
                        if promoted_value != entry.get("value"):
                            entry["promoted_DIFFERS"] = promoted_value
                    except Exception:
                        pass
            if source is not None or entry.get("value") not in (None, "(0.0,)"):
                inputs[name] = entry
        info["inputs"] = inputs
    except Exception as exc:
        info["snapshot_failed"] = str(exc)
    return info


def texture_snapshot(root) -> list:
    """Every file reference under a network, and whether it resolves.

    A texture path that does not exist renders black without any error
    Houdini would surface."""
    out = []
    if root is None:
        return out
    try:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.isNetwork():
                stack.extend(node.children())
            for parm in node.parms():
                try:
                    template = parm.parmTemplate()
                    import hou

                    if not isinstance(template, hou.StringParmTemplate):
                        continue
                    if template.stringType() != hou.stringParmType.FileReference:
                        continue
                except Exception:
                    continue
                value = parm.eval()
                if not value:
                    continue
                out.append({
                    "node": node.name(),
                    "parm": parm.name(),
                    "path": value,
                    "exists": os.path.exists(value),
                })
    except Exception as exc:
        out.append({"snapshot_failed": str(exc)})
    return out


def node_snapshot(node, depth: int = 1) -> dict:
    """Describe a node the way these bugs need it: type, children and
    their types, and connectivity. Written for the material/builder
    questions that keep coming up ('is it actually a Karma builder',
    'what did editmaterial hand back', 'is the shader wired')."""
    if node is None:
        return {}
    try:
        info = {
            "path": node.path(),
            "type": node.type().name(),
            "is_network": node.isNetwork(),
        }
        try:
            info["shader_language"] = node.shaderLanguageName()
        except Exception:
            pass
        for parm_name in ("shader_rendercontextname", "tabmenumask"):
            parm = node.parm(parm_name)
            if parm is not None:
                info[parm_name] = parm.eval()
        if depth > 0 and node.isNetwork():
            children = []
            for child in node.children():
                entry = {
                    "name": child.name(),
                    "type": child.type().name(),
                }
                try:
                    entry["inputs"] = [
                        i.name() if i else None for i in child.inputs()
                    ]
                except Exception:
                    pass
                children.append(entry)
            info["children"] = children
            info["child_count"] = len(children)
        return info
    except Exception as exc:
        return {"snapshot_failed": str(exc)}


def _install_excepthook() -> None:
    """Capture unhandled exceptions - including the ones raised inside Qt
    slots, which PySide prints to stderr and then swallows, so they never
    appear in a saved Houdini log."""
    global _excepthook_installed, _previous_excepthook
    if _excepthook_installed:
        return
    _previous_excepthook = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        try:
            # ALWAYS captured, Debug Mode or not - this is the whole
            # point of the crash recorder.
            _ensure_session()
            _write({
                "cat": "exception",
                "msg": "unhandled: %s" % exc_type.__name__,
                "data": {"value": str(exc_value)},
                "traceback": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ),
            })
        finally:
            if _previous_excepthook is not None:
                _previous_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = hook
    _excepthook_installed = True
