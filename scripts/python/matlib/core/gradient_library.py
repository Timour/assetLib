"""
Models for the Gradients ("Colors") section.

Every entry is a normal USER gradient, stored in <library dir>/
gradients.json with its full ramp (basis/key/value) so it re-applies
exactly as saved, in user-defined categories.

The curated palettes are just prefilled colours, not read-only -
SEEDED once into the user gradients on first run (see
GradientLibrary._seed_curated_once), from the JSON defs in res/def/
listed in CURATED_SETS: Sanzo Wada's "A Dictionary of Color Combinations"
(348 combinations; data github.com/dblodorn/sanzo-wada, MIT, source work
public domain), plus artist sets from colour theory - Paul Klee (palette
sampled from "Farbtafel qu 1", 1930), Josef Albers (Homage to the Square /
Interaction of Color), Johannes Itten (twelve-part Farbkreis). After
seeding they are ordinary gradients: moveable, editable, deletable, their
categories removable, their colour-theory notes editable. Each JSON
documents its own sources in a "source" field.
"""

import json
import os

import hou
from PySide6 import QtCore, QtGui

from matlib.core import thumbnails

THUMB_SIZE = 256


def _def_path(filename: str) -> str:
    base = hou.getenv("ASSETLIB")
    if not base:
        return ""
    return os.path.join(
        base, "scripts", "python", "matlib", "res", "def", filename
    )


# The curated (read-only) sets, in display order. Entry dicts get
# "type" = the set key, and everything downstream branches only on
# user-vs-curated, so adding a set here (plus its JSON in res/def/) is
# the whole job. "label" feeds the sidebar group names and the list
# view's Category column ("Wada 5 Colors", ...).
CURATED_SETS = (
    {"key": "wada", "label": "Wada", "file": "sanzo_wada.json"},
    {"key": "klee", "label": "Klee", "file": "paul_klee.json"},
    {"key": "albers", "label": "Albers", "file": "josef_albers.json"},
    {"key": "itten", "label": "Itten", "file": "johannes_itten.json"},
)


def _palette_ramp_data(colors: list) -> dict:
    """A STEPPED (constant-basis) ramp from a palette's hex colours, in
    the same shape as a saved user ramp - so a seeded palette re-applies
    exactly like the curated stepped-ramp did and paints as bands."""
    n = len(colors)
    keys, values = [], []
    for i, c in enumerate(colors):
        keys.append(i / n if n else 0.0)
        h = c["hex"].lstrip("#")
        values.append([int(h[j:j + 2], 16) / 255.0 for j in (0, 2, 4)])
    return {"keys": keys, "values": values, "bases": ["Constant"] * n}


class GradientCategories(QtCore.QAbstractListModel):
    """Sidebar list for the Gradients section: "All", then the user's
    categories (which after seeding include the palette groups - "Wada 5
    Colors", "Klee 3 Colors", ...). Rebuilt via refresh() on change."""

    def __init__(self, library, parent=None) -> None:
        super().__init__(parent)
        self._library = library
        self._labels = []
        self._filters = []
        self._rebuild()

    def _rebuild(self) -> None:
        self._labels = ["All"]
        self._filters = [("all", None)]
        for cat in self._library.user_categories():
            self._labels.append(cat)
            self._filters.append(("category", cat))

    def refresh(self) -> None:
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    # See category.py's SIDEBAR_COUNT_ROLE comment - keep in sync.
    COUNT_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 40

    def filter_for_row(self, row: int):
        """(kind, value) for the proxy: ("all", None) or
        ("category", name)."""
        if 0 <= row < len(self._filters):
            return self._filters[row]
        return ("all", None)

    def rowCount(self, parent=None) -> int:
        return len(self._labels)

    def data(self, index, role: int = 0):
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._labels[index.row()]
        if role == self.COUNT_ROLE:
            kind, value = self.filter_for_row(index.row())
            return self._library.count_for_filter(kind, value)
        return None


