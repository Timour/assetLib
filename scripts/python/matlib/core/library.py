"""
This Module holds the Model for the MaterialView/Thumbview for the MatlibPanel
"""

import os
import json
import importlib
from typing import Any
from PySide6 import QtCore, QtGui

import hou

from matlib.core import debug
from matlib.core import material, database, thumbnails
from matlib.helpers import helpers
from matlib.prefs import prefs
from matlib.render import thumbs, nodes, material_converter

importlib.reload(material)
importlib.reload(database)
importlib.reload(nodes)
importlib.reload(thumbs)
importlib.reload(material_converter)



class MaterialLibrary(QtCore.QAbstractListModel):
    """The Model for the ThumbList View in the MatLibPanel
    Subclasses QtCore.QAbstractListModel
    """

    #: which json file in the library dir backs this model - the COP
    #: section subclasses this model over its own cops.json (see
    #: core/cop_library.py), everything else shares library.json.
    DB_FILENAME = "library.json"

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        preferences: prefs.Prefs | None = None,
    ) -> None:
        super().__init__()

        # Share the panel's Prefs when given - the old per-model
        # instances each re-read settings.json at startup and drifted
        # from each other after any save (Elmar-era wart).
        if preferences is None:
            preferences = prefs.Prefs()
            preferences.load()
        self.preferences = preferences
        self._thumbsize = self.preferences.thumbsize

        db = database.DatabaseConnector(self.DB_FILENAME)
        self._data = db.load(self.preferences.dir)

        self._assets = [material.Material.from_dict(d) for d in self._data["assets"]]

        self._tags = self._data["tags"]

        # Engine deliveries arrive BY KEY - this maps them back to the
        # row to repaint. Rebuilt with the asset list.
        self._thumb_rows = {}
        thumbnails.engine.ready.connect(self._on_thumb_key_ready)

        self.IdRole = QtCore.Qt.ItemDataRole.UserRole  # 256
        self.CategoryRole = QtCore.Qt.ItemDataRole.UserRole + 1  # 257
        self.FavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 2  # 258
        self.RendererRole = QtCore.Qt.ItemDataRole.UserRole + 3  # 259
        self.TagRole = QtCore.Qt.ItemDataRole.UserRole + 4  # 260
        self.DateRole = QtCore.Qt.ItemDataRole.UserRole + 5  # 261
        self.RendererLabelRole = QtCore.Qt.ItemDataRole.UserRole + 6  # 262
        # is_usd is derived from each material's .interface file; cache the
        # result per material id so it is read once, not on every repaint.
        self._usd_cache = {}
        # Shader type (Standard/PBR/Toon/...) is derived from each
        # material's .mat file - same reasoning, cache per material id.
        self._shader_type_cache = {}

        self.rebuild_thumbs()

    def _get__mat_paths(self):
        self._mat_paths = []
        for elem in range(self.rowCount()):
            mat_id = self._assets[elem].mat_id
            is_fav = self._assets[elem].fav
            path = (
                self.preferences.dir
                + self.preferences.img_dir
                + mat_id
                + self.preferences.img_ext
            )
            self._mat_paths.append((path, is_fav, elem))

    def switch_model_data(self):
        # No worker teardown needed: engine deliveries are keyed by
        # material id, so a reload can't misroute in-flight loads -
        # same material keeps its cached image straight through.
        self.preferences.load()
        db = database.DatabaseConnector(self.DB_FILENAME)
        self._data = db.reload_with_path(self.preferences.dir)
        self._thumbsize = self.preferences.thumbsize

        self._assets = [material.Material.from_dict(d) for d in self._data["assets"]]
        self._tags = self._data["tags"]
        self._usd_cache = {}
        self._shader_type_cache = {}
        self.rebuild_thumbs()

    def flags(
        self, index: QtCore.QModelIndex | QtCore.QPersistentModelIndex
    ) -> QtCore.Qt.ItemFlag:
        default = super().flags(index)
        return default | QtCore.Qt.ItemFlag.ItemIsDragEnabled

    def rebuild_thumbs(self):
        """Rebuild the row->path and key->row maps. No loading happens
        here: the engine loads on first view (data()), so opening a
        big library costs only the visible screen."""
        self._mat_paths = []
        self._get__mat_paths()
        self._thumb_rows = {
            self._thumb_key(row): row for row in range(self.rowCount())
        }

    def _add_thumb_paths(self, index: QtCore.QModelIndex):
        """Refresh one row's thumbnail: forget the key so the repaint
        re-requests the (re)written PNG. Serves add_asset (new row),
        render_thumbnail and update_asset_content uniformly - and
        clears a sticky "missing" so a fresh render gets its retry."""
        self.rebuild_thumbs()
        row = index.row()
        if 0 <= row < self.rowCount():
            thumbnails.engine.discard(self._thumb_key(row))
            self.dataChanged.emit(
                self.index(row),
                self.index(row),
                [QtCore.Qt.ItemDataRole.DecorationRole],
            )

    def _on_thumb_key_ready(self, key) -> None:
        """The engine delivered (or failed) a key - repaint its row if
        it belongs to this model. Key-based, so reloads and reorders
        can never misroute an image; a key from another library simply
        isn't in this model's map."""
        row = self._thumb_rows.get(key)
        if row is None or not 0 <= row < self.rowCount():
            return
        self.dataChanged.emit(
            self.index(row),
            self.index(row),
            [QtCore.Qt.ItemDataRole.DecorationRole],
        )

    def _thumb_key(self, row: int):
        """Shared-RAM-cache key: stable across reloads (same
        material keeps its cached image through a library refresh) and
        collision-free across the models sharing the budget."""
        return (self.DB_FILENAME, self._assets[row].mat_id)

    #: Shared, lazily-rendered "Missing Thumbnail" image (the designed
    #: SVG asset ui/missing_thumbnail.svg) - one QImage reused by
    #: every missing row in every library (Cop inherits), so the
    #: delegate's scaled-pixmap cache holds exactly one entry for it.
    #: False = tried and failed to load (don't retry every batch).
    _missing_image_cache = None

    def _missing_thumb_image(self):
        cls = MaterialLibrary
        if cls._missing_image_cache is None:
            image = None
            try:
                path = (hou.getenv("ASSETLIB") or "") + (
                    "/scripts/python/matlib/ui/missing_thumbnail.svg"
                )
                if os.path.exists(path):
                    from PySide6 import QtSvg

                    renderer = QtSvg.QSvgRenderer(path)
                    if renderer.isValid():
                        img = QtGui.QImage(
                            512, 512, QtGui.QImage.Format.Format_ARGB32
                        )
                        img.fill(QtCore.Qt.GlobalColor.transparent)
                        painter = QtGui.QPainter(img)
                        renderer.render(painter)
                        painter.end()
                        image = img
            except Exception as exc:
                print(
                    "Amaze: could not load the missing-thumbnail "
                    "placeholder: " + str(exc)
                )
            cls._missing_image_cache = image if image is not None else False
        return cls._missing_image_cache or None

    def _decoration_image(self, index: QtCore.QModelIndex):
        """The tile thumbnail for a row - the engine's FILE loader over
        the library's own PNG. Overridden by the Code section, which has
        no PNGs and paints a code preview via the engine's PAINT path."""
        row = index.row()
        key = self._thumb_key(row)
        image = thumbnails.engine.request_file(key, self._mat_paths[row][0])
        if image is not None:
            return image
        if thumbnails.engine.is_missing(key):
            return self._missing_thumb_image()
        return None

    def set_custom_iconsize(self, size: QtCore.QSize) -> None:
        """Sets a custom IconSize - usually called via the View - Thumbnail Size Slider"""
        self._thumbsize = size.width()

    def rowCount(
        self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex | None = None
    ) -> int:
        return len(self._assets)

    def removeRow(
        self,
        row: int,
        /,
        parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex = ...,
    ) -> bool:
        self._assets.remove(self._assets[row])
        # Rows shifted - remap keys to rows (the removed asset's cached
        # image just ages out of the shared LRU naturally).
        self.rebuild_thumbs()
        return True

    def is_usd_material(self, asset) -> bool:
        """True if the material is a USD-builder type (rs_usd_material_builder
        or octane_solaris_material_builder), detected from its .interface file
        and cached per material id."""
        mid = asset.mat_id
        if mid in self._usd_cache:
            return self._usd_cache[mid]
        try:
            handler = nodes.NodeHandler(self.preferences)
            node_type = handler.get_saved_node_type(asset)
            result = node_type in nodes.NodeHandler.LOP_CAPABLE_NODE_TYPES
        except Exception:
            result = False
        self._usd_cache[mid] = result
        return result

    def shader_type_label(self, asset) -> str:
        """Best-effort specific shader-type suffix (Standard/PBR/Toon/...),
        cached per material id. Only meaningful for Redshift right now -
        every other renderer returns ''."""
        if "Redshift" not in str(asset.renderer or ""):
            return ""
        mid = asset.mat_id
        if mid in self._shader_type_cache:
            return self._shader_type_cache[mid]
        try:
            handler = nodes.NodeHandler(self.preferences)
            result = handler.get_shader_type_label(asset)
        except Exception:
            result = ""
        self._shader_type_cache[mid] = result
        return result

    def renderer_label(self, asset) -> str:
        """Human label for the material's renderer, prefixed 'USD ' for the
        USD-builder types (e.g. 'USD Redshift', 'Octane', 'Karma'), plus a
        ':<ShaderType>' suffix for Redshift when the underlying shader
        type can be determined (e.g. 'Redshift:Standard',
        'USD Redshift:PBR') - so "just Redshift" tiles are told apart
        by actual shading model, not only by the USD/classic builder
        split the plain 'USD ' prefix already covers.
        Empty string if the renderer is unknown."""
        renderer = str(asset.renderer or "").strip()
        if not renderer:
            return ""
        label = ("USD " + renderer) if self.is_usd_material(asset) else renderer
        shader_type = self.shader_type_label(asset)
        if shader_type:
            label += ":" + shader_type
        return label

    def data(
        self, index: QtCore.QModelIndex | QtCore.QPersistentModelIndex, role: int = 0
    ) -> Any:
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._assets[index.row()].name

        if role == self.RendererLabelRole:
            return self.renderer_label(self._assets[index.row()])

        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            # Raw stored image, no per-paint rescale - the delegate
            # scales (and caches) to the actual tile size, same as the
            # Textures/Geometry models have always behaved. The old
            # smooth-scale here ran for every visible tile on every
            # repaint, which is why material grids scrolled noticeably
            # heavier than the other sections.
            # One engine for every section (core/thumbnails.py):
            # cached image back instantly, else a background load is
            # queued and the dark tile paints until delivery. Evicted
            # keys (RAM budget) transparently reload from the library's
            # own PNG - disk is the swap, and it's already written.
            # Subclasses that don't store PNGs (the Code section paints
            # a preview) override _decoration_image; the default is the
            # engine's file loader over the library's own thumbnail PNG.
            return self._decoration_image(index)

        if role == self.CategoryRole:
            return self._assets[index.row()].categories

        if role == self.TagRole:
            return self._assets[index.row()].tags

        if role == self.FavoriteRole:
            return self._assets[index.row()].fav

        if role == self.RendererRole:
            return str(self._assets[index.row()].renderer)

        if role == self.DateRole:
            return str(self._assets[index.row()].date)

        if role == self.IdRole:
            return str(self._assets[index.row()].mat_id)

    def save(self) -> None:
        """Save data to disk as json"""
        db = database.DatabaseConnector(self.DB_FILENAME)
        data = {}
        data["tags"] = self._tags
        data["assets"] = [asset.get_as_dict() for asset in self._assets]
        db.set(data)
        db.save()

    @property
    def assets(self) -> list:
        """
        Docstring for assets

        :param self: Description
        :return: Description
        :rtype: list[Any]
        """
        return self._assets

    @property
    def tags(self) -> list:
        """
        Docstring for tags

        :param self: Description
        :return: Description
        :rtype: list[Any]
        """
        return self._tags

    @property
    def thumbsize(self) -> int:
        """
        Docstring for thumbsize

        :param self: Description
        :return: Description
        :rtype: int
        """
        return self._thumbsize

    @thumbsize.setter
    def thumbsize(self, val: int) -> None:
        self._thumbsize = val

    def sanitize_tags(self, tags):
        ts = []
        for t in tags.split(","):
            t = t.strip()
            if t != "":
                ts.append(t)

        # dict.fromkeys dedupes while preserving insertion order; a plain
        # set() here made tag order reshuffle unpredictably on every edit.
        ts = dict.fromkeys(ts)
        new_tags = ",".join(ts)
        return new_tags

    def set_assetdata(self, index: QtCore.QModelIndex, name, cats, tags, fav,
                      about=None, license=None) -> None:
        """Set Assetdata for the given index and parameters
        the library is saved immidiately after. about/license default to
        None = leave unchanged (only the Material Info dialog edits them)."""

        asset = self._assets[index.row()]

        name = name if "Multiple Values..." not in name else asset.name
        cats = cats if "Multiple Values..." not in cats else ", ".join(asset.categories)

        if "Multiple Values..." not in tags:
            tags = self.sanitize_tags(tags)
            self.check_add_tags(tags)
        else:
            tags = ", ".join(asset.tags)

        asset.set_data(name, cats, tags, fav, None, about=about, license=license)
        self.save()
        # Full-row repaint (all roles) - name/categories/tags/favorite
        # may all have changed.
        model_index = self.index(index.row(), 0)
        self.dataChanged.emit(model_index, model_index)

    def collapse_multicategory(self) -> int:
        """Multi-category was removed (a hazard that made sorting harder):
        every asset keeps only its FIRST category. Idempotent - once every
        asset has <=1 category a re-run is a no-op - so it's safe to call
        on every load; it saves only if it actually changed something.
        Returns the number of assets collapsed."""
        changed = 0
        for asset in self._assets:
            if len(asset.categories) > 1:
                asset.categories = asset.categories[0]  # setter takes a str
                changed += 1
        if changed:
            self.save()
        return changed

    def remove_asset(self, index: QtCore.QModelIndex) -> None:
        """Removes a material from this Library and Disk
        the library is saved immediately after"""
        if not self.hasIndex(index.row(), 0):
            return
        if len(self._assets) < index.row() + 1:
            return
        asset = self._assets[index.row()]

        # Remove Files from Disk

        asset_file_path = os.path.join(
            self.preferences.dir,
            self.preferences.asset_dir,
            asset.mat_id + self.preferences.ext,
        )
        img_file_path = os.path.join(
            self.preferences.dir,
            self.preferences.img_dir,
            asset.mat_id + self.preferences.img_ext,
        )
        interface_file_path = os.path.join(
            self.preferences.dir,
            self.preferences.asset_dir,
            asset.mat_id + ".interface",
        )

        cop_file_path = os.path.join(
            self.preferences.dir,
            self.preferences.asset_dir,
            asset.mat_id + "_cop" + self.preferences.ext,
        )
        if os.path.exists(cop_file_path):
            os.remove(cop_file_path)
        if os.path.exists(asset_file_path):
            os.remove(asset_file_path)
        if os.path.exists(img_file_path):
            os.remove(img_file_path)
        if os.path.exists(interface_file_path):
            os.remove(interface_file_path)

        self.removeRow(index.row())

        self.save()

    def check_add_tags(self, tag: str) -> None:
        """Checks if this tag exists and adds it if needed"""
        for t in tag.split(","):
            t = t.strip()
            if t != "" and t not in self.tags:
                self.tags.append(t)
        self.save()

    def get_current_network_node(self) -> None | hou.Node:
        """Return thre current Node in the Network Editor"""
        for pt in hou.ui.paneTabs():  # type: ignore
            if pt.type() == hou.paneTabType.NetworkEditor:
                return pt.currentNode()
        return None

    def remove_category(self, cat: str) -> None:
        """Removes the given category from the library (and also in all assets)"""
        # check assets against category and remove there also:
        for asset in self._assets:
            asset.remove_category(cat)

    def rename_category(self, old: str, new: str) -> None:
        """Renames the given category in the library (and also in all assets)"""
        # Update all Categories with that name in all assets
        for asset in self._assets:
            asset.rename_category(old, new)

    def add_asset(self, node: hou.Node, cats: str, tags: str, fav: bool) -> str:
        """Add a Material to this Library"""
        handler = nodes.NodeHandler(self.preferences)
        renderer = handler.get_renderer_from_node(node)
        new_mat = material.Material()
        tags = self.sanitize_tags(tags)
        new_mat.set_data(node.name(), cats, tags, fav, renderer)

        saved = handler.save_node(node, new_mat.mat_id, False)
        debug.event(
            "save", "save_node result",
            ok=bool(saved), name=new_mat.name,
            renderer=getattr(handler, "_renderer", None),
            node=node.path(), node_type=node.type().name(),
            mat_id=new_mat.mat_id,
        )
        if saved:
            new_mat.cop_net = handler.cop_info
            self._assets.append(new_mat)
            self._add_thumb_paths(self.index(self.rowCount() - 1, 0))
            self.save()
            # Stamp the scene node with its library id so a later
            # "Save to AssetLib" on the same node can offer
            # update-instead-of-duplicate (standard file-save semantics).
            try:
                node.setUserData("assetlib_id", str(new_mat.mat_id))
            except hou.OperationFailed:
                pass
        return renderer

    def find_asset_row_by_id(self, mat_id: str) -> int:
        """Row of the asset with the given id, or -1."""
        for row, asset in enumerate(self._assets):
            if str(asset.mat_id) == str(mat_id):
                return row
        return -1

    def find_asset_row_by_name(self, name: str) -> int:
        """Row of the asset whose (possibly sanitized) name matches, but
        only if the match is UNIQUE - with duplicates there is no safe
        answer, so -1 and the caller treats the save as a new material."""
        matches = []
        for row, asset in enumerate(self._assets):
            if asset.name == name or helpers.sanitize_usd_path(asset.name) == name:
                matches.append(row)
        return matches[0] if len(matches) == 1 else -1

    def update_asset_content(self, row: int, node: hou.Node) -> str:
        """Overwrite an EXISTING library entry's node content from the
        given scene node - same id, name, categories, tags and favorite;
        new node files, thumbnail, renderer/type info and date. This is
        the 'Update Existing' half of the file-save-style flow (the
        other half being a normal add_asset). Returns the detected
        renderer on success, '' on failure."""
        if row < 0 or row >= len(self._assets):
            return ""
        mat = self._assets[row]
        handler = nodes.NodeHandler(self.preferences)
        renderer = handler.get_renderer_from_node(node)
        # update=True: overwrites <id>.mat/.interface (+ COP companion)
        # and always re-renders the thumbnail, regardless of the
        # render_on_import preference.
        if not handler.save_node(node, mat.mat_id, True):
            return ""
        mat.cop_net = handler.cop_info
        if renderer:
            mat.renderer = renderer
        mat.set_current_date()
        # A content update is the one flow that can change what's inside
        # the saved files, so the per-id label caches (USD-ness, shader
        # type) genuinely go stale here - evict both.
        self._usd_cache.pop(mat.mat_id, None)
        self._shader_type_cache.pop(mat.mat_id, None)
        # Stale companion file: if the updated network no longer
        # references any COP net, remove the old <id>_cop.mat.
        if not mat.cop_net:
            cop_path = os.path.join(
                self.preferences.dir,
                self.preferences.asset_dir,
                str(mat.mat_id) + "_cop.mat",
            )
            if os.path.exists(cop_path):
                os.remove(cop_path)
        # Reload the freshly rendered thumbnail into the model - the
        # engine discard inside makes the repaint fetch the new PNG.
        self._add_thumb_paths(self.index(row, 0))
        self.save()
        try:
            node.setUserData("assetlib_id", str(mat.mat_id))
        except hou.OperationFailed:
            pass
        return renderer

    def add_asset_from_strings(
        self, name: str, cats: str, tags: str, fav: bool, renderer: str
    ):
        """Append an assset from Strings only - the user has to take care of copying files on disk"""
        new_asset = material.Material()
        tags = self.sanitize_tags(tags)
        new_asset.set_data(
            name,
            cats,
            tags,
            fav,
            renderer,
        )
        self._assets.append(new_asset)
        # self._add_thumb_paths(self.index(self.rowCount() - 1, 0))
        # self.save()
        return self._assets[self.rowCount() - 1]

    def cleanup_db(self, show_dialog: bool = True) -> int:
        """Removes orphan data from disk, rescues uncategorized materials
        and reports everything in a single summary dialog.
        Never renders anything. Returns 1 if materials were rescued.

        show_dialog=False skips the dialog so the panel can combine
        several libraries' cleanups (materials + COP networks + browser
        prefs) into ONE report - the lines are always left on
        self.last_cleanup_summary either way."""
        summary = []

        # --- Pass 1: scan assets (no mutation during iteration) ---
        rows_to_remove = []
        missing_thumbs = 0
        for row, asset in enumerate(self._assets):
            interface_path = os.path.join(
                self.preferences.dir,
                self.preferences.asset_dir,
                str(asset.mat_id) + ".interface",
            )
            mat_path = os.path.join(
                self.preferences.dir,
                self.preferences.asset_dir,
                str(asset.mat_id) + ".mat",
            )
            img_path = os.path.join(
                self.preferences.dir,
                self.preferences.img_dir,
                str(asset.mat_id) + self.preferences.img_ext,
            )

            if not os.path.exists(interface_path) or not os.path.exists(mat_path):
                print(f"Asset {asset.mat_id} ({asset.name}) missing on disk -> removed from library")
                rows_to_remove.append(row)
            elif not os.path.exists(img_path):
                missing_thumbs += 1
                print(f"Image for asset {asset.mat_id} ({asset.name}) missing on disk")

        # --- Pass 2: remove collected rows, highest row first so the
        # remaining indices stay valid ---
        for row in sorted(rows_to_remove, reverse=True):
            self.remove_asset(self.index(row))
        if rows_to_remove:
            summary.append(
                f"{len(rows_to_remove)} material(s) had missing files and were removed from the library."
            )
        if missing_thumbs:
            summary.append(
                f"{missing_thumbs} material(s) lack a thumbnail image. "
                "Use 'Update all Thumbnails' when convenient (renders take time)."
            )

        # --- Pass 3: lonely files on disk ---
        # The material and COP libraries share the same asset/img
        # directories, so "no entry in THIS database" is not enough to
        # call a file orphaned - union the ids from every sibling
        # database file before deleting anything.
        known_ids = self._all_known_asset_ids()
        lone_count = 0
        mats_path = os.path.join(self.preferences.dir, self.preferences.asset_dir)
        for f in os.listdir(mats_path):
            if f.endswith(".mat") or f.endswith(".interface"):
                split = f.split(".")[0]
                if split.endswith("_cop"):
                    split = split[: -len("_cop")]
                found = str(split) in known_ids
                if not found:
                    print(f"Lonely file {os.path.join(mats_path, f)} -> removed from disk")
                    try:
                        os.remove(os.path.join(mats_path, f))
                        lone_count += 1
                    except OSError:
                        pass

        mats_path = os.path.join(self.preferences.dir, self.preferences.img_dir)
        for f in os.listdir(mats_path):
            if f.endswith(".png"):
                split = f.split(".")[0]
                found = str(split) in known_ids
                if not found:
                    print(f"Lonely file {os.path.join(mats_path, f)} -> removed from disk")
                    try:
                        os.remove(os.path.join(mats_path, f))
                        lone_count += 1
                    except OSError:
                        pass
        if lone_count:
            summary.append(f"{lone_count} orphaned file(s) on disk were removed.")

        # --- Pass 4: rescue materials without a valid category and
        # normalize legacy whitespace-mangled category data ---
        mark_rescued = 0
        rescued_count = 0
        for asset in self._assets:
            cats = asset.categories
            if isinstance(cats, str):
                cats = cats.split(",") if cats else []
            cats = [c.strip() for c in cats if isinstance(c, str) and c.strip() != ""]
            if not cats:
                cats = ["Uncategorized"]
                mark_rescued = 1
                rescued_count += 1
                print(f"Asset {asset.mat_id} ({asset.name}) had no category -> moved to 'Uncategorized'")
            asset.categories = ", ".join(cats)
        if rescued_count:
            summary.append(
                f"{rescued_count} material(s) had no category and were moved to 'Uncategorized'."
            )

        if rows_to_remove or mark_rescued:
            self.save()

        self.last_cleanup_summary = list(summary)

        # --- Single summary dialog ---
        if show_dialog:
            if summary:
                hou.ui.displayMessage(
                    "Library cleanup finished:\n\n- " + "\n- ".join(summary)
                    + "\n\nDetails in the Python shell."  # type: ignore
                )
            else:
                hou.ui.displayMessage("Library cleanup finished: nothing to clean.")  # type: ignore

        return mark_rescued

    def _all_known_asset_ids(self) -> set:
        """Asset ids from EVERY database file in the library dir
        (library.json + cops.json), read directly from disk - used by
        cleanup_db so one library's cleanup never deletes files that
        belong to the other."""
        ids = {str(a.mat_id) for a in self._assets}
        for filename in ("library.json", "cops.json"):
            if filename == self.DB_FILENAME:
                continue
            full = os.path.join(self.preferences.dir, filename)
            try:
                with open(full, encoding="utf-8") as fh:
                    data = json.load(fh)
                for asset in data.get("assets", []):
                    ids.add(str(asset.get("id", asset.get("mat_id", ""))))
            except (OSError, ValueError):
                continue
        return ids

    def toggle_fav(self, index: QtCore.QModelIndex) -> None:
        """
        Toggle the Favorite Parameter for the given QModelIndex

        :param self: Description
        :param index: Description
        :type index: QtCore.QModelIndex
        """
        self._assets[index.row()].fav = False if self._assets[index.row()].fav else True
        self.save()
        model_index = self.index(index.row(), 0)
        self.dataChanged.emit(model_index, model_index, [self.FavoriteRole])

    def render_thumbnail(self, index: QtCore.QModelIndex) -> None:
        """
        Render the Thumbnail for the given QModelIndex

        :param self: Description
        :param index: Description
        :type index: QtCore.QModelIndex
        """
        renderer = thumbs.ThumbNailRenderer(self.preferences, self._assets[index.row()])
        renderer.create_thumbnail()
        self._add_thumb_paths(index)

    def render_thumbnails_batch(self, indexes, progress=None) -> None:
        """Render many thumbnails at once (Render All), reusing ONE
        Karma scaffold across every Karma/MaterialX material so the
        expensive shaderball USD stage loads a single time instead of
        per material. Non-Karma materials (Redshift/Octane/...) and COP
        assets (the subclass's own network-output path) fall back to
        per-item render_thumbnail, unchanged. progress(done, total)
        drives the caller's UI; ESC interrupts the whole batch."""
        renderer = thumbs.ThumbNailRenderer(self.preferences)
        scaffold = None
        total = len(indexes)
        # The bottleneck is PREP, not render: each material is
        # reconstructed, copied into the scaffold, and its per-material
        # lib/copnet nodes are created and destroyed - and every one of
        # those node changes records an UNDO block and triggers an
        # automatic COOK/UI refresh. Both are pure overhead here (nothing
        # in a headless render loop needs undo, and the render cooks its
        # own dependencies explicitly). Disable them for the whole batch;
        # restore the update mode in finally.
        prev_update_mode = hou.updateModeSetting()
        try:
            hou.setUpdateMode(hou.updateMode.Manual)
            with hou.undos.disabler(), hou.InterruptableOperation(
                "Rendering thumbnails", "Rendering thumbnails",
                open_interrupt_dialog=True,
            ) as operation:
                for n, index in enumerate(indexes):
                    operation.updateProgress(n / total if total else 1.0)
                    asset = self._assets[index.row()]
                    # Per-item guard: ONE material's failure (a missing
                    # renderer plugin, a corrupt file) must never abort
                    # the whole run - it just gets skipped and reported.
                    # Only OperationInterrupted (ESC) breaks the loop.
                    try:
                        if material.is_karma_renderer(asset.renderer):
                            node_handler = nodes.NodeHandler(self.preferences)
                            with debug.timed("batch", "import material",
                                             name=asset.name):
                                node_handler.import_asset_to_scene(asset)
                            try:
                                if scaffold is None:
                                    with debug.timed("batch", "build scaffold"):
                                        scaffold = renderer.build_karma_scaffold()
                                if scaffold is not None:
                                    with debug.timed("batch", "render into scaffold",
                                                     name=asset.name):
                                        renderer.render_karma_into(
                                            scaffold,
                                            node_handler.builder_node,
                                            asset.mat_id,
                                        )
                            finally:
                                with debug.timed("batch", "cleanup material",
                                                 name=asset.name):
                                    node_handler.cleanup()
                            self._add_thumb_paths(index)
                        else:
                            # Redshift/Octane/Mantra/Arnold, and COP in
                            # the subclass - their own per-item pipelines.
                            self.render_thumbnail(index)
                    except hou.OperationInterrupted:
                        raise
                    except Exception as exc:
                        print(
                            "Amaze: thumbnail failed for "
                            + asset.name + " (skipped): " + str(exc)
                        )
                    if progress is not None:
                        progress(n + 1, total)
        except hou.OperationInterrupted:
            print("Amaze: Render All interrupted by user")
        finally:
            if scaffold is not None:
                scaffold["net"].destroy()
            hou.setUpdateMode(prev_update_mode)

    def import_asset_to_scene(
        self,
        index: QtCore.QModelIndex,
        target: str = "auto",
        context_node=None,
    ):
        """
        Import the given QModelIndex into the Houdini scene.

        target: "auto" (context-aware), "mat", or "lop"; context_node
        optionally pins the destination context (drag release point).
        Returns (ok, reason) from the importer so the panel can report any
        materials that could not live in the requested context.

        :param self: Description
        :param index: Description
        :type index: QtCore.QModelIndex
        """
        importer = nodes.NodeHandler(self.preferences)
        return importer.import_asset_to_scene(
            self._assets[index.row()], target, context_node=context_node
        )

    def convert_redshift_to_karma(self, index: QtCore.QModelIndex):
        """Best-effort conversion of a Redshift material to a Karma/
        MaterialX equivalent (test/v0 - see render/material_converter.py
        for exactly what is and isn't handled). Registers the result as a
        new library entry in the same category(ies) as the source,
        alongside it - never replaces or touches the original.

        Returns (ok, report). report.summary_lines() explains what
        happened even when ok is True - a "successful" conversion can
        still have skipped/approximated inputs, nothing here is silently
        perfect."""
        mat = self._assets[index.row()]
        if "Redshift" not in mat.renderer:
            report = material_converter.ConversionReport(mat.name)
            report.skip("not a Redshift material")
            return False, report

        handler = nodes.NodeHandler(self.preferences)
        scratch = hou.node("/obj").createNode("matnet")
        try:
            # Build the converted material INSIDE a real Karma Material
            # Builder, not a bare matnet. Karma-context nodes (kma_*,
            # e.g. the proper kma_rampconst ramp) simply aren't valid
            # outside one, and a builder saves/reimports as a single
            # subnet exactly like a hand-built Karma material does
            # (load_items_file_mtlx's unwrap path) instead of as loose
            # items needing scaffolding on the way back in.
            # The Redshift converter is one ADAPTER feeding the shared
            # Karma material engine: it only produces the shader network;
            # the engine owns the container, wiring, layout and the
            # surface-terminal invariant check.
            report_holder = {}

            def produce(builder):
                shader, disp, report = material_converter.convert_redshift_material(
                    handler, mat, builder
                )
                report_holder["report"] = report
                return (shader, disp)

            builder, mtlx_node = nodes.build_karma_material(
                scratch, mat.name, produce
            )
            report = report_holder.get("report")
            if mtlx_node is None:
                return False, report
            self.add_asset(builder, ",".join(mat.categories), ",".join(mat.tags), False)
            return True, report
        finally:
            # The live Karma network only exists to be copied by
            # add_asset()'s own save path - never left in the scene,
            # same discipline as the Redshift scratch reconstruction in
            # convert_redshift_material() itself.
            scratch.destroy()
