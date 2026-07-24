"""
Stores the Category Model for the MatLib Panel and provides the data to it's corresponding view
Uses QtCore.QAbstractListModel as a Base Class
"""

from typing import Any
from PySide6 import QtCore

from matlib.prefs import prefs
from matlib.core import database

# Shared sidebar-count role: SidebarItemDelegate (panel.py) reads this
# from WHICHEVER model backs the sidebar and paints "Name (N)". Keep the
# number identical across category.py / texture_library.py /
# geo_library.py / gradient_library.py.
SIDEBAR_COUNT_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 40


class Categories(QtCore.QAbstractListModel):
    """
    Stores the Category Model for the MatLib Panel and provides the data to it's corresponding view
    Uses QtCore.QAbstractListModel as a Base Class
    """

    #: which json file in the library dir backs this model - the COP
    #: section subclasses this over its own cops.json.
    DB_FILENAME = "library.json"

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        preferences: prefs.Prefs | None = None,
    ) -> None:
        super().__init__()

        # Share the panel's Prefs when given (see MaterialLibrary).
        if preferences is None:
            preferences = prefs.Prefs()
            preferences.load()
        self.preferences = preferences
        db = database.DatabaseConnector(self.DB_FILENAME)
        self._data = db.load(self.preferences.dir)
        self._categories = self._data["categories"]
        self.CatSortRole = QtCore.Qt.ItemDataRole.UserRole  # 256
        # Active renderer filter (lowercased; "" = no filter). Pushed in
        # by the panel whenever the Renderer menu changes, so counts and
        # empty-category hiding agree with what the grid actually shows.
        self._renderer_filter = ""
        # One-pass count map cache (category -> visible-asset count,
        # "_All" = total). The old per-call scan walked every asset for
        # EVERY sidebar row on every repaint and every proxy filter
        # pass. Dropped on any mutation path: our own layoutChanged
        # (save/assign/update flows emit it), renderer switches,
        # reloads, saves, and the panel's sidebar refresh hook.
        self._count_cache = None
        self.layoutChanged.connect(self.drop_count_cache)

    def rowCount(
        self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex | None = None
    ) -> int:
        return len(self._categories)

    def reload(self):
        db = database.DatabaseConnector(self.DB_FILENAME)
        self._data = db.load(self.preferences.dir)
        self._categories = self._data["categories"]
        self.drop_count_cache()

    def data(
        self, index: QtCore.QModelIndex | QtCore.QPersistentModelIndex, role: int = 0
    ) -> Any:
        if role == self.CatSortRole:
            return self._categories[index.row()]

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            elem = self._categories[index.row()]
            if elem.startswith("_"):
                elem = elem[1:]
            return elem

        if role == SIDEBAR_COUNT_ROLE:
            return self._category_count(self._categories[index.row()])

    def set_renderer_filter(self, render_filter: str) -> None:
        """Store the grid's active renderer filter so counts and the
        sidebar's empty-category hiding evaluate against the same set of
        materials the grid shows. Pass the exact value the panel feeds
        MultiFilterProxyModel ("all_renderers" for All)."""
        self._renderer_filter = str(render_filter or "").lower()
        self.drop_count_cache()

    def drop_count_cache(self, *args) -> None:
        """Invalidate the one-pass count map (also a layoutChanged
        slot, hence the ignored args)."""
        self._count_cache = None

    def _asset_matches_renderer(self, asset: dict) -> bool:
        """Mirror MultiFilterProxyModel's RendererRole matching EXACTLY
        (case-insensitive substring; "all_renderers" passes everything
        with a non-empty renderer) so sidebar and grid can never
        disagree about what counts as visible."""
        rf = self._renderer_filter
        if not rf:
            return True
        renderer = str(asset.get("renderer", "")).lower()
        if rf in renderer:
            return True
        if renderer == "":
            return False
        return "all_renderers" in rf

    def showing_all_renderers(self) -> bool:
        """True when the Renderer filter is All (or unset). The sidebar
        uses this to reveal EVERY category, empty ones included, so they
        can be seen and deleted - "All" doubles as the manage-categories
        view."""
        rf = self._renderer_filter
        return (not rf) or ("all_renderers" in rf)

    def _category_count(self, raw_name: str) -> int:
        """How many VISIBLE assets live in this category ("_All" = every
        visible asset) - visible meaning matching the active renderer
        filter, so the number is exactly what clicking the row will
        show. Served from a one-pass map over the shared database dict,
        rebuilt lazily after any mutation (see drop_count_cache)."""
        counts = self._count_cache
        if counts is None:
            counts = {}
            total = 0
            for asset in self._data.get("assets", []):
                if not self._asset_matches_renderer(asset):
                    continue
                total += 1
                cats = asset.get("categories", [])
                if isinstance(cats, str):
                    cats = cats.split(",")
                seen = set()
                for cat in cats:
                    if isinstance(cat, str):
                        cleaned = cat.strip()
                        if cleaned and cleaned not in seen:
                            seen.add(cleaned)
                            counts[cleaned] = counts.get(cleaned, 0) + 1
            counts["_All"] = total
            self._count_cache = counts
        if raw_name == "_All":
            return counts.get("_All", 0)
        return counts.get(raw_name.strip(), 0)

    def switch_model_data(self):
        self.preferences.load()
        db = database.DatabaseConnector(self.DB_FILENAME)
        data = db.reload_with_path(self.preferences.dir)
        # Keep the whole dict, not just the category list - counts and
        # empty-category hiding read _data["assets"], which otherwise
        # stayed pointing at the PREVIOUS library after a switch.
        self._data = data
        self._categories = data["categories"]
        self.drop_count_cache()

    def remove_category(self, cat: str) -> None:
        """Removes the given category from the library (and also in all assets)"""
        self._categories.remove(cat)
        self.save()

    def rename_category(self, old: str, new: str) -> None:
        """Renames the given category in the library (and also in all assets)"""
        # Update Categories with that name
        for count, current in enumerate(self._categories):
            if current == old:
                self._categories[count] = new
        self.save()

    def normalize_categories(self) -> int:
        """Strip whitespace and remove duplicate/empty entries from the
        category list (legacy data cleanup). Returns changed entry count."""
        cleaned = []
        changed = 0
        for c in self._categories:
            c2 = c.strip() if isinstance(c, str) else ""
            if c2 == "" or c2 in cleaned:
                changed += 1
                continue
            if c2 != c:
                changed += 1
            cleaned.append(c2)
        if changed:
            self._categories = cleaned
            self.save()
        return changed

    def check_add_category(self, cat: str) -> None:
        """Checks if this category exists and adds it if needed"""
        if "Multiple Values..." in cat:
            return
        changed = False
        for c in cat.split(","):
            c = c.strip()
            if c != "" and c not in self._categories:
                self._categories.append(c)
                changed = True
        if changed:
            self.save()

    def save(self) -> None:
        """Save data to disk as json"""
        db = database.DatabaseConnector(self.DB_FILENAME)
        data = {}
        data["categories"] = self._categories
        db.set(data)
        db.save()
        self.drop_count_cache()


