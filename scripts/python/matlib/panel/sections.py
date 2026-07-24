"""Section objects - one per library tab, like a small node type.

The panel owns the widgets (cat_list, thumblist, filter box, star) and
builds every model in setup(). A Section encapsulates how ONE section
drives them: what its activate does, how it filters, what a sidebar
click means, what a double-click does. The panel's shared handlers then
dispatch to `panel._section()` instead of branching on
`current_section`, so a new section is a new class here, not edits to a
dozen handlers.

Three archetypes, mirroring the three model families:

* **AssetSection** - the curated-library machinery (a MaterialLibrary
  over its own json + a Categories sidebar): Materials, Cop, Code.
* **FolderSection** - a folder-pointer list over real files on disk:
  Textures, Geometry.
* **GradientSection** - the read-only palette library: Colors.

`activate()` deliberately delegates to the panel's existing
`_activate_<x>_section` method: that logic (folder restore, first-row
selection, online mode) is delicate and proven, so it stays put and this
refactor changes only the repetitive dispatch. The panel methods can be
inlined into the sections later.
"""

from __future__ import annotations

import hou
from PySide6 import QtCore


class Section:
    """Base protocol. A section is constructed with the panel and reads
    the panel's already-built models by attribute name."""

    key = ""
    #: Whether the favourites star applies here (Colors/Textures/Geometry
    #: have their own favourite state; the online browser has none).
    has_favorites = True
    #: Name of the panel method that points the widgets at this section's
    #: models. Kept on the panel for now (see module docstring).
    activate_method = ""
    #: Name of the panel method that builds this section's grid
    #: right-click menu.
    rc_menu_method = ""
    #: Name of the panel method that builds this section's SIDEBAR
    #: right-click menu (categories / folders).
    catlist_menu_method = ""

    def __init__(self, panel) -> None:
        self.panel = panel

    # -- lifecycle --------------------------------------------------------

    def activate(self) -> None:
        getattr(self.panel, self.activate_method)()

    def rc_menu(self) -> None:
        """Build and exec this section's grid right-click menu."""
        if self.rc_menu_method:
            getattr(self.panel, self.rc_menu_method)()

    def catlist_menu(self) -> None:
        """Build and exec this section's sidebar right-click menu."""
        if self.catlist_menu_method:
            getattr(self.panel, self.catlist_menu_method)()

    def edit_dialog(self) -> None:
        """Open this section's metadata/edit dialog, if it has one. The
        Section API's extension point for the Dialog concept (see
        docs/architecture/overview.md): a section owns its dialog like it
        owns its menu. Default: no dialog."""
        pass

    def save_node(self, node) -> None:
        """A scene node was dropped onto the panel (or otherwise handed
        in to save) while this section is active. Each section routes to
        its own save flow so the right dialog - with the right
        categories - opens. Default: explain why nothing happens, since
        the folder sections browse files on disk and have no node-save
        concept."""
        hou.ui.displayMessage(  # type: ignore
            "This section browses files on disk - a scene node can't "
            "be saved into it. Switch to Materials, Colors, Cop or "
            "Code first."
        )

    # -- the curated-library stack (asset sections only) -----------------

    def stack(self):
        """(model, proxy, selection, categories) for the material
        machinery, or None for sections that don't use it."""
        return None

    # -- shared handlers dispatch here -----------------------------------

    def filter_text(self, text: str) -> None:
        pass

    def filter_favorites(self, on: bool) -> None:
        pass

    def select_category(self, index) -> None:
        pass

    def double_click(self, index) -> None:
        pass

    # -- helper -----------------------------------------------------------

    def _p(self, attr):
        return getattr(self.panel, attr, None)


class AssetSection(Section):
    """Materials / Cop / Code: a MaterialLibrary-family model + a
    Categories sidebar, filtered by the MultiFilterProxyModel. All three
    share the same filter/favourite/category logic; they differ only in
    which models they name and what a double-click does."""

    model_attr = ""
    proxy_attr = ""
    selection_attr = ""
    category_attr = ""

    def stack(self):
        model = self._p(self.model_attr)
        category = self._p(self.category_attr)
        if not model or not category:
            return None
        return (
            model,
            self._p(self.proxy_attr),
            self._p(self.selection_attr),
            category,
        )

    def filter_text(self, text: str) -> None:
        st = self.stack()
        if st is None:
            return
        model, proxy, _selection, _categories = st
        proxy.layoutAboutToBeChanged.emit()
        if text.startswith(":"):
            # ":tag" searches the TagRole instead of the name.
            if len(text) > 1:
                proxy.invalidate()
                proxy.setFilter(model.TagRole, text[1:])
                proxy.removeFilter(QtCore.Qt.ItemDataRole.DisplayRole)
                proxy.sort(0)
        else:
            proxy.invalidate()
            proxy.removeFilter(model.TagRole)
            proxy.setFilter(QtCore.Qt.ItemDataRole.DisplayRole, text)
            proxy.sort(0)
        proxy.layoutChanged.emit()

    def filter_favorites(self, on: bool) -> None:
        st = self.stack()
        if st is None:
            return
        model, proxy, _selection, _categories = st
        proxy.setFilter(model.FavoriteRole, True if on else "")
        proxy.sort(0)

    def select_category(self, index) -> None:
        st = self.stack()
        if st is None:
            return
        model, proxy, _selection, _categories = st
        proxy.invalidate()
        proxy.setFilter(
            model.CategoryRole, "" if index.data() == "All" else index.data()
        )


