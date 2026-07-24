"""
Holds useful UI Elements
"""

import os

from PySide6 import QtWidgets, QtCore, QtGui, QtSvg

from matlib.helpers import theme


# The drag "name tag" - black rectangle, white text. Shared by BOTH the
# black self-managed drag's floating label (cop/color/code) AND the
# native drags' pixmap (materials), so every drag looks identical even
# though the mechanisms differ - "native" is only the drop mechanism,
# the picture is a separate choice.
DRAG_TAG_STYLE = (
    "background-color: #2d2d2d; color: #e6e6e6;"
    " border: 1px solid #555555; padding: 2px 8px;"
)


def name_tag_pixmap(name: str) -> QtGui.QPixmap:
    """The black-rectangle/white-text drag tag as a PIXMAP - the drag
    picture for the native drags (materials, and textures/geometry if
    wanted), matching the black system's floating label via the shared
    DRAG_TAG_STYLE."""
    label = QtWidgets.QLabel(str(name))
    label.setStyleSheet(DRAG_TAG_STYLE)
    label.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    label.adjustSize()
    label.ensurePolished()
    return label.grab()


def render_svg_pixmap(path, size, color_replacements=None):
    """Renders an SVG file onto a transparent square QPixmap, optionally
    swapping literal color strings in the SVG text first (the icon assets
    bake tint-target hexes like #5d7abd for exactly this). QSvgRenderer
    straight onto our own transparent-filled pixmap, never QIcon's own
    SVG engine - that engine's internal rasterization produced an opaque
    black background even onto a transparent destination. Returns a
    blank transparent pixmap if the file is missing, so callers
    degrade gracefully."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    if path and os.path.exists(path):
        with open(path, "r") as f:
            text = f.read()
        for old, new in (color_replacements or {}).items():
            text = text.replace(old, new)
        painter = QtGui.QPainter(pixmap)
        QtSvg.QSvgRenderer(QtCore.QByteArray(text.encode("utf-8"))).render(painter)
        painter.end()
    return pixmap


class ClickSlider(QtWidgets.QSlider):
    """
    The slider provides continuous updates on slideing
    and allows for snapping to mouse on click. Paints its own groove and
    handle (Houdini 22 style) instead of relying on QSlider's
    sub-page/add-page stylesheet selectors - those rendered unpredictably
    across styles/platforms (colors landed on the correct side, but their
    declared heights did not), so this draws deterministically instead.

    Dragging is free/continuous everywhere except within SNAP_RADIUS
    units of one of SNAP_MARKS, where the value locks exactly onto that
    mark - a magnet zone around each reference point (not a snap grid
    across the whole range). Small tick marks on the track show where
    each of those reference points is.
    """

    # Colors per the "ui_wireframe 2 only menu" design file (2026-07-19).
    # LEFT_COLOR doubles as the project accent default; runtime overrides
    # it from prefs.accent_color via set_accent_color() regardless.
    LEFT_COLOR = QtGui.QColor("#5d7abd")
    LEFT_WIDTH = 3
    RIGHT_COLOR = QtGui.QColor("#434343")
    RIGHT_WIDTH = 3
    HANDLE_COLOR = QtGui.QColor("#777f95")
    # Qt's pen for an ellipse is centered on its geometric edge, so the
    # border eats inward into the fill by roughly half its width rather
    # than sitting outside it - bumped the diameter up by 1 to compensate.
    HANDLE_DIAMETER = 11
    # Same grey as the toolbar-row background (panel.py), so the handle
    # border reads as punched out of the bar.
    HANDLE_BORDER_COLOR = QtGui.QColor("#2d2d2d")
    HANDLE_BORDER_WIDTH = 1
    DEFAULT_VALUE = 256
    SNAP_MARKS = (128, 256, 384)
    SNAP_RADIUS = 5
    TICK_COLOR = QtGui.QColor("#696969")
    # Pixel-art "circle" for the snap-mark tick, not a smooth ellipse -
    # exactly 5 pixels (center, up, down, left, right), not a thicker
    # diamond. Each "X" is one TICK_PIXEL_SIZE x TICK_PIXEL_SIZE square.
    TICK_PIXEL_SIZE = 1
    TICK_PATTERN = (
        ".X.",
        "XXX",
        ".X.",
    )

    def __init__(self) -> None:
        super(ClickSlider, self).__init__()
        # Instance-level so Preferences > Appearance > Accent Color can
        # override the class default per-panel without needing a subclass.
        self.left_color = QtGui.QColor(self.LEFT_COLOR)
        # Instance-level too: the snap marks (and their painted tick
        # dots) belong to the toolbar's thumbnail-size slider - other
        # uses (the Preferences parameter rows) set this to () for a
        # plain slider with no dots and no magnet zones.
        self.snap_marks = tuple(self.SNAP_MARKS)

    def set_accent_color(self, color: QtGui.QColor) -> None:
        """Overrides the filled (left) segment color and repaints."""
        self.left_color = QtGui.QColor(color)
        self.update()

    def _x_for_value(self, value: float) -> float:
        """X position for an arbitrary value using the same mapping as
        _handle_x() - shared so the default-value tick mark lines up
        exactly with where the handle would sit at that value. Inset by
        the handle radius on both ends so a circle centred here always
        stays fully inside the widget - without this, the centre reaches
        all the way to x=0/x=width at the value extremes and half of it
        gets clipped outside the widget bounds."""
        span = self.maximum() - self.minimum()
        fraction = 0.0 if span == 0 else (value - self.minimum()) / span
        radius = self.HANDLE_DIAMETER / 2
        usable = max(self.width() - 2 * radius, 0)
        return radius + fraction * usable

    def _handle_x(self) -> float:
        """X position of the handle centre for the current value."""
        return self._x_for_value(self.value())

    def _value_at_x(self, x: float) -> float:
        """Inverse of _x_for_value: maps a screen x back to a slider
        value, using the same radius inset. Without this, a click/drag at
        the left or right edge would set the value to the min/max, but
        the handle would then paint ~radius pixels away from where the
        click landed (since _x_for_value insets and this didn't)."""
        radius = self.HANDLE_DIAMETER / 2
        usable = max(self.width() - 2 * radius, 0)
        fraction = 0.0 if usable == 0 else (x - radius) / usable
        fraction = max(0.0, min(1.0, fraction))
        return self.minimum() + fraction * (self.maximum() - self.minimum())

    def _snap(self, value: float) -> int:
        """Locks onto the nearest SNAP_MARK if within SNAP_RADIUS of it,
        otherwise leaves the value as freely-dragged (just rounded to a
        whole number and clamped to the slider's own range)."""
        for mark in self.snap_marks:
            if abs(value - mark) <= self.SNAP_RADIUS:
                return mark
        return int(max(self.minimum(), min(self.maximum(), round(value))))

    def _draw_pixel_dot(self, painter: QtGui.QPainter, cx: float, cy: float) -> None:
        """Draws TICK_PATTERN centered at (cx, cy) as discrete filled
        squares - no antialiasing, so it reads as crisp pixel art rather
        than a smooth circle, matching the pixel-grid reference."""
        rows = self.TICK_PATTERN
        size = self.TICK_PIXEL_SIZE
        h = len(rows)
        w = len(rows[0])
        left = cx - (w * size) / 2.0
        top = cy - (h * size) / 2.0
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self.TICK_COLOR)
        for row, line in enumerate(rows):
            for col, char in enumerate(line):
                if char == "X":
                    painter.drawRect(
                        QtCore.QRectF(left + col * size, top + row * size, size, size)
                    )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        mid_y = self.height() / 2
        handle_x = self._handle_x()

        left_pen = QtGui.QPen(self.left_color)
        left_pen.setWidth(self.LEFT_WIDTH)
        left_pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        painter.setPen(left_pen)
        painter.drawLine(QtCore.QPointF(0, mid_y), QtCore.QPointF(handle_x, mid_y))

        right_pen = QtGui.QPen(self.RIGHT_COLOR)
        right_pen.setWidth(self.RIGHT_WIDTH)
        right_pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        painter.setPen(right_pen)
        painter.drawLine(
            QtCore.QPointF(handle_x, mid_y), QtCore.QPointF(self.width(), mid_y)
        )

        # No antialiasing for the tick marks - crisp pixel-art squares,
        # not smoothed geometry, per the pixel-grid reference.
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        for mark in self.snap_marks:
            if self.minimum() <= mark <= self.maximum():
                tick_x = self._x_for_value(mark)
                self._draw_pixel_dot(painter, tick_x, mid_y)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        handle_pen = QtGui.QPen(self.HANDLE_BORDER_COLOR)
        handle_pen.setWidth(self.HANDLE_BORDER_WIDTH)
        painter.setPen(handle_pen)
        painter.setBrush(self.HANDLE_COLOR)
        radius = self.HANDLE_DIAMETER / 2
        painter.drawEllipse(QtCore.QPointF(handle_x, mid_y), radius, radius)

        painter.end()

    def _apply_mouse_value(self, x: float) -> None:
        """Shared body of click and drag handling: snap the value under
        the cursor and jump straight there (page/single step sized to
        the distance so the jump is one step, not an animation)."""
        value = self._snap(self._value_at_x(x))
        try:
            stepsize = int(abs(self.value() - value))
            self.setPageStep(stepsize)
            self.setSingleStep(stepsize)
        except Exception:
            pass
        self.setValue(value)

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            e.accept()
            self._apply_mouse_value(e.pos().x())
        else:
            return super().mousePressEvent(e)

    def mouseMoveEvent(self, ev: QtGui.QMouseEvent) -> None:
        ev.accept()
        self._apply_mouse_value(ev.pos().x())


