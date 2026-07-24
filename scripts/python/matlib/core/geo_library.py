"""
Models for the Geometry section - a folder browser for geometry files
(.bgeo/.obj/.abc/.usd/...), mirroring the Textures section's design:
registered folder pointers in the sidebar (plus "All"), non-recursive
listing, disk-cached thumbnails, favorites keyed by full path, and the
same native file-path drag onto parameters.

The one structural difference from Textures: thumbnails are RENDERED BY
HOUDINI (a bbox-framed camera over the loaded file through Karma CPU at
thumbnail settings - thumbs.create_thumb_geo_file), which can
only happen on the main thread - so cache misses render in a blocking,
ESC-interruptable pass when a folder is opened, instead of filling in
on background threads the way texture conversions do. Cached to disk
(ThumbnailCache with a geo prefix, keyed on file mtime/size), so it's a
one-time cost per file; revisits are instant, and an interrupted pass
resumes where it left off.

Role numbering deliberately matches TextureFiles, so the shared
TextureFilterProxyModel, the drag code's PathRole lookup and the
AssetItemDelegate wiring all work unchanged across both sections.
"""

import os

import hou
from PySide6 import QtCore, QtGui, QtWidgets

from matlib.core import texture_library, thumbnails
from matlib.render import thumbs

GEO_EXTENSIONS = (
    ".bgeo.sc",
    ".bgeo.gz",
    ".bgeo",
    ".geo",
    ".obj",
    ".fbx",
    ".abc",
    ".usd",
    ".usda",
    ".usdc",
    ".ply",
    ".stl",
)


def matched_extension(name: str) -> str:
    """The GEO_EXTENSIONS entry the filename ends with (longest wins,
    so 'x.bgeo.sc' reports '.bgeo.sc', not '.bgeo'), or ''."""
    lowered = name.lower()
    for ext in GEO_EXTENSIONS:
        if lowered.endswith(ext):
            return ext
    return ""


def loader_sop_for(path: str) -> str:
    """The SOP type that reads the given geometry file: Alembic and USD
    have dedicated loaders, everything else goes through the File SOP.
    (FBX has no SOP-level loader at all - the File SOP fails to cook
    it, which surfaces as a missing thumbnail / empty import rather
    than an error; a real FBX import pipeline is a future chunk.)"""
    lowered = path.lower()
    if lowered.endswith(".abc"):
        return "alembic"
    if lowered.endswith((".usd", ".usda", ".usdc")):
        return "usdimport"
    return "file"


class GeoFolders(QtCore.QAbstractListModel):
    """Flat list of registered folder paths for the Geometry section,
    plus the synthetic "All" pseudo-entry at row 0 - same design as
    TextureFolders, over prefs.geometry_folders."""

    PathRole = QtCore.Qt.ItemDataRole.UserRole
    ALL_LABEL = "All"
    # See category.py's SIDEBAR_COUNT_ROLE comment - keep in sync.
    COUNT_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 40

    def __init__(self, preferences, parent=None) -> None:
        super().__init__()
        self.preferences = preferences
        # Cached per-folder file counts - cleared via refresh_counts()
        # so painting the sidebar never touches the disk.
        self._counts: dict = {}

    def refresh_counts(self) -> None:
        self._counts = {}

    def _folder_count(self, path: str) -> int:
        count = self._counts.get(path)
        if count is not None:
            return count
        count = 0
        try:
            if getattr(self.preferences, "geometry_include_subfolders", False):
                for _dirpath, dirnames, filenames in os.walk(path):
                    dirnames.sort()
                    count += sum(
                        1 for name in filenames if matched_extension(name)
                    )
            else:
                count = sum(
                    1
                    for name in os.listdir(path)
                    if matched_extension(name)
                )
        except OSError:
            count = 0
        self._counts[path] = count
        return count

    def rowCount(self, parent=None) -> int:
        return len(self.preferences.geometry_folders) + 1

    def data(self, index, role: int = 0):
        if not index.isValid():
            return None
        row = index.row()
        if row == 0:
            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                return self.ALL_LABEL
            if role == self.COUNT_ROLE:
                return sum(
                    self._folder_count(p)
                    for p in self.preferences.geometry_folders
                )
            return None
        path = self.preferences.geometry_folders[row - 1]
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
        if not path or path in self.preferences.geometry_folders:
            return
        row = len(self.preferences.geometry_folders) + 1
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self.preferences.add_geometry_folder(path)
        self.endInsertRows()
        self.refresh_counts()

    def remove_folder(self, row: int) -> None:
        if row <= 0 or not row - 1 < len(self.preferences.geometry_folders):
            return
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        path = self.preferences.geometry_folders[row - 1]
        self.preferences.remove_geometry_folder(path)
        self.endRemoveRows()
        self.refresh_counts()


