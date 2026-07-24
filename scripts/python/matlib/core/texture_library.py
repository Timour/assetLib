"""
Models for the Textures section: a flat list of registered folder
pointers (no subfolder recursion, no database) and the image files found
directly inside whichever folder is currently selected.
"""

import hashlib
import json
import os

from PySide6 import QtCore, QtGui

from matlib.core import thumbnails

from matlib.prefs import prefs

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".exr",
    ".tif",
    ".tiff",
    ".tga",
    ".bmp",
    ".hdr",
)

# Texture thumbnails generate at Preferences > RenderSize - the same
# resolution materials render their shaderball thumbnail at - rather than
# a separate hidden setting; the two are unified into a single setting.

# Local-machine-only cache (not the Jottacloud-synced install folder, not
# the repo) - thumbnails are cheap byproducts, no reason to sync them.
# The resolution is baked into the dir name so a RenderSize change can't
# serve stale cached images generated at the old size.
def _cache_dir_for(size: int, prefix: str = "texture_thumbnails") -> str:
    new_root = os.path.expanduser("~/Library/Caches/AssetLib")
    old_root = os.path.expanduser("~/Library/Caches/egMatLib")
    # One-time migration from the pre-rebrand cache location, so no
    # thumbnail regenerates just because the folder got a new name.
    if os.path.isdir(old_root) and not os.path.exists(new_root):
        try:
            os.rename(old_root, new_root)
        except OSError:
            pass
    return os.path.join(new_root, f"{prefix}_{size}")


class ThumbnailCache:
    """Disk-backed cache of generated texture thumbnails, keyed by source
    file path and kept 1:1 with what's actually in a folder: reconcile()
    drops any cached entry whose source file is gone or has changed
    (mtime/size), so stale thumbnails never linger. All methods are only
    ever called from the main thread - manifest mutation is not
    thread-safe by design, the background worker only generates images,
    it never touches the cache itself."""

    def __init__(self, size: int, prefix: str = "texture_thumbnails") -> None:
        self.size = size
        self.cache_dir = _cache_dir_for(size, prefix)
        self.manifest_path = os.path.join(self.cache_dir, "manifest.json")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._manifest: dict = self._load_manifest()
        self._dirty = False

    def _load_manifest(self) -> dict:
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(self._manifest, f)
            self._dirty = False
        except Exception as exc:
            print(f"Amaze: failed to save texture thumbnail cache manifest: {exc}")

    @staticmethod
    def _cache_filename(full_path: str) -> str:
        return hashlib.sha1(full_path.encode("utf-8")).hexdigest() + ".png"

    def _cache_path(self, full_path: str) -> str:
        return os.path.join(self.cache_dir, self._cache_filename(full_path))

    def valid_path(self, full_path: str) -> str | None:
        """The cached PNG's path if the manifest entry still matches the
        source file on disk (same mtime/size) - a stat, no decode. The
        engine's background file loader does the actual reading, so
        folder opens no longer pay a synchronous decode per cached
        file on the main thread."""
        entry = self._manifest.get(full_path)
        if not entry:
            return None
        try:
            st = os.stat(full_path)
        except OSError:
            return None
        if entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
            return None
        cache_path = self._cache_path(full_path)
        if not os.path.exists(cache_path):
            return None
        return cache_path

    def get(self, full_path: str) -> QtGui.QImage | None:
        """A cached thumbnail if the manifest entry still matches the file
        on disk (same mtime/size), else None."""
        entry = self._manifest.get(full_path)
        if not entry:
            return None
        try:
            st = os.stat(full_path)
        except OSError:
            return None
        if entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
            return None
        cache_path = self._cache_path(full_path)
        if not os.path.exists(cache_path):
            return None
        img = QtGui.QImage(cache_path)
        return img if not img.isNull() else None

    def put(self, full_path: str, image: QtGui.QImage) -> None:
        """Persist a freshly generated thumbnail and record it in the
        manifest. Does not flush to disk - call save() when convenient."""
        try:
            st = os.stat(full_path)
        except OSError:
            return
        try:
            image.save(self._cache_path(full_path), "PNG")
        except Exception as exc:
            print(f"Amaze: failed to write thumbnail cache for {full_path}: {exc}")
            return
        self._manifest[full_path] = {"mtime": st.st_mtime, "size": st.st_size}
        self._dirty = True

    def reconcile(self, folder: str, current_names: list) -> None:
        """Drop cache entries for this folder whose source file is gone or
        has changed, so the cache stays 1:1 with the folder's contents."""
        current_full = {os.path.join(folder, name) for name in current_names}
        stale = []
        for full_path, entry in self._manifest.items():
            if os.path.dirname(full_path) != folder:
                continue
            if full_path not in current_full:
                stale.append(full_path)
                continue
            try:
                st = os.stat(full_path)
            except OSError:
                stale.append(full_path)
                continue
            if entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
                stale.append(full_path)

        if not stale:
            return
        for full_path in stale:
            cache_path = self._cache_path(full_path)
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
            del self._manifest[full_path]
        self._dirty = True
        self.save()

    def invalidate(self, full_path: str) -> None:
        """Evict a single cache entry ("Rerender Thumbnail") - unlike
        reconcile(), which only drops entries whose source file is gone
        or changed, this drops a still-valid entry on request because the
        user wants a fresh render regardless."""
        if full_path not in self._manifest:
            return
        cache_path = self._cache_path(full_path)
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
            except OSError:
                pass
        del self._manifest[full_path]
        self._dirty = True
        self.save()

    def clear(self) -> None:
        """Delete every cached thumbnail file and reset the manifest, in
        memory and on disk. Sweeps every texture_thumbnails_* AND
        geo_thumbnails_* directory, not just the current one's - the
        geometry section keys its dirs by shading mode + background +
        resolution, so combinations tried once would otherwise sit
        orphaned in ~/Library/Caches forever."""
        parent = os.path.dirname(self.cache_dir)
        try:
            for name in os.listdir(parent):
                if name.startswith(("texture_thumbnails_", "geo_thumbnails_")):
                    shutil.rmtree(os.path.join(parent, name), ignore_errors=True)
        except OSError:
            pass
        os.makedirs(self.cache_dir, exist_ok=True)
        self._manifest = {}
        self._dirty = True
        self.save()


