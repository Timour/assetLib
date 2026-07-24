"""Constructs the Python panel Widget for the MatLib and provides Views to the Models"""

import os
import shutil
import sys
import subprocess
import importlib

from PySide6 import QtWidgets, QtGui, QtCore, QtUiTools
import hou

from matlib.panel import dragdrop_widgets, sections
from matlib.core import debug
from matlib.core import (
    material,
    thumbnails,
    library,
    category,
    multifilterproxy_model,
    texture_library,
    gradient_library,
    cop_library,
    geo_library,
    code_library,
    matx_library,
    matx_import,
    matx_sources,
)
from matlib.dialogs import (
    about_dialog,
    prefs_dialog,
    usd_dialog,
    gradient_dialog,
    code_dialog,
)
from matlib.prefs import prefs
from matlib.helpers import helpers, theme, ui_helpers, vex_syntax

# Before library - the models import the shared thumbnail engine.
importlib.reload(debug)
importlib.reload(thumbnails)
importlib.reload(library)
importlib.reload(category)
importlib.reload(prefs)
# Before ui_helpers - its class bodies read theme colors.
importlib.reload(theme)
importlib.reload(ui_helpers)
importlib.reload(helpers)
importlib.reload(texture_library)
importlib.reload(gradient_library)
# After library/category so its subclasses bind to the freshly
# reloaded material classes.
importlib.reload(cop_library)
# After texture_library - geo_library reuses its ThumbnailCache/proxy.
importlib.reload(geo_library)
# Before code_library/code_dialog - both consume its palette/tokenizer.
importlib.reload(vex_syntax)
# After library/category - subclasses the material machinery.
importlib.reload(code_library)

importlib.reload(about_dialog)
importlib.reload(prefs_dialog)
importlib.reload(usd_dialog)
importlib.reload(gradient_dialog)
importlib.reload(code_dialog)
importlib.reload(dragdrop_widgets)
importlib.reload(multifilterproxy_model)



