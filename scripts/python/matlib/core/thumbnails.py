"""The ONE thumbnail system - every section's thumbnails flow through
this engine: one system for all sections, by design.

Design:

- **Keys, not rows.** Every thumbnail is identified by a hashable key
  (e.g. ``("library.json", mat_id)``) and deliveries are BY KEY, so
  row reordering or a library reload can never mis-deliver an image -
  the whole generation-guard bug class from the old per-model workers
  dies at the root here.

- **One RAM budget.** A byte-capped LRU shared by every section (the
  "RAM Cache (MB)" preference). Eviction is safe because every
  thumbnail already exists on disk - an evicted row simply re-reads
  its file the next time it scrolls into view. Disk is the swap, and
  it is already written. Small sets (Cop) never reach the budget, so
  no section needs an exemption.

- **States.** absent = never requested, "pending" = in flight,
  "done" = delivered at least once (the image lives in the LRU; an
  evicted key re-queues on its next repaint), "missing" = the load
  genuinely failed - the model shows its placeholder; sticky until
  discard() (a rerender/overwrite discards, so failures get retried
  exactly when their file could actually have changed).

- **Providers.** How bytes become a QImage is the only per-source
  code. Shipped: FILE (materials/cop library PNGs, and texture rows
  already in the disk cache) and CONVERT (textures: native decode or
  sips -> iconvert, the QProcess/QEventLoop mechanics moved here
  VERBATIM from texture_library - the fork-safety and timeout sagas
  stay solved), RENDER (geometry: the model's main-thread Houdini
  pass deposits finished frames via deposit()) and PAINT (colors:
  synchronous paint-on-miss, also via deposit()). Migration complete -
  there is no other thumbnail machinery.
"""

import os
import shutil
import sys
import tempfile
from collections import OrderedDict

from PySide6 import QtCore, QtGui


# Formats QImage can decode natively. Anything else (EXR, HDR, TGA
# depending on the Qt build) needs converting first - see
# _convert_via_iconvert() below.
QT_NATIVE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif")


def _load_native(full_path: str, size: int) -> QtGui.QImage | None:
    img = QtGui.QImage(full_path)
    if img.isNull():
        return None
    return img.scaled(
        size,
        size,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )


def _run_process(program: str, args: list, timeout_ms: int = 30000) -> tuple:
    """Runs a subprocess to completion and returns (success, stderr_text).

    Uses QProcess rather than Python's subprocess module. Houdini on
    macOS is a multi-threaded Cocoa/Qt app, and spawning many child
    processes via Python's fork()-based subprocess from a background
    thread is a known deadlock hazard there: if the fork happens while
    another thread holds a system-library lock (malloc, the ObjC
    runtime), the child inherits that lock frozen and can hang forever in
    the fork-to-exec window, and Python's subprocess does non-trivial
    bookkeeping in that window (pipes/fds for error propagation) which
    widens the risk. QProcess uses Qt's own process-spawning path, which
    keeps that window minimal - safer to call from a Qt worker thread.

    Waits via a scoped QEventLoop rather than QProcess.waitForFinished():
    waitForFinished() is documented to work without a running event loop
    by polling internally, but that polling was found to misbehave on a
    QThread that never calls exec() (as the callers of this function
    don't - they just run a plain Python loop) - iconvert converted a
    real test file in 6.2s when called directly via subprocess.run() from
    the main thread, but the same file consistently hit a hard timeout
    via waitForFinished(). A local QEventLoop, quit from the process's
    finished signal, is the standard Qt idiom for synchronously waiting
    on an async operation from a thread with no persistent event loop of
    its own, and pumps Qt's event system properly regardless."""
    process = QtCore.QProcess()
    loop = QtCore.QEventLoop()
    state = {"timed_out": False}

    def _quit():
        if loop.isRunning():
            loop.quit()

    process.finished.connect(_quit)
    process.errorOccurred.connect(_quit)

    timer = QtCore.QTimer()
    timer.setSingleShot(True)

    def _on_timeout():
        state["timed_out"] = True
        _quit()

    timer.timeout.connect(_on_timeout)

    process.start(program, args)
    timer.start(timeout_ms)
    loop.exec()
    timer.stop()

    if state["timed_out"]:
        process.kill()
        process.waitForFinished(1000)
        return False, "timed out"

    if process.error() == QtCore.QProcess.ProcessError.FailedToStart:
        return False, "failed to start"

    if (
        process.exitStatus() != QtCore.QProcess.ExitStatus.NormalExit
        or process.exitCode() != 0
    ):
        stderr = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
        return False, f"exit {process.exitCode()}: {stderr}"

    return True, ""