class ThinProgressBar(QtWidgets.QWidget):
    """Minimal custom-painted progress bar. Deliberately not QProgressBar
    + a stylesheet: this project's Qt/macOS combination has repeatedly
    proven unreliable at honoring stylesheets on built-in widgets (see
    ClickSlider's history above), so this is hand-painted from the start
    instead of risking the same multi-iteration debugging cycle. Shares
    ClickSlider's fill color for a consistent look - no text, just a
    filled strip against a track."""

    FILL_COLOR = ClickSlider.LEFT_COLOR
    # Own constant, not ClickSlider.RIGHT_COLOR - that coupling meant
    # slider-only color tuning (a dedicated "dark side of the slider"
    # tweak) silently recolored this track too. Fixed value keeps the
    # look this always had before that coupling existed.
    TRACK_COLOR = QtGui.QColor("#1a1a1a")
    BAR_HEIGHT = 4

    def __init__(self) -> None:
        super().__init__()
        self._done = 0
        self._total = 0
        # Instance-level so Preferences > Appearance > Accent Color can
        # override the class default per-panel without needing a subclass.
        self.fill_color = QtGui.QColor(self.FILL_COLOR)
        self.setFixedHeight(self.BAR_HEIGHT)

    def set_accent_color(self, color: QtGui.QColor) -> None:
        """Overrides the fill color and repaints."""
        self.fill_color = QtGui.QColor(color)
        self.update()

    def set_progress(self, done: int, total: int) -> None:
        self._done = done
        self._total = total
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), self.TRACK_COLOR)
        if self._total > 0:
            fraction = min(max(self._done / self._total, 0.0), 1.0)
            fill_w = int(self.width() * fraction)
            if fill_w > 0:
                painter.fillRect(0, 0, fill_w, self.height(), self.fill_color)
        painter.end()