class AssetItemDelegate(QtWidgets.QStyledItemDelegate):
    """Paints each grid/list tile as thumbnail + name line + a greyed
    subtitle line beneath it (renderer for materials, file format for
    textures, etc.), in both grid (icon on top, text below) and list (icon
    left, text right) modes. Any failure falls back to the default painting
    so a bad case degrades to a plain name rather than breaking the view.
    Generic over which role feeds the subtitle line, so it's reused as-is
    for every section (Materials/Textures/...), not just materials."""

    PAD = 4
    GAP = 8
    TEXT_COLOR = QtGui.QColor("#cdc8bc")
    # Class value = the accent DEFAULT; setup()/show_prefs() overwrite
    # it per-instance from prefs.accent_color so the subtitle line
    # tracks the accent preference live (it was deliberately matched to
    # the accent in the H21 color pass, then drifted every time the
    # accent changed - now it follows).
    DIM = QtGui.QColor("#5d7abd")
    # Confirmed keeper (started as a "TEST"; its color has since been
    # tuned, settling that it stays): dark background behind
    # the thumbnail area, so a non-square image (e.g. a wide HDR
    # panorama) still shows a visible tile boundary instead of blending
    # into the panel background.
    # Own constant, not tied to ClickSlider.RIGHT_COLOR - it briefly was,
    # which meant the later slider-specific color tuning (a request for
    # a slider-only "right side dark color") silently recolored
    # every thumbnail tile's background too, an unintended side effect
    # nobody asked for.
    THUMB_BG_COLOR = theme.color("surface_low")

    # GRID tiles only (the asset103-vs-104 annotated design shot):
    # quieter text than list mode - name in the neutral grey the design
    # system already uses for unselected tabs, subtitle a dimmer grey
    # instead of the accent.
    # The text block sits 10R (5c) in from the tile's LEFT edge and is
    # ANCHORED TO THE CELL BOTTOM with 10R padding ("10px padding from
    # the bottom, not to the picture") - its position is independent of
    # where the thumbnail ends. The ~16R measured to the next row
    # emerges from bottom padding + the next cell's own top pad. List
    # mode keeps TEXT_COLOR/DIM untouched (Type column stays accent).
    GRID_NAME_COLOR = theme.color("text")
    GRID_SUBTITLE_COLOR = theme.color("text_dim")
    GRID_TEXT_INSET = 5
    # Text-to-card-bottom padding: 16R, same as the margins between
    # cards (the simplified spec). 5c by QRect - the spec's measurements
    # read the GLYPH bottom, which sits the font descent (~5R) inside
    # the QRect, so 10R + descent lands the visible gap at ~16R.
    GRID_BOTTOM_PAD = 5
    # Image-bottom -> text-top gap. The tile is now "square": the image
    # fills the tile width, the text sits GRID_IMG_TEXT_GAP below it and
    # GRID_BOTTOM_PAD above the card's bottom edge (spec: 16px to the
    # image, 16px to the edge). grid_cell_size() sizes the cell to match
    # so there's no leftover slack making tiles read tall.
    GRID_IMG_TEXT_GAP = 8

    # List mode's Category column text - the design's yellow, shared with
    # the "Category" header label (ListColumnHeader.CATEGORY_COLOR).
    CATEGORY_COLOR = QtGui.QColor("#ebc658")
    # Selected rows/tiles paint ALL text black (the accent/
    # yellow columns were hard to read against the amber selection
    # highlight; the palette's highlightedText wasn't reliably dark).
    SELECTED_TEXT = QtGui.QColor("#000000")

    #: rendered star badge per size - one SVG rasterization per badge
    #: size for the whole app
    _star_cache = {}
    #: (family, pointsize, selected) -> (name_font, rend_font, fm, fm)
    _font_cache = {}

    @classmethod
    def grid_cell_size(cls, ts, base_font):
        """The grid cell (gridSize) matching _paint's square layout:
        width = ts + 10, height = top pad + a width-filling square image
        + the 16R image->text gap + the two text lines + the 16R bottom
        pad. Sizing the cell to the layout is what keeps tiles tight/
        square instead of a small image adrift in a tall block."""
        pad = cls.PAD
        width = ts + 10
        icon_side = max(width - 2 * pad, 1)
        _nf, _rf, fm_name, fm_rend = cls.fonts_for(base_font, False)
        block_h = fm_name.height() + fm_rend.height()
        height = (
            pad
            + icon_side
            + cls.GRID_IMG_TEXT_GAP
            + block_h
            + cls.GRID_BOTTOM_PAD
        )
        return QtCore.QSize(width, height)

    @classmethod
    def fonts_for(cls, option_font, selected):
        """Cached (name_font, subtitle_font, name_metrics,
        subtitle_metrics) for an option font + selection state.
        Building fonts and metrics per row per repaint is measurable
        churn while scrolling, and the option font only changes with
        Houdini's UI scale - the cache holds a couple of entries for
        the app's whole life. Never mutated after creation
        (painter.setFont copies), so sharing is safe. The sidebar
        delegate shares this cache."""
        key = (option_font.family(), option_font.pointSizeF(), selected)
        cached = cls._font_cache.get(key)
        if cached is None:
            name_font = QtGui.QFont(option_font)
            rend_font = QtGui.QFont(option_font)
            # Sub-line reads as secondary via the grey colour, not by
            # being tiny: keep it at the name size with a 12pt floor.
            rend_font.setPointSizeF(max(option_font.pointSizeF(), 12.0))
            if selected:
                # Black-on-yellow reads too thin at regular weight
                # - bold everything on the highlight.
                name_font.setBold(True)
                rend_font.setBold(True)
            cached = (
                name_font,
                rend_font,
                QtGui.QFontMetrics(name_font),
                QtGui.QFontMetrics(rend_font),
            )
            cls._font_cache[key] = cached
        return cached

    def __init__(
        self, subtitle_role, parent=None, category_role=None, favorite_role=None
    ):
        super().__init__(parent)
        # Favorited tiles get a small amber star badge drawn LIVE from
        # this role - uniformly across every section. Replaces the old
        # material-only mechanism that baked a star into the cached
        # thumbnail image, which never visibly worked in this panel's
        # whole life (the feature went unnoticed until a review
        # mentioned it).
        self._favorite_role = favorite_role
        # Scaled tiles are cached (see _icon_pixmap) - 64MB covers a
        # full screen of tiles at any slider size several times over.
        QtGui.QPixmapCache.setCacheLimit(65536)
        self._subtitle_role = subtitle_role
        # List mode's Category column source (materials/cop: the
        # categories list; textures: the containing folder; gradients:
        # user category or curated set). None = column stays empty.
        self._category_role = category_role
        # List-mode spreadsheet columns (Thumbnail | Name | Type |
        # Category), pushed in by the panel's _update_list_columns() -
        # the Name column is sized to the longest currently-visible name.
        self._list_thumb_w = None
        self._list_name_w = None
        self._list_type_w = None

    def set_list_columns(self, thumb_w, name_w, type_w):
        self._list_thumb_w = int(thumb_w)
        self._list_name_w = int(name_w)
        self._list_type_w = int(type_w)

    @staticmethod
    def _to_pixmap(icon):
        """The model returns a QImage for the thumbnail (0 before it renders);
        normalise to a QPixmap for painting, or None if there's nothing."""
        if isinstance(icon, QtGui.QPixmap):
            return icon if not icon.isNull() else None
        if isinstance(icon, QtGui.QImage):
            if icon.isNull():
                return None
            return QtGui.QPixmap.fromImage(icon)
        if isinstance(icon, QtGui.QIcon):
            pm = icon.pixmap(256, 256)
            return pm if not pm.isNull() else None
        return None

    @staticmethod
    def _icon_pixmap(icon, side, dpr=1.0):
        """Scaled tile pixmap, cached per (source image, target size, dpr)
        in QPixmapCache - without this, every visible tile smooth-scales
        its image again on EVERY repaint (scrolling repaints the whole
        viewport continuously). QImage.cacheKey() is stable for the
        stored, never-mutated thumbnails, so cache hits survive across
        paints; a rerendered thumbnail is a new QImage with a new key,
        so stale tiles can't be served. The pixmap is rendered at the
        display's PHYSICAL resolution (side * dpr) with its devicePixel-
        Ratio set, so drawPixmap paints it crisp on Retina rather than
        upscaling a logical-size pixmap 2x - the callers still position
        it in logical units (see _logical_size)."""
        target = max(1, round(side * dpr))
        if isinstance(icon, QtGui.QImage) and not icon.isNull():
            key = "assetlib_%s_%s_%s" % (icon.cacheKey(), side, dpr)
            cached = QtGui.QPixmapCache.find(key)
            if cached is not None and not cached.isNull():
                return cached
            scaled = QtGui.QPixmap.fromImage(icon).scaled(
                target,
                target,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            QtGui.QPixmapCache.insert(key, scaled)
            return scaled
        pixmap = AssetItemDelegate._to_pixmap(icon)
        if pixmap is None:
            return None
        scaled = pixmap.scaled(
            target,
            target,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        return scaled

    @staticmethod
    def _logical_size(pixmap):
        """Device-independent (logical) w, h of a (possibly Retina)
        pixmap - what the centering math must use, since width()/height()
        return physical pixels once devicePixelRatio is set."""
        r = pixmap.devicePixelRatio() or 1.0
        return round(pixmap.width() / r), round(pixmap.height() / r)

    #: The stamped-hole fill = the GRID's own background (#313131, the
    #: thumblist Base set in setup) - change in lockstep with the grid
    #: bg. This is the "background" star color mode's value.
    STAR_HOLE_COLOR = theme.color_hex("surface_high")
    #: Effective star color (hex) - pushed by the panel from the
    #: star_color_mode/star_custom_color prefs (background = the
    #: stamped-hole look, yellow = amber sticker, custom = user pick).
    _star_color = STAR_HOLE_COLOR

    @classmethod
    def set_star_color(cls, hex_color: str) -> None:
        if hex_color and hex_color != cls._star_color:
            cls._star_color = hex_color
            cls._star_cache = {}

    @classmethod
    def _star_pixmap(cls, side):
        """The star shape filled flat with the effective star color
        (see set_star_color)."""
        pixmap = cls._star_cache.get(side)
        if pixmap is None:
            path = (
                (hou.getenv("ASSETLIB") or "")
                + "/scripts/python/matlib/ui/star_on.svg"
            )
            if os.path.exists(path):
                pixmap = ui_helpers.render_svg_pixmap(
                    path, side, {"#fcb900": cls._star_color}
                )
            else:
                pixmap = QtGui.QPixmap()
            cls._star_cache[side] = pixmap
        return pixmap

    def _paint_favorite_badge(self, painter, index, area_x, area_y, icon_side):
        """Small amber star in the icon area's top-right corner when the
        item is favorited (no-op when no favorite role is wired)."""
        if self._favorite_role is None:
            return
        if not index.data(self._favorite_role):
            return
        badge = max(12, min(icon_side // 4, 22))
        star = self._star_pixmap(badge)
        if not star.isNull():
            painter.drawPixmap(
                area_x + icon_side - star.width() - 2, area_y + 2, star
            )

    def sizeHint(self, option, index):
        """Without this override, Qt falls back to its own heuristic (partly
        based on whether DecorationRole currently has an icon), independent
        of the gridSize apply_view_mode() sets on the view - the delegate
        then paints correctly inside whatever (possibly much smaller) rect
        Qt handed it, which looks like a tiny thumbnail floating in a mostly
        empty row/tile. Materials mostly hid this because a placeholder icon
        is always present immediately; textures return None until a
        thumbnail actually generates, exposing it. Mirror gridSize() (set in
        apply_view_mode()) exactly so layout and paint rect always agree."""
        try:
            grid = option.widget.gridSize()
            if grid.isValid() and grid.height() > 0:
                is_list = (
                    option.widget.viewMode()
                    == QtWidgets.QListView.ViewMode.ListMode
                )
                if is_list:
                    width = option.widget.viewport().width()
                    return QtCore.QSize(width if width > 0 else grid.width(), grid.height())
                if grid.width() > 0:
                    return grid
        except Exception:
            pass
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        try:
            self._paint(painter, option, index)
        except Exception:
            super().paint(painter, option, index)

    def _paint(self, painter, option, index):
        painter.save()

        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        alternate = bool(
            option.features & QtWidgets.QStyleOptionViewItem.ViewItemFeature.Alternate
        )
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif alternate:
            painter.fillRect(option.rect, option.palette.alternateBase())

        rect = option.rect
        icon = index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        name = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        renderer = index.data(self._subtitle_role) or ""

        is_list = False
        try:
            is_list = (
                option.widget.viewMode()
                == QtWidgets.QListView.ViewMode.ListMode
            )
        except Exception:
            is_list = False

        # Render tile images at the display's physical resolution so they
        # stay crisp on a Retina screen instead of being upscaled 2x when
        # painted (photos tolerate that, sharp-edged content like the code
        # preview does not).
        try:
            dpr = option.widget.devicePixelRatioF()
        except Exception:
            dpr = 1.0

        name_font, rend_font, fm_name, fm_rend = self.fonts_for(
            option.font, selected
        )
        h_name = fm_name.height()
        h_rend = fm_rend.height()

        # Selection turns EVERY column black - name, type and category
        # colors all fight the amber highlight otherwise.
        name_color = self.SELECTED_TEXT if selected else self.TEXT_COLOR
        dim_color = self.SELECTED_TEXT if selected else self.DIM
        category_color = self.SELECTED_TEXT if selected else self.CATEGORY_COLOR

        pad = self.PAD
        if is_list:
            # Spreadsheet layout (the table mockup): Thumbnail |
            # Name | Type columns with divider lines, instead of the old
            # name-with-subtitle stack. Column widths come from
            # set_list_columns(); sane fallbacks if it never ran.
            header = ui_helpers.ListColumnHeader
            thumb_w = self._list_thumb_w or rect.height()
            name_w = self._list_name_w or 200
            type_w = self._list_type_w or 150
            col_pad = header.COL_PAD
            icon_side = max(rect.height() - 2 * pad, 1)
            icon_y = rect.top() + pad
            painter.fillRect(
                QtCore.QRect(rect.left() + pad, icon_y, icon_side, icon_side),
                self.THUMB_BG_COLOR,
            )
            scaled = self._icon_pixmap(icon, icon_side, dpr) if icon else None
            if scaled is not None:
                _, lh = self._logical_size(scaled)
                iy = rect.top() + (rect.height() - lh) // 2
                painter.drawPixmap(rect.left() + pad, iy, scaled)
            self._paint_favorite_badge(
                painter, index, rect.left() + pad, rect.top() + pad, icon_side
            )
            # Name column - single line, vertically centered.
            nx = rect.left() + thumb_w + col_pad
            name_avail = max(name_w - 2 * col_pad, 1)
            painter.setFont(name_font)
            painter.setPen(name_color)
            painter.drawText(
                QtCore.QRect(nx, rect.top(), name_avail, rect.height()),
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                fm_name.elidedText(
                    name, QtCore.Qt.TextElideMode.ElideRight, name_avail
                ),
            )
            # Type column - the renderer/format label in its own column
            # (accent color, same as the grid subtitle).
            if renderer:
                tx = rect.left() + thumb_w + name_w + col_pad
                type_avail = max(type_w - 2 * col_pad, 1)
                painter.setFont(rend_font)
                painter.setPen(dim_color)
                painter.drawText(
                    QtCore.QRect(tx, rect.top(), type_avail, rect.height()),
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    fm_rend.elidedText(
                        renderer, QtCore.Qt.TextElideMode.ElideRight, type_avail
                    ),
                )
            # Category column (when scrolling All it's useful to see
            # what category items belong to) - list mode only,
            # quiet grey. The role's value may be a list (materials).
            if self._category_role is not None:
                category = index.data(self._category_role)
                if isinstance(category, (list, tuple)):
                    category = ", ".join(str(c) for c in category)
                if category:
                    cx = rect.left() + thumb_w + name_w + type_w + col_pad
                    cat_avail = max(rect.right() - cx - pad, 1)
                    painter.setFont(rend_font)
                    painter.setPen(category_color)
                    painter.drawText(
                        QtCore.QRect(cx, rect.top(), cat_avail, rect.height()),
                        QtCore.Qt.AlignmentFlag.AlignLeft
                        | QtCore.Qt.AlignmentFlag.AlignVCenter,
                        fm_rend.elidedText(
                            str(category),
                            QtCore.Qt.TextElideMode.ElideRight,
                            cat_avail,
                        ),
                    )
            # Column dividers (Name | Type and Type | Category - the
            # thumbnails delimit their own column), drawn per row at
            # fixed x positions so they read as continuous vertical
            # lines.
            painter.setPen(header.DIVIDER)
            x = rect.left() + thumb_w + name_w
            painter.drawLine(x, rect.top(), x, rect.bottom())
            x = rect.left() + thumb_w + name_w + type_w
            painter.drawLine(x, rect.top(), x, rect.bottom())
        else:
            text_x = rect.left() + pad + self.GRID_TEXT_INSET
            text_w = max(rect.width() - 2 * pad - self.GRID_TEXT_INSET, 1)
            block_h = h_name + (h_rend if renderer else 0)
            # "Square" tile: the image fills the tile width, the text
            # sits GRID_IMG_TEXT_GAP below it (not floating with leftover
            # slack, which read as too-tall tiles). grid_cell_size()
            # sizes the cell to this exact layout.
            icon_side = max(rect.width() - 2 * pad, 1)
            icon_x = rect.left() + pad
            icon_y = rect.top() + pad
            # The tile's dark backing covers the WHOLE tile - the image
            # area AND the text block below it, down to the cell's
            # bottom edge (per the red-marked 104 design shot: the grey
            # extends under the text, one continuous card).
            # Text top-anchored 16R below the image; the card ends
            # GRID_BOTTOM_PAD below the text. Both gaps are 16R, so the
            # card reads square/tight rather than a small image adrift
            # in a tall grey block.
            text_top = icon_y + icon_side + self.GRID_IMG_TEXT_GAP
            card_bottom = text_top + block_h + self.GRID_BOTTOM_PAD
            painter.fillRect(
                QtCore.QRect(
                    icon_x, icon_y, icon_side, card_bottom - icon_y
                ),
                self.THUMB_BG_COLOR,
            )
            if selected:
                # The card fill just covered the selection highlight the
                # base pass painted - restore it on the TEXT zone (below
                # the image), so a selected tile reads like it always
                # did: yellow band, black bold text, thumbnail intact.
                zone_top = icon_y + icon_side
                painter.fillRect(
                    QtCore.QRect(
                        icon_x,
                        zone_top,
                        icon_side,
                        max(card_bottom - zone_top, 0),
                    ),
                    option.palette.highlight(),
                )
            scaled = self._icon_pixmap(icon, icon_side, dpr) if icon else None
            if scaled is not None:
                lw, _ = self._logical_size(scaled)
                ix = rect.left() + (rect.width() - lw) // 2
                painter.drawPixmap(ix, icon_y, scaled)
            self._paint_favorite_badge(painter, index, icon_x, icon_y, icon_side)
            painter.setFont(name_font)
            painter.setPen(
                self.SELECTED_TEXT if selected else self.GRID_NAME_COLOR
            )
            painter.drawText(
                QtCore.QRect(text_x, text_top, text_w, h_name),
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                fm_name.elidedText(
                    name, QtCore.Qt.TextElideMode.ElideRight, text_w
                ),
            )
            if renderer:
                painter.setFont(rend_font)
                painter.setPen(
                    self.SELECTED_TEXT
                    if selected
                    else self.GRID_SUBTITLE_COLOR
                )
                painter.drawText(
                    QtCore.QRect(text_x, text_top + h_name, text_w, h_rend),
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    fm_rend.elidedText(
                        renderer, QtCore.Qt.TextElideMode.ElideRight, text_w
                    ),
                )

        painter.restore()



class SidebarItemDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the category/folder sidebar's rows by hand for one reason:
    the SELECTION color. Houdini's app-wide stylesheet renders list-item
    selection itself (QSS outranks widget palettes), so palette changes
    on cat_list never actually changed the visible highlight - the
    sidebar showed the stylesheet's darker brown while the grid (whose
    AssetItemDelegate paints selection manually) showed the real
    palette yellow. Owning the row painting is the same fix the grid
    uses, and avoids the documented alternative (a stylesheet on
    cat_list), which knocks its scrollbar off native rendering."""

    PAD = 6

    def __init__(self, highlight: QtGui.QColor, parent=None):
        super().__init__(parent)
        self._highlight = QtGui.QColor(highlight)
        self._selected_text = QtGui.QColor("#000000")
        # Counts on individual categories can be toggled off in
        # Preferences; "All" always shows its total (fixed rule).
        self.show_counts = True
        # Row highlighted while an asset is dragged over it (drop target
        # feedback, in the accent/select purple). -1 = none.
        self.drag_row = -1
        self._drag_color = QtGui.QColor(AssetItemDelegate.DIM)

    def set_drag_color(self, color: QtGui.QColor) -> None:
        self._drag_color = QtGui.QColor(color)

    def paint(self, painter, option, index):
        try:
            painter.save()
            selected = bool(
                option.state & QtWidgets.QStyle.StateFlag.State_Selected
            )
            drag_hover = index.row() == self.drag_row
            if drag_hover:
                # Drop-target feedback wins over the selection fill.
                painter.fillRect(option.rect, self._drag_color)
            elif selected:
                painter.fillRect(option.rect, self._highlight)
            name = str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
            # Entry count suffix ("All (345)") - every sidebar model
            # exposes the same shared count role (see category.py's
            # SIDEBAR_COUNT_ROLE); painted here so no DisplayRole text
            # changes (category matching, restore-by-text and filters
            # all key off the clean name). Drawn at 50% opacity so the
            # numbers inform without competing with the names
            # (full-strength counts cluttered readability).
            count = index.data(int(QtCore.Qt.ItemDataRole.UserRole) + 40)
            if not self.show_counts and name != "All":
                count = None
            count_text = " (%s)" % count if count is not None else ""
            # Same shared font/metrics cache as the tile delegate
            # (bold-on-selection matches the tiles' rule).
            font, _rf, metrics, _rfm = AssetItemDelegate.fonts_for(
                option.font, selected
            )
            painter.setFont(font)
            base_color = (
                self._selected_text
                if (selected or drag_hover)
                else AssetItemDelegate.TEXT_COLOR
            )
            text_rect = option.rect.adjusted(self.PAD, 0, -self.PAD, 0)
            count_width = (
                metrics.horizontalAdvance(count_text) if count_text else 0
            )
            name_avail = max(text_rect.width() - count_width, 1)
            elided = metrics.elidedText(
                name, QtCore.Qt.TextElideMode.ElideRight, name_avail
            )
            painter.setPen(base_color)
            painter.drawText(
                text_rect,
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                elided,
            )
            if count_text:
                dim_color = QtGui.QColor(base_color)
                dim_color.setAlphaF(0.5)
                painter.setPen(dim_color)
                count_x = text_rect.left() + metrics.horizontalAdvance(elided)
                painter.drawText(
                    QtCore.QRect(
                        count_x,
                        text_rect.top(),
                        max(text_rect.right() - count_x, 1),
                        text_rect.height(),
                    ),
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    count_text,
                )
            painter.restore()
        except Exception:
            painter.restore()
            super().paint(painter, option, index)


class MatLibPanel(QtWidgets.QWidget):
    """Constructs the Python panel Widget for the MatLib and provides Views to the Models"""

    def __init__(self) -> None:
        super(MatLibPanel, self).__init__()
        # Arm the always-on crash recorder before anything else, so a
        # failure during construction lands in the log even with Debug
        # Mode off (see core/debug.py).
        debug.install()
        try:
            self._build()
        except Exception as exc:
            debug.exception("panel construction", exc)
            raise

    def _build(self) -> None:
        # Initialize
        self.script_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        self.prefs = prefs.Prefs()

        if self.prefs.load():
            # Configured before anything else runs: a crash during
            # setup() is precisely the case a debug log has to survive.
            debug.configure(self.prefs.debug_mode)
            debug.prefs_snapshot(self.prefs)
            self.load()
            self.init_ui()
            self.setup()
        else:
            self.init_ui()
            self.material_model = None
            self.category_model = None
            # Same "no library configured" defaults for the Cop stack -
            # save_cop_from_node (reachable from a node right-click at
            # any time) guards on cop_model, and without this the guard
            # itself would raise AttributeError.
            self.cop_model = None
            self.cop_category_model = None
            self.code_model = None
            self.code_category_model = None

    # Menu title -> icon asset, per the "ui_wireframe 2 only menu"
    # design file: gear = Library, eye = View, 3D box = Renderer, each with a
    # baked-in corner triangle as the "opens a menu" hint.
    MENU_ICON_FILES = {
        "Library": "icon_library.svg",
        "View": "icon_view.svg",
        "Renderer": "icon_renderer.svg",
    }

    def _ui_icon_path(self, filename: str) -> str:
        """Absolute path of an icon asset in the plugin's ui/ folder, or
        "" if $ASSETLIB isn't set or no filename was given (callers and
        render_svg_pixmap treat "" as icon-missing and degrade). The
        four icon-loading sites all computed this same join inline
        before."""
        base = hou.getenv("ASSETLIB")
        if not base or not filename:
            return ""
        return os.path.join(
            base, "scripts", "python", "matlib", "ui", filename
        )

    def _make_menu_button(self, menu: QtWidgets.QMenu) -> "ui_helpers.IconMenuButton":
        """Stands in for a real QMenuBar item - QMainWindow reserves a
        dedicated dock area for its menu bar that can't share a row with
        other widgets, so the real menu bar (self.menu) stays alive and
        owns these QMenu objects but is hidden; this opens the same
        QMenu instance from an icon button instead (IconMenuButton in
        ui_helpers.py - hand-painted chips, tinted icon)."""
        icon_path = self._ui_icon_path(
            self.MENU_ICON_FILES.get(menu.title(), "")
        )
        return ui_helpers.IconMenuButton(menu, icon_path)

    def setup(self):
        self.category_model = category.Categories(preferences=self.prefs)
        self.category_sorted_model = category.CategoriesSidebarProxy()
        self.category_sorted_model.setSourceModel(self.category_model)
        self.category_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.category_sorted_model.setSortRole(self.category_model.CatSortRole)
        self.category_sorted_model.hide_empty = self.prefs.hide_empty_categories
        self.category_sorted_model.sort(0)

        self.material_model = library.MaterialLibrary(preferences=self.prefs)
        self.material_sorted_model = multifilterproxy_model.MultiFilterProxyModel()
        self.material_sorted_model.setSourceModel(self.material_model)
        self.material_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.material_sorted_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.material_sorted_model.sort(0)
        self.material_sorted_model.setDynamicSortFilter(False)  # Improves Performance
        self.material_selection_model = QtCore.QItemSelectionModel(
            self.material_sorted_model
        )
        self.thumb_delegate = AssetItemDelegate(
            self.material_model.RendererLabelRole,
            self.thumblist,
            category_role=self.material_model.CategoryRole,
            favorite_role=self.material_model.FavoriteRole,
        )

        # v2: Textures section models - a flat folder-pointer list (plus a
        # synthetic "All" entry aggregating every folder) and a live
        # (non-recursive, non-persisted) listing of whichever folder is
        # selected, filtered through TextureFilterProxyModel for the
        # search box / favorites-only star - same shape as the Materials
        # model/proxy pair below. See core/texture_library.py.
        self.texture_folders_model = texture_library.TextureFolders(self.prefs)
        self.texture_files_model = texture_library.TextureFiles(self.prefs)
        self.texture_sorted_model = texture_library.TextureFilterProxyModel()
        self.texture_sorted_model.setSourceModel(self.texture_files_model)
        self.texture_selection_model = QtCore.QItemSelectionModel(
            self.texture_sorted_model
        )
        self.texture_delegate = AssetItemDelegate(
            self.texture_files_model.FormatRole,
            self.thumblist,
            category_role=self.texture_files_model.FolderRole,
            favorite_role=self.texture_files_model.FavoriteRole,
        )
        # Tile subtitle line follows the accent preference (instance
        # attribute shadows the class default; refreshed again in
        # show_prefs() when the accent changes).
        for tile_delegate in (self.thumb_delegate, self.texture_delegate):
            tile_delegate.DIM = theme.accent(self.prefs.accent_color)
        # The "Type" header label follows the same accent as the type
        # entries the delegates paint.
        self.list_header.set_accent_color(theme.accent(self.prefs.accent_color))
        AssetItemDelegate.set_star_color(self._effective_star_color())
        self.sidebar_delegate.show_counts = self.prefs.sidebar_counts
        thumbnails.engine.set_budget_mb(self.prefs.ram_cache_mb)
        self.texture_files_model.progress_changed.connect(self._on_texture_progress)

        # v2: Gradients section - Sanzo Wada's color combinations as
        # curated, read-only content (see core/gradient_library.py).
        # Painted thumbnails, no files/workers, so the model trio is all
        # there is to set up.
        self.gradient_model = gradient_library.GradientLibrary(self.prefs)
        self.gradient_categories_model = gradient_library.GradientCategories(
            self.gradient_model
        )
        self.gradient_sorted_model = gradient_library.GradientFilterProxyModel()
        self.gradient_sorted_model.setSourceModel(self.gradient_model)
        self.gradient_selection_model = QtCore.QItemSelectionModel(
            self.gradient_sorted_model
        )
        self.gradient_delegate = AssetItemDelegate(
            self.gradient_model.SubtitleRole,
            self.thumblist,
            category_role=self.gradient_model.CategoryLabelRole,
            favorite_role=self.gradient_model.FavoriteRole,
        )
        self.gradient_delegate.DIM = theme.accent(self.prefs.accent_color)

        # v2: Cop section - standalone COP-network assets. A second,
        # fully independent material-style stack over its own cops.json
        # database (see core/cop_library.py); mirrors the material
        # model/proxy/selection construction above exactly, and reuses
        # thumb_delegate (identical roles - RendererLabelRole shows
        # "COP" as the tile subtitle).
        self.cop_model = cop_library.CopLibrary(preferences=self.prefs)
        self.cop_category_model = cop_library.CopCategories(preferences=self.prefs)
        self.cop_category_sorted_model = category.CategoriesSidebarProxy()
        self.cop_category_sorted_model.setSourceModel(self.cop_category_model)
        self.cop_category_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.cop_category_sorted_model.setSortRole(self.cop_category_model.CatSortRole)
        self.cop_category_sorted_model.hide_empty = self.prefs.hide_empty_categories
        self.cop_category_sorted_model.sort(0)
        self.cop_sorted_model = multifilterproxy_model.MultiFilterProxyModel()
        self.cop_sorted_model.setSourceModel(self.cop_model)
        self.cop_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.cop_sorted_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.cop_sorted_model.sort(0)
        self.cop_sorted_model.setDynamicSortFilter(False)
        self.cop_selection_model = QtCore.QItemSelectionModel(self.cop_sorted_model)

        # v2: Code section - reusable snippets over its own code.json
        # (see core/code_library.py). Same material machinery as COP,
        # storing snippet text inline and painting a code preview.
        self.code_model = code_library.CodeLibrary(preferences=self.prefs)
        self.code_category_model = code_library.CodeCategories(
            preferences=self.prefs
        )
        self.code_category_sorted_model = category.CategoriesSidebarProxy()
        self.code_category_sorted_model.setSourceModel(self.code_category_model)
        self.code_category_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.code_category_sorted_model.setSortRole(self.code_category_model.CatSortRole)
        self.code_category_sorted_model.hide_empty = self.prefs.hide_empty_categories
        self.code_category_sorted_model.sort(0)
        self.code_sorted_model = multifilterproxy_model.MultiFilterProxyModel()
        self.code_sorted_model.setSourceModel(self.code_model)
        self.code_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.code_sorted_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.code_sorted_model.sort(0)
        self.code_sorted_model.setDynamicSortFilter(False)
        self.code_selection_model = QtCore.QItemSelectionModel(self.code_sorted_model)
        # Seed the curated "Starter Toolbox" snippets once per library.
        self.code_model.seed_starter_snippets(self.code_category_model)

        # Multi-category was removed: collapse every asset to a single
        # category (its first). Idempotent, so this one-time migration
        # just no-ops on every subsequent launch.
        for _model in (self.material_model, self.cop_model, self.code_model):
            try:
                _model.collapse_multicategory()
            except Exception as exc:
                print("Amaze: category collapse failed: %s" % exc)

        # v2: online MaterialX browser. Not a section - a VIEW MODE over
        # the Materials grid (View menu > Online Materials). Uses the
        # same role numbers as MaterialLibrary, so the existing delegate
        # and filter proxy serve it unchanged.
        self.matx_online_model = matx_library.MatxOnlineLibrary(
            preferences=self.prefs
        )
        self.matx_sorted_model = multifilterproxy_model.MultiFilterProxyModel()
        self.matx_sorted_model.setSourceModel(self.matx_online_model)
        self.matx_sorted_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.matx_sorted_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.matx_sorted_model.setDynamicSortFilter(False)
        self.matx_selection_model = QtCore.QItemSelectionModel(
            self.matx_sorted_model
        )
        self.matx_source_model = matx_library.MatxSidebarModel(
            self.matx_online_model
        )
        # Preview downloads drive the same thin bar as texture/geo thumbs.
        self.matx_online_model.progress_changed.connect(
            self._on_online_preview_progress
        )
        self.online_mode = False
        self.online_source = None

        # v2: Geometry section - a folder browser like Textures (see
        # core/geo_library.py). Role numbering matches TextureFiles, so
        # the texture filter proxy and drag machinery are reused as-is.
        self.geo_folders_model = geo_library.GeoFolders(self.prefs)
        self.geo_files_model = geo_library.GeoFiles(self.prefs)
        self.geo_sorted_model = texture_library.TextureFilterProxyModel()
        self.geo_sorted_model.setSourceModel(self.geo_files_model)
        self.geo_selection_model = QtCore.QItemSelectionModel(
            self.geo_sorted_model
        )
        self.geo_delegate = AssetItemDelegate(
            self.geo_files_model.FormatRole,
            self.thumblist,
            category_role=self.geo_files_model.FolderRole,
            favorite_role=self.geo_files_model.FavoriteRole,
        )
        self.geo_delegate.DIM = theme.accent(self.prefs.accent_color)
        # Same thin progress bar the texture generation drives - the
        # geometry pass emits identical (done, total) pairs.
        self.geo_files_model.progress_changed.connect(self._on_texture_progress)

        # Keep list mode's Name column fitted to the longest visible
        # name as filters, categories, renames or folder changes alter
        # what's shown (no-op outside list mode). These go through the
        # cache-dropping wrapper since they mean content changed.
        for change_model in (
            self.material_sorted_model,
            self.texture_sorted_model,
            self.gradient_sorted_model,
            self.cop_sorted_model,
            self.geo_sorted_model,
            self.code_sorted_model,
        ):
            change_model.layoutChanged.connect(self._invalidate_list_columns)
            change_model.rowsInserted.connect(self._invalidate_list_columns)
            change_model.rowsRemoved.connect(self._invalidate_list_columns)
            change_model.modelReset.connect(self._invalidate_list_columns)
            change_model.dataChanged.connect(self._invalidate_list_columns)

        self.material_selection_model.selectionChanged.connect(self.update_details_view)

        # The section registry: one object per tab, each encapsulating how
        # it drives the shared widgets (activate/filter/favourites/
        # category-select/double-click). The shared handlers dispatch to
        # self._section() instead of branching on current_section - a new
        # section is a new class in panel/sections.py, not edits here.
        self.sections = sections.build_sections(self)

        # Start on the first ENABLED section (usually Materials, but a
        # user may have hidden it). Models all exist regardless of which
        # tabs are shown.
        first_key = next(
            (k for k, _ in self.ALL_SECTIONS if k in self.prefs.enabled_sections),
            "material",
        )
        if first_key == "material":
            self._activate_material_section()
        else:
            self._on_tab_toggled(first_key, True)
        self.section_tabs.setChecked(first_key, emit=False)
        self.filter_renderer()
        self.click_slider.setValue(self._active_thumbsize())
        self.slide()
        self.apply_view_state()

    def open(self) -> None:
        """Open the currently in preferences specified library"""
        if not self.material_model or not self.category_model:
            return
        self.material_model.save()
        # self.material_model = library.MaterialLibrary()
        self.prefs.load()
        self.load()
        if not self.material_model:
            self.setup()

        self.material_model.layoutAboutToBeChanged.emit()
        self.category_model.layoutAboutToBeChanged.emit()

        self.category_model.switch_model_data()
        self.material_model.switch_model_data()
        if getattr(self, "cop_model", None):
            self.cop_model.layoutAboutToBeChanged.emit()
            self.cop_category_model.layoutAboutToBeChanged.emit()
            self.cop_category_model.switch_model_data()
            self.cop_model.switch_model_data()
            self.cop_model.layoutChanged.emit()
            self.cop_category_model.layoutChanged.emit()
        if getattr(self, "code_model", None):
            self.code_model.layoutAboutToBeChanged.emit()
            self.code_category_model.layoutAboutToBeChanged.emit()
            self.code_category_model.switch_model_data()
            self.code_model.switch_model_data()
            self.code_model.layoutChanged.emit()
            self.code_category_model.layoutChanged.emit()

        self.click_slider.setValue(self._active_thumbsize())

        self.category_model.layoutChanged.emit()
        self.material_model.layoutChanged.emit()

        print("Amaze: Library Reloaded successfully!")  # type: ignore

    def load(self) -> None:
        """Load the currently in preferences specified library
        Copies necessary data to the target directory if not created yet"""
        new_folder = False
        if not os.path.exists(self.prefs.dir + "/library.json"):
            oldpath = (
                hou.getenv("ASSETLIB") + "/scripts/python/matlib/res/def/library.json"
            )
            shutil.copy(oldpath, self.prefs.dir + "/library.json")
            new_folder = True
        if not os.path.exists(self.prefs.dir + self.prefs.img_dir):
            os.mkdir(self.prefs.dir + self.prefs.img_dir)
            os.mkdir(self.prefs.dir + self.prefs.asset_dir)
            new_folder = True
        if new_folder:
            print("Amaze: A new library has been created successfully")

    def set_library(self) -> None:
        """
        User Sets library via Menu Option so we have to reroute

        """
        # if not self.material_model or not self.category_model:
        #   return
        if self.prefs.get_dir_from_user():
            self.prefs.load()
            self.load()
            if not self.material_model:
                self.setup()

            self.material_model.layoutAboutToBeChanged.emit()
            self.category_model.layoutAboutToBeChanged.emit()

            self.category_model.switch_model_data()
            self.material_model.switch_model_data()
            self.click_slider.setValue(self._active_thumbsize())
            self.category_model.layoutChanged.emit()
            self.material_model.layoutChanged.emit()

    def toggle_catview(self) -> None:
        """Show and Hide the Category View via Menu"""
        if self.action_catview.isChecked():
            self.cat_wrapper.setVisible(True)
            self.action_catview.setChecked(True)
        else:
            self.cat_wrapper.setVisible(False)
            self.action_catview.setChecked(False)
        # Remember the choice across sessions
        self.prefs.show_categories = self.action_catview.isChecked()
        self.prefs.save()

    def edit_material_info(self) -> None:
        """Open the material info dialog for the current selection
        (right-click "Edit Info"). update_details_view already keeps the
        form populated from the selection, so this just shows/raises the
        floating dialog."""
        if not self.material_model:
            return
        self.update_details_view()
        self.details.setVisible(True)
        self.details_dialog.show()
        self.details_dialog.raise_()
        self.details_dialog.activateWindow()

    def apply_view_state(self) -> None:
        """Apply the persisted category/details visibility from preferences"""
        self.action_catview.setChecked(self.prefs.show_categories)
        self.cat_wrapper.setVisible(self.prefs.show_categories)
        self.apply_view_mode()

    def apply_view_mode(self) -> None:
        """Apply the persisted grid/list view mode to the thumbnail list and
        sync the toggle button and menu. The size slider drives icon size in
        both modes: grid icons grow (fewer per row), list rows grow taller.
        Wrapped so a bad state falls back to grid instead of hanging."""
        if not self.material_model:
            return
        ts = self.material_model.thumbsize
        try:
            if self.prefs.view_mode == "list":
                self.thumblist.setViewMode(QtWidgets.QListView.ViewMode.ListMode)
                self.thumblist.setFlow(QtWidgets.QListView.Flow.TopToBottom)
                self.thumblist.setWrapping(False)
                self.thumblist.setIconSize(QtCore.QSize(ts, ts))
                # Explicit, always-valid row size (never an empty QSize, which
                # was the crash/hang source): full-width rows, height fits the
                # icon plus padding. Floor lowered 52 -> 24 (2026-07-19): the
                # 52 dated from the old two-text-line rows and silently
                # stopped rows shrinking below slider ~38 once the slider
                # minimum dropped to 16; the spreadsheet layout only needs
                # one text line, which 24 still fits.
                vw = self.thumblist.viewport().width()
                row_w = vw if vw > 80 else 400
                row_h = max(ts + 14, 24)
                self.thumblist.setGridSize(QtCore.QSize(row_w, row_h))
                self.thumblist.setAlternatingRowColors(True)
                self.list_header.setVisible(True)
                self._update_list_columns()
            else:
                self.thumblist.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
                self.thumblist.setAlternatingRowColors(False)
                self.thumblist.setFlow(QtWidgets.QListView.Flow.LeftToRight)
                self.thumblist.setWrapping(True)
                self.thumblist.setIconSize(QtCore.QSize(ts, ts))
                # Extra height over the icon for the two text lines.
                self.thumblist.setGridSize(
                    AssetItemDelegate.grid_cell_size(ts, self.thumblist.font())
                )
                self.list_header.setVisible(False)
            self.thumblist.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        except Exception as e:
            print(
                "Amaze: view mode switch failed ("
                + str(e)
                + ") - falling back to grid"
            )
            try:
                self.thumblist.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
                self.thumblist.setGridSize(
                    AssetItemDelegate.grid_cell_size(ts, self.thumblist.font())
                )
                self.list_header.setVisible(False)
            except Exception:
                pass
        self._sync_view_mode_controls()

    def _update_list_columns(self, *_args) -> None:
        """Sizes list mode's Name column to the longest name currently
        shown (the column width is determined by the length of the
        material names) and pushes matching column
        positions to both row delegates and the header strip. Connected
        to the proxy models' change signals so filtering, category
        switches, renames and section changes all re-fit the column."""
        if (
            not self.material_model
            or self.prefs.view_mode != "list"
            or not hasattr(self, "thumb_delegate")
        ):
            return
        model = self.thumblist.model()
        if model is None:
            return
        fm = QtGui.QFontMetrics(self.thumblist.font())
        # Memoized longest-name measure: this runs on EVERY slider tick
        # while in list mode (via apply_view_mode), and re-measuring
        # hundreds of names per tick is pure waste when nothing about
        # the names changed. The cache key covers model identity and row
        # count; content-only changes (renames, filters) drop the cache
        # through _invalidate_list_columns, which is what the model
        # signals connect to.
        # The Type column is measured the same way as Name - fitted to
        # the longest currently-visible type string (the row type
        # column should adapt to the longest word), read via the active
        # delegate's subtitle role since each section feeds a different
        # one.
        subtitle_role = getattr(
            self.thumblist.itemDelegate(), "_subtitle_role", None
        )
        cache_key = (id(model), model.rowCount(), fm.height())
        cached = getattr(self, "_list_maxw_cache", None)
        if cached is not None and cached[0] == cache_key:
            max_w, type_max_w = cached[1], cached[2]
        else:
            max_w = 0
            type_max_w = 0
            for row in range(model.rowCount()):
                idx = model.index(row, 0)
                text = model.data(idx, QtCore.Qt.ItemDataRole.DisplayRole)
                if text:
                    w = fm.horizontalAdvance(str(text))
                    if w > max_w:
                        max_w = w
                if subtitle_role is not None:
                    text = model.data(idx, subtitle_role)
                    if text:
                        w = fm.horizontalAdvance(str(text))
                        if w > type_max_w:
                            type_max_w = w
            self._list_maxw_cache = (cache_key, max_w, type_max_w)
        col_pad = ui_helpers.ListColumnHeader.COL_PAD
        name_w = max(max_w + 2 * col_pad, 120)
        # Thumbnail column = the row height (icon side plus its padding),
        # same formula (and same 24 floor) as the list branch of
        # apply_view_mode above - keep the two in sync.
        thumb_w = max(self.material_model.thumbsize + 14, 24)
        # Type fitted to its longest visible string (floor keeps room
        # for the header label); Category takes whatever remains.
        type_w = max(type_max_w + 2 * col_pad, 60)
        # Keep room for the Type + Category columns when the panel is
        # narrow - long names elide rather than pushing them out of
        # view entirely.
        vw = self.thumblist.viewport().width()
        if vw > 300:
            name_w = min(name_w, max(vw - thumb_w - type_w - 120, 120))
        # ALL section delegates get the positions - gradient_delegate
        # was missing here originally, which left the Colors section's
        # rows painting default column positions under a header strip
        # using the computed ones (a reported misalignment).
        for delegate in (
            self.thumb_delegate,
            self.texture_delegate,
            self.gradient_delegate,
            self.geo_delegate,
        ):
            delegate.set_list_columns(thumb_w, name_w, type_w)
        self.list_header.set_columns(thumb_w, name_w, type_w)
        self.thumblist.viewport().update()

    def _invalidate_list_columns(self, *_args) -> None:
        """Model CONTENT changed (rename, filter, add/remove, section
        switch) - drop the cached longest-name width, then re-fit. The
        model change signals connect here; apply_view_mode's per-tick
        calls go straight to _update_list_columns and reuse the cache."""
        self._list_maxw_cache = None
        self._update_list_columns()

    def _sync_view_mode_controls(self) -> None:
        """Reflect prefs.view_mode on the toggle button and menu. Sets both
        menu actions explicitly (one on, one off) so they can never both read
        as selected, and suppresses handler re-entry while doing so."""
        self._suppress_view_signals = True
        try:
            is_list = self.prefs.view_mode == "list"
            if hasattr(self, "cb_viewmode"):
                self.cb_viewmode.setChecked(is_list)
            if getattr(self, "view_actions", None):
                grid_act = self.view_actions.get("grid")
                list_act = self.view_actions.get("list")
                if grid_act is not None:
                    grid_act.setChecked(not is_list)
                if list_act is not None:
                    list_act.setChecked(is_list)
        finally:
            self._suppress_view_signals = False

    def _active_thumbsize(self) -> int:
        """The persisted icon size for the CURRENT view mode. Grid and
        list each remember their own size (grid at 128 and list
        at 32 should coexist, with the slider jumping to whichever mode
        is active)."""
        if self.prefs.view_mode == "list":
            return self.prefs.thumbsize_list
        return self.prefs.thumbsize

    def _set_view_mode(self, mode: str) -> None:
        """Central entry: persist and apply a view mode ('grid' or 'list')."""
        self.prefs.view_mode = mode
        self.prefs.save()
        # Jump the slider to the new mode's own remembered size. If the
        # value actually changes this fires slide(), which re-applies
        # sizing/icons; the explicit apply_view_mode() below covers the
        # equal-values case (setValue emits nothing then, but the view
        # still has to restructure for the new mode).
        self.click_slider.setValue(self._active_thumbsize())
        self.apply_view_mode()

    def on_viewmode_button(self, checked: bool) -> None:
        """Filter-row toggle: checked = list, unchecked = grid."""
        if getattr(self, "_suppress_view_signals", False):
            return
        self._set_view_mode("list" if checked else "grid")

    def on_viewmode_menu(self, action) -> None:
        """View-menu Grid/List selection."""
        if getattr(self, "_suppress_view_signals", False):
            return
        self._set_view_mode("list" if action.text().startswith("List") else "grid")

    def init_ui(self) -> None:
        """Creates the panel-view on load"""
        # Load UI from ui.file
        loader = QtUiTools.QUiLoader()
        file = QtCore.QFile(self.script_path + "/ui/matlib.ui")
        # Override Widgets for Drag and Drop Support
        loader.registerCustomWidget(dragdrop_widgets.DragDropCentralWidget)
        loader.registerCustomWidget(dragdrop_widgets.DragDropListView)

        file.open(QtCore.QFile.ReadOnly)  # type: ignore
        self.ui = loader.load(file)
        file.close()

        # Apply Houdini's own Qt stylesheet so standard widgets (combo
        # boxes, scrollbars, etc.) render like native Houdini UI instead
        # of the OS-default Qt style. hou.qt.styleSheet() is SideFX's
        # documented mechanism for this in custom PySide panels. The
        # toolbar's own controls are all hand-painted widgets and ignore
        # it entirely.
        try:
            self.ui.setStyleSheet(hou.qt.styleSheet())
        except AttributeError:
            pass
        # The .ui root carries an upstream 420x400 minimumSize (840x800
        # rendered at 2x) that stopped the pane from shrinking - drop it
        # (same runtime-neutralize treatment the save dialog's .ui
        # minimum got; the .ui file itself stays untouched).
        self.ui.setMinimumSize(0, 0)
        # Even with the explicit minimum gone, a widget inside a layout
        # is still floored at its layout-derived minimumSizeHint (the
        # sum/max of every child's own minimum) - which is what kept a
        # residual floor after the first fix. Ignored size policy makes
        # the pane free to shrink to anything; content simply clips,
        # exactly how Houdini's own panes behave when squeezed.
        self.ui.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Ignored,
        )

        # Match Houdini's UI font and track the user's Global UI Size
        # preference. Empirical finding: Qt text in the panel renders
        # visually ~1pt smaller than Houdini's native UI text at the same
        # nominal point size, so nudge up by one point to match.
        try:
            hou_font = hou.qt.mainWindow().font()
            # Target 12pt: on setups reporting 11pt the +1 compensates the
            # Qt-vs-native rendering difference; on setups already at 12pt
            # a blind +1 overshot to 13 (observed), so floor instead of add.
            if 0 < hou_font.pointSizeF() < 12.0:
                hou_font.setPointSizeF(12.0)
            self.ui.setFont(hou_font)
        except AttributeError:
            pass

        # Load Ui Element so self
        self.menu = self.ui.findChild(QtWidgets.QMenuBar, "menubar")
        # No background/border styling here: the real menu bar is
        # permanently hidden further down (self.menu.setVisible(False)),
        # once its items move into the merged toolbar row - a stylesheet
        # on a hidden widget has no visual effect at all, so one was
        # never worth keeping around once that move happened.
        self.action_prefs = self.ui.action_prefs  # type: ignore
        self.action_prefs.triggered.connect(self.show_prefs)

        self.action_catview = self.ui.action_show_cat  # type: ignore
        self.action_catview.triggered.connect(self.toggle_catview)

        # Details is a dialog now (Edit Info); the old docked-panel
        # View-menu toggle (action_show_details) was removed from the
        # .ui in the 2026-07-21 Designer clean-up.
        self.action_cleanup_db = self.ui.action_cleanup_db  # type: ignore
        self.action_cleanup_db.triggered.connect(self.cleanup_db)

        self.action_open_folder = self.ui.action_open_folder  # type: ignore
        self.action_open_folder.triggered.connect(self.open_usdlib_folder)

        self.action_about = self.ui.action_about  # type: ignore
        self.action_about.triggered.connect(self.show_about)

        self.action_open = self.ui.action_open  # type: ignore
        self.action_open.triggered.connect(self.open)

        self.action_set_library = self.ui.action_set_library  # type: ignore
        self.action_set_library.triggered.connect(self.set_library)

        # MatLib v1 import dropped entirely; its action_import_lib_v1 was
        # removed from the .ui in the 2026-07-21 Designer clean-up.

        # Overwrite the widgets for Drag and Drop in dragdrop_widgets.py
        self.centralwidget = self.ui.centralwidget  # type: ignore

        central_layout = self.centralwidget.layout()
        self.toolbar_layout = None
        if central_layout is not None:
            # Merge the menu bar and the filter row into one strip
            # (reference: Houdini's own pane toolbars put menu
            # items and icon controls on a single row, not stacked).
            # QMainWindow reserves a dedicated dock area for its menu bar
            # that can't share a row with arbitrary other widgets, so the
            # real QMenuBar (self.menu) is hidden - its QMenus/QActions
            # stay alive and fully wired, just opened from flat buttons
            # instead (see _make_menu_button). horizontalLayout (the old
            # filter row) is a bare nested <layout> with no widget of its
            # own to paint a background on - same issue documented
            # elsewhere in this file - so its contents move into a new
            # QWidget wrapper that can be colored to match the menu bar.
            if self.menu is not None:
                self.menu.setVisible(False)
            filter_row = self.ui.findChild(QtWidgets.QHBoxLayout, "horizontalLayout")
            self.toolbar_row = QtWidgets.QWidget()
            self.toolbar_row.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True
            )
            # border: none first clears whatever Houdini's own base
            # stylesheet (applied panel-wide, self.ui.setStyleSheet(...))
            # might otherwise contribute now that WA_StyledBackground puts
            # this widget on the CSS rendering path - that's what was
            # producing a border on all sides instead of just the bottom
            # one actually being set here.
            # QSS border-width renders as literal screen pixels, not
            # scaled by the ~2x factor widget geometry (setFixedHeight
            # etc.) goes through - confirmed live: "1px" renders
            # as an actual 1px, not 2. Divider color #434343 per
            # the "ui_wireframe 2 only menu" design (was #414141).
            self.toolbar_row.setStyleSheet(
                "background-color: " + theme.color_hex("surface")
                + "; border: none;"
                + " border-bottom: 1px solid "
                + theme.color_hex("field") + ";"
            )
            # 30 code px = the design's 60px bar (down from the old 80px
            # row that MenuBarButton's height used to drive).
            self.toolbar_row.setFixedHeight(30)
            self.toolbar_layout = QtWidgets.QHBoxLayout(self.toolbar_row)
            # Design rev 2026-07-19 ("moved down 1px, spaces cleaned
            # up"): content is now dead-centered vertically (the earlier
            # rev floated everything 1-3px above center; it was nudged
            # back down), so no top/bottom bias. Right margin 2 -> the
            # design's ~12px rendered edge inset, most of which the last
            # icon button's own internal padding already provides.
            self.toolbar_layout.setContentsMargins(0, 0, 2, 0)
            self.toolbar_layout.setSpacing(0)

            # No menu buttons on the left anymore - the design moves all
            # three menus (Renderer/View/Library) to the toolbar's right
            # end as icon buttons, appended at the end of setup once the
            # Renderer menu exists. The leading stretch pushes the whole
            # content cluster (Filter/slider/toggles/menus) to the right,
            # matching the design's empty left region (reserved for
            # a planned section-tab integration).
            self.toolbar_layout.addStretch()

            if filter_row is not None:
                central_layout.removeItem(filter_row)
            central_layout.insertWidget(0, self.toolbar_row)

        self.thumblist = self.ui.thumbview  # type: ignore
        # Per-PIXEL scrolling, not Qt's default per-ITEM mode - one
        # "item" is a whole tile row in grid mode, so per-item wheel
        # steps jumped enormous distances (and interacted erratically
        # with trackpad deltas: sometimes turbo, sometimes crawling).
        self.thumblist.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.thumblist.doubleClicked.connect(self.import_asset_auto)
        self.thumblist.clicked.connect(self.update_details_view)
        # Grid and details panel had two different, unstyled/native
        # backgrounds - unified to #313131 via QPalette (Base role, same
        # role QListView paints its viewport from) rather than
        # setStyleSheet(), consistent with the cat_list fix above.
        thumblist_palette = self.thumblist.palette()
        thumblist_palette.setColor(QtGui.QPalette.ColorRole.Base, theme.color("surface_high"))
        self.thumblist.setPalette(thumblist_palette)

        # v2: thin progress bar for texture thumbnail generation, docked
        # above thumbview in its own layout (verticalLayout_7 in matlib.ui
        # wraps only thumbview, isolated from catview/details in the
        # splitter) - added in code, not the .ui file, same as the other
        # v2 widgets. Hidden until a folder with actual work to do is
        # selected; see _on_texture_progress().
        self.texture_progress = ui_helpers.ThinProgressBar()
        self.texture_progress.set_accent_color(theme.accent(self.prefs.accent_color))
        self.texture_progress.setVisible(False)
        thumb_layout = self.ui.findChild(QtWidgets.QVBoxLayout, "verticalLayout_7")
        if thumb_layout is not None:
            thumb_layout.insertWidget(0, self.texture_progress)

        # Spreadsheet-style column header for list mode (Thumbnail |
        # Name | Type), docked right above the rows; shown/hidden by
        # apply_view_mode(), columns sized by _update_list_columns().
        self.list_header = ui_helpers.ListColumnHeader()
        self.list_header.setVisible(False)
        if thumb_layout is not None:
            thumb_layout.insertWidget(1, self.list_header)

        # Category UI
        self.cat_list = self.ui.catview  # type: ignore
        # Palette, not setStyleSheet() - a stylesheet on cat_list itself
        # (not just an ancestor) also pushes Qt onto the CSS rendering
        # path for parts this stylesheet doesn't cover, namely its own
        # scrollbar, which is the most likely reason its scrollbar still
        # doesn't match the grid's (thumbview never got a stylesheet at
        # all) even after moving the *ancestor* (cat_wrapper) background
        # to a palette. QListView paints its viewport from Base/Text.
        cat_list_palette = self.cat_list.palette()
        cat_list_palette.setColor(
            QtGui.QPalette.ColorRole.Base, theme.color("surface_low")
        )
        cat_list_palette.setColor(
            QtGui.QPalette.ColorRole.Text, AssetItemDelegate.TEXT_COLOR
        )
        # Sidebar selection matches the grid's own highlight yellow
        # (copied from thumblist's palette rather than hardcoded, so
        # they can't drift), with black text for the same readability
        # reason as the tiles (hard to read otherwise). Applied
        # to both the Active and Inactive color groups - the sidebar is
        # rarely the focused widget, and the Inactive group is what
        # actually paints then.
        grid_highlight = self.thumblist.palette().color(
            QtGui.QPalette.ColorRole.Highlight
        )
        for group in (
            QtGui.QPalette.ColorGroup.Active,
            QtGui.QPalette.ColorGroup.Inactive,
        ):
            cat_list_palette.setColor(
                group, QtGui.QPalette.ColorRole.Highlight, grid_highlight
            )
            cat_list_palette.setColor(
                group,
                QtGui.QPalette.ColorRole.HighlightedText,
                QtGui.QColor("#000000"),
            )
        self.cat_list.setPalette(cat_list_palette)
        # The hand-painting delegate is what actually makes the sidebar
        # selection match the grid (see SidebarItemDelegate's docstring
        # - the palette Highlight above alone loses to Houdini's app
        # stylesheet at paint time).
        self.sidebar_delegate = SidebarItemDelegate(grid_highlight, self.cat_list)
        self.sidebar_delegate.set_drag_color(
            theme.accent(self.prefs.accent_color)
        )
        self.cat_list.setItemDelegate(self.sidebar_delegate)
        self.cat_list.clicked.connect(self.update_selected_cat)
        # Make the sidebar a real DROP TARGET: drag assets from the grid
        # onto a category to recategorise them. The filter also
        # keeps such a drop from falling through to the central widget's
        # save-node handler ("... already exists in the library").
        self._cat_drop_filter = dragdrop_widgets.CategoryDropFilter(
            self.cat_list, self
        )

        self.line_filter = self.ui.line_filter  # type: ignore
        self.line_filter.textEdited.connect(self.filter_thumb_view)
        # .ui sets placeholderText "Filter" - redundant now that there's
        # both a "Filters" label to the left of the box and the funnel
        # icon; cleared in code - the .ui file is maintained externally
        # in Qt Designer and is never edited from code.
        self.line_filter.setPlaceholderText("")
        # Per the "ui_wireframe 2 only menu" design: borderless box,
        # #434343 fill, magnifier icon inside the left edge. Stylesheet
        # set directly on the widget itself, not an ancestor (same "avoid
        # the details-panel regression class of bug" reasoning used
        # everywhere else in this file). padding-left reserves room so
        # typed text doesn't start underneath the icon.
        self.line_filter.setStyleSheet(
            "QLineEdit { border: none; background-color: "
            + theme.color_hex("field")
            + "; padding-left: 20px; }"
        )
        # 20 code px = the design's 40px rendered box. The old height was
        # 22 to compensate for a 1px QSS border eating into the fill
        # (rendered = (code - 2) * 2, see git history) - borderless now,
        # so the plain 2x relationship applies again.
        self.line_filter.setFixedHeight(20)
        # Sizing rule: 300px rendered max, 75px min (the .ui's
        # own 80 minimum is overridden here - setMinimumWidth wins over
        # the .ui value at runtime).
        self.line_filter.setMinimumWidth(38)
        # Max raised 150 -> 200 (300R -> 400R),
        # together with the slider's.
        self.line_filter.setMaximumWidth(200)
        # Magnifier icon (replaces the old funnel) pinned to the LEFT
        # edge - a real overlay QLabel + SideIconPinner (ui_helpers.py),
        # not a QLineEdit addAction() icon, since that API doesn't expose
        # control over the exact margin and this project has consistently
        # favored precise hand-positioning over accepting Qt's own
        # default spacing wherever an exact pixel value was asked for.
        try:
            filter_icon_path = self._ui_icon_path("icon_search.svg")
            if filter_icon_path and os.path.exists(filter_icon_path):
                # Design: ~25px rendered magnifier, ~9px in from the box
                # edge -> 13 code px icon at a 4px pin margin.
                icon_size = 13
                # render_svg_pixmap = QSvgRenderer straight onto a
                # transparent pixmap; QIcon's own SVG engine produced an
                # opaque black background here (see git history).
                pixmap = ui_helpers.render_svg_pixmap(
                    filter_icon_path, icon_size
                )
                self.filter_icon_label = QtWidgets.QLabel(self.line_filter)
                self.filter_icon_label.setPixmap(pixmap)
                self.filter_icon_label.resize(icon_size, icon_size)
                self.filter_icon_label.setAttribute(
                    QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
                )
                # The dark square was the label WIDGET's own background,
                # not the pixmap's alpha (the QSvgRenderer switch fixed
                # the pixmap; a clean, label-sized square pointed
                # elsewhere) - almost certainly Houdini's panel-wide
                # stylesheet (self.ui.setStyleSheet(hou.qt.styleSheet()))
                # defining a default QLabel background that this label
                # inherited since nothing had told it not to. Both belt
                # and suspenders here since it's cheap: the attribute
                # stops Qt from auto-filling the widget's background, the
                # stylesheet explicitly overrides whatever panel-wide rule
                # would otherwise apply to a plain QLabel.
                self.filter_icon_label.setAttribute(
                    QtCore.Qt.WidgetAttribute.WA_TranslucentBackground
                )
                self.filter_icon_label.setStyleSheet("background: transparent;")
                self._filter_icon_pinner = ui_helpers.SideIconPinner(
                    self.line_filter, self.filter_icon_label, 4, side="left"
                )
        except (TypeError, AttributeError):
            pass
        if self.toolbar_layout is not None:
            # "Filter" label (singular, #dddddd - both per the design) to
            # the left of the box - a real QLabel, not placeholder text
            # inside line_filter itself. Font stays the panel-wide
            # Houdini font stamp; the design's Helvetica was dummy text.
            self.filter_label = QtWidgets.QLabel("Filter")
            self.filter_label.setStyleSheet(
                "color: " + theme.color_hex("text_bright") + ";"
            )
            self.toolbar_layout.addWidget(self.filter_label)
            self.toolbar_layout.addSpacing(12)
            self.toolbar_layout.addWidget(self.line_filter)
            # The size slider is built much later in this method but sits
            # between the box and the star in the design - remember this
            # spot so its block can insert itself here.
            self._after_filter_index = self.toolbar_layout.count()

        # v2: section tabs - restructured 2026-07-19 per the
        # "ui_wireframe 2 only menu" design: a full-width strip BELOW
        # the toolbar (no longer inside the category sidebar), holding a
        # rounded-top tray of full-word text tabs (Materials / Textures
        # / Colors / Cop / Geometry). Hand-built (SectionTabBar in
        # ui_helpers.py) for the same reasons as its SegmentedControl
        # predecessor. Only Materials/Textures/Colors have real content
        # behind them so far; see _on_tab_toggled. catview has no
        # wrapper of its own in matlib.ui (it's a direct QSplitter
        # pane), so it's reparented into a new wrapper widget, then that
        # wrapper takes catview's old place in the splitter.
        self.current_section = "material"
        # Per-section view memory: sidebar choice + grid scroll,
        # captured on every tab switch so returning to a section lands
        # exactly where it was left (losing your place in a big
        # database and having to find the material again on every
        # switch is very annoying). In-memory only - Textures
        # additionally persist their folder across sessions via prefs,
        # which stays as is.
        self._section_view_state = {}
        splitter = self.cat_list.parentWidget()
        cat_index = -1
        if isinstance(splitter, QtWidgets.QSplitter):
            cat_index = splitter.indexOf(self.cat_list)
            # Width set explicitly, painting left fully native otherwise
            # (no color, no hand-painted grip dots/hover). A prior round
            # tried a full custom paint (color + hand-painted grip dots +
            # hover) via an event filter, which strayed too far from
            # Houdini's own native look (read as too hand-painted) -
            # reverted that entirely, keeping only the explicit width
            # from before that revert (6, matching what was actually in
            # code at the time). setHandleWidth() alone doesn't touch
            # how the handle is painted, just how much room native
            # painting has to work with, so this keeps the native grip
            # dots/hover intact.
            splitter.setHandleWidth(6)
        # catview's own <maximumSize width="220"> in matlib.ui is what
        # kept the category pane narrow in the splitter - that property
        # stays on catview itself after reparenting below, but the
        # splitter now sees cat_wrapper (unconstrained by default) as its
        # pane widget, not catview, so it no longer has any width limit
        # to respect. An earlier attempt at this fix tried to capture and
        # reapply splitter.sizes() around the reparent instead - that
        # backfired badly (a capture taken before the panel is ever shown
        # can be [0, 0, 0], not real pixel widths, and reapplying it
        # collapsed the grid pane to nothing) and has been dropped in
        # favor of just propagating the same real constraint catview
        # already had.
        self.cat_wrapper = QtWidgets.QWidget()
        self.cat_wrapper.setMaximumWidth(220)
        # Backdrop fill for the whole category section (tab row's own
        # margins, any space the list doesn't cover) - deliberately
        # darker than BG1 (#2d2d2d, still on cat_list/line_tags) so the
        # section reads as one frame with the list as a distinct surface
        # inside it. Set via QPalette, not setStyleSheet()/WA_StyledBackground
        # - a stylesheet on this ancestor would push cat_list onto Qt's CSS
        # rendering path for parts it doesn't style itself (its scrollbar),
        # knocking it off Houdini's native look, the same class of bug
        # documented elsewhere in this file for the details panel. Palette
        # changes don't cascade that way.
        self.cat_wrapper.setAutoFillBackground(True)
        cat_wrapper_palette = self.cat_wrapper.palette()
        cat_wrapper_palette.setColor(QtGui.QPalette.ColorRole.Window, theme.color("surface_low"))
        self.cat_wrapper.setPalette(cat_wrapper_palette)
        cat_wrapper_layout = QtWidgets.QVBoxLayout(self.cat_wrapper)
        cat_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        cat_wrapper_layout.setSpacing(0)

        cat_wrapper_layout.addWidget(self.cat_list)
        if splitter is not None and cat_index >= 0:
            splitter.insertWidget(cat_index, self.cat_wrapper)

        # The tab strip itself lives OUTSIDE the sidebar now: full panel
        # width, directly under the toolbar row (central_layout index 1,
        # toolbar_row sits at 0). Tab order, labels and chip styling per
        # the design; "Colors" is the Gradients section's user-facing
        # name (internal key stays "gradient"), Cop and Geometry are
        # placeholders. Chip colors are fixed design constants, not
        # accent-derived, so there's no set_accent_color here.
        self.section_tabs = None
        self._central_layout = central_layout
        # Built from the enabled_sections pref (rebuildable when that
        # pref changes in Preferences) - see _build_section_tabs.
        self._build_section_tabs()

        # Favorites toggle: hand-painted ChipToggleButton with the exact
        # same grey hover chip and icon-whitening as the icon menu
        # buttons (hover must match across favorites/grid-list/
        # menus). The .ui's own cb_FavsOnly is unused and hidden - left
        # unparented it could paint stray in the panel.
        self.ui.cb_FavsOnly.setVisible(False)  # type: ignore
        self.cb_favsonly = ui_helpers.ChipToggleButton()
        try:
            icon_off = self._ui_icon_path("star.svg")
            icon_on = self._ui_icon_path("star_on.svg")
            rs = 16 * ui_helpers.ChipToggleButton.RENDER_SCALE
            lit = {
                ui_helpers.IconMenuButton.IDLE_BODY:
                    ui_helpers.IconMenuButton.LIT_BODY,
            }
            self.cb_favsonly.set_state_pixmaps(
                ui_helpers.render_svg_pixmap(icon_off, rs),
                ui_helpers.render_svg_pixmap(icon_on, rs),
                ui_helpers.render_svg_pixmap(icon_off, rs, lit),
                # Checked + hovered keeps the amber star: the fill IS the
                # on/off signal, whitening it would make the two states
                # indistinguishable mid-hover. Flag if unwanted.
                ui_helpers.render_svg_pixmap(icon_on, rs),
            )
        except (TypeError, AttributeError):
            pass
        self.cb_favsonly.toggled.connect(self.filter_favs)
        if self.toolbar_layout is not None:
            self.toolbar_layout.addWidget(self.cb_favsonly)
            # Tight 2px gaps through the right-hand icon cluster - the
            # design's ~21px rendered icon-to-icon spacing is mostly
            # provided by each button's own internal padding already.
            self.toolbar_layout.addSpacing(2)

        # Grid/List view-mode toggle: same hand-painted ChipToggleButton
        # treatment as the star. Unchecked = grid (icon mode), checked =
        # list mode; the icon shows the CURRENT mode, both hover
        # variants whiten to the shared light color.
        self.ui.cb_ViewMode.setVisible(False)  # type: ignore
        self.cb_viewmode = ui_helpers.ChipToggleButton()
        try:
            icon_grid = self._ui_icon_path("grid.svg")
            icon_list = self._ui_icon_path("list.svg")
            rs = 16 * ui_helpers.ChipToggleButton.RENDER_SCALE
            lit = {
                ui_helpers.IconMenuButton.IDLE_BODY:
                    ui_helpers.IconMenuButton.LIT_BODY,
            }
            self.cb_viewmode.set_state_pixmaps(
                ui_helpers.render_svg_pixmap(icon_grid, rs),
                ui_helpers.render_svg_pixmap(icon_list, rs),
                ui_helpers.render_svg_pixmap(icon_grid, rs, lit),
                ui_helpers.render_svg_pixmap(icon_list, rs, lit),
            )
        except (TypeError, AttributeError):
            pass
        self.cb_viewmode.toggled.connect(self.on_viewmode_button)
        if self.toolbar_layout is not None:
            self.toolbar_layout.addWidget(self.cb_viewmode)
            # Fixed gap to the icon-menu cluster appended right after -
            # the design right-anchors everything from the star outward.
            self.toolbar_layout.addSpacing(2)

        # Updated Details UI
        self.details = self.ui.details_widget  # type: ignore
        # Match the grid's #313131 (see thumblist above). QPalette +
        # setAutoFillBackground(), not setStyleSheet() - a stylesheet
        # here previously knocked the Name/Category/Tags fields off
        # their own native box rendering entirely (see the "details-panel
        # color pass" entry earlier in this file); palette changes don't
        # cascade onto descendants the way a stylesheet does.
        self.details.setAutoFillBackground(True)
        details_palette = self.details.palette()
        details_palette.setColor(QtGui.QPalette.ColorRole.Window, theme.color("surface_high"))
        self.details.setPalette(details_palette)
        self.line_name = self.ui.line_name  # type: ignore
        self.line_cat = self.ui.line_cat  # type: ignore

        # Every asset has exactly ONE category now - the multi-category
        # feature was removed (a hazard that made sorting harder).
        # The single-category dropdown is the only category input; the old
        # "Multi-category material" tick box and its comma-separated
        # textbox are hidden (kept in the .ui, never shown).
        self.cat_combo = self.ui.cat_combo  # type: ignore
        self.box_multicat = self.ui.box_multicat  # type: ignore
        self.cat_combo.setEnabled(True)
        try:
            form = self.ui.findChild(  # type: ignore
                QtWidgets.QFormLayout, "details_form"
            )
            if form is not None:
                form.setRowVisible(self.box_multicat, False)
                form.setRowVisible(self.line_cat, False)
        except Exception:
            self.box_multicat.setVisible(False)
            self.line_cat.setVisible(False)

        self.line_tags = self.ui.line_tags  # type: ignore
        self.line_tags.setStyleSheet(
            "background-color: " + theme.color_hex("surface") + ";"
        )
        self.line_id = self.ui.line_id  # type: ignore
        self.line_id.setDisabled(True)
        self.line_id.setStyleSheet(
            "QLineEdit:disabled { background-color: #333333; }"
        )

        self.line_date = self.ui.line_date  # type: ignore
        self.line_date.setDisabled(True)
        self.line_date.setStyleSheet(
            "QLineEdit:disabled { background-color: #333333; }"
        )

        # Greyed renderer row inserted directly under Name (e.g. "USD
        # Redshift" / "Redshift" / "Karma"). Inserted in code so we don't have
        # to renumber the whole form; disabled so it reads as metadata.
        self.line_renderer = QtWidgets.QLineEdit()
        self.line_renderer.setReadOnly(True)
        self.line_renderer.setDisabled(True)
        self.line_renderer.setStyleSheet(
            "QLineEdit:disabled { background-color: #333333; }"
        )
        # Provenance rows for downloaded online materials (empty for local
        # ones): the License in its own field, and a multi-line About /
        # credit block at the very bottom to pay homage to the creators
        # (source, author, link). Both editable, saved with the rest of
        # the Material Info form.
        self.line_license = QtWidgets.QLineEdit()
        self.text_about = QtWidgets.QPlainTextEdit()
        self.text_about.setFixedHeight(84)   # ~4 lines
        try:
            details_form = self.ui.findChild(  # type: ignore
                QtWidgets.QFormLayout, "details_form"
            )
            name_row, _ = details_form.getWidgetPosition(self.line_name)
            details_form.insertRow(name_row + 1, "Type", self.line_renderer)
            # Appended, so they sit at the bottom of the form.
            details_form.addRow("License", self.line_license)
            details_form.addRow("About", self.text_about)
        except Exception:
            pass

        self.box_fav = self.ui.cb_set_fav  # type: ignore
        self.box_fav.clicked.connect(self.box_fav_clicktoggle)

        self.btn_update = self.ui.btn_update  # type: ignore
        # The .ui text "Update Material" now collides with the real
        # content-update in the save flow - this button only saves
        # name/category/tags/favorite. Property change at runtime, not a
        # .ui edit (standing practice).
        self.btn_update.setText("Update Info")
        self.btn_update.clicked.connect(self.user_update_asset)

        # Material metadata now lives in a FLOATING DIALOG, not a docked
        # panel (the panel ate grid width and only materials
        # used it). Reparenting details_widget into a QDialog removes it
        # from the splitter, so the grid gets the freed space; the edit
        # form (update_details_view / user_update_asset) is unchanged.
        self.details_dialog = QtWidgets.QDialog(self)
        self.details_dialog.setWindowTitle("Material Info")
        _dlg_layout = QtWidgets.QVBoxLayout(self.details_dialog)
        _dlg_layout.setContentsMargins(8, 8, 8, 8)
        _dlg_layout.addWidget(self.details)  # reparents out of the splitter
        self.details.setVisible(True)
        self.details.setMinimumWidth(360)

        # Renderer filter lives in a menubar "Renderer" menu (moved out
        # of the details view). Labels must match the renderer strings the
        # filter compares against.
        self.menu_renderer = QtWidgets.QMenu("Renderer", self.menu)
        self.menu.addMenu(self.menu_renderer)
        if self.toolbar_layout is not None:
            # All three menus live at the toolbar's right end as icon
            # buttons, per the "ui_wireframe 2 only menu" design - order
            # left to right: Renderer (box), View (eye), Library (gear,
            # outermost). Appended here because this is the point where
            # the Renderer menu finally exists; the star/toggle blocks
            # above already ended with their fixed 6px gap into this
            # cluster.
            menu_file = self.ui.findChild(QtWidgets.QMenu, "menu_file")
            menu_view = self.ui.findChild(QtWidgets.QMenu, "menuView")
            self.toolbar_layout.addWidget(self._make_menu_button(self.menu_renderer))
            if menu_view is not None:
                self.toolbar_layout.addSpacing(2)
                self.toolbar_layout.addWidget(self._make_menu_button(menu_view))
            if menu_file is not None:
                self.toolbar_layout.addSpacing(2)
                self.toolbar_layout.addWidget(self._make_menu_button(menu_file))
        self.renderer_action_group = QtGui.QActionGroup(self.menu_renderer)
        self.renderer_action_group.setExclusive(True)
        self.renderer_actions = {}
        renderer_defs = [
            ("All", True),
            ("Karma", self.prefs.renderer_matx_enabled),
            ("Mantra", self.prefs.renderer_mantra_enabled),
            ("Redshift", self.prefs.renderer_redshift_enabled),
            ("Octane", self.prefs.renderer_octane_enabled),
            # Materials imported from the online MaterialX libraries get
            # their own renderer. A NORMAL renderer - it is part of
            # "All" and needs no special case.
            ("MtlX", self.prefs.renderer_mtlx_enabled),
        ]
        for label, visible in renderer_defs:
            act = self.menu_renderer.addAction(label)
            act.setCheckable(True)
            act.setVisible(visible)
            self.renderer_action_group.addAction(act)
            self.renderer_actions[label] = act

        # Restore the last selected renderer from preferences
        last_act = self.renderer_actions.get(self.prefs.last_renderer)
        if last_act is None or not last_act.isVisible():
            last_act = self.renderer_actions["All"]
        last_act.setChecked(True)
        self.renderer_action_group.triggered.connect(self.renderer_menu_changed)

        # "Render All Thumbnails" lives in the Library menu (moved out of
        # the material right-click menu)
        menu_file = self.ui.findChild(QtWidgets.QMenu, "menu_file")
        if menu_file is not None:
            self.action_render_all = menu_file.addAction("Render All Thumbnails")
            self.action_render_all.triggered.connect(self.update_all_assets)

        # View menu, in the order specified in ui-text.md:
        #   Material Library / Online Materials / --- / Show Categories /
        #   Grid View / List View
        # The .ui supplies action_show_cat ("Show Category View") + a
        # trailing separator at the top; drop that separator, relabel the
        # action to "Show Categories", and insert the material-source items
        # above it.
        self.view_actions = {}
        menu_view = self.ui.findChild(QtWidgets.QMenu, "menuView")
        if menu_view is not None:
            for a in list(menu_view.actions()):
                if a.isSeparator():
                    menu_view.removeAction(a)
            anchor = self.action_catview        # = action_show_cat
            self.action_catview.setText("Show Categories")
            # Render "Show Categories" with a radio-style CIRCLE indicator
            # to match the other View items, not a checkmark. A
            # standalone checkable action draws a checkmark; an action in
            # an exclusive group draws a circle. ExclusiveOptional keeps it
            # a free on/off toggle (a lone member can be unchecked) while
            # still rendering as a circle.
            self.show_cat_group = QtGui.QActionGroup(self)
            self.show_cat_group.setExclusionPolicy(
                QtGui.QActionGroup.ExclusionPolicy.ExclusiveOptional
            )
            self.show_cat_group.addAction(self.action_catview)

            # Material Library (the local library) and each online source
            # form ONE exclusive group - exactly one is active, so picking
            # a source unchecks Material Library and vice versa. Material
            # Library is the default (local view). Browsing online is a
            # VIEW MODE over the Materials tab, which is why it lives in the
            # View menu rather than the Renderer menu.
            self.online_source_group = QtGui.QActionGroup(self)
            self.online_source_group.setExclusive(True)
            self.action_material_library = QtGui.QAction(
                "Material Library", self
            )
            self.action_material_library.setCheckable(True)
            self.action_material_library.setChecked(True)
            self.online_source_group.addAction(self.action_material_library)
            menu_view.insertAction(anchor, self.action_material_library)

            self.online_menu = QtWidgets.QMenu("Online Materials", menu_view)
            menu_view.insertMenu(anchor, self.online_menu)
            self.online_source_actions = {}
            # Just the source NAMES here - the online model isn't built
            # until setup(); handlers resolve names against it later.
            for source in matx_sources.all_sources():
                act = self.online_menu.addAction(source.name)
                act.setCheckable(True)
                self.online_source_group.addAction(act)
                self.online_source_actions[source.name] = act
            menu_view.insertSeparator(anchor)   # divider before Show Categories
            self.online_source_group.triggered.connect(self._on_online_source)

            # Grid / List after Show Categories (exclusive), mirrored by the
            # filter-row toggle button. Both drive prefs.view_mode.
            self.view_action_group = QtGui.QActionGroup(menu_view)
            self.view_action_group.setExclusive(True)
            for label in ("Grid", "List"):
                act = menu_view.addAction(label + " View")
                act.setCheckable(True)
                self.view_action_group.addAction(act)
                self.view_actions[label.lower()] = act
            self.view_action_group.triggered.connect(self.on_viewmode_menu)

        # The .ui's original icon-size slider is superseded by ClickSlider
        # below; keep it hidden rather than removing it from the .ui file
        # (the .ui file is maintained externally in Qt Designer, and
        # edited versions of it are sometimes handed over).
        self.ui.slide_iconSize.setVisible(False)  # type: ignore

        # Set Up Clickable Slider
        self.click_slider = ui_helpers.ClickSlider()
        self.click_slider.setOrientation(QtCore.Qt.Horizontal)  # type: ignore
        self.click_slider.setRange(16, 512)
        self.click_slider.setValue(ui_helpers.ClickSlider.DEFAULT_VALUE)
        self.click_slider.setSingleStep(50)
        self.click_slider.setPageStep(50)
        self.click_slider.set_accent_color(theme.accent(self.prefs.accent_color))
        # Sizing rule, same as the filter box: 400px rendered
        # max (raised from 300), 75px min.
        self.click_slider.setMinimumWidth(38)
        self.click_slider.setMaximumWidth(200)
        # The design's order is [Filter box] [slider] [star] [toggle]
        # [menus], but this block runs after the star/toggle blocks -
        # insert at the spot remembered right after the filter box was
        # added. Both slider-side gaps are 20 (40px rendered each -
        # up from the design rev's 10/6).
        if self.toolbar_layout is not None:
            idx = getattr(
                self, "_after_filter_index", self.toolbar_layout.count()
            )
            self.toolbar_layout.insertSpacing(idx, 20)
            self.toolbar_layout.insertWidget(idx + 1, self.click_slider)
            self.toolbar_layout.insertSpacing(idx + 2, 20)
        # Debounce for persisting the per-mode icon size: slide() fires
        # on every pixel of a drag, and settings.json lives in the
        # cloud-synced install folder - write once, shortly after the
        # drag settles, instead of dozens of times per second.
        self._thumbsize_save_timer = QtCore.QTimer(self)
        self._thumbsize_save_timer.setSingleShot(True)
        self._thumbsize_save_timer.setInterval(500)
        self._thumbsize_save_timer.timeout.connect(self.prefs.save)
        self.click_slider.valueChanged.connect(self.slide)
        # Houdini-22-style look (groove/handle) is painted directly by
        # ClickSlider.paintEvent - QSS sub-page/add-page styling proved
        # unreliable (colors landed on the correct side, but the declared
        # heights didn't) so this widget draws itself deterministically
        # instead of relying on the style/stylesheet system for it.

        # TEST: mirror the whole toolbar row to
        # the left. All toolbar_layout additions are complete by this
        # point (the slider insertion above is the last one).
        self._mirror_toolbar()

        # RC Menus
        self.thumblist.customContextMenuRequested.connect(self.thumblist_rc_menu)
        self.cat_list.customContextMenuRequested.connect(self.catlist_rc_menu)

        # set main layout and attach to widget
        mainlayout = QtWidgets.QVBoxLayout()
        mainlayout.addWidget(self.ui)
        mainlayout.setContentsMargins(0, 0, 0, 0)  # Remove Margins

        self.setLayout(mainlayout)

    def _mirror_toolbar(self) -> None:
        """TEST: mirror the toolbar row - every
        item flows from the LEFT edge in the reverse of the designed
        order (Library/View/Renderer icons first, then grid-list toggle,
        star, slider, filter box, "Filter" label, with the stretch
        landing at the far right). The layout is still BUILT in its
        designed right-aligned order everywhere above, then reversed
        here in one pass - so ending the test is just deleting this
        method and its call, nothing else moves."""
        if self.toolbar_layout is None:
            return
        items = []
        while self.toolbar_layout.count():
            items.append(self.toolbar_layout.takeAt(0))
        for item in reversed(items):
            self.toolbar_layout.addItem(item)
        # Exception to the literal mirror: the "Filter" label
        # still reads left-to-right, so it stays on the LEFT of its box.
        # A plain reversal lands it on the box's right as
        # [box][12-gap][label] - swap the label (and re-seat the gap)
        # back to [label][12-gap][box]. Strict about the expected
        # adjacency so a future construction change can't silently
        # shuffle the wrong items.
        i_box = self.toolbar_layout.indexOf(self.line_filter)
        i_label = self.toolbar_layout.indexOf(self.filter_label)
        if i_box >= 0 and i_label == i_box + 2:
            label_item = self.toolbar_layout.takeAt(i_label)
            gap_item = self.toolbar_layout.takeAt(i_label - 1)
            self.toolbar_layout.insertItem(i_box, gap_item)
            self.toolbar_layout.insertItem(i_box, label_item)
        # The designed row insets its RIGHT edge by 2 (outside the
        # outermost icon button); mirrored, that inset belongs on the
        # left edge instead.
        self.toolbar_layout.setContentsMargins(2, 0, 0, 0)

    def get_category_names(self) -> list[str]:
        """Return ALL existing category names (empty ones included),
        excluding the 'All' pseudo-category. Reads the SOURCE model, not
        the sidebar proxy - the sidebar hides empty categories, but
        every ASSIGNMENT surface (save dialog, details dropdown,
        Move to/Add to menus) must still offer the complete list."""
        names = []
        if not self.category_model:
            return names
        for elem in range(self.category_model.rowCount()):
            cidx = self.category_model.index(elem, 0)
            name = self.category_model.data(
                cidx, QtCore.Qt.ItemDataRole.DisplayRole
            )
            if name and name != "All":
                names.append(name)
        return sorted(names, key=str.lower)

    def assign_category_active(self, category: str) -> None:
        """Set (replace) the category of the ACTIVE section's selected
        assets. Reused by the Materials "Move to" menu and by dragging
        assets onto a sidebar category, for every section with real
        categories: Materials / Cop / Code (the curated-library stack) and
        Colors (user gradients). A single category per asset now - the
        multi-category feature was removed."""
        category = (category or "").strip()
        if not category:
            return
        if self.current_section == "gradient":
            self._assign_gradient_category(category)
            return
        stack = self._active_asset_stack()
        if stack is None:
            return
        model, proxy, selmodel, catmodel = stack
        indexes = selmodel.selectedIndexes()
        if not indexes:
            return
        model.layoutAboutToBeChanged.emit()
        catmodel.layoutAboutToBeChanged.emit()
        catmodel.check_add_category(category)
        for index in indexes:
            idx = model.index(proxy.mapToSource(index).row())
            asset = model.assets[idx.row()]
            model.set_assetdata(
                idx, asset.name, category, ", ".join(asset.tags), asset.fav
            )
        model.layoutChanged.emit()
        catmodel.layoutChanged.emit()
        self._refresh_sidebar_categories()

    def _assign_gradient_category(self, category: str) -> None:
        """Move the selected gradients to a category - every gradient is a
        normal editable entry now (the seeded palettes included)."""
        rows = [
            self.gradient_sorted_model.mapToSource(i).row()
            for i in self.gradient_selection_model.selectedIndexes()
        ]
        moved = self.gradient_model.set_user_category(rows, category)
        if moved:
            self._refresh_sidebar_categories()

    def thumblist_rc_menu(self) -> None:
        """Grid right-click - the active section builds its own menu."""
        if self._is_online():
            self._matx_rc_menu()
            return
        section = self._section()
        if section is not None:
            section.rc_menu()

    def _material_rc_menu(self) -> None:
        cmenu = QtWidgets.QMenu(self)

        action_edit = cmenu.addAction("Edit Info")
        action_import_mat = cmenu.addAction("Import to MAT")
        action_import_lop = cmenu.addAction("Import to LOP")
        action_toggle_fav = cmenu.addAction("Toggle Favorite")
        action_render = cmenu.addAction("Rerender Thumbnail")
        # action_thumb_viewport = cmenu.addAction("Thumbnail from Viewport")
        action_convert_karma = None
        if self._selection_has_redshift():
            action_convert_karma = cmenu.addAction("Convert to Karma (test)")
        cmenu.addSeparator()
        move_menu = cmenu.addMenu("Move to")
        move_actions = {}
        for cat_name in self.get_category_names():
            move_actions[move_menu.addAction(cat_name)] = cat_name
        cmenu.addSeparator()
        action_delete = cmenu.addAction("Delete Entry")
        action = cmenu.exec_(QtGui.QCursor.pos())

        if action == action_edit:
            self.edit_material_info()
        elif action == action_delete:
            self.delete_asset()
        elif action == action_render:
            self.update_single_asset()
        elif action == action_import_mat:
            self.import_asset_to_mat()
        elif action == action_import_lop:
            self.import_asset_to_lop()
        elif action == action_toggle_fav:
            self.toggle_fav()
        elif action_convert_karma is not None and action == action_convert_karma:
            # The "is not None" guard matters: this action only exists
            # when a Redshift material is selected, and dismissing the
            # menu without choosing anything makes action None too - a
            # plain == comparison would match None == None and fire the
            # converter (with its "Converted 0 of 0" dialog) on every
            # dismissed right-click.
            self.convert_selected_to_karma()
        elif action in move_actions:
            self.assign_category_active(move_actions[action])

    def _texture_rc_menu(self) -> None:
        """Right-click menu for the Textures section: load the (single)
        selected texture onto whichever node is selected in the scene,
        toggle favorite on the whole selection, or force-regenerate the
        whole selection's cached thumbnails. Indexes come from
        texture_selection_model, which wraps texture_sorted_model (the
        filter proxy) - mapToSource() is needed before touching
        texture_files_model directly by row number."""
        proxy_indexes = self.texture_selection_model.selectedIndexes()
        cmenu = QtWidgets.QMenu(self)
        action_load = cmenu.addAction("Load to Node")
        action_toggle_fav = cmenu.addAction("Toggle Favorite")
        action_rerender = cmenu.addAction("Rerender Thumbnail")
        action = cmenu.exec_(QtGui.QCursor.pos())

        if action == action_load:
            if len(proxy_indexes) == 1:
                self.set_texture_on_selected_node(proxy_indexes[0])
            else:
                hou.ui.displayMessage("Select a single texture to load.")  # type: ignore
        elif action == action_toggle_fav:
            for proxy_index in proxy_indexes:
                source_index = self.texture_sorted_model.mapToSource(proxy_index)
                self.texture_files_model.toggle_favorite(source_index.row())
        elif action == action_rerender:
            rows = [
                self.texture_sorted_model.mapToSource(i).row() for i in proxy_indexes
            ]
            self.texture_files_model.rerender_thumbnails(rows)

    def catlist_rc_menu(self) -> None:
        """Sidebar right-click - the active section builds its own menu."""
        if self._is_online():
            # A remote catalogue's categories aren't ours to edit.
            return
        section = self._section()
        if section is not None:
            section.catlist_menu()

    def _gradient_catlist_menu(self) -> None:
        cmenu = QtWidgets.QMenu(self)
        action_add = cmenu.addAction("Add Category")
        # Remove is only offered when the selected row IS a user
        # category - not "All", not the read-only Wada size groups.
        action_remove = None
        cat_name = None
        current = self.cat_list.currentIndex() if self.cat_list else None
        if current is not None and current.isValid():
            kind, value = self.gradient_categories_model.filter_for_row(
                current.row()
            )
            if kind == "category":
                cat_name = value
                action_remove = cmenu.addAction('Remove Category "%s"' % value)
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action == action_add:
            dialog = gradient_dialog.CategoryDialog()
            dialog.exec_()
            if not dialog.canceled and dialog.name:
                self.gradient_model.add_user_category(dialog.name)
                self.gradient_categories_model.refresh()
        elif action_remove is not None and action == action_remove:
            # "is not None" guard: dismissing the menu yields None.
            count = self.gradient_model.count_in_category(cat_name)
            message = 'Remove category "%s"?' % cat_name
            if count:
                message += " Its %s gradient%s will be kept (shown under All)." % (
                    count,
                    "" if count == 1 else "s",
                )
            if hou.ui.displayConfirmation(message):  # type: ignore
                self.gradient_model.remove_user_category(cat_name)
                self.gradient_categories_model.refresh()
                # The removed row may have been the selection - fall
                # back to "All" so the sidebar never points nowhere.
                self.gradient_sorted_model.set_sidebar_filter("all", None)
                target = self.gradient_categories_model.index(0, 0)
                self.cat_list.setCurrentIndex(target)

    def _texture_catlist_menu(self) -> None:
        cmenu = QtWidgets.QMenu(self)
        action_add = cmenu.addAction("Add Folder")
        action_remove = cmenu.addAction("Remove Folder")
        cmenu.addSeparator()
        action_sub = cmenu.addAction("Include Subfolders")
        action_sub.setCheckable(True)
        action_sub.setChecked(self.prefs.texture_include_subfolders)
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action == action_add:
            self.add_texture_folder_user()
        elif action == action_remove:
            self.remove_texture_folder_user()
        elif action == action_sub:
            self.prefs.texture_include_subfolders = action_sub.isChecked()
            self.prefs.save()
            self.texture_folders_model.refresh_counts()
            # Rescan whatever's showing under the new mode.
            self.update_selected_cat()

    def _geometry_catlist_menu(self) -> None:
        cmenu = QtWidgets.QMenu(self)
        action_add = cmenu.addAction("Add Folder")
        action_remove = cmenu.addAction("Remove Folder")
        cmenu.addSeparator()
        action_sub = cmenu.addAction("Include Subfolders")
        action_sub.setCheckable(True)
        action_sub.setChecked(self.prefs.geometry_include_subfolders)
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action == action_add:
            path = hou.ui.selectFile(file_type=hou.fileType.Directory)  # type: ignore
            if path:
                self.geo_folders_model.add_folder(hou.expandString(path))
        elif action == action_remove:
            rows = sorted(
                (i.row() for i in self.cat_list.selectedIndexes()),
                reverse=True,
            )
            for row in rows:
                self.geo_folders_model.remove_folder(row)
        elif action == action_sub:
            self.prefs.geometry_include_subfolders = action_sub.isChecked()
            self.prefs.save()
            self.geo_folders_model.refresh_counts()
            self.update_selected_cat()

    def _asset_catlist_menu(self) -> None:
        # COP and Code share the same category machinery (the
        # material Categories model over their own json) - one
        # branch drives both via the active stack.
        stack = self._active_asset_stack()
        if stack is None:
            return
        asset_model, _proxy, sel_model, cat_model = stack
        cmenu = QtWidgets.QMenu(self)
        action_add = cmenu.addAction("Add Category")
        action_rename = cmenu.addAction("Rename Category")
        action_remove = cmenu.addAction("Remove Category")
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action == action_add:
            dialog = gradient_dialog.CategoryDialog("Add Category")
            dialog.exec_()
            if not dialog.canceled and dialog.name:
                cat_model.layoutAboutToBeChanged.emit()
                cat_model.check_add_category(dialog.name)
                cat_model.layoutChanged.emit()
        elif action == action_rename:
            dialog = gradient_dialog.CategoryDialog("Rename Category")
            dialog.exec_()
            if not dialog.canceled and dialog.name:
                asset_model.layoutAboutToBeChanged.emit()
                cat_model.layoutAboutToBeChanged.emit()
                for index in self.cat_list.selectedIndexes():
                    name = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
                    if name == "All":
                        continue
                    asset_model.rename_category(name, dialog.name)
                    cat_model.rename_category(name, dialog.name)
                asset_model.save()
                asset_model.layoutChanged.emit()
                cat_model.layoutChanged.emit()
        elif action == action_remove:
            asset_model.layoutAboutToBeChanged.emit()
            cat_model.layoutAboutToBeChanged.emit()
            sel_model.clearSelection()
            for index in self.cat_list.selectedIndexes():
                name = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
                if name == "All":
                    continue
                asset_model.remove_category(name)
                cat_model.remove_category(name)
            asset_model.save()
            asset_model.layoutChanged.emit()
            cat_model.layoutChanged.emit()

    def _material_catlist_menu(self) -> None:
        cmenu = QtWidgets.QMenu(self)

        action_add = cmenu.addAction("Add Category")
        action_rename = cmenu.addAction("Rename Category")
        action_remove = cmenu.addAction("Remove Category")
        action = cmenu.exec_(QtGui.QCursor.pos())

        if action == action_remove:
            self.rmv_category_user()
        elif action == action_rename:
            self.rename_category_user()
        elif action == action_add:
            self.add_category_user()

    def toggle_fav(self) -> None:
        """Toggle the Favorite Stat for the currently selected Index"""
        if not self.material_model or not self.category_model:
            return
        self.material_model.layoutAboutToBeChanged.emit()
        indexes = self.material_selection_model.selectedIndexes()
        for index in indexes:
            idx = self.material_sorted_model.mapToSource(index)
            self.material_model.toggle_fav(idx)
        self.material_model.layoutChanged.emit()
        self.update_details_view()

    def _selection_has_redshift(self) -> bool:
        """Whether any currently-selected material is Redshift - gates
        showing "Convert to Karma (test)" in the right-click menu."""
        if not self.material_model:
            return False
        for index in self.material_selection_model.selectedIndexes():
            idx = self.material_sorted_model.mapToSource(index)
            mat = self.material_model.assets[idx.row()]
            if "Redshift" in mat.renderer:
                return True
        return False

    def convert_selected_to_karma(self) -> None:
        """Right-click "Convert to Karma (test)": best-effort node-graph
        conversion of selected Redshift materials to Karma/MaterialX (see
        core/library.py's convert_redshift_to_karma() and
        render/material_converter.py for what is and isn't handled).
        Non-Redshift items in a mixed selection are silently skipped, not
        errored on. Everything - success and skips alike - lands in one
        summary dialog, since a "successful" conversion can still have
        approximated or skipped inputs worth reviewing; nothing here is
        claimed to be a faithful reproduction."""
        if not self.material_model:
            return
        indexes = self.material_selection_model.selectedIndexes()
        if not indexes:
            return
        self.material_model.layoutAboutToBeChanged.emit()
        all_lines = []
        converted_count = 0
        redshift_count = 0
        for index in indexes:
            idx = self.material_sorted_model.mapToSource(index)
            mat = self.material_model.assets[idx.row()]
            if "Redshift" not in mat.renderer:
                continue
            redshift_count += 1
            try:
                ok, report = self.material_model.convert_redshift_to_karma(idx)
            except Exception as exc:
                # An exception here previously aborted the whole batch
                # silently (no summary dialog ever reached, and any
                # earlier successes in the same selection were never
                # reported either) - one bad material must not take the
                # rest of the selection down with it.
                all_lines.append(f'"{mat.name}": crashed - {exc}')
                continue
            if ok:
                converted_count += 1
            all_lines.extend(report.summary_lines())
        self.material_model.layoutChanged.emit()
        hou.ui.displayMessage(  # type: ignore
            f"Converted {converted_count} of {redshift_count} Redshift "
            "material(s) to Karma.\n\n" + "\n".join(all_lines)
        )

    def show_about(self) -> None:
        """Show the About Dialog"""
        about = about_dialog.AboutDialog()
        about.exec_()

    def show_prefs(self) -> None:
        """Show the Preferences Dialog"""
        if not self.material_model or not self.category_model:
            hou.ui.displayMessage("Please open a library first")  # type: ignore
            return
        # Flush any pending debounced thumbsize save first - prefs.load()
        # further down re-reads settings.json, and a still-pending write
        # would silently revert the newest slider value.
        if self._thumbsize_save_timer.isActive():
            self._thumbsize_save_timer.stop()
            self.prefs.save()
        old_dir = self.prefs.dir
        prefs_dialog.PrefsDialog(self.prefs, self.texture_files_model).exec_()

        # Update Thumblist Grid (mode-aware, respects grid/list)
        self.apply_view_mode()
        self.update_renderer_toggles()
        self.prefs.load()
        debug.configure(self.prefs.debug_mode)
        # Only a changed library DIRECTORY needs the models rebuilt.
        # The old unconditional reload re-read the json, rebuilt every
        # Material, dropped the per-id usd/shader caches (re-derived by
        # reading two files per material on the next paint) and
        # re-loaded every thumbnail PNG - well over a thousand file
        # reads on every single Preferences close, for nothing.
        if self.prefs.dir != old_dir:
            self.material_model.switch_model_data()
            self.category_model.switch_model_data()
            if getattr(self, "cop_model", None):
                self.cop_model.switch_model_data()
            if getattr(self, "cop_category_model", None):
                self.cop_category_model.switch_model_data()
            if getattr(self, "code_model", None):
                self.code_model.switch_model_data()
            if getattr(self, "code_category_model", None):
                self.code_category_model.switch_model_data()
        self.click_slider.setValue(self._active_thumbsize())
        accent = theme.accent(self.prefs.accent_color)
        self.click_slider.set_accent_color(accent)
        self.texture_progress.set_accent_color(accent)
        # Tile subtitle line ("Redshift:Standard", "HDR", ...) tracks
        # the accent too.
        for tile_delegate in (
            self.thumb_delegate,
            self.texture_delegate,
            self.gradient_delegate,
            self.geo_delegate,
        ):
            tile_delegate.DIM = accent
        self.list_header.set_accent_color(accent)
        AssetItemDelegate.set_star_color(self._effective_star_color())
        self.sidebar_delegate.set_drag_color(accent)
        self.sidebar_delegate.show_counts = self.prefs.sidebar_counts
        thumbnails.engine.set_budget_mb(self.prefs.ram_cache_mb)
        # Empty-category hiding toggled in Preferences: push the flag
        # into both sidebar proxies and re-evaluate. If turning it ON
        # just hid the category the user was standing in, fall back to
        # All like a renderer switch does.
        for _sidebar_proxy in (
            getattr(self, "category_sorted_model", None),
            getattr(self, "cop_category_sorted_model", None),
        ):
            if _sidebar_proxy is not None:
                _sidebar_proxy.hide_empty = self.prefs.hide_empty_categories
        self._refresh_sidebar_categories()
        self._ensure_material_sidebar_selection()
        self.cat_list.viewport().update()
        self.thumblist.viewport().update()
        # Geometry look prefs (shading mode / background) may have
        # changed: the cache key covers them, but nothing re-runs the
        # folder scan while the section is showing - re-run the current
        # selection so the new look renders without re-clicking the
        # folder.
        if self.current_section == "geometry":
            self.update_selected_cat()
        # Safe point to (re)start texture thumbnail generation - back in
        # the plain main event loop, no longer nested inside the modal
        # Preferences dialog. See TextureFiles.clear_cache()'s docstring.
        self.texture_files_model.refresh_current_folder()
        # Visible section tabs may have changed in Preferences.
        self._apply_enabled_sections()

    def update_renderer_toggles(self):
        """Show/hide the Renderer-menu actions from the per-renderer prefs
        flags. Replaces the old checkbox toggles (those widgets were removed
        when the renderer filter moved to the menubar), and is what makes the
        menu update live when renderers are enabled/disabled in prefs or by
        saving a material of a new renderer."""
        if not hasattr(self, "renderer_actions"):
            return
        flags = {
            "Karma": self.prefs.renderer_matx_enabled,
            "Mantra": self.prefs.renderer_mantra_enabled,
            "Redshift": self.prefs.renderer_redshift_enabled,
            "Octane": self.prefs.renderer_octane_enabled,
        }
        for label, visible in flags.items():
            act = self.renderer_actions.get(label)
            if act is not None:
                act.setVisible(visible)
        # If the currently-checked renderer just got hidden, fall back to All.
        checked = self.renderer_action_group.checkedAction()
        if checked is not None and not checked.isVisible():
            self.renderer_actions["All"].setChecked(True)
            self.prefs.last_renderer = "All"

    def cleanup_db(self) -> None:
        """Cleans the WHOLE v2 estate in one pass, one combined report:
        the material library, the COP library (same integrity passes
        over cops.json), registered texture/geometry folder pointers
        whose directory no longer exists, and favorites pointing at
        files (or curated gradient entries) that are gone."""
        if not self.material_model:
            hou.ui.displayMessage("Please open a library first")  # type: ignore
            return
        sections = []

        rescued = self.material_model.cleanup_db(show_dialog=False)
        if rescued:
            self.category_model.check_add_category("Uncategorized")
        normalized = self.category_model.normalize_categories()
        if normalized:
            print(f"Cleaned {normalized} legacy entr(y/ies) in the category list")
        mat_summary = list(
            getattr(self.material_model, "last_cleanup_summary", [])
        )
        if mat_summary:
            sections.append("Materials:\n- " + "\n- ".join(mat_summary))
        self.category_model.layoutChanged.emit()
        self.material_model.layoutChanged.emit()

        if getattr(self, "cop_model", None):
            cop_rescued = self.cop_model.cleanup_db(show_dialog=False)
            if cop_rescued:
                self.cop_category_model.check_add_category("Uncategorized")
            self.cop_category_model.normalize_categories()
            cop_summary = list(
                getattr(self.cop_model, "last_cleanup_summary", [])
            )
            if cop_summary:
                sections.append("COP networks:\n- " + "\n- ".join(cop_summary))
            self.cop_category_model.layoutChanged.emit()
            self.cop_model.layoutChanged.emit()

        browser_lines = self._cleanup_browser_prefs()
        if browser_lines:
            sections.append("Folders and favorites:\n- " + "\n- ".join(browser_lines))

        if sections:
            hou.ui.displayMessage(  # type: ignore
                "Library cleanup finished:\n\n"
                + "\n\n".join(sections)
                + "\n\nDetails in the Python shell."
            )
        else:
            hou.ui.displayMessage(  # type: ignore
                "Library cleanup finished: nothing to clean."
            )

    def _cleanup_browser_prefs(self) -> list:
        """The folder-browser sections' cleanup: drops registered
        texture/geometry folder pointers whose directory no longer
        exists, favorites whose file is gone, and curated-gradient
        favorite keys that no longer match any entry. Only pointers and
        prefs entries are touched - never anything on disk."""
        lines = []

        folders_removed = 0
        for path in list(self.prefs.texture_folders):
            if not os.path.isdir(path):
                print("Amaze: missing texture folder pointer removed: " + path)
                self.prefs.remove_texture_folder(path)
                folders_removed += 1
        for path in list(self.prefs.geometry_folders):
            if not os.path.isdir(path):
                print("Amaze: missing geometry folder pointer removed: " + path)
                self.prefs.remove_geometry_folder(path)
                folders_removed += 1
        if folders_removed:
            lines.append(
                f"{folders_removed} folder pointer(s) whose directory no "
                "longer exists were removed."
            )
            for model in (
                getattr(self, "texture_folders_model", None),
                getattr(self, "geo_folders_model", None),
            ):
                if model is not None:
                    model.layoutChanged.emit()

        favs_removed = 0
        for path in list(self.prefs.texture_favorites):
            if not os.path.exists(path):
                print("Amaze: favorite pointing at a missing file removed: " + path)
                self.prefs.remove_texture_favorite(path)
                favs_removed += 1
        for path in list(self.prefs.geometry_favorites):
            if not os.path.exists(path):
                print("Amaze: favorite pointing at a missing file removed: " + path)
                self.prefs.remove_geometry_favorite(path)
                favs_removed += 1
        if getattr(self, "gradient_model", None):
            valid_keys = set()
            for row in range(self.gradient_model.rowCount()):
                entry = self.gradient_model.entry(row)
                if entry is not None and entry.get("type") != "user":
                    valid_keys.add(self.gradient_model._fav_key(entry))
            for key in list(self.prefs.gradient_favorites):
                if key not in valid_keys:
                    print("Amaze: stale gradient favorite removed: " + key)
                    self.prefs.remove_gradient_favorite(key)
                    favs_removed += 1
        if favs_removed:
            lines.append(
                f"{favs_removed} favorite(s) pointing at missing files/"
                "entries were removed."
            )
        return lines

    def open_usdlib_folder(self) -> None:
        """Open the Library Folder in the System explorer"""
        if not self.material_model:
            hou.ui.displayMessage("Please open a library first")  # type: ignore
            return
        lib_dir = self.prefs.dir
        if sys.platform == "linux" or sys.platform == "linux2":  # Linux
            opener = "open"
            subprocess.call([opener, lib_dir])
            return
        elif sys.platform == "darwin":  # MacOS
            opener = "open"
            subprocess.call([opener, lib_dir])
        elif sys.platform == "win32":  # MacOS:  # Windows
            os.startfile(lib_dir)

    def add_texture_folder_user(self) -> None:
        """Register a new folder pointer for the Textures section. Only
        stores the path - never scans or copies anything until the
        folder is actually selected in the list."""
        path = hou.ui.selectFile(file_type=hou.fileType.Directory)  # type: ignore
        if not path:
            return
        self.texture_folders_model.add_folder(hou.expandString(path))

    def remove_texture_folder_user(self) -> None:
        """Unregister the selected folder pointer(s). Only removes the
        pointer from the list - never touches anything on disk."""
        rows = sorted(
            (i.row() for i in self.cat_list.selectedIndexes()), reverse=True
        )
        for row in rows:
            self.texture_folders_model.remove_folder(row)

    def add_category_user(self) -> None:
        """User adds a new category via a given string -
        if not yet in the library the category will be added"""
        if not self.material_model or not self.category_model:
            return
        choice, cat = hou.ui.readInput("Please enter the new category name:")  # type: ignore
        if choice:  # Return if no
            return

        self.category_model.layoutAboutToBeChanged.emit()
        self.category_model.check_add_category(cat)
        self.category_model.layoutChanged.emit()

    def rmv_category_user(self) -> None:
        """Removes a category - called by user change in UI"""
        # Prevent Deletion of "All" - Category
        if not self.material_model or not self.category_model:
            return
        self.material_model.layoutAboutToBeChanged.emit()
        self.category_model.layoutAboutToBeChanged.emit()
        self.material_selection_model.clearSelection()
        for index in self.cat_list.selectedIndexes():
            if index.data(QtCore.Qt.ItemDataRole.DisplayRole) == "All":
                return
            self.material_model.remove_category(
                index.data(QtCore.Qt.ItemDataRole.DisplayRole)
            )
            self.category_model.remove_category(
                index.data(QtCore.Qt.ItemDataRole.DisplayRole)
            )

        self.material_model.save()
        self.material_model.layoutChanged.emit()

        self.category_model.layoutChanged.emit()

    def rename_category_user(self) -> None:
        """Renames a category - called by user change in UI"""
        if not self.material_model or not self.category_model:
            return

        choice, cat = hou.ui.readInput("Please enter the new category name:")  # type: ignore
        if choice:  # Return if no
            return

        self.material_model.layoutAboutToBeChanged.emit()
        self.category_model.layoutAboutToBeChanged.emit()
        for index in self.cat_list.selectedIndexes():
            if index.data(QtCore.Qt.ItemDataRole.DisplayRole) == "All":
                return

            self.material_model.rename_category(
                index.data(QtCore.Qt.ItemDataRole.DisplayRole), cat
            )
            self.category_model.rename_category(
                index.data(QtCore.Qt.ItemDataRole.DisplayRole), cat
            )

        self.material_model.save()
        self.material_model.layoutChanged.emit()
        self.category_model.layoutChanged.emit()

    #: Fixed section order + labels; the enabled_sections pref chooses
    #: which of these actually appear (let a user show only the
    #: sections they use, e.g. Materials + Code).
    ALL_SECTIONS = (
        ("material", "Materials"),
        ("texture", "Textures"),
        ("gradient", "Colors"),
        ("cop", "Cop"),
        ("geometry", "Geometry"),
        ("code", "Code"),
    )

    def _build_section_tabs(self) -> None:
        """(Re)build the section tab strip from the enabled_sections
        pref. Called at construction and whenever that pref changes."""
        enabled = self.prefs.enabled_sections
        segments = [(k, lbl) for (k, lbl) in self.ALL_SECTIONS if k in enabled]
        if not segments:
            segments = [("material", "Materials")]
        # Replace any existing strip in the layout (index 1, under the
        # toolbar row).
        if getattr(self, "section_tabs", None) is not None:
            if self._central_layout is not None:
                self._central_layout.removeWidget(self.section_tabs)
            self.section_tabs.deleteLater()
        self.section_tabs = ui_helpers.SectionTabBar(segments)
        self.section_tabs.segmentClicked.connect(
            lambda key: self._on_tab_toggled(key, True)
        )
        if self._central_layout is not None:
            self._central_layout.insertWidget(1, self.section_tabs)
        # Keep the current section checked if it survived; else the
        # first available. emit=False: setup() activates the section
        # explicitly (and the models may not exist yet at construction).
        keys = [k for k, _ in segments]
        current = getattr(self, "current_section", "material")
        self.section_tabs.setChecked(
            current if current in keys else keys[0], emit=False
        )
        # A rebuild resets the label to "Materials"; re-apply "Online" if
        # the online browser is currently showing.
        self._sync_material_tab_label()

    def _apply_enabled_sections(self) -> None:
        """After Preferences may have changed enabled_sections: rebuild
        the strip, and if the section that was showing got hidden,
        switch to the first still-enabled one."""
        enabled = self.prefs.enabled_sections
        self._build_section_tabs()
        if self.current_section not in enabled and self.material_model:
            keys = [k for k, _ in self.ALL_SECTIONS if k in enabled] or [
                "material"
            ]
            self._on_tab_toggled(keys[0], True)

    def _on_tab_toggled(self, key: str, checked: bool) -> None:
        """Section-tab click (Materials / Textures / Colors / Cop /
        Geometry). Geometry is still a placeholder - its content is not
        built yet, so the view underneath simply doesn't change when
        it's clicked."""
        if not checked:
            return
        if self.material_model is None:
            # setup() (which creates category_sorted_model,
            # texture_folders_model, material_selection_model, etc.)
            # never ran - no library is configured yet. init_ui() builds
            # and enables the tab strip unconditionally, so this is
            # reachable by just clicking a tab before that - same class
            # of crash as the emit=False fix above, different
            # precondition. Same guard pattern used everywhere else in
            # this file for "library not set up yet".
            return
        # Snapshot the OUTGOING section's view state before anything
        # changes - current_section still names it here.
        self._capture_section_state()
        self.current_section = key
        self._sync_toolbar_for_mode()
        debug.event("section", "switched", to=key, online=self._is_online())
        section = self.sections.get(key)
        if section is None:
            print(
                f"Amaze: '{key}' section isn't built yet - "
                "the view below won't change."
            )
            return
        section.activate()
        self._restore_section_state(key)

    def _capture_section_state(self) -> None:
        """Remember the current section's sidebar choice and grid scroll
        position (keyed by section) for _restore_section_state."""
        state = {}
        current = self.cat_list.currentIndex() if self.cat_list else None
        if current is not None and current.isValid():
            state["cat_text"] = current.data()
        state["scroll"] = self.thumblist.verticalScrollBar().value()
        self._section_view_state[self.current_section] = state

    def _restore_section_state(self, key: str) -> None:
        """Re-select the sidebar entry the section had when last left
        (overriding the activation method's default) and bring the grid
        scroll back. Matching is by display TEXT, so category renames
        or reordering between visits degrade gracefully to the default
        instead of selecting the wrong row. Textures skip the sidebar
        part - their folder restore (prefs-based, survives relaunches)
        already ran in _activate_texture_section."""
        state = self._section_view_state.get(key)
        if not state:
            return
        cat_text = state.get("cat_text")
        if (
            cat_text
            and key not in ("texture", "geometry")
            and self.cat_list is not None
        ):
            model = self.cat_list.model()
            selection_model = self.cat_list.selectionModel()
            if model is not None and selection_model is not None:
                for row in range(model.rowCount()):
                    idx = model.index(row, 0)
                    if idx.data() == cat_text:
                        selection_model.select(
                            idx,
                            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                        )
                        self.cat_list.setCurrentIndex(idx)
                        # Re-applies the right filter for whichever
                        # section this is - same handler a real click
                        # runs.
                        self.update_selected_cat()
                        break
        scroll = state.get("scroll")
        if scroll:
            # Deferred one event-loop turn: the view has just swapped
            # models and relaid itself out - an immediate setValue gets
            # clamped/overridden by that layout pass.
            QtCore.QTimer.singleShot(
                0,
                lambda: self.thumblist.verticalScrollBar().setValue(scroll),
            )

    # ------------------------------------------------------------------
    # Online MaterialX browser (View menu > Online Materials)
    # ------------------------------------------------------------------

    def _sync_toolbar_for_mode(self) -> None:
        """Toolbar controls that don't apply to every view.

        Online records have no favourite state (the role always answers
        False), so the star would filter the grid to nothing. Disabled
        beats a control that looks live and isn't. Called from the two
        places that can change section or mode, rather than from each
        _activate_* - otherwise going online and then switching tabs
        leaves the star stranded."""
        if self.cb_favsonly is not None:
            self.cb_favsonly.setEnabled(not self._is_online())

    def _section(self):
        """The active Section object (panel/sections.py). None only before
        setup() has built the registry."""
        return getattr(self, "sections", {}).get(self.current_section)

    def _is_online(self) -> bool:
        """Is the shared grid currently showing the online browser?

        Online is a VIEW MODE over the Materials section, not a section
        of its own, so it is a second axis of state on top of
        current_section - and every shared handler has to consult both.
        One predicate so they cannot drift apart (they had: four
        handlers knew about online mode and six did not)."""
        return (
            getattr(self, "online_mode", False)
            and self.current_section == "material"
        )

    def _on_online_source(self, action) -> None:
        """A View menu material-source entry was clicked. Material Library
        returns to the local library; an online source enters its browser.
        They share one exclusive group, so Qt keeps exactly one checked."""
        if action is self.action_material_library:
            self.exit_online_materials()
        elif action.isChecked():
            self.open_online_source(action.text())

    def open_online_source(self, source_name: str) -> None:
        """Enter the online browser showing one source. Not a section and
        not a filter - a VIEW MODE over the Materials widgets."""
        self.online_mode = True
        self.online_source = source_name
        act = self.online_source_actions.get(source_name)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        debug.event("online", "source opened", source=source_name)
        if self.current_section != "material":
            self.section_tabs.setChecked("material")
        self._sync_toolbar_for_mode()
        self.matx_online_model.set_source(source_name)
        self._activate_online_materials()
        # Picking a source starts you on "All" - not a stale category from
        # a previous source and not an unhighlighted sidebar. Done here (on
        # the explicit source-pick) rather than in _activate_online_
        # materials(), so switching tabs away and back doesn't reset the
        # category you were browsing.
        self._select_online_all()

    def _select_online_all(self) -> None:
        """Select the online sidebar's "All" row (row 0) and clear any
        category filter, so the grid shows the whole source."""
        if not self.cat_list or self.matx_source_model.rowCount() == 0:
            return
        idx = self.matx_source_model.index(0, 0)
        sel = self.cat_list.selectionModel()
        if sel is not None:
            sel.select(
                idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            )
            self.cat_list.setCurrentIndex(idx)
        self.update_selected_cat()

    def exit_online_materials(self) -> None:
        """Leave the online browser, back to the local library (Material
        Library)."""
        self.online_mode = False
        if getattr(self, "action_material_library", None) is not None:
            # Exclusive group: checking this unchecks any source.
            self.action_material_library.setChecked(True)
        debug.event("online", "exited")
        self._sync_toolbar_for_mode()
        if self.current_section != "material":
            # Material Library implies the Materials tab; switching there
            # re-activates it (online_mode is already False -> local view).
            self.section_tabs.setChecked("material")
        else:
            self._activate_material_section()

    def _activate_online_materials(self) -> None:
        """Point the shared grid at the online model for the current
        source and load (cache-instant, background refresh)."""
        if self.cat_list:
            self.cat_list.setModel(self.matx_source_model)
        self.thumblist.setModel(self.matx_sorted_model)
        self.thumblist.setSelectionModel(self.matx_selection_model)
        self.thumblist.setItemDelegate(self.thumb_delegate)
        self.texture_progress.setVisible(False)
        self.matx_online_model.reload()
        self._update_list_columns()
        self._sync_material_tab_label()

    def _sync_material_tab_label(self) -> None:
        """The Materials tab reads "Online" while the online browser is
        showing, "Materials" otherwise - the online view looks so much
        like the local library that the tab label is the cue you've left
        it."""
        if getattr(self, "section_tabs", None) is None:
            return
        online = getattr(self, "online_mode", False)
        self.section_tabs.set_label(
            "material", "Online" if online else "Materials"
        )

    def _matx_selected_records(self):
        rows = [
            self.matx_sorted_model.mapToSource(i).row()
            for i in self.matx_selection_model.selectedIndexes()
        ]
        return [
            r for r in (self.matx_online_model.record(x) for x in rows)
            if r is not None
        ]

    #: Sub-steps per material for the download bar - a smooth 0..N*SCALE
    #: range folds each material's own 0..1 download fraction into the
    #: overall multi-import progress.
    _IMPORT_PROGRESS_SCALE = 1000

    def _import_online_records(self, records) -> None:
        """Import one or more online records, showing the download bar.

        The download is synchronous (it blocks the UI thread), so the byte
        callback pumps events per 64KB chunk with ExcludeUserInputEvents -
        the bar animates without a second click re-entering mid-import.
        Same pattern as the geometry thumbnail pass."""
        total = len(records)
        if not total:
            return
        scale = self._IMPORT_PROGRESS_SCALE
        pump = QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
        # The event pumping below can deliver a late preview-worker signal;
        # this flag keeps that from repainting the bar with preview counts
        # while the download owns it.
        self._online_download_active = True
        self.texture_progress.setVisible(True)
        self.texture_progress.set_progress(0, total * scale)
        QtWidgets.QApplication.processEvents(pump)
        try:
            for i, rec in enumerate(records):
                def on_progress(frac, i=i):
                    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
                    self.texture_progress.set_progress(
                        int((i + frac) * scale), total * scale
                    )
                    QtWidgets.QApplication.processEvents(pump)
                self.import_online_material(rec, on_progress=on_progress)
        finally:
            self._online_download_active = False
            self.texture_progress.setVisible(False)

    def import_online_material(self, record, on_progress=None) -> None:
        """Download (if needed) and register one online material as a
        normal library material with renderer MtlX. on_progress(frac) is
        called with a 0..1 fraction during the download when given."""
        if record is None or not self.material_model:
            return
        source = next(
            (s for s in self.matx_online_model.sources
             if s.name == record.source),
            None,
        )
        if source is None:
            return
        resolution = None
        if record.kind != "values":
            available = source.resolutions(record)
            resolution = matx_sources.pick_resolution(
                available, self.prefs.matx_resolution
            )
            if resolution is None:
                hou.ui.displayMessage(  # type: ignore
                    '"%s" has no downloadable package.' % record.title
                )
                return
        self.material_model.layoutAboutToBeChanged.emit()
        try:
            ok, reason = matx_import.import_record(
                record, source, resolution, self.material_model, self.prefs,
                progress=on_progress,
            )
        finally:
            self.material_model.layoutChanged.emit()
        if not ok:
            hou.ui.displayMessage(reason or "Import failed.")  # type: ignore
            return
        # check_add_category() writes and saves SILENTLY - no model
        # signal - so the sidebar never learned about the new row. The
        # data was always correct; only the view was stale.
        self.category_model.layoutAboutToBeChanged.emit()
        self.category_model.check_add_category(record.category)
        self.category_model.layoutChanged.emit()
        self._refresh_sidebar_categories()

    def _matx_rc_menu(self) -> None:
        """Right-click in the online browser: import (single or many)."""
        records = self._matx_selected_records()
        cmenu = QtWidgets.QMenu(self)
        action_import = None
        if records:
            action_import = cmenu.addAction(
                "Import Material" if len(records) == 1
                else "Import %d Materials" % len(records)
            )
        action_refresh = cmenu.addAction("Refresh")
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action is None:
            return
        if action is action_refresh:
            self.matx_online_model.reload()
            return
        if action is action_import:
            self._import_online_records(records)

    def _activate_material_section(self) -> None:
        """Point the shared list/grid widgets back at the Materials
        models (also the initial state set up in setup())."""
        if getattr(self, "online_mode", False):
            # Leaving the Materials tab and coming back must not silently
            # drop out of the online browser: the View menu would still
            # say "Online Materials", every online-aware handler would
            # still take the online path, and the grid would be showing
            # the local library. Restoring it here also gives online mode
            # the same section memory every other section has.
            self._activate_online_materials()
            return
        if self.cat_list:
            self.cat_list.setModel(self.category_sorted_model)
        self.thumblist.setModel(self.material_sorted_model)
        self.thumblist.setSelectionModel(self.material_selection_model)
        self.thumblist.setItemDelegate(self.thumb_delegate)
        self.texture_progress.setVisible(False)
        # Different section = different names; re-fit the Name column.
        self._update_list_columns()
        self._sync_material_tab_label()  # back to "Materials"

    def _activate_texture_section(self) -> None:
        """Point the shared list/grid widgets at the Textures models.
        No details panel yet - registered folders are plain pointers
        with no per-file metadata to edit."""
        self.texture_folders_model.refresh_counts()
        if self.cat_list:
            self.cat_list.setModel(self.texture_folders_model)
        self.thumblist.setModel(self.texture_sorted_model)
        self.thumblist.setSelectionModel(self.texture_selection_model)
        self.thumblist.setItemDelegate(self.texture_delegate)
        # Different section = different names; re-fit the Name column.
        self._update_list_columns()

        # cat_list has no persistent selection model of its own (unlike
        # thumblist), so setModel() above always leaves it with nothing
        # selected - auto-select something so the grid isn't left blank
        # with nothing highlighted every time this section opens.
        # Restores the last folder (or "All") the user actually picked,
        # persisted in prefs so it survives both a tab switch within this
        # session and a full Houdini relaunch; falls back to the first
        # real folder (not "All") if nothing's been picked yet, or if the
        # remembered folder was since removed - "All" eagerly scans and
        # queues thumbnails for every registered folder at once, which
        # should not happen by surprise as the default.
        has_real_folders = self.texture_folders_model.rowCount() > 1
        target_row = 1 if has_real_folders else 0
        last = self.prefs.last_texture_folder
        if last == self.texture_folders_model.ALL_LABEL:
            target_row = 0
        elif last and last in self.prefs.texture_folders:
            target_row = self.prefs.texture_folders.index(last) + 1
        if self.cat_list and self.texture_folders_model.rowCount() > 0:
            target_index = self.texture_folders_model.index(target_row, 0)
            selection_model = self.cat_list.selectionModel()
            if selection_model is not None:
                selection_model.select(
                    target_index,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                self.cat_list.setCurrentIndex(target_index)
            self.update_selected_cat()

    def _activate_geometry_section(self) -> None:
        """Point the shared list/grid widgets at the Geometry models -
        the Textures design over geometry files (see core/
        geo_library.py). Restores the last folder like Textures does;
        first visit to an uncached folder renders thumbnails in a
        blocking, ESC-interruptable pass (Houdini renders are main-
        thread-only, unlike the texture converters)."""
        self.geo_folders_model.refresh_counts()
        if self.cat_list:
            self.cat_list.setModel(self.geo_folders_model)
        self.thumblist.setModel(self.geo_sorted_model)
        self.thumblist.setSelectionModel(self.geo_selection_model)
        self.thumblist.setItemDelegate(self.geo_delegate)
        self.texture_progress.setVisible(False)
        self._update_list_columns()

        has_real_folders = self.geo_folders_model.rowCount() > 1
        target_row = 1 if has_real_folders else 0
        last = self.prefs.last_geometry_folder
        if last == self.geo_folders_model.ALL_LABEL:
            target_row = 0
        elif last and last in self.prefs.geometry_folders:
            target_row = self.prefs.geometry_folders.index(last) + 1
        if self.cat_list and self.geo_folders_model.rowCount() > 0:
            target_index = self.geo_folders_model.index(target_row, 0)
            selection_model = self.cat_list.selectionModel()
            if selection_model is not None:
                selection_model.select(
                    target_index,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                self.cat_list.setCurrentIndex(target_index)
            self.update_selected_cat()

    def _activate_gradient_section(self) -> None:
        """Point the shared list/grid widgets at the Gradients models
        (Sanzo Wada combinations, read-only in v1). No details panel, no
        progress bar - thumbnails are painted, not generated."""
        if self.cat_list:
            self.cat_list.setModel(self.gradient_categories_model)
        self.thumblist.setModel(self.gradient_sorted_model)
        self.thumblist.setSelectionModel(self.gradient_selection_model)
        self.thumblist.setItemDelegate(self.gradient_delegate)
        self.texture_progress.setVisible(False)
        # Different section = different names; re-fit the Name column.
        self._update_list_columns()
        # Start on "All" (row 0) with the size filter cleared - the
        # programmatic select below doesn't fire clicked(), so the
        # filter is reset explicitly.
        self.gradient_sorted_model.set_sidebar_filter("all", None)
        if self.cat_list and self.gradient_categories_model.rowCount() > 0:
            selection_model = self.cat_list.selectionModel()
            if selection_model is not None:
                target = self.gradient_categories_model.index(0, 0)
                selection_model.select(
                    target,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                self.cat_list.setCurrentIndex(target)

    def _activate_cop_section(self) -> None:
        """Point the shared list/grid widgets at the Cop-section models
        (the material machinery over its own cops.json - see
        core/cop_library.py). Details panel stays hidden in v1: metadata
        lives in the save dialog, favorites in the right-click menu and
        the star filter. Reuses thumb_delegate - the roles are inherited
        from the material model, so the subtitle line shows "COP"."""
        if self.cat_list:
            self.cat_list.setModel(self.cop_category_sorted_model)
        self.thumblist.setModel(self.cop_sorted_model)
        self.thumblist.setSelectionModel(self.cop_selection_model)
        self.thumblist.setItemDelegate(self.thumb_delegate)
        self.texture_progress.setVisible(False)
        self._update_list_columns()
        # Start on the sidebar's first row - "All", once the _All sort
        # convention holds - and set the category filter FROM the row
        # that actually got selected, never from an assumption about
        # what sorts first. (The programmatic select doesn't fire
        # clicked(), so the filter must be applied explicitly - and a
        # blanket "" here while row 0 happened to be a real category
        # produced an "Abstract highlighted but everything
        # shown" mismatch on pre-migration data.)
        selected_name = None
        if self.cat_list and self.cop_category_sorted_model.rowCount() > 0:
            selection_model = self.cat_list.selectionModel()
            if selection_model is not None:
                target = self.cop_category_sorted_model.index(0, 0)
                selection_model.select(
                    target,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                self.cat_list.setCurrentIndex(target)
                selected_name = target.data()
        self.cop_sorted_model.setFilter(
            self.cop_model.CategoryRole,
            "" if selected_name in (None, "All") else selected_name,
        )

    def _cop_rc_menu(self) -> None:
        """Right-click menu for the Cop section: import, favorites,
        thumbnail rerender and delete - the material menu's essentials
        without the renderer-specific actions (MAT/LOP targets, Karma
        conversion) that have no meaning for a COP network."""
        proxy_indexes = self.cop_selection_model.selectedIndexes()
        if not proxy_indexes:
            return
        cmenu = QtWidgets.QMenu(self)
        action_import = cmenu.addAction("Import")
        action_fav = cmenu.addAction("Toggle Favorite")
        action_rerender = cmenu.addAction("Rerender Thumbnail")
        cmenu.addSeparator()
        action_delete = cmenu.addAction("Delete Entry")
        action = cmenu.exec_(QtGui.QCursor.pos())

        if action == action_import:
            self.import_cop_assets()
        elif action == action_fav:
            self.cop_model.layoutAboutToBeChanged.emit()
            for proxy_index in proxy_indexes:
                self.cop_model.toggle_fav(
                    self.cop_sorted_model.mapToSource(proxy_index)
                )
            self.cop_model.layoutChanged.emit()
        elif action == action_rerender:
            self.cop_model.layoutAboutToBeChanged.emit()
            for proxy_index in proxy_indexes:
                self.cop_model.render_thumbnail(
                    self.cop_sorted_model.mapToSource(proxy_index)
                )
            self.cop_model.layoutChanged.emit()
        elif action == action_delete:
            if hou.ui.displayConfirmation(  # type: ignore
                "This will delete the selected COP network(s) from Disk. "
                "Are you sure?"
            ):
                real_indexes = sorted(
                    (
                        self.cop_sorted_model.mapToSource(i)
                        for i in proxy_indexes
                    ),
                    key=lambda idx: idx.row(),
                    reverse=True,
                )
                self.cop_model.layoutAboutToBeChanged.emit()
                for idx in real_indexes:
                    self.cop_model.remove_asset(idx)
                self.cop_model.layoutChanged.emit()
                self._refresh_sidebar_categories()

    def import_cop_assets(self) -> None:
        """Import every selected Cop-section asset, reporting failures
        in one summary dialog (same shape as the material importer)."""
        failures = []
        for index in self.thumblist.selectedIndexes():
            source_index = self.cop_sorted_model.mapToSource(index)
            try:
                ok, reason = self.cop_model.import_asset_to_scene(source_index)
            except Exception as e:
                try:
                    name = self.cop_model.assets[source_index.row()].name
                except Exception:
                    name = "COP network"
                failures.append(f'"{name}" failed to import: {e}')
                continue
            if not ok and reason:
                failures.append(reason)
        if failures:
            hou.ui.displayMessage(  # type: ignore
                "Some COP networks could not be imported:\n\n"
                + "\n".join(failures)
            )

    # ------------------------------------------------------------------
    # Code section
    # ------------------------------------------------------------------

    def _activate_code_section(self) -> None:
        """Point the shared list/grid widgets at the Code models (the
        material machinery over code.json - see core/code_library.py).
        Details hidden; tiles show a painted code preview + language."""
        if self.cat_list:
            self.cat_list.setModel(self.code_category_sorted_model)
        self.thumblist.setModel(self.code_sorted_model)
        self.thumblist.setSelectionModel(self.code_selection_model)
        self.thumblist.setItemDelegate(self.thumb_delegate)
        self.texture_progress.setVisible(False)
        self._update_list_columns()
        selected_name = None
        if self.cat_list and self.code_category_sorted_model.rowCount() > 0:
            selection_model = self.cat_list.selectionModel()
            if selection_model is not None:
                target = self.code_category_sorted_model.index(0, 0)
                selection_model.select(
                    target,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                self.cat_list.setCurrentIndex(target)
                selected_name = target.data()
        self.code_sorted_model.setFilter(
            self.code_model.CategoryRole,
            "" if selected_name in (None, "All") else selected_name,
        )

    def get_code_category_names(self) -> list[str]:
        """All Code-section category names (empty ones included),
        excluding 'All' - source model, like get_cop_category_names."""
        names = []
        for elem in range(self.code_category_model.rowCount()):
            cidx = self.code_category_model.index(elem, 0)
            name = self.code_category_model.data(
                cidx, QtCore.Qt.ItemDataRole.DisplayRole
            )
            if name and name != "All":
                names.append(name)
        return sorted(names, key=str.lower)

    def _current_code_category(self) -> str:
        """The category selected in the sidebar when the Code section is
        showing, "" for All - the save/new dialog's default category."""
        if self.current_section != "code" or not self.cat_list:
            return ""
        sel = self.cat_list.selectedIndexes()
        if sel:
            name = sel[0].data()
            if name and name != "All":
                return name
        return ""

    def _add_code_snippet(
        self, code: str, language: str, default_name: str
    ) -> None:
        """Shared save flow for both Save-from-Node and New Snippet:
        open the Code dialog prefilled, then register the snippet."""
        if not self.code_model:
            hou.ui.displayMessage(  # type: ignore
                "Please set a library first. Use the AssetLib panel - "
                "Library/Open Dialog."
            )
            return
        dialog = code_dialog.CodeDialog(
            self.get_code_category_names(),
            name=default_name,
            language=language or "VEX",
            category=self._current_code_category(),
            code=code,
        )
        dialog.exec_()
        if dialog.canceled:
            return
        if dialog.category:
            self.code_category_model.check_add_category(dialog.category)
        self.code_model.layoutAboutToBeChanged.emit()
        self.code_category_model.layoutAboutToBeChanged.emit()
        self.code_model.add_asset(
            dialog.code,
            dialog.name,
            dialog.language,
            dialog.category,
            dialog.tags,
            False,
            dialog.description,
        )
        self.code_model.layoutChanged.emit()
        self.code_category_model.layoutChanged.emit()
        self._refresh_sidebar_categories()

    def save_code_from_node(self, node: hou.Node | None = None) -> None:
        """Node right-click "Save Code to AssetLib": grab the node's
        code/snippet parm and open the save dialog prefilled."""
        if node is None:
            sel = hou.selectedNodes()
            node = sel[0] if len(sel) == 1 else None
        if node is None:
            hou.ui.displayMessage(  # type: ignore
                "Right-click a wrangle (or other node with a code "
                "parameter) to save its snippet."
            )
            return
        parm = helpers.find_code_parm(node)
        if parm is None:
            hou.ui.displayMessage(  # type: ignore
                '"%s" has no code/snippet parameter.' % node.name()
            )
            return
        self._add_code_snippet(
            parm.eval(),
            helpers.code_parm_language(parm),
            node.name(),
        )

    def new_code_snippet(self) -> None:
        """Create a snippet by typing/pasting into an empty editor."""
        self._add_code_snippet("", "VEX", "")

    def _apply_code_index(self, index: QtCore.QModelIndex) -> None:
        """Apply the double-clicked/selected snippet to the single
        selected scene node's code parm."""
        source_index = self.code_sorted_model.mapToSource(index)
        ok, reason = self.code_model.import_asset_to_scene(source_index)
        if not ok and reason:
            hou.ui.displayMessage(reason)  # type: ignore

    def _code_rc_menu(self) -> None:
        """Right-click menu for the Code section. Always offers New
        Snippet; per-selection: View/Copy, Apply to Node, Edit, Toggle
        Favorite, Delete."""
        proxy_indexes = self.code_selection_model.selectedIndexes()
        cmenu = QtWidgets.QMenu(self)
        action_new = cmenu.addAction("New Snippet")
        action_view = action_apply = action_edit = None
        action_fav = action_delete = None
        if proxy_indexes:
            cmenu.addSeparator()
            action_view = cmenu.addAction("View / Copy Code")
            action_apply = cmenu.addAction("Apply to Selected Node")
            if len(proxy_indexes) == 1:
                action_edit = cmenu.addAction("Edit Snippet")
            action_fav = cmenu.addAction("Toggle Favorite")
            cmenu.addSeparator()
            action_delete = cmenu.addAction("Delete Entry")
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action is None:
            return
        if action == action_new:
            self.new_code_snippet()
            return
        rows = [
            self.code_sorted_model.mapToSource(i).row() for i in proxy_indexes
        ]
        if action == action_view and rows:
            asset = self.code_model.assets[rows[0]]
            code_dialog.CodeViewDialog(asset.name, asset.code).exec_()
        elif action == action_apply and rows:
            self._apply_code_index(proxy_indexes[0])
        elif action == action_edit and rows:
            self._edit_code_row(rows[0])
        elif action == action_fav:
            self.code_model.layoutAboutToBeChanged.emit()
            for row in rows:
                self.code_model.toggle_fav(self.code_model.index(row, 0))
            self.code_model.layoutChanged.emit()
        elif action == action_delete and rows:
            if hou.ui.displayConfirmation(  # type: ignore
                "Delete the selected snippet(s)?"
            ):
                self.code_model.layoutAboutToBeChanged.emit()
                for row in sorted(rows, reverse=True):
                    self.code_model.remove_asset(self.code_model.index(row, 0))
                self.code_model.save()
                self.code_model.layoutChanged.emit()
                self._refresh_sidebar_categories()

    def _edit_code_row(self, row: int) -> None:
        asset = self.code_model.assets[row]
        dialog = code_dialog.CodeDialog(
            self.get_code_category_names(),
            name=asset.name,
            language=asset.renderer,
            category=asset.categories[0] if asset.categories else "",
            tags=", ".join(asset.tags),
            code=asset.code,
            description=asset.description,
            title="Edit Snippet",
        )
        dialog.exec_()
        if dialog.canceled:
            return
        if dialog.category:
            self.code_category_model.check_add_category(dialog.category)
        self.code_model.layoutAboutToBeChanged.emit()
        self.code_category_model.layoutAboutToBeChanged.emit()
        self.code_model.update_asset(
            row, dialog.code, dialog.name, dialog.language,
            dialog.category, dialog.tags, dialog.description,
        )
        self.code_model.layoutChanged.emit()
        self.code_category_model.layoutChanged.emit()
        self._refresh_sidebar_categories()

    def get_cop_category_names(self) -> list[str]:
        """All Cop-section category names (empty ones included),
        excluding 'All' - source model, not the hiding sidebar proxy,
        same reasoning as get_category_names."""
        names = []
        for elem in range(self.cop_category_model.rowCount()):
            cidx = self.cop_category_model.index(elem, 0)
            name = self.cop_category_model.data(
                cidx, QtCore.Qt.ItemDataRole.DisplayRole
            )
            if name and name != "All":
                names.append(name)
        return sorted(names, key=str.lower)

    def save_cop_from_node(self, node: hou.Node | None = None) -> None:
        """Node right-click "Save to AssetLib" on a COP network
        container (rc_calls.save_cop passes the clicked node through).
        v1 keeps standard-new semantics only - no Overwrite flow yet."""
        if not self.cop_model:
            hou.ui.displayMessage(  # type: ignore
                "Please set a library first. Use the AssetLib panel - "
                "Library/Open Dialog."
            )
            return
        if node is None:
            sel = hou.selectedNodes()
            node = sel[0] if len(sel) == 1 else None
        if node is None:
            hou.ui.displayMessage(  # type: ignore
                "Right-click a COP network node to save it."
            )
            return
        # Container vs selection save: right-clicking a copnet CONTAINER
        # saves the whole network (original flow); right-clicking a node
        # INSIDE a Copernicus network saves the current selection there
        # (plus the clicked node), named after the clicked node.
        items = None
        if node.type().name() == "copnet":
            if not node.children():
                hou.ui.displayMessage(  # type: ignore
                    "The COP network is empty - nothing to save."
                )
                return
        else:
            net = node.parent()
            items = [i for i in hou.selectedItems() if i.parent() == net]
            if not any(i == node for i in items):
                items.append(node)

        # Pre-select the category active in the panel when the Cop
        # section is showing (mirrors the material save dialog).
        current_cat = ""
        if self.current_section == "cop":
            cat_selection = self.cat_list.selectedIndexes()
            if cat_selection:
                selected_name = cat_selection[0].data()
                if selected_name and selected_name != "All":
                    current_cat = selected_name

        dialog = usd_dialog.UsdDialog(
            self.get_cop_category_names(), current_cat, name=node.name()
        )
        r = dialog.exec_()
        if dialog.canceled or not r:
            return
        if dialog.categories:
            self.cop_category_model.check_add_category(dialog.categories)
        if dialog.tags:
            self.cop_model.check_add_tags(dialog.tags)

        self.cop_model.layoutAboutToBeChanged.emit()
        self.cop_category_model.layoutAboutToBeChanged.emit()
        result = self.cop_model.add_asset(
            node,
            dialog.categories,
            dialog.tags,
            dialog.fav,
            items=items,
            name=dialog.name,
        )
        self.cop_model.layoutChanged.emit()
        self.cop_category_model.layoutChanged.emit()
        if not result:
            hou.ui.displayMessage(  # type: ignore
                "The COP network could not be saved."
            )

    def _geo_rc_menu(self) -> None:
        """Right-click menu for the Geometry section: import the
        selection to /obj, toggle favorites, or force-regenerate the
        selection's cached thumbnails - the Textures menu's shape with
        import-to-scene instead of load-to-node."""
        proxy_indexes = self.geo_selection_model.selectedIndexes()
        if not proxy_indexes:
            return
        # NO delete action here, deliberately - this section browses
        # REAL files outside the library, and the short-lived "Delete
        # File" (os.remove, no Trash) permanently deleted real
        # production models on a misclick. Removed for that reason; if
        # file deletion is ever wanted again it must go through the OS
        # Trash, never a permanent unlink.
        cmenu = QtWidgets.QMenu(self)
        action_import = cmenu.addAction("Import")
        action_toggle_fav = cmenu.addAction("Toggle Favorite")
        action_rerender = cmenu.addAction("Rerender Thumbnail")
        action = cmenu.exec_(QtGui.QCursor.pos())

        if action == action_import:
            for proxy_index in proxy_indexes:
                self.import_geo_asset(proxy_index)
        elif action == action_toggle_fav:
            for proxy_index in proxy_indexes:
                source_index = self.geo_sorted_model.mapToSource(proxy_index)
                self.geo_files_model.toggle_favorite(source_index.row())
        elif action == action_rerender:
            rows = [
                self.geo_sorted_model.mapToSource(i).row()
                for i in proxy_indexes
            ]
            self.geo_files_model.rerender_thumbnails(rows)

    def _active_network_pwd(self) -> hou.Node | None:
        """The network the user is most likely looking at: the visible
        (current-tab) network editor's pwd, falling back to any open
        editor - the same preference get_active_network_editor uses on
        the material side."""
        editors = [
            pt
            for pt in hou.ui.paneTabs()  # type: ignore
            if pt.type() == hou.paneTabType.NetworkEditor
        ]
        if not editors:
            return None
        visible = [e for e in editors if e.isCurrentTab()]
        try:
            return (visible or editors)[0].pwd()
        except AttributeError:
            return None

    @staticmethod
    def _is_sop_container(node: hou.Node) -> bool:
        """True for anything whose children are SOPs - a geo object, a
        SOP Create LOP, a plain sopnet - i.e. a valid drop-INTO target
        for a geometry file."""
        try:
            category = node.childTypeCategory()
        except (AttributeError, hou.OperationFailed):
            return False
        return category is not None and category.name() == "Sop"

    @staticmethod
    def _create_loader_inside(container: hou.Node, loader_type: str):
        """Create the loader in the DEEPEST SOP network in/under the
        container, falling outward on failure. A SOP Create LOP is a
        locked HDA whose EDITABLE network sits at sopcreate/sopnet/
        create - picking the first SOP-children network found landed on
        the locked middle level and raised hou.PermissionError
        ("Cannot create a node inside a locked asset", from a live log),
        so depth-first-preference plus try-per-candidate is the robust
        form: whichever level actually accepts the node wins."""
        candidates = []

        def walk(node, depth):
            try:
                category = node.childTypeCategory()
            except (AttributeError, hou.OperationFailed):
                category = None
            if category is not None and category.name() == "Sop":
                candidates.append((depth, node))
            if depth < 3:
                for child in node.children():
                    walk(child, depth + 1)

        walk(container, 0)
        last_error = None
        for _depth, net in sorted(candidates, key=lambda c: -c[0]):
            try:
                return net.createNode(loader_type)
            except hou.Error as exc:
                last_error = exc
        raise last_error or hou.OperationFailed("no SOP network found")

    def import_geo_asset(self, index: QtCore.QModelIndex) -> None:
        """Double-click/right-click import for a geometry file -
        CONTEXT-AWARE (per spec): in an OBJ network, a new geo
        named after the file with the right loader SOP inside; already
        inside a SOP network (a geo node's innards, a SOP Create's), a
        new loader SOP in place; in a LOP network, a new SOP Create
        holding the loader - geometry lives directly in the stage,
        never as stray /obj exports. The drag imports the same way at
        the release point (drop_geo_at_release)."""
        path = index.data(self.geo_files_model.PathRole)
        if not path:
            return
        self._import_geo_in_context(path, self._active_network_pwd())

    def _import_geo_in_context(
        self, path: str, dest: hou.Node | None
    ) -> None:
        base = os.path.basename(path)
        name = helpers.sanitize_usd_path(os.path.splitext(base)[0]) or "geo"
        loader_type = geo_library.loader_sop_for(path)

        category = ""
        if dest is not None:
            try:
                cat_obj = dest.childTypeCategory()
                category = cat_obj.name() if cat_obj is not None else ""
            except (AttributeError, hou.OperationFailed):
                category = ""

        container = None  # created here; cleaned up if the import fails
        try:
            if category == "Sop":
                loader = dest.createNode(loader_type)
            elif category == "Lop":
                container = dest.createNode("sopcreate", name)
                loader = self._create_loader_inside(container, loader_type)
            elif category == "Object":
                container = dest.createNode("geo", name)
                loader = container.createNode(loader_type)
            else:
                # Not a network that can hold geometry (mat/cop/...):
                # fall back to a fresh geo at /obj, the old behavior.
                obj = hou.node("/obj")
                if obj is None:
                    raise hou.OperationFailed("no /obj network")
                container = obj.createNode("geo", name)
                loader = container.createNode(loader_type)
        # hou.Error, NOT just OperationFailed: the locked-asset case
        # raises hou.PermissionError (a SIBLING class) - catching too
        # narrowly turned the failure into a silent traceback with a
        # dead sopcreate left in the scene.
        except hou.Error as exc:
            if container is not None:
                try:
                    container.destroy()
                except (hou.OperationFailed, hou.ObjectWasDeleted):
                    pass
            print(f"Amaze: geometry import failed for {base}: {exc}")
            hou.ui.displayMessage(  # type: ignore
                f"Could not import {base}: {exc}"
            )
            return
        parm = helpers.find_file_parm(loader)
        if parm is None:
            try:
                (container or loader).destroy()
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
            hou.ui.displayMessage(  # type: ignore
                f'The "{loader_type}" SOP has no file parameter to set.'
            )
            return
        parm.set(path)
        try:
            loader.setName(name, unique_name=True)
        except hou.OperationFailed:
            pass
        loader.setDisplayFlag(True)
        try:
            loader.setRenderFlag(True)
        except AttributeError:
            pass
        loader.moveToGoodPosition()
        if container is not None:
            container.moveToGoodPosition()

    def drop_geo_at_release(self, index: QtCore.QModelIndex) -> None:
        """Geometry drag released: import in context at the release
        point - on a SOP-capable node (geo, SOP Create) the loader
        lands inside it; an OBJ network gets a new geo; a LOP network
        a new SOP Create. Release over nothing is silent - a miss is a
        normal drag outcome, not an error."""
        context = self._drop_context_under_cursor(
            self._is_sop_container, include_viewports=True
        )
        if context is None:
            return
        path = index.data(self.geo_files_model.PathRole)
        if not path:
            return
        self._import_geo_in_context(path, context)

    def _gradient_rc_menu(self) -> None:
        """Right-click menu for the Gradients section: apply the (single)
        selected combination to the scene as a stepped color ramp, or
        pick one of its colors for a single color parm (e.g. a
        material's base color) - each swatch action carries a solid
        color icon so the menu doubles as a preview."""
        proxy_indexes = self.gradient_selection_model.selectedIndexes()
        if len(proxy_indexes) != 1:
            return
        source_index = self.gradient_sorted_model.mapToSource(proxy_indexes[0])
        entry = self.gradient_model.entry(source_index.row())
        if entry is None:
            return
        cmenu = QtWidgets.QMenu(self)
        action_ramp = cmenu.addAction("Apply Ramp")
        # Every gradient is a normal user gradient now (the curated palettes
        # are seeded in). A banded/stepped palette can be reinterpreted as a
        # smooth blend; a gradient already carrying smooth bases keeps its
        # own recorded shape, so only offer "linear" for banded ones.
        action_linear = None
        if gradient_library.GradientLibrary._is_banded(entry):
            action_linear = cmenu.addAction("Apply as Linear Ramp")
        swatch_menu = cmenu.addMenu("Apply Color to Selected Node")
        swatch_actions = {}
        for color in entry["colors"]:
            pixmap = QtGui.QPixmap(14, 14)
            pixmap.fill(QtGui.QColor(color["hex"]))
            act = swatch_menu.addAction(QtGui.QIcon(pixmap), color["name"])
            swatch_actions[act] = color
        action_fav = cmenu.addAction("Toggle Favorite")
        action_edit = cmenu.addAction("Edit Info")
        cmenu.addSeparator()
        action_delete = cmenu.addAction("Delete Gradient")
        action = cmenu.exec_(QtGui.QCursor.pos())
        if action is None:
            return
        if action == action_ramp:
            self._apply_gradient_ramp(entry)
        elif action_linear is not None and action == action_linear:
            self._apply_gradient_ramp(entry, linear=True)
        elif action == action_fav:
            self.gradient_model.toggle_favorite(source_index.row())
        elif action == action_edit:
            self.edit_gradient_info()
        elif action in swatch_actions:
            self._apply_gradient_swatch(swatch_actions[action])
        elif action == action_delete:
            if hou.ui.displayConfirmation(  # type: ignore
                'Delete gradient "%s" from the library?' % entry["name"]
            ):
                self.gradient_model.remove_user_gradient(source_index.row())
                self.gradient_categories_model.refresh()

    def edit_gradient_info(self) -> None:
        """Edit Info dialog for the selected gradient: Name, Category and
        free-text Notes - the same editing every gradient gets, curated
        or saved (curated palettes are ordinary editable entries, just
        prefilled colours - not read-only)."""
        proxy_indexes = self.gradient_selection_model.selectedIndexes()
        if len(proxy_indexes) != 1:
            return
        source_index = self.gradient_sorted_model.mapToSource(proxy_indexes[0])
        row = source_index.row()
        entry = self.gradient_model.entry(row)
        if entry is None:
            return
        dialog = gradient_dialog.GradientInfoDialog(
            self.gradient_model.user_categories(),
            name=entry.get("name", ""),
            category=entry.get("category", ""),
            note=entry.get("note", ""),
        )
        dialog.exec_()
        if dialog.canceled:
            return
        self.gradient_model.update_gradient(
            row, dialog.name, dialog.category, dialog.note
        )
        self.gradient_categories_model.refresh()

    def _selected_scene_node(self) -> hou.Node | None:
        """The single selected scene node, or None (with the user told
        why) - shared guard for the gradient apply actions."""
        sel = hou.selectedNodes()
        if len(sel) != 1:
            hou.ui.displayMessage(  # type: ignore
                "Select a single node in the network editor first."
            )
            return None
        return sel[0]

    def _apply_gradient_ramp(self, entry: dict, linear: bool = False) -> None:
        """Sets the entry onto the selected node's first color ramp
        parm. Curated combinations become STEPPED ramps by default
        (discrete bands - the colors stay readable) or
        smooth LINEAR ramps via the explicit menu action; user gradients
        apply their saved ramp exactly as recorded."""
        node = self._selected_scene_node()
        if node is None:
            return
        parm = helpers.find_color_ramp_parm(node)
        if parm is None:
            hou.ui.displayMessage(  # type: ignore
                f'"{node.name()}" ({node.type().name()}) has no color '
                "ramp parameter to set."
            )
            return
        parm.set(self._entry_ramp(entry, linear))

    @staticmethod
    def _entry_ramp(entry: dict, linear: bool = False) -> hou.Ramp:
        # A banded palette explicitly asked for "linear" is rebuilt as a
        # smooth blend from its colours; otherwise the recorded ramp
        # (bases/keys/values) applies exactly as saved.
        if linear and gradient_library.GradientLibrary._is_banded(entry):
            return helpers.build_linear_ramp([c["hex"] for c in entry["colors"]])
        ramp = entry.get("ramp")
        if ramp:
            return helpers.data_to_ramp(ramp)
        return helpers.build_stepped_ramp([c["hex"] for c in entry["colors"]])


    def _apply_gradient_swatch(self, color: dict) -> None:
        """Sets one swatch onto the selected node's first color parm
        (base color and the like), found generically."""
        node = self._selected_scene_node()
        if node is None:
            return
        parm_tuple = helpers.find_color_parm_tuple(node)
        if parm_tuple is None:
            hou.ui.displayMessage(  # type: ignore
                f'"{node.name()}" ({node.type().name()}) has no color '
                "parameter to set."
            )
            return
        h = color["hex"].lstrip("#")
        parm_tuple.set(
            (
                int(h[0:2], 16) / 255.0,
                int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0,
            )
        )

    def _drop_context_under_cursor(
        self, matcher, include_viewports: bool = False
    ) -> hou.Node | None:
        """Resolve where a drag was RELEASED, for drops Houdini's native
        handling ignores: network editors (the canvas takes no native
        node drops - DRAGTEST log, 2026-07-19). Returns the node to
        import against - the node under the cursor when it matches
        (matcher = a type-name substring like "materiallibrary"/
        "copnet", or a callable(node) -> bool, e.g. geometry's
        SOP-container test), else the editor's own pwd (which is itself
        the target when the user is working inside one).

        include_viewports (geometry drops): a release over a Scene
        Viewer resolves to the network the VIEWPORT is showing (its
        pwd - /obj for the object view, /stage for the Solaris view, a
        geo node's innards at SOP level), so the same context rules
        apply as everywhere else. Materials deliberately keep this off:
        their viewport drops are handled NATIVELY by the mime drag, and
        a native rejection (empty space) must stay a silent miss.

        None = the release wasn't over anything import-worthy."""
        cursor = QtGui.QCursor.pos()
        for pane_tab in hou.ui.paneTabs():  # type: ignore
            try:
                if not pane_tab.isCurrentTab():
                    continue
                geo = pane_tab.qtScreenGeometry()
            except AttributeError:
                continue
            if geo is None or not geo.contains(cursor):
                continue
            if (
                include_viewports
                and pane_tab.type() == hou.paneTabType.SceneViewer
            ):
                try:
                    return pane_tab.pwd()
                except AttributeError:
                    return None
            if pane_tab.type() != hou.paneTabType.NetworkEditor:
                return None
            local_x = cursor.x() - geo.left()
            local_y = geo.height() - (cursor.y() - geo.top())
            node = None
            try:
                hits = pane_tab.networkItemsInBox(
                    hou.Vector2(local_x - 2, local_y - 2),
                    hou.Vector2(local_x + 2, local_y + 2),
                    for_drop=True,
                )
            except (AttributeError, TypeError, hou.OperationFailed):
                hits = ()
            for item in hits:
                candidates = (
                    item if isinstance(item, (tuple, list)) else (item,)
                )
                for candidate in candidates:
                    if isinstance(candidate, hou.Node):
                        node = candidate
                        break
                if node is not None:
                    break
            if node is not None:
                hit = (
                    matcher(node)
                    if callable(matcher)
                    else matcher in node.type().name()
                )
                if hit:
                    return node
            try:
                return pane_tab.pwd()
            except AttributeError:
                return None
        return None

    def drop_cop_at_release(self, index) -> None:
        """Cop drag released: same context rules as double-click, but
        against the network editor under the RELEASE POINT - released
        on a copnet node (or inside a Copernicus network), the saved
        nodes load directly into it; any other network gets a fresh
        container. A release over nothing is silent - a miss is a
        normal drag outcome, not an error."""
        if not self.cop_model:
            return
        context = self._drop_context_under_cursor("copnet")
        if context is None:
            return
        try:
            source_index = self.cop_sorted_model.mapToSource(index)
        except Exception:
            return
        ok, reason = self.cop_model.import_asset_to_scene(
            source_index, context_node=context
        )
        if not ok and reason:
            hou.ui.displayMessage(reason)  # type: ignore

    def _run_material_drag(self, index) -> None:
        """Materials stay on the NATIVE (white-family) drag, not the black
        self-managed one: only a native node QDrag
        makes Houdini's own viewport handler fire its "Drop Actions" menu
        (Set as Material on mesh...) - the self-managed black system
        resolves the drop itself and so can never trigger that menu.

        A fresh copy imports to /mat, then a real QDrag carries
        hou.qt.mimeType.nodePath so Houdini's OWN drop handling covers the
        viewports natively (OBJ shop_materialpath, Solaris materiallibrary
        + assignmaterial). A network-editor release comes back IGNORED, so
        we resolve it ourselves: a LOP context routes into the materiallib
        under the cursor / a new one (staging copy removed first); any
        other network editor keeps the /mat copy; a release over nothing
        deletes the copy silently."""
        if not self.material_model:
            return
        try:
            source_index = self.material_sorted_model.mapToSource(index)
        except Exception:
            return
        mat_net = hou.node("/mat")
        if mat_net is None:
            return
        before = {c.path() for c in mat_net.children()}
        ok, reason = self.material_model.import_asset_to_scene(
            source_index, "mat"
        )
        if not ok:
            if reason:
                hou.ui.displayMessage(reason)  # type: ignore
            return
        mat_net = hou.node("/mat")
        new_nodes = sorted({c.path() for c in mat_net.children()} - before)
        if not new_nodes:
            print("Amaze: material drag - import produced no /mat node")
            return
        copy_node = hou.node(new_nodes[0])

        try:
            mime_type = hou.qt.mimeType.nodePath
        except AttributeError:
            mime_type = "application/sidefx-houdini-node.path"
        drag = QtGui.QDrag(self.thumblist)
        mime = QtCore.QMimeData()
        mime.setData(mime_type, copy_node.path().encode("utf-8"))
        # Native parity: a real node drag also carries the item-path
        # format and plain text - match it fully rather than guess which
        # format each handler reads.
        try:
            mime.setData(
                hou.qt.mimeType.itemPath,
                copy_node.path().encode("utf-8"),
            )
        except AttributeError:
            pass
        mime.setText(copy_node.path())
        drag.setMimeData(mime)
        # Drag PICTURE = the black name tag (shared with the black
        # self-managed system), NOT the thumbnail - "native" is only the
        # drop mechanism, so a native material drag can look identical to
        # a cop/color drag.
        name = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        drag.setPixmap(ui_helpers.name_tag_pixmap(name))
        # Dropped on the sidebar? The CategoryDropFilter recategorises the
        # selection DURING exec() and sets this flag - so afterwards we
        # just discard the throwaway /mat copy (the import happens for
        # every material drag regardless).
        self._drag_hit_sidebar = False
        result = drag.exec(
            QtCore.Qt.DropAction.CopyAction
            | QtCore.Qt.DropAction.MoveAction
            | QtCore.Qt.DropAction.LinkAction
        )
        if self._drag_hit_sidebar:
            try:
                copy_node.destroy()
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
            return
        if result != QtCore.Qt.DropAction.IgnoreAction:
            # A viewport accepted the drop - Houdini did the assignment,
            # and the /mat copy is the material it points at.
            return
        context = self._drop_context_under_cursor("materiallibrary")
        if context is None:
            try:
                copy_node.destroy()
            except (hou.OperationFailed, hou.ObjectWasDeleted):
                pass
            return
        typename = context.type().name()
        try:
            child_cat = context.childTypeCategory().name().lower()
        except Exception:
            child_cat = ""
        is_lop = (
            "stage" in typename
            or "lopnet" in typename
            or "materiallibrary" in typename
            or "lop" in child_cat
        )
        if not is_lop:
            # OBJ/mat-side network editor: the /mat copy IS the import.
            return
        try:
            copy_node.destroy()
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass
        ok, reason = self.material_model.import_asset_to_scene(
            source_index, "auto", context_node=context
        )
        if not ok and reason:
            hou.ui.displayMessage(reason)  # type: ignore

    def drop_code_at_release(self, index, node: hou.Node) -> None:
        """Code snippet drag released (self-managed): apply the snippet to
        the node under the cursor - same as a double-click, but targeting
        where the drag landed. A release over nothing is silent; a node
        with no code/snippet parm reports why."""
        if not self.code_model or index is None or node is None:
            return
        try:
            source_index = self.code_sorted_model.mapToSource(index)
        except Exception:
            return
        ok, reason = self.code_model.apply_to_node(source_index.row(), node)
        if not ok and reason:
            hou.ui.displayMessage(reason)  # type: ignore

    #: Sections whose sidebar holds real, assignable categories. Textures
    #: and Geometry are excluded on purpose - their sidebar is a list of
    #: filesystem FOLDERS, so a drop there would mean moving files on disk,
    #: a different and dangerous operation, not a metadata change.
    CATEGORY_SECTIONS = ("material", "cop", "code", "gradient")

    def _category_under_cursor(self):
        """The assignable category name under the GLOBAL cursor, or None -
        the self-managed drags (cop/color/code) resolve their drop target
        this way. (Materials use the Qt-drop filter, which already has a
        local event position - see _category_at_point.)"""
        if self.cat_list is None or not self.cat_list.isVisible():
            return None
        vp = self.cat_list.viewport()
        pos = vp.mapFromGlobal(QtGui.QCursor.pos())
        if not vp.rect().contains(pos):
            return None
        return self._category_at_point(pos)

    def _droppable_category_index(self, pos):
        """The sidebar index under a sidebar-local point IF it's a
        droppable category - not 'All', and only in a section with real
        categories. Else None. The single place the drop-target validity
        rules live."""
        if self.current_section not in self.CATEGORY_SECTIONS:
            return None
        index = self.cat_list.indexAt(pos)
        if not index.isValid():
            return None
        name = index.data(QtCore.Qt.ItemDataRole.DisplayRole)
        if not name or name == "All":
            return None
        if self.current_section == "gradient":
            # Defensive: every gradient category listed is a real, editable
            # user category now (the palettes are seeded as such), but guard
            # in case the sidebar ever lists synthetic rows again.
            if name not in self.gradient_model.user_categories():
                return None
        return index

    def _category_at_point(self, pos):
        """The droppable category NAME at a sidebar-local point (a
        QDropEvent position), or None - used by the sidebar drop target."""
        index = self._droppable_category_index(pos)
        if index is None:
            return None
        return index.data(QtCore.Qt.ItemDataRole.DisplayRole)

    def _set_drag_hover_row(self, row: int) -> None:
        """Highlight (row) or clear (row=-1) the sidebar category being
        dragged over, in the accent/select purple - drop-target feedback."""
        if getattr(self, "sidebar_delegate", None) is None:
            return
        if self.sidebar_delegate.drag_row != row:
            self.sidebar_delegate.drag_row = row
            if self.cat_list is not None:
                # repaint(), not update(): a material's native QDrag.exec()
                # runs macOS's own drag loop, which doesn't process Qt's
                # DEFERRED paint events (from update()) until the drag ends
                # - so the highlight never showed. repaint() forces the
                # paint synchronously, right now, inside the drag loop.
                self.cat_list.viewport().repaint()

    def _update_category_drag_hover(self, pos) -> None:
        """Set the drag-hover highlight from a sidebar-local point."""
        index = self._droppable_category_index(pos)
        self._set_drag_hover_row(index.row() if index is not None else -1)

    def _update_category_drag_hover_global(self) -> None:
        """Drag-hover highlight for the SELF-MANAGED drags (cop/color/code,
        which have no Qt drag events) - maps the global cursor into the
        sidebar and highlights whatever droppable category it's over."""
        if self.cat_list is None or not self.cat_list.isVisible():
            self._set_drag_hover_row(-1)
            return
        vp = self.cat_list.viewport()
        pos = vp.mapFromGlobal(QtGui.QCursor.pos())
        if not vp.rect().contains(pos):
            self._set_drag_hover_row(-1)
            return
        self._update_category_drag_hover(pos)

    def _can_drop_category(self, event) -> bool:
        """The sidebar accepts a drop only from OUR OWN grid, and only in a
        section with real categories - so a node dragged in from a Houdini
        network editor still falls through to the save-node handler."""
        return (
            self.current_section in self.CATEGORY_SECTIONS
            and event.source() is self.thumblist
        )

    def _handle_category_drop(self, event) -> bool:
        """A grid drag was dropped on the sidebar. Recategorise the
        selection if it landed on a real category; consume the drop either
        way (even over 'All' or empty space) so it never reaches the
        central widget's save-node flow. Sets a flag so the material
        native drag knows to discard its throwaway /mat copy."""
        if not self._can_drop_category(event):
            return False
        self._drag_hit_sidebar = True
        category = self._category_at_point(event.position().toPoint())
        if category is not None:
            self.assign_category_active(category)
        return True

    def _node_under_cursor(self) -> hou.Node | None:
        """The scene node the cursor is over. In a network editor: the
        node under the mouse via networkItemsInBox at the pane-local
        point - LOWER-LEFT origin in plain logical pixels, hits arriving
        as (item, ...) tuples (both confirmed by live console probes,
        2026-07-19). Over a Parameter Editor: the node whose parameters
        that pane is showing - dropping a gradient on the parm pane you
        are already looking at beats hunting the node, especially for
        ramps. Geometric pane detection throughout; known
        narrow trade-off: no z-order awareness, so two genuinely
        overlapping panes could match the wrong one."""
        cursor = QtGui.QCursor.pos()
        for pane_tab in hou.ui.paneTabs():  # type: ignore
            try:
                pane_type = pane_tab.type()
                if not pane_tab.isCurrentTab():
                    continue
                geo = pane_tab.qtScreenGeometry()
            except AttributeError:
                continue
            if geo is None or not geo.contains(cursor):
                continue
            if pane_type == hou.paneTabType.Parm:
                try:
                    return pane_tab.currentNode()
                except (AttributeError, hou.OperationFailed):
                    return None
            if pane_type != hou.paneTabType.NetworkEditor:
                continue
            local_x = cursor.x() - geo.left()
            local_y = geo.height() - (cursor.y() - geo.top())
            try:
                hits = pane_tab.networkItemsInBox(
                    hou.Vector2(local_x - 2, local_y - 2),
                    hou.Vector2(local_x + 2, local_y + 2),
                    for_drop=True,
                )
            except (AttributeError, TypeError, hou.OperationFailed):
                return None
            for item in hits:
                candidates = (
                    item if isinstance(item, (tuple, list)) else (item,)
                )
                for candidate in candidates:
                    if isinstance(candidate, hou.Node):
                        return candidate
            return None
        return None

    def apply_gradient_to_node(
        self, index: QtCore.QModelIndex, node: hou.Node
    ) -> None:
        """Drag-drop completion for the Gradients section: apply the
        dragged combination to the node the drag was released over.
        A release over empty canvas is silent (a miss is a normal drag
        outcome, not an error) - but a release ON a node that has no
        color ramp parm reports why nothing happened, since that was a
        deliberate target."""
        if index is None or not index.isValid():
            return
        source_index = self.gradient_sorted_model.mapToSource(index)
        entry = self.gradient_model.entry(source_index.row())
        if entry is None:
            return
        parm = helpers.find_color_ramp_parm(node)
        if parm is None:
            hou.ui.displayMessage(  # type: ignore
                f'"{node.name()}" ({node.type().name()}) has no color '
                "ramp parameter to set."
            )
            return
        parm.set(self._entry_ramp(entry))

    def save_gradient_from_node(self, node: hou.Node | None = None) -> None:
        """"Save Gradient to AssetLib" (node right-click, or any caller
        with a ramp-bearing node): serializes the node's first color
        ramp and registers it as a user gradient in the Gradients
        section, in a category chosen (or created) in the save dialog.
        Follows the material save flow's conventions - selection-based
        fallback, specific error messages instead of silent no-ops."""
        if not self.material_model:
            hou.ui.displayMessage(  # type: ignore
                "Please set a library first. Use the AssetLib panel - "
                "Library/Open Dialog."
            )
            return
        if node is None:
            sel = hou.selectedNodes()
            if len(sel) != 1:
                hou.ui.displayMessage(  # type: ignore
                    "Select a single node with a color ramp first."
                )
                return
            node = sel[0]
        parm = helpers.find_color_ramp_parm(node)
        if parm is None:
            hou.ui.displayMessage(  # type: ignore
                f'"{node.name()}" ({node.type().name()}) has no color '
                "ramp parameter to save."
            )
            return
        ramp_data = helpers.ramp_to_data(parm.evalAsRamp())
        dialog = gradient_dialog.GradientDialog(
            self.gradient_model.user_categories(), default_name=node.name()
        )
        dialog.exec_()
        if dialog.canceled:
            return
        self.gradient_model.add_user_gradient(
            dialog.name, dialog.category, ramp_data
        )
        self.gradient_categories_model.refresh()

    def _on_texture_progress(self, done: int, total: int) -> None:
        """Shows/updates the thin progress bar above the thumbnail grid
        while texture thumbnails are generating for the selected folder.
        Hidden when there's nothing to do (fully cached / empty folder)
        or once generation completes."""
        if total <= 0 or done >= total:
            self.texture_progress.setVisible(False)
            return
        self.texture_progress.setVisible(True)
        self.texture_progress.set_progress(done, total)

    def _on_online_preview_progress(self, done: int, total: int) -> None:
        """Same bar, for the online preview pool - but only while the
        online browser is actually showing. Previews load lazily, so a
        worker finishing after you've switched away must not flash the bar
        over another section."""
        if not self._is_online():
            return
        if getattr(self, "_online_download_active", False):
            return  # a download import owns the bar right now
        self._on_texture_progress(done, total)

    def _effective_star_color(self) -> str:
        """The favorite badge's fill per the star_color_mode pref:
        background = the stamped-hole grid color, yellow = the classic
        amber, custom = the user-picked hex."""
        mode = getattr(self.prefs, "star_color_mode", "background")
        if mode == "yellow":
            return theme.color_hex("star")
        if mode == "custom":
            return getattr(self.prefs, "star_custom_color", "#fcb900")
        return AssetItemDelegate.STAR_HOLE_COLOR

    def _active_asset_stack(self):
        """(model, proxy, selection model, category model) of whichever
        curated-library section is showing (Materials / Cop / Code), or
        None for a folder/gradient section or before setup. The section
        object owns this now - see panel/sections.py AssetSection.stack."""
        section = self._section()
        return section.stack() if section is not None else None

    def filter_thumb_view(self) -> None:
        """Search box changed - the active section applies it."""
        if self._is_online():
            # Online browsing searches the SOURCE's API, not a local
            # model - the whole catalogue is never resident.
            self.matx_online_model.set_search(self.line_filter.text())
            return
        section = self._section()
        if section is not None:
            section.filter_text(self.line_filter.text())

    def filter_favs(self) -> None:
        """Favourites star toggled - the active section applies it."""
        if self._is_online():
            # Belt and braces: the button is disabled online, but a
            # stale checked state must never filter the material proxy
            # while the online grid is showing.
            return
        section = self._section()
        if section is not None:
            section.filter_favorites(self.cb_favsonly.isChecked())

    def renderer_menu_changed(self, action) -> None:
        """Renderer picked in the menubar: apply filter and remember it"""
        self.filter_renderer()
        self.prefs.last_renderer = action.text()
        self.prefs.save()

    def filter_renderer(self) -> None:
        """Get Filter from user and trigger view update"""
        if not self.material_model or not self.category_model:
            return
        checked = self.renderer_action_group.checkedAction()
        render_filter = checked.text() if checked is not None else "All"
        if render_filter == "All":
            render_filter = "all_renderers"
        self.material_sorted_model.setFilter(
            self.material_model.RendererRole, render_filter
        )
        self.material_sorted_model.sort(0)
        # Renderer-aware empty-category hiding: the sidebar only lists
        # categories with at least one material visible under this
        # renderer filter, and the counts follow the same rule - push
        # the filter into the category model and re-evaluate. If the
        # category the user was standing in just vanished, fall back
        # to All so the grid never silently shows a stale filter.
        self.category_model.set_renderer_filter(render_filter)
        self._refresh_sidebar_categories()
        self._ensure_material_sidebar_selection()

    def _refresh_sidebar_categories(self) -> None:
        """Re-evaluate empty-category hiding and counts after anything
        that changed which materials/COPs exist, what they belong to,
        or which renderer filter is active. Flows that already emit
        category_model.layoutChanged refilter automatically - this is
        for the ones that don't (deletes, overwrite, renderer switch)."""
        for cats_model in (
            getattr(self, "category_model", None),
            getattr(self, "cop_category_model", None),
            getattr(self, "code_category_model", None),
        ):
            if cats_model is not None:
                cats_model.drop_count_cache()
        proxy = getattr(self, "category_sorted_model", None)
        if proxy is not None:
            proxy.invalidateFilter()
        cop_proxy = getattr(self, "cop_category_sorted_model", None)
        if cop_proxy is not None:
            cop_proxy.invalidateFilter()
        code_proxy = getattr(self, "code_category_sorted_model", None)
        if code_proxy is not None:
            code_proxy.invalidateFilter()
        if self.cat_list is not None:
            self.cat_list.viewport().update()

    def _ensure_material_sidebar_selection(self) -> None:
        """If the renderer filter just hid the sidebar category the
        user was standing in, fall back to All and refilter the grid -
        the sidebar must never sit with an empty/hidden selection."""
        if self.current_section != "material":
            return
        if (
            self.cat_list is None
            or self.cat_list.model() is not self.category_sorted_model
        ):
            return
        selection_model = self.cat_list.selectionModel()
        if selection_model is None:
            return
        indexes = self.cat_list.selectedIndexes()
        if indexes and indexes[0].isValid():
            # The selected category survived the refilter (proxy
            # selections track items, not row numbers).
            return
        for row in range(self.category_sorted_model.rowCount()):
            idx = self.category_sorted_model.index(row, 0)
            if idx.data() == "All":
                self.cat_list.setCurrentIndex(idx)
                selection_model.select(
                    idx,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
                break
        self.update_selected_cat()

    def user_update_asset(self) -> None:
        """User modifies an assete in the detailview"""
        if not self.material_model or not self.category_model:
            return
        indexes = self.material_selection_model.selectedIndexes()
        # About/license are per-material provenance - only save them for a
        # single selection, so editing a multi-selection can't overwrite
        # everyone's credits with one material's text (None = keep).
        single = len(indexes) == 1
        about = self.text_about.toPlainText() if single else None
        license_ = self.line_license.text() if single else None
        self.material_model.layoutAboutToBeChanged.emit()
        self.category_model.layoutAboutToBeChanged.emit()

        for index in indexes:
            idx = self.material_model.index(
                self.material_sorted_model.mapToSource(index).row()
            )

            name = self.line_name.text()
            tags = self.line_tags.text()
            cats = self.cat_combo.currentText()
            fav = self.box_fav.isChecked()
            self.category_model.check_add_category(cats)
            self.material_model.set_assetdata(
                idx, name, cats, tags, fav, about=about, license=license_
            )
        self.material_model.layoutChanged.emit()
        self.category_model.layoutChanged.emit()

    def _refresh_cat_combo(self) -> None:
        """Repopulate the category dropdown from the current category list"""
        current = self.cat_combo.currentText()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItems(self.get_category_names())
        i = self.cat_combo.findText(current)
        if i >= 0:
            self.cat_combo.setCurrentIndex(i)
        self.cat_combo.blockSignals(False)

    def update_details_view(self) -> None:
        """Update upon changes in Detail view"""
        if self.current_section != "material":
            return
        if not self.material_model or not self.category_model:
            return
        if not self.material_selection_model.hasSelection():
            self.line_name.setText("")
            self.line_id.setText("")
            self.line_date.setText("")
            self.line_renderer.setText("")
            self.line_tags.setText("")
            self.line_license.setText("")
            self.text_about.setPlainText("")
            self.box_fav.setCheckState(QtCore.Qt.CheckState.Unchecked)
            return

        indexes = self.material_selection_model.selectedIndexes()

        asset_id = ""
        name = ""
        date = ""
        sel_cats = []
        sel_tags = []
        fav = []
        for pos, idx in enumerate(indexes):
            curr_asset = self.material_model.index(
                self.material_sorted_model.mapToSource(idx).row()
            )
            name = curr_asset.data(QtCore.Qt.ItemDataRole.DisplayRole)
            asset_id = curr_asset.data(self.material_model.IdRole)
            date = curr_asset.data(self.material_model.DateRole)

            for cat in curr_asset.data(self.material_model.CategoryRole):
                sel_cats.append(cat)
                # for tag in curr_asset.data(self.material_model.TagRole):
            sel_tags.append(curr_asset.data(self.material_model.TagRole))

            fav.append(curr_asset.data(self.material_model.FavoriteRole))

        clean_name = name
        msg = "Multiple Values..." if len(indexes) > 1 else clean_name
        self.line_name.setText(msg)

        msg = "Multiple Values..." if len(indexes) > 1 else asset_id
        self.line_id.setText(msg)

        msg = "Multiple Values..." if len(indexes) > 1 else date
        self.line_date.setText(msg)

        if len(indexes) > 1:
            self.line_renderer.setText("Multiple Values...")
        else:
            self.line_renderer.setText(
                curr_asset.data(self.material_model.RendererLabelRole) or ""
            )

        msg = (
            QtCore.Qt.CheckState.Checked
            if fav[0] is True
            else QtCore.Qt.CheckState.Unchecked
        )
        for f in fav:
            if f != fav[0]:
                msg = QtCore.Qt.CheckState.PartiallyChecked
                break
        self.box_fav.setCheckState(msg)

        self._refresh_cat_combo()
        # Single category per asset now. Show it in the dropdown; a mixed
        # multi-selection just shows the first item's category as the
        # editable value (updating applies it to all selected).
        cats_clean = [str(c).strip() for c in sel_cats if c and str(c).strip()]
        single = cats_clean[0] if cats_clean else ""
        i = self.cat_combo.findText(single)
        if i >= 0:
            self.cat_combo.setCurrentIndex(i)

        if sel_tags:
            # dict.fromkeys dedupes while preserving order; a plain set()
            # here made displayed tag order reshuffle unpredictably.
            msg = ", ".join(dict.fromkeys(filter(None, sel_tags[0])))
            if len(sel_tags) > 1:
                for elem in sel_tags:
                    if elem != sel_tags[0]:
                        msg = "Multiple Values..."
            self.line_tags.setText(msg)
        else:
            self.line_tags.setText("")

        # Provenance (per-material) - only meaningful for a single
        # selection; blanked for a multi-selection so nothing is shown as
        # shared that isn't (and user_update_asset won't overwrite it).
        if len(indexes) == 1:
            src_row = self.material_sorted_model.mapToSource(indexes[0]).row()
            asset = self.material_model.assets[src_row]
            self.line_license.setText(asset.license)
            self.text_about.setPlainText(asset.about)
        else:
            self.line_license.setText("")
            self.text_about.setPlainText("")

    # Update the Views when selection changes
    def update_selected_cat(self) -> None:
        """Update thumb view on change of category (Materials) or browse
        the selected folder's images (Textures)."""
        # A ctrl-click on the already-selected row DEselects it, leaving
        # the grid showing contents with nothing highlighted - makes no
        # sense for a category/folder list, so it is removed. Qt has
        # no "single selection but never empty" mode, so an emptied
        # selection is simply re-selected in place; the active category
        # never actually changed, so nothing else needs to run.
        if self._is_online():
            sel = self.cat_list.selectedIndexes()
            if sel:
                cat = self.matx_source_model.category_at(sel[0].row())
                if cat is None:
                    self.matx_sorted_model.removeFilter(
                        self.matx_online_model.CategoryRole
                    )
                else:
                    self.matx_sorted_model.setFilter(
                        self.matx_online_model.CategoryRole, cat
                    )
                self.thumblist.scrollToTop()
            return
        indexes = self.cat_list.selectedIndexes()
        if not indexes:
            current = self.cat_list.currentIndex()
            selection_model = self.cat_list.selectionModel()
            if current.isValid() and selection_model is not None:
                selection_model.select(
                    current,
                    QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )
            return

        section = self._section()
        if section is not None:
            section.select_category(indexes[0])

    # Library Stuffs
    def update_all_assets(self) -> None:
        """Rerenders all currently visible (filtered) assets in the library -
        The UI is blocked for the duration of the render"""
        if self._is_online():
            # The dialog promises "all visible assets", but the visible
            # grid is a remote catalogue - unguarded, this rendered every
            # material in the local library instead, blocking Houdini for
            # the duration, while the user looked at online results.
            hou.ui.displayMessage(  # type: ignore
                "Render All Thumbnails works on the library. Turn off "
                "View > Online Materials first."
            )
            return
        stack = self._active_asset_stack()
        if stack is None:
            hou.ui.displayMessage("Please open a library first")  # type: ignore
            return
        model, proxy, _selection, _categories = stack

        if not hou.ui.displayConfirmation(
            """This can take a long time to render. Houdini will not be responsive during that time.
            Do you want continue rendering all visible assets?"""  # type: ignore
        ):  # type: ignore
            return
        model.layoutAboutToBeChanged.emit()
        # Iterate the filtered proxy, not the unfiltered source model, so
        # this actually matches the "visible assets" wording above - and
        # the ACTIVE stack, so it rerenders Cop thumbnails when the Cop
        # section is showing.
        indexes = [
            proxy.mapToSource(proxy.index(row, 0))
            for row in range(proxy.rowCount())
        ]
        # Batch path reuses ONE Karma scaffold across the whole run (the
        # shaderball USD loads once, not per material) - a big win for
        # Karma-heavy libraries. Non-Karma/COP fall back to per-item
        # inside the batch. Older models without the method loop as
        # before.
        if hasattr(model, "render_thumbnails_batch"):
            model.render_thumbnails_batch(indexes)
        else:
            for source_index in indexes:
                model.render_thumbnail(source_index)
        model.layoutChanged.emit()
        # No "finished" dialog - the refreshed grid IS the report
        # (having to click OK after a render is a wrong use of
        # a dialog). Dialogs here only ever ASK (the confirmation
        # above), never applaud.

    # Rerender Selected Asset
    def update_single_asset(self) -> None:
        """Rerenders a single Asset in the library
        The UI is blocked for the duration of the render"""
        if not self.material_model or not self.category_model:
            return
        indexes = self.material_selection_model.selectedIndexes()
        self.material_model.layoutAboutToBeChanged.emit()
        for index in indexes:
            idx = self.material_sorted_model.mapToSource(index)
            self.material_model.render_thumbnail(idx)
        self.material_model.layoutChanged.emit()
        # No "updated" dialog - the fresh thumbnail on screen is the
        # confirmation (bad UI to require an OK click here).

    def delete_asset(self) -> None:
        """Deletes the selected material from Disk and Library"""
        if not hou.ui.displayConfirmation(
            "This will delete the selected material(s) from Disk. Are you sure?"  # type: ignore
        ):
            return
        if not self.material_model or not self.category_model:
            return
        indexes = self.material_selection_model.selectedIndexes()
        self.material_model.layoutAboutToBeChanged.emit()

        real_indexes = []
        for index in indexes:
            idx = self.material_sorted_model.mapToSource(index)
            real_indexes.append(idx)

        real_indexes.sort(key=lambda idx: idx.row(), reverse=True)
        for idx in real_indexes:
            self.material_model.remove_asset(idx)

        self.material_model.layoutChanged.emit()
        # A category may just have emptied - re-evaluate sidebar hiding.
        self._refresh_sidebar_categories()

    #  Saves a material to the Library
    def save_asset(self) -> None:
        """Saves the selected nodes (Network Editor) to the Library.

        Standard file-save semantics (by design): if the selected
        node matches an EXISTING library material - via the id stamp a
        previous save/import left on it, or a unique name match - offer
        Update Existing / Save as New / Cancel; otherwise go straight to
        the normal new-material dialog. Multi-selections always save new
        materials, as before."""
        # Get Selected from Network View
        sel = hou.selectedNodes()
        # Check selection
        if not sel:
            hou.ui.displayMessage("No material selected")  # type: ignore
            return
        if not self.material_model:
            hou.ui.displayMessage(
                "Please set a library first. Use the AssetLib panel - Library/Open Dialog."  # type: ignore
            )
            return
        if len(sel) == 1:
            row = self._find_existing_asset_row(sel[0])
            if row >= 0:
                name = self.material_model.data(
                    self.material_model.index(row, 0),
                    QtCore.Qt.ItemDataRole.DisplayRole,
                )
                choice = hou.ui.displayMessage(  # type: ignore
                    '"%s" already exists in the library.' % name,
                    buttons=("Overwrite", "Save as New", "Cancel"),
                    default_choice=0,
                    close_choice=2,
                    title="Save to AssetLib",
                )
                if choice == 2:
                    return
                if choice == 0:
                    self._update_existing_asset(row, sel[0])
                    return
        self.get_material_info_user(sel)

    def _find_existing_asset_row(self, node: hou.Node) -> int:
        """Source-model row of the library material this node came from,
        or -1. The id stamp (setUserData on save/import) is authoritative;
        a UNIQUE name match is the fallback for nodes imported before
        stamping existed."""
        mat_id = node.userData("assetlib_id")
        if mat_id:
            row = self.material_model.find_asset_row_by_id(mat_id)
            if row >= 0:
                return row
        return self.material_model.find_asset_row_by_name(node.name())

    def _update_existing_asset(self, row: int, node: hou.Node) -> None:
        """Overwrite an existing library entry's content from the scene
        node: same entry/metadata, new node files + thumbnail + type."""
        self.material_model.layoutAboutToBeChanged.emit()
        renderer = self.material_model.update_asset_content(row, node)
        self.material_model.layoutChanged.emit()
        # Overwrite can re-detect a different renderer - counts and
        # renderer-aware hiding may shift.
        self._refresh_sidebar_categories()
        if not renderer:
            hou.ui.displayMessage(
                "Update failed - the library material was not changed."  # type: ignore
            )
            return
        self.enable_renderer_on_add(renderer)
        self.prefs.save()
        self.update_renderer_toggles()

    def get_material_info_user(self, sel: list[hou.Node]) -> None:
        if not self.material_model or not self.category_model:
            return
        """Query user for input upon material-save"""
        # Get Stuff from User
        self.usd_dialog_category_model = QtCore.QSortFilterProxyModel()
        # Source model, NOT the sidebar proxy: the sidebar hides empty
        # categories, but the save dialog must offer every category
        # (this proxy sorts and All-filters on its own regardless).
        self.usd_dialog_category_model.setSourceModel(self.category_model)
        usd_filter = "^(?!All).*$"
        self.usd_dialog_category_model.setFilterRegularExpression(usd_filter)
        self.usd_dialog_category_model.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)  # type: ignore
        self.usd_dialog_category_model.sort(0)

        cats = []
        for elem in range(self.usd_dialog_category_model.rowCount()):
            idx = self.usd_dialog_category_model.index(elem, 0)
            cats.append(self.usd_dialog_category_model.data(idx))

        # Default the dialog to the category currently selected in the
        # panel (skip the "All" pseudo-category and empty selections).
        current_cat = ""
        cat_selection = self.cat_list.selectedIndexes()
        if cat_selection:
            selected_name = cat_selection[0].data()
            if selected_name and selected_name != "All":
                current_cat = selected_name

        dialog = usd_dialog.UsdDialog(cats, current_cat)
        r = dialog.exec_()

        if dialog.canceled or not r:
            return

        # Check if Category or Tags already exist
        if dialog.categories:
            self.category_model.check_add_category(dialog.categories)
        if dialog.tags:
            self.material_model.check_add_tags(dialog.tags)

        self.material_model.layoutAboutToBeChanged.emit()
        self.category_model.layoutAboutToBeChanged.emit()
        renderers = []
        for asset in sel:
            renderer = self.material_model.add_asset(
                asset, dialog.categories, dialog.tags, dialog.fav
            )
            renderers.append(renderer)

        for renderer in renderers:
            self.enable_renderer_on_add(renderer)
        self.prefs.save()
        self.update_renderer_toggles()

        self.material_model.layoutChanged.emit()
        self.category_model.layoutChanged.emit()

    def enable_renderer_on_add(self, renderer: str) -> None:
        if "MtlX" in renderer:
            # Checked BEFORE the Karma family: MtlX is behaviourally
            # Karma but has its own visibility toggle.
            self.prefs.renderer_mtlx_enabled = True
        elif material.is_karma_renderer(renderer):
            self.prefs.renderer_matx_enabled = True
        elif "Mantra" in renderer:
            self.prefs.renderer_mantra_enabled = True
        elif "Redshift" in renderer:
            self.prefs.renderer_redshift_enabled = True
        elif "Octane" in renderer:
            self.prefs.renderer_octane_enabled = True
        self.prefs.save()

    def import_asset(self, target: str = "auto"):
        """Import the selected materials.

        target: "auto" lets MatLib decide from the active network editor
        (double-click); "mat" forces /mat; "lop" forces a LOP
        materiallibrary. Materials that cannot live in the requested context
        are skipped and collected into a single summary dialog."""
        if not self.material_model or not self.category_model:
            return
        failures = []
        for index in self.thumblist.selectedIndexes():
            source_index = self.material_sorted_model.mapToSource(index)
            try:
                ok, reason = self.material_model.import_asset_to_scene(
                    source_index, target
                )
            except Exception as e:
                # An unexpected failure (corrupt .interface file, unusual
                # node structure) previously surfaced as a raw traceback
                # instead of joining the normal per-material failure
                # report; catch it here so the rest of the selection still
                # gets a chance to import.
                try:
                    name = self.material_model.assets[source_index.row()].name
                except Exception:
                    name = "material"
                failures.append(f'"{name}" failed to import: {e}')
                continue
            if not ok and reason:
                failures.append(reason)
        if failures:
            hou.ui.displayMessage(  # type: ignore
                "Some materials could not be imported:\n\n" + "\n".join(failures)
            )

    def import_asset_auto(self, index: QtCore.QModelIndex | None = None):
        """Double-click handler, shared across sections since thumblist is
        reused for all of them. Materials: context-aware import (the model
        index isn't used here - it never reaches the "auto"/"mat"/"lop"
        target argument that import_asset() expects a string for).
        Textures: push the double-clicked file's path onto a selected
        texture node's image parm - here the index *is* what's needed, to
        know which file was double-clicked."""
        if self._is_online():
            # Without this the online branch fell through to Materials,
            # and import_asset() reads the MATERIAL selection model -
            # which still holds whatever was selected before going
            # online, so double-clicking imported an unrelated local
            # material. Double-click is "the primary action" in every
            # section; here that is importing the record.
            if index is not None and index.isValid():
                source = self.matx_sorted_model.mapToSource(index)
                record = self.matx_online_model.record(source.row())
                if record is not None:
                    self._import_online_records([record])
            return
        section = self._section()
        if section is not None:
            section.double_click(index)

    def _apply_texture_to_node(self, node: hou.Node, path: str) -> None:
        """Shared by set_texture_on_selected_node (double-click and the
        "Load to Node" right-click action): finds a file parm on node
        generically via helpers.find_file_parm() (any file-reference-type
        string parm), not a hardcoded per-renderer lookup - this covers
        Karma, Redshift, Octane, Copernicus/COP file nodes and anything
        else with a file-browse parm without needing to special-case
        each one. No renderer is excluded here (Arnold included) since
        this mechanism carries no renderer-specific knowledge at all to
        exclude one with."""
        parm = helpers.find_file_parm(node)
        if parm is None:
            hou.ui.displayMessage(  # type: ignore
                f'"{node.name()}" ({node.type().name()}) has no '
                "file/image parameter to set."
            )
            return
        parm.set(path)

    def set_texture_on_selected_node(self, index: QtCore.QModelIndex | None) -> None:
        """Double-click in the Textures section: push the file's path onto
        the file parm of whichever single node is currently selected in
        the scene - hou.selectedNodes() is the same source save_asset()
        already uses for "what node is the user pointing at"."""
        if index is None or not index.isValid():
            return
        path = index.data(self.texture_files_model.PathRole)
        if not path:
            return

        sel = hou.selectedNodes()
        if len(sel) != 1:
            hou.ui.displayMessage(  # type: ignore
                "Select a single node with a file/image parameter first."
            )
            return
        self._apply_texture_to_node(sel[0], path)

    def import_asset_to_mat(self):
        """Explicitly import the selected materials into /mat."""
        self.import_asset("mat")

    def import_asset_to_lop(self):
        """Explicitly import the selected materials into a LOP materiallibrary."""
        self.import_asset("lop")

    def slide(self) -> None:
        """Set IconSize via Slider - writes to the ACTIVE view mode's own
        persisted size (grid and list are independent)."""
        if not self.material_model or not self.category_model:
            return
        value = self.click_slider.value()
        if self.prefs.view_mode == "list":
            self.prefs.thumbsize_list = value
        else:
            self.prefs.thumbsize = value
        # Persist debounced (500ms after the last slider tick) - saving
        # settings.json on every pixel of a drag would thrash a file
        # that lives in the cloud-synced install folder.
        self._thumbsize_save_timer.start()
        self.material_model.thumbsize = value

        # Apply sizing for the active mode (grid grows icons, list grows rows).
        self.apply_view_mode()
        # Also need to resize the images!
        self.material_model.set_custom_iconsize(QtCore.QSize(value, value))

    def box_fav_clicktoggle(self):
        if self.box_fav.checkState() == QtCore.Qt.CheckState.PartiallyChecked:
            self.box_fav.nextCheckState()