def _convert_via_sips(full_path: str, size: int) -> QtGui.QImage | None:
    """macOS-only fast path: sips (Apple's built-in image conversion CLI,
    backed by the ImageIO framework) decodes EXR and Radiance HDR
    natively with no Houdini-process startup cost. Confirmed on a real
    test file at ~0.08s (EXR) and ~1.0s (HDR), against iconvert's ~6.2s -
    roughly an order of magnitude faster for EXR. Tried first; falls back
    to _convert_via_iconvert if sips is missing (non-macOS, or an
    unexpectedly stripped-down macOS install) or fails for any reason -
    iconvert is the guaranteed-correct path since it's the same libraries
    Houdini itself uses, sips is purely a speed optimization on top."""
    if sys.platform != "darwin":
        return None
    sips = shutil.which("sips")
    if sips is None:
        print("Amaze: sips not found on PATH, falling back to iconvert")
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ok, err = _run_process(sips, ["-s", "format", "png", full_path, "--out", tmp_path])
        if not ok:
            print(f"Amaze: sips failed for {full_path} ({err}), falling back to iconvert")
            return None
        image = _load_native(tmp_path, size)
        if image is None:
            print(
                f"Amaze: sips ran but produced an unreadable image for {full_path}, "
                "falling back to iconvert"
            )
        return image
    except Exception as exc:
        print(f"Amaze: sips exception for {full_path}: {exc}, falling back to iconvert")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _convert_via_iconvert(full_path: str, hfs: str, size: int) -> QtGui.QImage | None:
    """Fallback path for when sips isn't available or fails: shells out to
    Houdini's bundled iconvert to get a PNG, without touching the Houdini
    scene at all. Best-effort: falls back to no thumbnail if iconvert
    isn't found or the conversion fails for any reason."""
    iconvert = os.path.join(hfs, "bin", "iconvert")
    if not os.path.exists(iconvert):
        print(f"Amaze: iconvert not found at {iconvert}")
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ok, err = _run_process(iconvert, [full_path, tmp_path])
        if not ok:
            print(f"Amaze: iconvert failed for {full_path} ({err})")
            return None
        image = _load_native(tmp_path, size)
        if image is None:
            print(f"Amaze: iconvert ran but produced an unreadable image for {full_path}")
        return image
    except Exception as exc:
        print(f"Amaze: iconvert exception for {full_path}: {exc}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


class _FileLoader(QtCore.QThread):
    """Loads image files for a batch of keys off the main thread.
    Failures deliberately emit nothing - a key still pending when the
    batch finishes is how the engine knows the file is missing."""

    loaded = QtCore.Signal(object, QtGui.QImage)

    def __init__(self, items) -> None:
        super().__init__()
        self._items = items  # [(key, path)]

    def keys(self):
        return [key for key, _path in self._items]

    def run(self) -> None:
        for key, path in self._items:
            image = QtGui.QImage(path)
            if not image.isNull():
                self.loaded.emit(key, image)


class _ConvertLoader(QtCore.QThread):
    """The CONVERT provider's worker: generates texture thumbnails off
    the UI thread. Each item may shell out to iconvert (native Houdini
    startup overhead, up to a 30s timeout), which is why this work is
    the one kind worth cancelling when the user browses away (see
    ThumbnailEngine.cancel_pending_converts). Only generates images -
    it never touches the disk cache, whose manifest is main-thread-only
    by design (the model writes the cache when a delivery lands)."""

    loaded = QtCore.Signal(object, QtGui.QImage)
    #: fired after EVERY item, success or failure - the progress bar
    #: must advance on a file that fails/times out too, or it stalls
    #: short of 100%.
    attempted = QtCore.Signal(object)

    def __init__(self, items, hfs, force_iconvert) -> None:
        super().__init__()
        self._items = items  # [(key, full_path, ext, size)]
        self._hfs = hfs
        self._force_iconvert = force_iconvert
        self._stop = False
        self._canceled = False

    def keys(self):
        return [item[0] for item in self._items]

    def cancel(self) -> None:
        self._stop = True
        self._canceled = True

    def run(self) -> None:
        for key, full_path, ext, size in self._items:
            if self._stop:
                return
            try:
                if ext in QT_NATIVE_EXTENSIONS:
                    image = _load_native(full_path, size)
                elif self._force_iconvert:
                    image = _convert_via_iconvert(full_path, self._hfs, size)
                else:
                    image = _convert_via_sips(full_path, size)
                    if image is None:
                        image = _convert_via_iconvert(
                            full_path, self._hfs, size
                        )
            except Exception as exc:
                print(
                    f"Amaze: texture thumbnail failed for {full_path}: {exc}"
                )
                image = None
            if self._stop:
                return
            if image is not None:
                self.loaded.emit(key, image)
            self.attempted.emit(key)



class ThumbnailEngine(QtCore.QObject):
    #: a key's image arrived (repaint it) - or its load failed (the
    #: model's data() will now see is_missing() and paint a placeholder)
    ready = QtCore.Signal(object)
    #: a convert item was attempted, success or failure - drives the
    #: texture progress bar
    convert_attempted = QtCore.Signal(object)

    def __init__(self, budget_mb: int = 256) -> None:
        super().__init__()
        self._lru = OrderedDict()  # key -> (QImage, nbytes)
        self._bytes = 0
        self._budget = int(budget_mb) * 1024 * 1024
        self._states = {}  # key -> "pending" | "done" | "missing"
        self._file_queue = []  # [(key, path)] awaiting dispatch
        self._dispatch_scheduled = False
        self._convert_queue = []  # [(key, path, ext, size)]
        self._convert_scheduled = False
        # Convert options, pushed by the texture model per batch (so
        # Preferences changes apply without a restart, same as always).
        self._convert_hfs = ""
        self._convert_parallel = 4
        self._convert_force_iconvert = False
        # Threads stay referenced until finished - dropping a QThread's
        # only Python reference while it runs risks a garbage-collected
        # C++ thread object (the texture worker's #21 lesson).
        self._threads = []

    # -- budget ---------------------------------------------------------

    def set_budget_mb(self, budget_mb) -> None:
        try:
            budget_mb = int(budget_mb)
        except (TypeError, ValueError):
            return
        self._budget = max(64, budget_mb) * 1024 * 1024
        self._evict()

    def _evict(self) -> None:
        # Always keep the newest entry, so one oversized image can't
        # evict itself into a reload loop.
        while self._bytes > self._budget and len(self._lru) > 1:
            _key, (_image, nbytes) = self._lru.popitem(last=False)
            self._bytes -= nbytes

    def _cache_get(self, key):
        item = self._lru.get(key)
        if item is None:
            return None
        self._lru.move_to_end(key)
        return item[0]

    def _cache_put(self, key, image) -> None:
        try:
            nbytes = max(int(image.sizeInBytes()), 1)
        except AttributeError:
            nbytes = 1
        old = self._lru.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        self._lru[key] = (image, nbytes)
        self._bytes += nbytes
        self._evict()

    # -- the request surface models talk to ------------------------------

    def request_file(self, key, path):
        """The FILE provider: return the cached image, or queue a
        background load of `path` and return None (the caller paints
        its loading/placeholder state). Everything queued during one
        paint pass coalesces into a single loader batch via the
        zero-timer. Called from data(), so it must stay cheap."""
        image = self._cache_get(key)
        if image is not None:
            return image
        state = self._states.get(key)
        if state == "pending" or state == "missing":
            return None
        # Never requested - or delivered once and since evicted: load.
        self._states[key] = "pending"
        self._file_queue.append((key, path))
        if not self._dispatch_scheduled:
            self._dispatch_scheduled = True
            QtCore.QTimer.singleShot(0, self._dispatch_files)
        return None

    def peek(self, key):
        """Cache lookup only - no request on miss. Convert-sourced rows
        use this, since their generation is queued eagerly per folder
        rather than driven by paints."""
        return self._cache_get(key)

    def is_pending(self, key) -> bool:
        return self._states.get(key) == "pending"

    def is_missing(self, key) -> bool:
        return self._states.get(key) == "missing"

    def deposit(self, key, image) -> None:
        """Main-thread providers hand finished images straight in:
        geometry's Houdini render pass (renders can't leave the main
        thread) and colors' synchronous paints. Cached under the
        budget, marked done, announced like any other delivery."""
        self._cache_put(key, image)
        self._states[key] = "done"
        self.ready.emit(key)

    def discard(self, key) -> None:
        """Forget a key entirely (image AND state) - a rerender or
        overwrite calls this so the next repaint reloads the fresh
        file, and a previously-missing key gets its retry."""
        old = self._lru.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        self._states.pop(key, None)

    def clear(self) -> None:
        self._lru.clear()
        self._bytes = 0
        self._states = {
            k: s for k, s in self._states.items() if s == "pending"
        }

    # -- the CONVERT provider (textures) ----------------------------------

    def configure_convert(self, hfs, parallel, force_iconvert) -> None:
        self._convert_hfs = hfs
        self._convert_parallel = max(1, min(8, int(parallel)))
        self._convert_force_iconvert = bool(force_iconvert)

    def request_convert(self, key, full_path, ext, size) -> None:
        """Queue a texture for background generation (native decode, or
        sips -> iconvert for EXR/HDR/TGA). Queued eagerly per folder by
        the model - conversions are the expensive one-time work, so the
        whole folder generates on open (with the progress bar) instead
        of waiting for each tile to scroll into view."""
        if self._states.get(key) == "pending":
            return
        self._states[key] = "pending"
        self._convert_queue.append((key, full_path, ext, size))
        if not self._convert_scheduled:
            self._convert_scheduled = True
            QtCore.QTimer.singleShot(0, self._dispatch_converts)

    def cancel_pending_converts(self) -> None:
        """A folder switch abandons its unfinished conversions - the
        one kind of work expensive enough to be worth stopping (a
        revisit simply re-queues). Undelivered keys reset to
        unrequested, never to missing; a canceled loader's late
        delivery is dropped by the state check in _on_loaded."""
        for item in self._convert_queue:
            if self._states.get(item[0]) == "pending":
                self._states.pop(item[0], None)
        self._convert_queue = []
        for thread in self._threads:
            if isinstance(thread, _ConvertLoader) and not thread.isFinished():
                thread.cancel()
                for key in thread.keys():
                    if self._states.get(key) == "pending":
                        self._states.pop(key, None)

    def _dispatch_converts(self) -> None:
        """Split the queued batch round-robin across N concurrent
        loaders (Preferences > Parallel Conversions): each iconvert
        call pays a fixed Houdini-process startup cost regardless of
        file size, so N at once cuts wall-clock roughly by N."""
        self._convert_scheduled = False
        items = self._convert_queue
        self._convert_queue = []
        if not items:
            return
        parallel = self._convert_parallel
        chunks = [c for c in (items[i::parallel] for i in range(parallel)) if c]
        for chunk in chunks:
            loader = _ConvertLoader(
                chunk, self._convert_hfs, self._convert_force_iconvert
            )
            loader.loaded.connect(self._on_loaded)
            loader.attempted.connect(self._on_convert_attempted)
            loader.finished.connect(self._prune_threads)
            self._threads.append(loader)
            loader.start()

    def _on_convert_attempted(self, key) -> None:
        self.convert_attempted.emit(key)

    # -- delivery ---------------------------------------------------------

    def _dispatch_files(self) -> None:
        self._dispatch_scheduled = False
        items = self._file_queue
        self._file_queue = []
        if not items:
            return
        loader = _FileLoader(items)
        loader.loaded.connect(self._on_loaded)
        loader.finished.connect(self._prune_threads)
        self._threads.append(loader)
        loader.start()

    def _on_loaded(self, key, image) -> None:
        if self._states.get(key) != "pending":
            # Discarded while in flight (library switched away, or a
            # rerender superseded it) - drop the stale delivery.
            return
        self._cache_put(key, image)
        self._states[key] = "done"
        self.ready.emit(key)

    def _prune_threads(self) -> None:
        """A loader finished. Its deliveries were queued before its
        finished signal, so any of its keys STILL pending never
        delivered - the file is genuinely unreadable/absent: mark
        missing and notify, so the row repaints with its placeholder."""
        finished = [t for t in self._threads if t.isFinished()]
        self._threads = [t for t in self._threads if not t.isFinished()]
        for thread in finished:
            if getattr(thread, "_canceled", False):
                # Cancelled work is unrequested, not missing - the
                # revisit re-queues it.
                continue
            for key in thread.keys():
                if self._states.get(key) == "pending":
                    self._states[key] = "missing"
                    self.ready.emit(key)


#: the app-wide engine every section shares
engine = ThumbnailEngine()