class SectionTabBar(QtWidgets.QWidget):
    """Full-width section tab strip below the toolbar, per the
    "ui_wireframe 2 only menu" design file (2026-07-19 rev): a rounded-top
    tray at the left holding one text-label tab per section; the
    selected tab gets a rounded chip fill with a thin ring, unselected
    tabs are plain text. The tray's bottom edge is flush with the strip
    bottom and its color matches the category section's backdrop
    (#262626), so it reads as connected to the sidebar below - a
    folder-tab look. Replaces the SegmentedControl that used to sit
    inside cat_wrapper.

    Hand-painted for the same reason as its predecessor (and
    ClickSlider before that): stylesheet-driven buttons never reliably
    held their geometry on macOS native chrome.

    Design measurements (rendered px -> code px at the confirmed 2x
    display scale; heights/sizes/paddings ARE exact, button placement
    is not): tray height 46 -> 23, tray top corner radius
    8 -> 4, chip height 34 -> 17, chip corner radius 8 -> 4, ring
    2 -> 1, text side padding inside a chip 15 -> 7.5, gap between
    chips 5 -> 2.5, tray-edge-to-first-chip inset 6 -> 3, tray left
    offset 6 -> 3. Half-pixel code values are painted via QRectF - on
    the 2x display they land on physical pixel boundaries. Full strip
    height is 28 (56 rendered vs the design's 55 - 55 isn't reachable
    with integer widget heights; the spare pixel goes above the tray).
    """

    #: emits the key of the tab that just became checked (only on an
    #: actual change, matching QAbstractButton.setChecked()'s own
    #: emit-only-on-change behavior)
    segmentClicked = QtCore.Signal(str)

    HEIGHT = 28
    TRAY_HEIGHT = 23
    TRAY_RADIUS = 4
    TRAY_LEFT = 3
    CHIP_HEIGHT = 17
    CHIP_RADIUS = 4
    CHIP_PAD_X = 7.5
    CHIP_GAP = 2.5
    CHIP_INSET = 3

    # Theme-derived (helpers/theme.py): identical to the old literal
    # constants under Houdini's default theme, follows any other theme
    # automatically. The chip pair is an ACCENT shade (Houdini's own
    # example panel drives its checked/tab states from accent variants).
    STRIP_COLOR = theme.color("surface")  # matches the toolbar row
    TRAY_COLOR = theme.color("surface_low")  # matches cat backdrop
    CHIP_FILL = theme.color("tab_chip")
    CHIP_RING = theme.color("tab_ring")
    TEXT_SELECTED = theme.color("text_bright")
    TEXT_UNSELECTED = theme.color("text")
    # Not in the design (no tab hover state drawn there) - a modest
    # text-whitening on hover, matching the toolbar icons' hover color.
    TEXT_HOVER = QtGui.QColor("#cccdcd")

    def __init__(self, segments: list) -> None:
        """segments: list of (key, label) pairs, left to right."""
        super().__init__()
        self._segments = list(segments)
        self._checked_key = None
        self._hover_key = None
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def setChecked(self, key: str, emit: bool = True) -> None:
        """Selects the given tab. Emits segmentClicked only if this
        actually changes the current selection.

        emit=False lets a caller set the initial visual "checked" state
        at construction time without firing the signal - needed because
        panel.py builds this widget inside init_ui(), before setup() has
        created the models _on_tab_toggled's handlers depend on."""
        if key != self._checked_key:
            self._checked_key = key
            self.update()
            if emit:
                self.segmentClicked.emit(key)

    def checkedKey(self):
        return self._checked_key

    def set_label(self, key: str, label: str) -> None:
        """Change a tab's displayed text (e.g. Materials -> Online while
        the online browser is showing, so it's obvious you've left your
        local library). No-op if the key is absent or the label is
        unchanged; reflows the chip to the new text width on change."""
        for i, (k, lbl) in enumerate(self._segments):
            if k == key:
                if lbl != label:
                    self._segments[i] = (k, label)
                    self.updateGeometry()   # width changed
                    self.update()
                return

    def _chip_rects(self) -> list:
        """[((key, label), QRectF), ...] - the chip-sized rect for every
        tab (also the hit target for unselected tabs, which paint text
        only). Measured against the CURRENT font, so the panel's font
        stamp is always respected regardless of construction order."""
        metrics = self.fontMetrics()
        tray_top = self.height() - self.TRAY_HEIGHT
        chip_y = tray_top + (self.TRAY_HEIGHT - self.CHIP_HEIGHT) / 2.0
        x = self.TRAY_LEFT + self.CHIP_INSET
        rects = []
        for key, label in self._segments:
            w = metrics.horizontalAdvance(label) + 2 * self.CHIP_PAD_X
            rects.append(
                ((key, label), QtCore.QRectF(x, chip_y, w, self.CHIP_HEIGHT))
            )
            x += w + self.CHIP_GAP
        return rects

    def _key_at(self, pos) -> str | None:
        for (key, _), rect in self._chip_rects():
            if rect.contains(QtCore.QPointF(pos)):
                return key
        return None

    def sizeHint(self) -> QtCore.QSize:
        rects = self._chip_rects()
        right = rects[-1][1].right() + self.CHIP_INSET if rects else 0
        return QtCore.QSize(int(right) + self.TRAY_LEFT, self.HEIGHT)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        key = self._key_at(event.pos())
        if key is not None:
            self.setChecked(key)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        key = self._key_at(event.pos())
        if key != self._hover_key:
            self._hover_key = key
            # Pointing hand only over an actual tab - this strip spans
            # the whole panel width, most of it empty.
            if key is not None:
                self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._hover_key is not None:
            self._hover_key = None
            self.unsetCursor()
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), self.STRIP_COLOR)

        rects = self._chip_rects()
        if rects:
            # Tray: rounded top corners only - the rect is extended one
            # radius past the widget bottom, so the bottom rounding is
            # clipped off and the tray sits flush against whatever is
            # below (visually connecting to the category sidebar).
            tray_top = self.height() - self.TRAY_HEIGHT
            tray_right = rects[-1][1].right() + self.CHIP_INSET
            tray_path = QtGui.QPainterPath()
            tray_path.addRoundedRect(
                QtCore.QRectF(
                    self.TRAY_LEFT,
                    tray_top,
                    tray_right - self.TRAY_LEFT,
                    self.TRAY_HEIGHT + self.TRAY_RADIUS,
                ),
                self.TRAY_RADIUS,
                self.TRAY_RADIUS,
            )
            painter.fillPath(tray_path, self.TRAY_COLOR)

        for (key, label), rect in rects:
            checked = key == self._checked_key
            if checked:
                chip_path = QtGui.QPainterPath()
                chip_path.addRoundedRect(rect, self.CHIP_RADIUS, self.CHIP_RADIUS)
                painter.fillPath(chip_path, self.CHIP_FILL)
                pen = QtGui.QPen(self.CHIP_RING)
                pen.setWidthF(1.0)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(
                    rect.adjusted(0.5, 0.5, -0.5, -0.5),
                    self.CHIP_RADIUS,
                    self.CHIP_RADIUS,
                )
            if checked:
                painter.setPen(self.TEXT_SELECTED)
            elif key == self._hover_key:
                painter.setPen(self.TEXT_HOVER)
            else:
                painter.setPen(self.TEXT_UNSELECTED)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)

        painter.end()