class MaterialSection(AssetSection):
    key = "material"
    activate_method = "_activate_material_section"
    rc_menu_method = "_material_rc_menu"
    catlist_menu_method = "_material_catlist_menu"
    model_attr = "material_model"
    proxy_attr = "material_sorted_model"
    selection_attr = "material_selection_model"
    category_attr = "category_model"

    def double_click(self, index) -> None:
        # Context-aware import (the index isn't used - import_asset reads
        # the selection and the network under the cursor).
        self.panel.import_asset("auto")

    def edit_dialog(self) -> None:
        self.panel.edit_material_info()

    def save_node(self, node) -> None:
        # Materials support multi-selection saves, so the flow is
        # selection-based - the drop handler selects the node first.
        self.panel.save_asset()


class CopSection(AssetSection):
    key = "cop"
    activate_method = "_activate_cop_section"
    rc_menu_method = "_cop_rc_menu"
    catlist_menu_method = "_asset_catlist_menu"
    model_attr = "cop_model"
    proxy_attr = "cop_sorted_model"
    selection_attr = "cop_selection_model"
    category_attr = "cop_category_model"

    def double_click(self, index) -> None:
        self.panel.import_cop_assets()

    def save_node(self, node) -> None:
        self.panel.save_cop_from_node(node)


class CodeSection(AssetSection):
    key = "code"
    activate_method = "_activate_code_section"
    rc_menu_method = "_code_rc_menu"
    catlist_menu_method = "_asset_catlist_menu"
    model_attr = "code_model"
    proxy_attr = "code_sorted_model"
    selection_attr = "code_selection_model"
    category_attr = "code_category_model"

    def double_click(self, index) -> None:
        if index is not None and index.isValid():
            self.panel._apply_code_index(index)

    def save_node(self, node) -> None:
        self.panel.save_code_from_node(node)


class FolderSection(Section):
    """Textures / Geometry: a folder-pointer list + a files model, filtered
    by TextureFilterProxyModel. Selecting a folder browses its files;
    there is no category machinery (stack() is None)."""

    files_proxy_attr = ""
    folders_attr = ""
    files_attr = ""
    last_folder_pref = ""

    def filter_text(self, text: str) -> None:
        proxy = self._p(self.files_proxy_attr)
        if proxy is not None:
            proxy.set_name_filter(text)

    def filter_favorites(self, on: bool) -> None:
        proxy = self._p(self.files_proxy_attr)
        if proxy is not None:
            proxy.set_favorites_only(on)

    def _browse(self, path) -> None:
        """Point the files model at a folder path (None = the synthetic
        'All' row), remember it, and persist."""
        files = self._p(self.files_attr)
        folders = self._p(self.folders_attr)
        if path is None:
            setattr(self.panel.prefs, self.last_folder_pref, folders.ALL_LABEL)
            files.set_all_folders()
        else:
            setattr(self.panel.prefs, self.last_folder_pref, path)
            files.set_folder(path)
        self.panel.prefs.save()

    def select_category(self, index) -> None:
        folders = self._p(self.folders_attr)
        self._browse(index.data(folders.PathRole))


class TextureSection(FolderSection):
    key = "texture"
    activate_method = "_activate_texture_section"
    rc_menu_method = "_texture_rc_menu"
    catlist_menu_method = "_texture_catlist_menu"
    files_proxy_attr = "texture_sorted_model"
    folders_attr = "texture_folders_model"
    files_attr = "texture_files_model"
    last_folder_pref = "last_texture_folder"

    def double_click(self, index) -> None:
        self.panel.set_texture_on_selected_node(index)


class GeometrySection(FolderSection):
    key = "geometry"
    activate_method = "_activate_geometry_section"
    rc_menu_method = "_geo_rc_menu"
    catlist_menu_method = "_geometry_catlist_menu"
    files_proxy_attr = "geo_sorted_model"
    folders_attr = "geo_folders_model"
    files_attr = "geo_files_model"
    last_folder_pref = "last_geometry_folder"

    def double_click(self, index) -> None:
        if index is not None and index.isValid():
            self.panel.import_geo_asset(index)


class GradientSection(Section):
    key = "gradient"
    activate_method = "_activate_gradient_section"
    rc_menu_method = "_gradient_rc_menu"
    catlist_menu_method = "_gradient_catlist_menu"

    def filter_text(self, text: str) -> None:
        self.panel.gradient_sorted_model.set_name_filter(text)

    def filter_favorites(self, on: bool) -> None:
        self.panel.gradient_sorted_model.set_favorites_only(on)

    def select_category(self, index) -> None:
        kind, value = self.panel.gradient_categories_model.filter_for_row(
            index.row()
        )
        self.panel.gradient_sorted_model.set_sidebar_filter(kind, value)

    def double_click(self, index) -> None:
        if index is not None and index.isValid():
            source = self.panel.gradient_sorted_model.mapToSource(index)
            entry = self.panel.gradient_model.entry(source.row())
            if entry is not None:
                self.panel._apply_gradient_ramp(entry)

    def save_node(self, node) -> None:
        self.panel.save_gradient_from_node(node)


#: The section registry, in tab order. Built by the panel after its
#: models exist. Adding a section = one class here.
SECTION_CLASSES = (
    MaterialSection,
    TextureSection,
    GradientSection,
    CopSection,
    GeometrySection,
    CodeSection,
)


def build_sections(panel) -> dict:
    """Instantiate every section against a constructed panel."""
    return {cls.key: cls(panel) for cls in SECTION_CLASSES}