class TextureFolders(QtCore.QAbstractListModel):
    """Flat list of registered folder paths for the Textures section, plus
    a synthetic "All" pseudo-entry always pinned at row 0 (mirroring the
    Materials category list's own "All" pseudo-category) that aggregates
    every registered folder's contents when selected - see
    TextureFiles.set_all_folders(). These are pointers, not an index of
    the images themselves - adding a folder does not scan or copy
    anything. No subfolder recursion: if a folder has subfolders worth
    browsing, add them as separate entries."""

    PathRole = QtCore.Qt.ItemDataRole.UserRole
    ALL_LABEL = "All"
    # See category.py's SIDEBAR_COUNT_ROLE comment - keep in sync.
    COUNT_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 40

    def __init__(self, preferences: prefs.Prefs, parent: QtCore.QObject | None = None) -> None:
        super().__init__()
        self.preferences = preferences
        # Folder file-counts, cached so painting the sidebar never
        # touches the disk - cleared via refresh_counts() on section
        # activation, folder add/remove and subfolder-toggle changes.
        self._counts: dict = {}

    def refresh_counts(self) -> None:
        self._counts = {}

    def _folder_count(self, path: str) -> int:
        count = self._counts.get(path)
        if count is not None:
            return count
        count = 0
        try:
            if getattr(self.preferences, "texture_include_subfolders", False):
                for _dirpath, dirnames, filenames in os.walk(path):
                    dirnames.sort()
                    count += sum(
                        1
                        for name in filenames
                        if name.lower().endswith(IMAGE_EXTENSIONS)
                    )
            else:
                count = sum(
                    1
                    for name in os.listdir(path)
                    if name.lower().endswith(IMAGE_EXTENSIONS)
                )
        except OSError:
            count = 0
        self._counts[path] = count
        return count

    def rowCount(
        self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex | None = None
    ) -> int:
        return len(self.preferences.texture_folders) + 1

    def data(
        self, index: QtCore.QModelIndex | QtCore.QPersistentModelIndex, role: int = 0
    ):
        if not index.isValid():
            return None
        row = index.row()
        if row == 0:
            # The "All" row has no real path - PathRole (and everything
            # else except the count) stays None, which callers use to
            # detect it.
            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                return self.ALL_LABEL
            if role == self.COUNT_ROLE:
                return sum(
                    self._folder_count(p)
                    for p in self.preferences.texture_folders
                )
            return None
        path = self.preferences.texture_folders[row - 1]
        if role == self.COUNT_ROLE:
            return self._folder_count(path)
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return os.path.basename(path.rstrip("/\\")) or path
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            return path
        if role == self.PathRole:
            return path
        return None

    def add_folder(self, path: str) -> None:
        """Register a folder pointer. No-op if already registered."""
        if not path or path in self.preferences.texture_folders:
            return
        row = len(self.preferences.texture_folders) + 1
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self.preferences.add_texture_folder(path)
        self.endInsertRows()
        self.refresh_counts()

    def remove_folder(self, row: int) -> None:
        # Row 0 is the synthetic "All" entry, not a real registered
        # folder - nothing to remove.
        if row <= 0 or not row - 1 < len(self.preferences.texture_folders):
            return
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        path = self.preferences.texture_folders[row - 1]
        self.preferences.remove_texture_folder(path)
        self.endRemoveRows()
        self.refresh_counts()