def draw_chip(painter, rect, fill, ring, inner_border=None):
    """Draws the design's rounded button chip: fill + 1px light ring on
    the outer edge, optionally a 1px darker ring just inside it (the
    clicked state has the inner ring, the hover state doesn't). Shared
    by IconMenuButton and ChipToggleButton so the two hover looks can't
    drift apart. Rects sit on half-pixel centers so 1px pens draw
    crisp."""
    outer = QtCore.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(QtGui.QPen(ring, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(outer, 5, 5)
    if inner_border is not None:
        inner = QtCore.QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5)
        painter.setPen(QtGui.QPen(inner_border, 1))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner, 4, 4)


class IconMenuButton(QtWidgets.QWidget):
    """Icon button that pops a QMenu (Library/View/Renderer at the
    toolbar's right end, per the "ui_wireframe 2 only menu" design
    file).

    Fully hand-painted. The first version was a plain QToolButton meant
    to inherit Houdini's own button chrome for the pressed/open look -
    live testing showed that chrome provides NO visible open state at
    all in this panel, plus a stray line artifact under each button
    (menu-indicator/chrome residue), so this went the same way every
    styled widget in this project eventually has: own every pixel
    (ClickSlider, SegmentedControl, and the old text MenuBarButton all
    hit the same wall). Hover/open state is tracked explicitly and
    cleared via the menu's aboutToHide signal - the proven MenuBarButton
    pattern that avoids the stuck-highlight-after-popup Qt quirk.

    States, per the design's "Hover" and "Clicked" groups: idle = icon
    in #5d7abd, no chip; hover = grey chip (#424142 fill, #555455 outer
    ring, no inner ring) behind the whitened icon; open = blue chip
    (#2d4075 fill, #1e2c50 inner border, #707ca3 outer ring) behind the
    whitened icon. In both chip states the icon's punch-out details
    switch to the chip's own fill color so they keep reading as holes."""

    IDLE_BODY = "#5d7abd"
    LIT_BODY = "#cccdcd"
    IDLE_TRIANGLE = "#7f807f"
    LIT_TRIANGLE = "#a5b3d4"
    PUNCH_OUT = "#2d2d2d"
    OPEN_PUNCH_OUT = "#2d4075"
    HOVER_PUNCH_OUT = "#424142"
    CHIP_FILL = QtGui.QColor("#2d4075")
    CHIP_BORDER = QtGui.QColor("#1e2c50")
    CHIP_RING = QtGui.QColor("#707ca3")
    HOVER_CHIP_FILL = QtGui.QColor("#424142")
    HOVER_CHIP_RING = QtGui.QColor("#555455")
    TEXT_COLOR = QtGui.QColor("#e6e6e6")
    # Icons are rendered at 4x the icon size so painting scales a sharp
    # pixmap down on the retina display instead of a soft one up.
    RENDER_SCALE = 4

    def __init__(
        self,
        menu: QtWidgets.QMenu,
        svg_path: str,
        # 18 with the 36-unit body-centered viewBoxes keeps the icon
        # body at the same ~29px rendered size as the design.
        icon_size: int = 18,
        button_size: int = 24,
    ) -> None:
        super().__init__()
        self._menu = menu
        self._hovered = False
        self._open = False
        self._icon_size = icon_size
        self.setFixedSize(button_size, button_size)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        render_size = icon_size * self.RENDER_SCALE
        self._idle_pm = render_svg_pixmap(svg_path, render_size)
        self._hover_pm = render_svg_pixmap(
            svg_path,
            render_size,
            {
                self.IDLE_BODY: self.LIT_BODY,
                self.IDLE_TRIANGLE: self.LIT_TRIANGLE,
                self.PUNCH_OUT: self.HOVER_PUNCH_OUT,
            },
        )
        self._open_pm = render_svg_pixmap(
            svg_path,
            render_size,
            {
                self.IDLE_BODY: self.LIT_BODY,
                self.IDLE_TRIANGLE: self.LIT_TRIANGLE,
                self.PUNCH_OUT: self.OPEN_PUNCH_OUT,
            },
        )
        # Graceful fallback if the asset is missing: draw the menu title
        # as text so the button stays usable.
        self._fallback_text = (
            "" if (svg_path and os.path.exists(svg_path)) else menu.title()
        )
        menu.aboutToHide.connect(self._on_menu_closed)

    def _on_menu_closed(self) -> None:
        self._open = False
        # The mouse may or may not still be over this button once the
        # menu closes, and Qt does not reliably resend hover/leave
        # events across a popup closing - check the cursor directly.
        self._hovered = self.rect().contains(
            self.mapFromGlobal(QtGui.QCursor.pos())
        )
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self._open = True
        self.update()
        self._menu.popup(self.mapToGlobal(QtCore.QPoint(0, self.height())))

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(
            QtGui.QPainter.RenderHint.SmoothPixmapTransform, True
        )
        if self._open:
            # The design's clicked chip: blue fill, light outer ring,
            # darker border ring just inside it.
            draw_chip(
                painter, self.rect(), self.CHIP_FILL, self.CHIP_RING,
                self.CHIP_BORDER,
            )
        elif self._hovered:
            # The design's hover chip: the grey sibling - fill + light
            # outer ring only, no inner border ring.
            draw_chip(
                painter, self.rect(), self.HOVER_CHIP_FILL,
                self.HOVER_CHIP_RING,
            )
        if self._fallback_text:
            painter.setPen(self.TEXT_COLOR)
            painter.drawText(
                self.rect(),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                self._fallback_text,
            )
        else:
            if self._open:
                pm = self._open_pm
            elif self._hovered:
                pm = self._hover_pm
            else:
                pm = self._idle_pm
            offset = (self.width() - self._icon_size) // 2
            target = QtCore.QRect(
                offset, offset, self._icon_size, self._icon_size
            )
            painter.drawPixmap(target, pm)
        painter.end()