class CategoriesSidebarProxy(QtCore.QSortFilterProxyModel):
    """Sidebar NAVIGATION proxy over Categories: sorts like the plain
    proxy it replaces, and additionally hides categories with zero
    visible assets - you can never click your way to an empty grid.
    "Visible" respects the Materials renderer filter (pushed into the
    source model via Categories.set_renderer_filter), so with Redshift
    selected a category holding only Karma materials hides too; "_All"
    always shows. Editing surfaces (save dialog, details dropdown,
    Move to/Add to menus) deliberately do NOT use this proxy - they
    read the source model, so empty categories stay assignable and
    come back to life the moment a material is filed into them.

    The hiding is optional (prefs.hide_empty_categories, pushed in by
    the panel): with hide_empty False this proxy passes every row,
    behaving exactly like the plain sorting proxy it replaced."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.hide_empty = True

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
    ) -> bool:
        if not self.hide_empty:
            return True
        model = self.sourceModel()
        if model is None:
            return True
        # Renderer "All" shows every category, empty ones included - it's
        # the view where you can see and delete unused categories. A
        # specific renderer still hides its empties.
        if model.showing_all_renderers():
            return True
        raw = model.index(source_row, 0).data(model.CatSortRole)
        if raw == "_All":
            return True
        return model._category_count(raw) > 0