class GeoFiles(QtCore.QAbstractListModel):
    """Geometry files directly (non-recursively) inside the selected
    folder, or every registered folder in "All" mode."""

    FormatRole = QtCore.Qt.ItemDataRole.UserRole + 1
    PathRole = QtCore.Qt.ItemDataRole.UserRole + 2
    FavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 3
    FolderRole = QtCore.Qt.ItemDataRole.UserRole + 4

    #: (done, total) during a render pass - drives the same thin
    #: progress bar above the grid the texture generation uses.
    progress_changed = QtCore.Signal(int, int)

    def __init__(self, preferences, parent=None) -> None:
        super().__init__()
        self.preferences = preferences
        self._files: list = []  # (folder, filename) pairs
        # Per-row engine spec: (key, kind, payload). "file" = valid
        # disk-cache PNG (lazy, loads on view); "render" = source path
        # (rendered in the blocking main-thread pass, deposited into
        # the engine as each frame finishes).
        self._row_specs: list = []
        self._key_rows: dict = {}
        self._cache = None
        self._cache_size = None
        thumbnails.engine.ready.connect(self._on_thumb_key_ready)

    def _get_cache(self):
        """Size- AND shading-mode-aware cache, rebuilt when either
        changes. The mode is baked into the cache dir name so switching
        the Preferences shading mode can never serve renders made in
        the old mode - each mode keeps its own cache, so switching back
        is instant."""
        size = self.preferences.rendersize
        mode = getattr(
            self.preferences, "geometry_shading_mode", "smoothwireshaded"
        )
        bg = getattr(self.preferences, "geometry_bg", "white")
        key = (size, mode, bg)
        if self._cache is None or self._cache_size != key:
            self._cache = texture_library.ThumbnailCache(
                size, prefix="geo_thumbnails_%s_%s" % (mode, bg)
            )
            self._cache_size = key
        return self._cache

    def set_folder(self, path: str) -> None:
        self._load([path])

    def set_all_folders(self) -> None:
        self._load(list(self.preferences.geometry_folders))

    def _scan(self, folder: str) -> list:
        """[(containing_dir, name)] pairs - flat by default, recursive
        when the include-subfolders toggle (sidebar right-click) is on.
        Files keep their CONTAINING dir so paths, cache keys and the
        Category column stay correct per subfolder."""
        results = []
        if not folder or not os.path.isdir(folder):
            return results
        if getattr(self.preferences, "geometry_include_subfolders", False):
            for dirpath, dirnames, filenames in os.walk(folder):
                dirnames.sort(key=str.lower)
                for name in sorted(filenames, key=str.lower):
                    if matched_extension(name):
                        results.append((dirpath, name))
        else:
            try:
                names = sorted(os.listdir(folder), key=str.lower)
            except OSError:
                return []
            results = [(folder, n) for n in names if matched_extension(n)]
        return results

    def _thumb_key(self, full_path: str):
        """cache_dir encodes size + shading mode + background, so a
        look change makes NEW keys and the old-look images simply age
        out of the shared LRU."""
        return ("geo", full_path, self._get_cache().cache_dir)

    def _load(self, folders: list) -> None:
        cache = self._get_cache()
        self.beginResetModel()
        self._files = []
        self._row_specs = []
        self._key_rows = {}
        misses = []
        for folder in folders:
            entries = self._scan(folder)
            per_dir = {}
            for dirpath, name in entries:
                per_dir.setdefault(dirpath, []).append(name)
            for dirpath, names in per_dir.items():
                cache.reconcile(dirpath, names)
            for dirpath, name in entries:
                row = len(self._files)
                self._files.append((dirpath, name))
                full = os.path.join(dirpath, name)
                key = ("geo", full, cache.cache_dir)
                self._key_rows[key] = row
                cached_png = cache.valid_path(full)
                if cached_png is not None:
                    # Lazy: the engine's file loader reads it on view -
                    # no synchronous decode per cached file on open.
                    self._row_specs.append((key, "file", cached_png))
                else:
                    self._row_specs.append((key, "render", full))
                    misses.append((row, full))
        self.endResetModel()
        if misses:
            self._render_misses(misses)

    def _on_thumb_key_ready(self, key) -> None:
        row = self._key_rows.get(key)
        if row is None:
            return
        index = self.index(row, 0)
        self.dataChanged.emit(
            index, index, [QtCore.Qt.ItemDataRole.DecorationRole]
        )

    def _render_misses(self, misses: list) -> None:
        """Render thumbnails for cache misses NOW, on the main thread -
        Houdini renders can't run anywhere else (unlike the texture
        converters, which are external processes). Blocking but
        ESC-interruptable; every finished file lands in the disk cache
        immediately, so an interrupted pass resumes where it left off
        on the next visit (or via Rerender Thumbnail)."""
        cache = self._get_cache()
        size = self.preferences.rendersize
        thumber = thumbs.ThumbNailRenderer(self.preferences)
        tmp_path = os.path.join(cache.cache_dir, "_render_tmp.png")
        total = len(misses)
        done = 0
        self.progress_changed.emit(0, total)
        try:
            with hou.InterruptableOperation(
                "Rendering geometry thumbnails",
                "Rendering geometry thumbnails",
                open_interrupt_dialog=True,
            ) as operation:
                for row, full in misses:
                    operation.updateProgress(done / total)
                    print(
                        "Amaze: geometry thumbnail %d/%d: %s"
                        % (done + 1, total, os.path.basename(full))
                    )
                    ok = False
                    try:
                        ok = thumber.create_thumb_geo_file(full, tmp_path, size)
                    except hou.OperationInterrupted:
                        raise
                    except Exception as exc:
                        print(
                            "Amaze: geometry thumbnail failed for %s: %s"
                            % (full, exc)
                        )
                    if ok:
                        image = QtGui.QImage(tmp_path)
                        if not image.isNull():
                            cache.put(full, image)
                            # deposit() announces the key; the ready
                            # hook repaints the row (the processEvents
                            # below actually paints it mid-pass).
                            thumbnails.engine.deposit(
                                ("geo", full, cache.cache_dir), image
                            )
                    done += 1
                    self.progress_changed.emit(done, total)
                    # The pass BLOCKS the event loop (Houdini renders
                    # are main-thread-only), so without this nothing
                    # repaints until the very end - finished tiles piled
                    # up invisibly and then "popped" all at once, which
                    # read as a hang. Pump paint/queued events
                    # only - user input stays excluded, so nothing can
                    # re-enter the panel mid-pass.
                    QtWidgets.QApplication.processEvents(
                        QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                    )
        except hou.OperationInterrupted:
            print(
                "Amaze: geometry thumbnail pass interrupted - %s of %s "
                "done (cached; the rest render on the next visit or via "
                "Rerender Thumbnail)" % (done, total)
            )
        finally:
            self.progress_changed.emit(0, 0)
            cache.save()
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _full_path(self, row: int):
        if 0 <= row < len(self._files):
            folder, name = self._files[row]
            return os.path.join(folder, name)
        return None

    def toggle_favorite(self, row: int) -> None:
        full = self._full_path(row)
        if not full:
            return
        if full in self.preferences.geometry_favorites:
            self.preferences.remove_geometry_favorite(full)
        else:
            self.preferences.add_geometry_favorite(full)
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.FavoriteRole])

    def rerender_thumbnails(self, rows: list) -> None:
        """Force-regenerate the given rows' thumbnails (evicts their
        still-valid cache entries first)."""
        cache = self._get_cache()
        misses = []
        for row in rows:
            full = self._full_path(row)
            if not full:
                continue
            cache.invalidate(full)
            key = ("geo", full, cache.cache_dir)
            thumbnails.engine.discard(key)
            if 0 <= row < len(self._row_specs):
                self._row_specs[row] = (key, "render", full)
            index = self.index(row, 0)
            self.dataChanged.emit(
                index, index, [QtCore.Qt.ItemDataRole.DecorationRole]
            )
            misses.append((row, full))
        if misses:
            self._render_misses(misses)

    def rowCount(self, parent=None) -> int:
        return len(self._files)

    def data(self, index, role: int = 0):
        if not index.isValid():
            return None
        row = index.row()
        if not 0 <= row < len(self._files):
            return None
        folder, name = self._files[row]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return name
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            if not 0 <= row < len(self._row_specs):
                return None
            key, kind, payload = self._row_specs[row]
            if kind == "file":
                return thumbnails.engine.request_file(key, payload)
            # Render-sourced row: serve what the pass has deposited;
            # after eviction the disk cache takes over as a file load.
            image = thumbnails.engine.peek(key)
            if image is not None:
                return image
            cache = self._get_cache()
            cached_png = cache.valid_path(payload)
            if cached_png is not None:
                return thumbnails.engine.request_file(key, cached_png)
            return None
        if role == self.FormatRole:
            return matched_extension(name).lstrip(".").upper()
        if role in (self.PathRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            return os.path.join(folder, name)
        if role == self.FavoriteRole:
            return (
                os.path.join(folder, name)
                in self.preferences.geometry_favorites
            )
        if role == self.FolderRole:
            return os.path.basename(folder.rstrip("/\\")) or folder
        return None