class ChipToggleButton(QtWidgets.QToolButton):
    """Checkable icon button (favorites star, grid/list toggle) with the
    exact same hand-painted grey hover chip as IconMenuButton - the
    hover state is meant to match across favorites/grid-list/menu
    buttons, with icons whitening to the same light color.

    Subclasses QToolButton so all existing wiring keeps working
    untouched (toggled signal, setChecked/isChecked, signal blocking),
    but paints entirely itself: QAbstractButton still handles the
    click-to-toggle mechanics, which don't depend on paint. No popup
    menu is involved, so plain enter/leave hover tracking is safe here -
    the stuck-hover Qt quirk only bites across a popup closing."""

    RENDER_SCALE = 4

    def __init__(self, button_size: int = 24, icon_size: int = 16) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(button_size, button_size)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._icon_size = icon_size
        self._hovered = False
        self._pms = {}

    def set_state_pixmaps(self, off_pm, on_pm, hover_off_pm, hover_on_pm):
        """Pixmaps keyed by (checked, hovered)."""
        self._pms = {
            (False, False): off_pm,
            (True, False): on_pm,
            (False, True): hover_off_pm,
            (True, True): hover_on_pm,
        }
        self.update()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(
            QtGui.QPainter.RenderHint.SmoothPixmapTransform, True
        )
        if self._hovered:
            draw_chip(
                painter, self.rect(), IconMenuButton.HOVER_CHIP_FILL,
                IconMenuButton.HOVER_CHIP_RING,
            )
        pm = self._pms.get((self.isChecked(), self._hovered))
        if pm is not None:
            offset = (self.width() - self._icon_size) // 2
            painter.drawPixmap(
                QtCore.QRect(offset, offset, self._icon_size, self._icon_size),
                pm,
            )
        painter.end()