class GradientLibrary(QtCore.QAbstractListModel):
    """User gradients first, then the Wada combinations. Entries are
    dicts with a "type" key ("user"/"wada"); user entries carry their
    full ramp data, Wada entries their color list. Thumbnails are
    painted on demand and cached - Wada as stacked horizontal bands
    (the dictionary's own presentation), user ramps as a left-to-right
    gradient (banded when fully constant-basis)."""

    SubtitleRole = QtCore.Qt.ItemDataRole.UserRole + 1
    ColorsRole = QtCore.Qt.ItemDataRole.UserRole + 2
    FavoriteRole = QtCore.Qt.ItemDataRole.UserRole + 3
    #: list mode's Category column: the user category for saved
    #: gradients, the curated set's label (Wada/Klee/...) otherwise
    CategoryLabelRole = QtCore.Qt.ItemDataRole.UserRole + 4

    def __init__(self, preferences=None, parent=None) -> None:
        super().__init__(parent)
        self._preferences = preferences
        self._user = []
        self._user_categories = []
        self._load_user()
        # The curated palettes are no longer a read-only class of their
        # own - they're SEEDED once into the user gradients (like the Code
        # section's Starter Toolbox), so they can be moved, edited, deleted
        # and their categories removed just like any saved gradient -
        # ordinary editable entries, not read-only. After seeding
        # every entry is a normal user gradient.
        self._seed_curated_once()
        self._entries = self._all_entries()

    #: bump when the seed contents change, so a new set re-seeds
    _SEED_MARKER = ".assetlib_gradient_seed_v1"

    def _seed_curated_once(self) -> None:
        """First run per library: turn every curated combination into a
        normal user gradient (stepped ramp, its set+size as the category,
        its colour-theory note kept and now editable). Guarded by a marker
        file so a later delete/edit/move sticks and it never re-seeds.
        Best-effort - never blocks construction."""
        if self._preferences is None:
            return
        marker = os.path.join(self._preferences.dir, self._SEED_MARKER)
        if os.path.exists(marker):
            return
        try:
            seeded = 0
            for curated in CURATED_SETS:
                path = _def_path(curated["file"])
                if not path or not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    combos = json.load(f).get("combinations", [])
                for combo in combos:
                    colors = combo.get("colors") or []
                    if not colors:
                        continue
                    n = len(colors)
                    category = "%s %s Color%s" % (
                        curated["label"], n, "" if n == 1 else "s"
                    )
                    if category not in self._user_categories:
                        self._user_categories.append(category)
                    name = combo.get("name") or "Combination %s" % combo.get("id")
                    self._user.append({
                        "type": "user",
                        "name": name,
                        "category": category,
                        "colors": colors,
                        "note": combo.get("note", ""),
                        "ramp": _palette_ramp_data(colors),
                        "favorite": False,
                    })
                    seeded += 1
            # Only mark "done" once we actually seeded something: if the
            # def files were unreachable this run (e.g. ASSETLIB not set
            # yet) we added nothing, so leave the marker off and retry next
            # launch rather than permanently blocking the seed.
            if seeded:
                self._save_user()
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write("seeded %d curated palettes\n" % seeded)
        except Exception as exc:
            print("Amaze: gradient seed failed: %s" % exc)

    # ------------------------------------------------------------------
    # User-gradient persistence: <library dir>/gradients.json - lives
    # with the library data (synced along with it), not in the app
    # install or settings.
    def _user_file(self) -> str:
        if self._preferences is None:
            return ""
        return os.path.join(self._preferences.dir, "gradients.json")

    def _load_user(self) -> None:
        path = self._user_file()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._user = data.get("gradients", [])
            for entry in self._user:
                entry["type"] = "user"
            self._user_categories = data.get("categories", [])
        except (OSError, ValueError) as exc:
            print("Amaze: could not load gradients.json: " + str(exc))

    def _save_user(self) -> None:
        path = self._user_file()
        if not path:
            return
        data = {
            "categories": self._user_categories,
            "gradients": [
                {k: v for k, v in entry.items() if k != "type"}
                for entry in self._user
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except OSError as exc:
            print("Amaze: could not save gradients.json: " + str(exc))

    def _all_entries(self) -> list:
        # Everything is a user gradient now (curated palettes are seeded
        # in on first run - see _seed_curated_once).
        return list(self._user)

    def _reset_entries(self) -> None:
        self.beginResetModel()
        self._entries = self._all_entries()
        self.endResetModel()

    def user_categories(self) -> list:
        return list(self._user_categories)

    def add_user_category(self, name: str) -> None:
        name = (name or "").strip()
        if name and name not in self._user_categories:
            self._user_categories.append(name)
            self._save_user()

    def count_in_category(self, name: str) -> int:
        return sum(1 for e in self._user if e.get("category") == name)

    def count_for_filter(self, kind: str, value) -> int:
        """Entry count for a sidebar filter - same semantics as
        GradientFilterProxyModel.filterAcceptsRow, minus search/favs."""
        if kind == "category":
            return self.count_in_category(value)
        return len(self._entries)

    # ------------------------------------------------------------------
    # Favorites - every gradient keeps its flag inline in gradients.json.
    def is_favorite(self, row: int) -> bool:
        entry = self.entry(row)
        return bool(entry.get("favorite")) if entry is not None else False

    def toggle_favorite(self, row: int) -> None:
        entry = self.entry(row)
        if entry is None:
            return
        entry["favorite"] = not entry.get("favorite")
        self._save_user()
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.FavoriteRole])

    def set_user_category(self, rows: list, category: str) -> int:
        """Move the given rows' gradients to a category (dragged onto a
        sidebar category, or the Move-to menu). Returns how many moved."""
        category = (category or "").strip()
        if not category:
            return 0
        if category not in self._user_categories:
            self._user_categories.append(category)
        moved = 0
        for row in rows:
            entry = self.entry(row)
            if entry is not None and entry.get("category") != category:
                entry["category"] = category
                moved += 1
        if moved:
            self._save_user()
            self._reset_entries()
        return moved

    def update_gradient(self, row: int, name: str, category: str,
                        note: str) -> None:
        """Edit Info: rename, recategorise and set the notes of one
        gradient. A new category is created; blank clears it."""
        entry = self.entry(row)
        if entry is None:
            return
        category = (category or "").strip()
        if category and category not in self._user_categories:
            self._user_categories.append(category)
        entry["name"] = (name or "").strip() or entry.get("name") or "Gradient"
        entry["category"] = category
        entry["note"] = note or ""
        self._save_user()
        self._reset_entries()

    def remove_user_category(self, name: str) -> None:
        """Drops the category itself; its gradients are kept, just
        uncategorized (still listed under "All")."""
        if name in self._user_categories:
            self._user_categories.remove(name)
        changed = False
        for entry in self._user:
            if entry.get("category") == name:
                entry["category"] = ""
                changed = True
        self._save_user()
        if changed:
            # Subtitles show the category, so affected rows repaint.
            self._reset_entries()

    def add_user_gradient(self, name: str, category: str, ramp_data: dict) -> None:
        """Registers a saved ramp. The color list is derived from the
        ramp values so search/swatches/subtitles work identically to the
        Wada entries (hex stands in for a color name)."""
        colors = []
        for value in ramp_data.get("values", []):
            hex_color = "#%02x%02x%02x" % tuple(
                max(0, min(255, round(c * 255))) for c in value[:3]
            )
            colors.append({"name": hex_color, "hex": hex_color})
        category = (category or "").strip()
        if category and category not in self._user_categories:
            self._user_categories.append(category)
        self._user.insert(
            0,
            {
                "type": "user",
                "name": (name or "Gradient").strip() or "Gradient",
                "category": category,
                "colors": colors,
                "ramp": ramp_data,
            },
        )
        self._save_user()
        self._reset_entries()

    def remove_user_gradient(self, row: int) -> None:
        entry = self.entry(row)
        if entry is None:
            return
        self._user.remove(entry)
        self._save_user()
        self._reset_entries()

    # ------------------------------------------------------------------
    def rowCount(self, parent=None) -> int:
        return len(self._entries)

    def entry(self, row: int) -> dict | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    @staticmethod
    def _is_banded(entry: dict) -> bool:
        """A palette / stepped ramp paints as bands; a smooth ramp as a
        gradient. True when every basis is Constant (or there's no ramp
        yet - a freshly seeded palette)."""
        bases = (entry.get("ramp") or {}).get("bases") or []
        return bool(entry.get("colors")) and (
            not bases or all(b == "Constant" for b in bases)
        )

    @classmethod
    def _entry_thumb_key(cls, entry: dict):
        """Content-addressed (the hexes, plus ramp bases) - renames can't
        stale it, edits naturally mint a new key and the old image ages
        out of the shared LRU."""
        hexes = tuple(c["hex"] for c in entry["colors"])
        bases = tuple((entry.get("ramp") or {}).get("bases") or ())
        return ("grad", cls._is_banded(entry), hexes, bases, THUMB_SIZE)

    def _thumb(self, row: int) -> QtGui.QImage:
        entry = self._entries[row]
        key = self._entry_thumb_key(entry)
        image = thumbnails.engine.peek(key)
        if image is not None:
            return image
        image = QtGui.QImage(
            THUMB_SIZE, THUMB_SIZE, QtGui.QImage.Format.Format_RGB32
        )
        painter = QtGui.QPainter(image)
        if self._is_banded(entry):
            # Palette / stepped ramp -> horizontal colour bands (the
            # dictionary's own presentation, kept for the seeded palettes).
            colors = entry["colors"]
            band_h = THUMB_SIZE / max(len(colors), 1)
            for i, color in enumerate(colors):
                painter.fillRect(
                    QtCore.QRectF(0, i * band_h, THUMB_SIZE, band_h + 1),
                    QtGui.QColor(color["hex"]),
                )
        else:
            self._paint_ramp(painter, entry.get("ramp") or {})
        painter.end()
        # PAINT provider: synchronous paint-on-miss, deposited under
        # the same shared budget as every other section's thumbnails.
        thumbnails.engine.deposit(key, image)
        return image

    @staticmethod
    def _paint_ramp(painter: QtGui.QPainter, ramp_data: dict) -> None:
        """Left-to-right preview of a saved ramp: hard bands when the
        ramp is fully constant-basis, otherwise a linear-interpolated
        gradient (close enough visually for the smooth bases)."""
        keys = ramp_data.get("keys", [])
        values = ramp_data.get("values", [])
        bases = ramp_data.get("bases", [])
        if not keys or not values:
            painter.fillRect(0, 0, THUMB_SIZE, THUMB_SIZE, QtGui.QColor("#444444"))
            return
        if all(b == "Constant" for b in bases):
            edges = list(keys) + [1.0]
            for i, value in enumerate(values):
                x0 = max(0.0, min(1.0, edges[i])) * THUMB_SIZE
                x1 = max(0.0, min(1.0, edges[i + 1])) * THUMB_SIZE
                color = QtGui.QColor.fromRgbF(*value[:3])
                painter.fillRect(QtCore.QRectF(x0, 0, x1 - x0 + 1, THUMB_SIZE), color)
            return
        gradient = QtGui.QLinearGradient(0, 0, THUMB_SIZE, 0)
        for key, value in zip(keys, values):
            gradient.setColorAt(
                max(0.0, min(1.0, key)), QtGui.QColor.fromRgbF(*value[:3])
            )
        painter.fillRect(0, 0, THUMB_SIZE, THUMB_SIZE, QtGui.QBrush(gradient))

    def data(self, index, role: int = 0):
        row = index.row()
        entry = self.entry(row)
        if entry is None:
            return None
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return entry.get("name") or "Gradient"
        if role == self.SubtitleRole:
            # Uniformly "Gradient" - the Type column/grid subtitle names
            # the KIND of thing, consistent with Materials' "Redshift"
            # and Textures' "HDR". Which set/palette
            # size an entry belongs to is Category-column information.
            return "Gradient"
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            return self._thumb(row)
        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            from matlib.helpers import helpers

            names = ", ".join(c["name"] for c in entry["colors"])
            note = entry.get("note")
            # Klee entries carry the theory principle behind the
            # combination - surfaced in the tooltip. Word-wrapped in a
            # max-width box (same treatment as the Code section).
            text = note + "\n" + names if note else names
            return helpers.tooltip_html(text)
        if role == self.ColorsRole:
            return entry["colors"]
        if role == self.FavoriteRole:
            return self.is_favorite(row)
        if role == self.CategoryLabelRole:
            return entry.get("category") or "Uncategorized"
        return None


class GradientFilterProxyModel(QtCore.QSortFilterProxyModel):
    """Search over names AND the color names inside entries, combined
    with the sidebar filter: a user category, or a Wada palette size."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._name_filter = ""
        self._kind = "all"
        self._value = None
        self._favorites_only = False

    def set_name_filter(self, text: str) -> None:
        self._name_filter = (text or "").strip().lower()
        self.invalidateFilter()

    def set_favorites_only(self, enabled: bool) -> None:
        self._favorites_only = bool(enabled)
        self.invalidateFilter()

    def set_sidebar_filter(self, kind: str, value) -> None:
        """("all", None) or ("category", name)."""
        self._kind = kind
        self._value = value
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent) -> bool:
        model = self.sourceModel()
        entry = model.entry(source_row)
        if entry is None:
            return False
        if self._favorites_only and not model.is_favorite(source_row):
            return False
        if self._kind == "category" and entry.get("category") != self._value:
            return False
        if not self._name_filter:
            return True
        index = model.index(source_row, 0)
        name = (model.data(index, QtCore.Qt.ItemDataRole.DisplayRole) or "").lower()
        if self._name_filter in name:
            return True
        for color in entry["colors"]:
            if self._name_filter in color["name"].lower():
                return True
        return False