class TextureFiles(QtCore.QAbstractListModel):
    """Image files found directly (non-recursive) inside either the
    currently selected texture folder, or - in "All" mode - every
    registered folder aggregated together (still non-recursive per
    folder). No database, no persistence of the file list itself -
    rescanned live every time the folder selection changes. Thumbnails
    are cached to disk (ThumbnailCache) so revisiting a folder is
    instant; everything flows through the unified thumbnail engine
    (core/thumbnails.py): cached rows load lazily as they scroll into
    view (FILE provider), uncached rows generate eagerly per folder in
    the background (CONVERT provider) with the progress bar, and the
    shared RAM budget owns residency."""

    FormatRole = QtCore.Qt.ItemDataRole.UserRole + 1
    PathRole = QtCore.Qt.ItemDataRole.UserRole + 2
    FavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 3
    #: the containing folder's display name - list mode's Category
    #: column (textures have no categories; the folder plays that part)
    FolderRole = QtCore.Qt.ItemDataRole.UserRole + 4

    # (done, total) for the current folder's background generation batch.
    # total == 0 means nothing to generate (fully cached, or empty folder).
    progress_changed = QtCore.Signal(int, int)

    def __init__(self, preferences: prefs.Prefs, parent: QtCore.QObject | None = None) -> None:
        super().__init__()
        self.preferences = preferences
        self._folder = ""
        self._all_folders_mode = False
        # (folder, filename) pairs, not just filenames - "All" mode
        # aggregates files from several different folders at once, so
        # there's no single shared self._folder to reconstruct a full
        # path from any more.
        self._files: list[tuple] = []
        self._cache: ThumbnailCache | None = None
        # Per-row engine spec: (key, kind, payload). kind "file" points
        # at the valid cached PNG (lazy, loads on view); "convert"
        # carries the source path (queued eagerly at folder load, with
        # a disk fallback in data() for revisits after eviction).
        self._row_specs: list[tuple] = []
        self._key_rows: dict = {}
        # Freshly generated keys whose image still needs the
        # main-thread cache write when its delivery lands
        # (ThumbnailCache's manifest is main-thread-only by design).
        self._pending_writes: dict = {}
        self._progress_keys: set = set()
        self._progress_done = 0
        self._progress_total = 0
        thumbnails.engine.ready.connect(self._on_thumb_key_ready)
        thumbnails.engine.convert_attempted.connect(self._on_convert_attempted)

    def _get_cache(self) -> ThumbnailCache:
        """Texture thumbnails generate at Preferences > RenderSize (shared
        with materials). Preferences is a modal
        dialog, so RenderSize can only change between folder browses, not
        mid-batch - refreshing here (called at the top of _load() and
        clear_cache()) is always enough to pick up a change."""
        size = self.preferences.rendersize
        if self._cache is None or self._cache.size != size:
            self._cache = ThumbnailCache(size)
        return self._cache

    def rowCount(
        self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex | None = None
    ) -> int:
        return len(self._files)

    def set_folder(self, path: str) -> None:
        """Rescan a single folder (flat, no recursion) for image files."""
        self._all_folders_mode = False
        self._folder = path
        self._load([path] if path else [])

    def set_all_folders(self) -> None:
        """The "All" pseudo-folder: aggregate every registered folder's
        contents (each still scanned non-recursively) into one combined
        listing."""
        self._all_folders_mode = True
        self._folder = ""
        self._load(list(self.preferences.texture_folders))

    def _load(self, folders: list) -> None:
        """Shared scan/cache/dispatch logic for both set_folder() (a
        single-element list) and set_all_folders() (every registered
        folder) - serves whatever's already cached immediately, and
        kicks off background generation for the rest."""
        # A folder switch abandons the old folder's unfinished
        # conversions (the revisit re-queues them).
        thumbnails.engine.cancel_pending_converts()
        self._cache = self._get_cache()

        include_sub = bool(
            getattr(self.preferences, "texture_include_subfolders", False)
        )
        self.beginResetModel()
        self._files = []
        for folder in folders:
            if folder and os.path.isdir(folder):
                if include_sub:
                    # Opt-in recursion (sidebar right-click toggle) -
                    # files keep their CONTAINING dir in the pair, so
                    # paths, cache keys and the Category column (folder
                    # name) all stay correct per subfolder.
                    for dirpath, dirnames, filenames in os.walk(folder):
                        dirnames.sort(key=str.lower)
                        for name in sorted(filenames, key=str.lower):
                            if name.lower().endswith(IMAGE_EXTENSIONS):
                                self._files.append((dirpath, name))
                else:
                    for name in sorted(os.listdir(folder)):
                        if name.lower().endswith(IMAGE_EXTENSIONS):
                            self._files.append((folder, name))
        self.endResetModel()

        if not self._files:
            self._row_specs = []
            self._key_rows = {}
            self._pending_writes = {}
            self._progress_done = 0
            self._progress_total = 0
            self.progress_changed.emit(0, 0)
            return

        # Reconcile per CONTAINING dir (== the registered folder in
        # flat mode; each subfolder in recursive mode).
        containing = {}
        for f, name in self._files:
            containing.setdefault(f, []).append(name)
        for f, names in containing.items():
            self._cache.reconcile(f, names)

        self._configure_engine_convert()
        size = self._cache.size
        self._row_specs = []
        self._key_rows = {}
        # Canceled conversions never deliver, so entries from an
        # abandoned folder would linger (and could later trigger a
        # redundant cache rewrite on a plain file load) - start clean.
        self._pending_writes = {}
        self._progress_keys = set()
        misses = 0
        for row, (folder, name) in enumerate(self._files):
            full_path = os.path.join(folder, name)
            key = ("tex", full_path, size)
            self._key_rows[key] = row
            cached_png = self._cache.valid_path(full_path)
            if cached_png is not None:
                # Lazy: the engine's file loader reads it when the row
                # scrolls into view (folder opens no longer pay a
                # synchronous decode per cached file).
                self._row_specs.append((key, "file", cached_png))
            else:
                # Eager: conversions are the expensive one-time work -
                # generate the whole folder in the background now.
                self._row_specs.append((key, "convert", full_path))
                ext = os.path.splitext(name)[1].lower()
                thumbnails.engine.discard(key)
                thumbnails.engine.request_convert(key, full_path, ext, size)
                self._pending_writes[key] = full_path
                self._progress_keys.add(key)
                misses += 1

        self._progress_done = 0
        self._progress_total = misses
        self.progress_changed.emit(self._progress_done, self._progress_total)

    def _configure_engine_convert(self) -> None:
        """Push the live conversion options (they apply on the next
        batch without a restart, as always)."""
        import hou

        thumbnails.engine.configure_convert(
            hou.expandString("$HFS"),
            self.preferences.texture_parallel_conversions,
            self.preferences.texture_force_iconvert,
        )

    def _on_thumb_key_ready(self, key) -> None:
        """Engine delivery: repaint the row, and give a freshly
        GENERATED image its main-thread disk-cache write (cache-hit
        file loads skip that - they came FROM the cache)."""
        row = self._key_rows.get(key)
        if row is None:
            return
        full_path = self._pending_writes.pop(key, None)
        if full_path is not None and self._cache is not None:
            image = thumbnails.engine.peek(key)
            if image is not None:
                self._cache.put(full_path, image)
        self.dataChanged.emit(
            self.index(row),
            self.index(row),
            [QtCore.Qt.ItemDataRole.DecorationRole],
        )

    def _on_convert_attempted(self, key) -> None:
        """Advance the progress bar for every attempted item, success
        or failure (a failed/timed-out file must not stall the bar
        short of 100%); flush the cache manifest once the batch
        completes."""
        if key not in self._progress_keys:
            return
        self._progress_keys.discard(key)
        self._progress_done += 1
        self.progress_changed.emit(self._progress_done, self._progress_total)
        if self._progress_done >= self._progress_total and self._cache:
            self._cache.save()

    def clear_cache(self) -> None:
        """Wipe the on-disk thumbnail cache (all resolutions - see
        ThumbnailCache.clear()) and forget everything cached in memory
        (Preferences > Clear Texture Thumbnail Cache). Deliberately does
        NOT kick off regeneration here - this runs from inside the modal
        Preferences dialog (itself further nested inside Houdini's native
        confirm/message popups), and starting new QThreads whose results
        need to reach the UI via queued cross-thread signals while still
        nested that deep proved unreliable in practice. See
        refresh_current_folder(), called by the panel once Preferences
        has fully closed and normal event processing has resumed."""
        self._get_cache().clear()
        # Forget the in-memory copies too - every section's, deliberately:
        # the engine's budget refills from disk on view, and materials
        # reload their PNGs for pennies.
        thumbnails.engine.clear()

    def refresh_current_folder(self) -> None:
        """Re-runs the current selection (single folder or "All"). Safe
        to call unconditionally and often - if nothing's changed since
        the last visit (nothing was cleared, no files edited) every item
        is still a cache hit and this is a cheap no-op."""
        if self._all_folders_mode:
            self.set_all_folders()
        elif self._folder:
            self.set_folder(self._folder)

    def toggle_favorite(self, row: int) -> None:
        if not 0 <= row < len(self._files):
            return
        folder, name = self._files[row]
        full_path = os.path.join(folder, name)
        if full_path in self.preferences.texture_favorites:
            self.preferences.remove_texture_favorite(full_path)
        else:
            self.preferences.add_texture_favorite(full_path)
        self.dataChanged.emit(self.index(row), self.index(row), [self.FavoriteRole])

    def rerender_thumbnails(self, rows: list) -> None:
        """Force-regenerates the cached thumbnail for specific rows -
        evicts the disk entry and the engine key, then queues a fresh
        conversion for just those rows (not a full folder rescan)."""
        if self._cache is None:
            self._cache = self._get_cache()
        self._configure_engine_convert()
        size = self._cache.size
        count = 0
        for row in rows:
            if not 0 <= row < len(self._files):
                continue
            folder, name = self._files[row]
            full_path = os.path.join(folder, name)
            self._cache.invalidate(full_path)
            key = ("tex", full_path, size)
            thumbnails.engine.discard(key)
            if 0 <= row < len(self._row_specs):
                self._row_specs[row] = (key, "convert", full_path)
            ext = os.path.splitext(name)[1].lower()
            thumbnails.engine.request_convert(key, full_path, ext, size)
            self._pending_writes[key] = full_path
            self._progress_keys.add(key)
            count += 1
            self.dataChanged.emit(
                self.index(row),
                self.index(row),
                [QtCore.Qt.ItemDataRole.DecorationRole],
            )
        if not count:
            return
        self._progress_done = 0
        self._progress_total = count
        self.progress_changed.emit(self._progress_done, self._progress_total)

    def data(
        self, index: QtCore.QModelIndex | QtCore.QPersistentModelIndex, role: int = 0
    ):
        if not index.isValid():
            return None
        row = index.row()
        folder, name = self._files[row]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return name
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            if not 0 <= row < len(self._row_specs):
                return None
            key, kind, payload = self._row_specs[row]
            if kind == "file":
                return thumbnails.engine.request_file(key, payload)
            # Convert-sourced row: generation was queued eagerly at
            # folder load - just serve what's landed so far.
            image = thumbnails.engine.peek(key)
            if image is not None:
                return image
            if not thumbnails.engine.is_pending(key) and not (
                thumbnails.engine.is_missing(key)
            ):
                # Generated on an earlier visit and since evicted from
                # the RAM budget - the disk cache has it now.
                cached_png = (
                    self._cache.valid_path(payload) if self._cache else None
                )
                if cached_png is not None:
                    return thumbnails.engine.request_file(key, cached_png)
            return None
        if role == self.FormatRole:
            return os.path.splitext(name)[1].lstrip(".").upper()
        if role in (self.PathRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            return os.path.join(folder, name)
        if role == self.FolderRole:
            return os.path.basename(folder.rstrip("/\\")) or folder
        if role == self.FavoriteRole:
            return os.path.join(folder, name) in self.preferences.texture_favorites
        return None


class TextureFilterProxyModel(QtCore.QSortFilterProxyModel):
    """Combines a filename text filter with a favorites-only toggle for
    the Textures section - deliberately not MultiFilterProxyModel
    (core/multifilterproxy_model.py), which hardcodes Material-specific
    role numbers (e.g. 258 as its own FavoriteRole) that would collide
    with TextureFiles' unrelated role numbering rather than actually
    generalizing across both models."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._name_filter = ""
        self._favorites_only = False

    def set_name_filter(self, text: str) -> None:
        self._name_filter = text or ""
        self.invalidateFilter()

    def set_favorites_only(self, enabled: bool) -> None:
        self._favorites_only = enabled
        self.invalidateFilter()

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
    ) -> bool:
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if self._favorites_only and not index.data(model.FavoriteRole):
            return False
        if self._name_filter:
            name = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
            if self._name_filter.lower() not in name.lower():
                return False
        return True
        return None