class ListColumnHeader(QtWidgets.QWidget):
    """Spreadsheet-style column header (Thumbnail | Name | Type) for the
    thumbnail list's LIST mode, per the table mockup. A plain
    painted strip - static labels, no click-sorting or drag-resizing,
    because the list itself stays a QListView (keeping all the existing
    selection/drag/context-menu machinery). Column x-positions arrive
    via set_columns() and are the same values the row delegate paints
    with, so header and rows can't drift apart."""

    HEIGHT = 20
    BG = QtGui.QColor("#2a2a2a")
    TEXT_COLOR = QtGui.QColor("#999999")
    #: "Type" header matches the type entries' accent (instance copy
    #: refreshed from prefs via set_accent_color, like the delegates);
    #: "Category" header and its entries share the same yellow.
    TYPE_COLOR = QtGui.QColor("#5d7abd")
    CATEGORY_COLOR = QtGui.QColor("#ebc658")
    DIVIDER = QtGui.QColor("#454545")
    COL_PAD = 8
    # No label over the thumbnail column, and no divider after it either
    # (the pictures delimit the column by themselves) - dividers only at
    # Name | Type and Type | Category.
    LABELS = ("", "Name", "Type", "Category")

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self._thumb_w = 52
        self._name_w = 200
        self._type_w = 150
        self.type_color = QtGui.QColor(self.TYPE_COLOR)

    def set_accent_color(self, color: QtGui.QColor) -> None:
        self.type_color = QtGui.QColor(color)
        self.update()

    def set_columns(self, thumb_w: int, name_w: int, type_w: int) -> None:
        self._thumb_w = int(thumb_w)
        self._name_w = int(name_w)
        self._type_w = int(type_w)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), self.BG)
        h = self.height()
        xs = (
            self.COL_PAD,
            self._thumb_w + self.COL_PAD,
            self._thumb_w + self._name_w + self.COL_PAD,
            self._thumb_w + self._name_w + self._type_w + self.COL_PAD,
        )
        colors = (
            self.TEXT_COLOR,
            self.TEXT_COLOR,
            self.type_color,
            self.CATEGORY_COLOR,
        )
        for label, x, color in zip(self.LABELS, xs, colors):
            if not label:
                continue
            painter.setPen(color)
            painter.drawText(
                QtCore.QRect(x, 0, max(self.width() - x - self.COL_PAD, 1), h),
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        painter.setPen(self.DIVIDER)
        for x in (
            self._thumb_w + self._name_w,
            self._thumb_w + self._name_w + self._type_w,
        ):
            painter.drawLine(x, 0, x, h)
        painter.end()


class SideIconPinner(QtCore.QObject):
    """Keeps an icon QLabel pinned to one edge of a line edit (left or
    right), inset by a fixed margin, vertically centered. Needed because
    the line edit isn't fixed-width (only max-width) - a one-time move()
    wouldn't stay correct across a panel resize, so this reacts to the
    line edit's own Resize events instead. Used for line_filter's filter
    icon."""

    def __init__(
        self,
        line_edit: QtWidgets.QLineEdit,
        icon_label: QtWidgets.QLabel,
        margin: int,
        side: str = "right",
    ) -> None:
        super().__init__(line_edit)
        self._line_edit = line_edit
        self._icon_label = icon_label
        self._margin = margin
        self._side = side
        line_edit.installEventFilter(self)
        self.reposition()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if obj is self._line_edit and event.type() == QtCore.QEvent.Type.Resize:
            self.reposition()
        return False

    def reposition(self) -> None:
        w = self._icon_label.width()
        h = self._icon_label.height()
        if self._side == "left":
            x = self._margin
        else:
            x = self._line_edit.width() - self._margin - w
        y = (self._line_edit.height() - h) // 2
        self._icon_label.move(max(x, 0), max(y, 0))
