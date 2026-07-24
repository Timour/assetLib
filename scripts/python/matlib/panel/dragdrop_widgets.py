"""
Module For Drag and Drop Widgets handling the Drag and Drop from and to Houdini
"""

from PySide6 import QtWidgets, QtGui, QtCore
import hou

from matlib.helpers import ui_helpers


def _find_panel(widget: QtWidgets.QWidget):
    """Walk up widget's parent chain to the MatLibPanel instance.

    More robust than a fixed parentWidget() depth: the panel's widget
    hierarchy has been restructured several times (menubar renderer
    filter, details-form rebuild, grid/list toggle), and a fixed-depth
    chain silently breaks - and calls a method that doesn't exist on
    whatever widget it lands on - every time that nesting changes.
    """
    w = widget.parentWidget()
    while w is not None:
        if hasattr(w, "import_asset"):
            return w
        w = w.parentWidget()
    return None


class DragDropListView(QtWidgets.QListView):
    """
    Handle Dragging and Dropping from the Thumblist View in the MatLib Panel
    This comes into effect when the Details Panel is closed
    """

    #: Non-real-path sections resolve their drop TARGET from a scene NODE
    #: under the cursor (vs a network CONTEXT). Only these look one up at
    #: release; the rest resolve a network context themselves.
    NODE_TARGET_SECTIONS = ("gradient", "code")
    #: Sections on the black SELF-MANAGED gesture (one look, one
    #: mechanism). Texture (real path) and Material (native node drag,
    #: for Houdini's Drop Actions menu) are NATIVE drags, not this.
    SELF_MANAGED_SECTIONS = ("gradient", "geometry", "cop", "code")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        # Per-section press/drag tracking - see mousePressEvent/
        # mouseMoveEvent below. _drag_start is None whenever no such
        # left-button press is in progress. _drag_panel is cached at
        # press so the gesture never re-walks the widget tree per move.
        self._drag_start = None
        self._drag_section = None
        self._drag_index = None
        self._drag_panel = None
        # Self-managed label-drag state, shared by EVERY non-real-path
        # section (materials, cop, gradient/color, code, geometry) - one
        # gesture, one look; the release dispatches to the section's own
        # action (see mouseReleaseEvent).
        self._dragging = False
        self._preview = None
        super().__init__(parent)

    # Wheel scrolling handled manually: even with per-pixel scroll mode,
    # Qt's own wheel handling overshoots in this environment (roughly
    # 2x too fast), so trackpad pixel deltas are applied directly,
    # scaled by the scroll_speed preference (read fresh per event, so a
    # Preferences change applies immediately). SCROLL_SPEED is only the
    # fallback when no panel/prefs is reachable.
    SCROLL_SPEED = 0.75
    WHEEL_NOTCH_PX = 60  # classic mouse wheel: px per notch, pre-scaling

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        delta = event.pixelDelta().y()
        if delta == 0:
            # Classic mouse wheel - no pixel data, only 120-unit notches.
            delta = event.angleDelta().y() / 120.0 * self.WHEEL_NOTCH_PX
        if delta == 0:
            super().wheelEvent(event)
            return
        panel = _find_panel(self)
        prefs = getattr(panel, "prefs", None) if panel is not None else None
        speed = getattr(prefs, "scroll_speed", None) or self.SCROLL_SPEED
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - round(delta * speed))
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        panel = _find_panel(self)
        section = (
            getattr(panel, "current_section", None) if panel is not None else None
        )
        # Online rows are catalogue entries, not assets: there is no
        # node to drag and no file on disk yet. Arming the material drag
        # here mapped an ONLINE proxy index through the MATERIAL proxy,
        # which drags whichever local material happens to sit at that row.
        online = bool(panel is not None and panel._is_online())
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and not online
            and section in (("texture", "material") + self.SELF_MANAGED_SECTIONS)
        ):
            self._drag_start = event.pos()
            self._drag_section = section
            self._drag_index = self.indexAt(event.pos())
            self._drag_panel = panel
        else:
            self._drag_start = None
            self._drag_section = None
            self._drag_index = None
            self._drag_panel = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        """Two drag systems, split by task:

        - REAL-PATH sections (texture; geometry rides the same PathRole)
          drag the file PATH as native file mime - the OS renders its own
          filepath tag and Houdini's parm fields accept it natively, so
          nothing of ours runs during the drag (_run_texture_file_drag).

        - Every NON-real-path section (materials, cop, gradient/color,
          code, geometry's import gesture) has no file to hand off, so
          the drop target (a node/network) must be resolved by US, which
          means the gesture must stay in our hands: a real QDrag traps it
          in macOS's native drag run loop where our code can't run at all
          (proven during the texture saga - a polling QTimer fired zero
          times inside QDrag.exec()). One SELF-MANAGED gesture, one look
          (a floating name tag); mouseReleaseEvent dispatches to the
          section's own action.

        Speed: the target is resolved ONCE at release, not polled every
        frame during the drag - the per-move networkItemsInBox poll was
        the lag. The floating tag just follows the cursor.
        """
        if self._drag_start is not None:
            moved = (event.pos() - self._drag_start).manhattanLength()
            if self._drag_section == "texture":
                if moved >= QtWidgets.QApplication.startDragDistance():
                    self._drag_start = None
                    self._run_texture_file_drag()
                # Never call super() here (even before the threshold's
                # crossed) - that's what used to let QAbstractItemView
                # fall back to rubber-band selection during this gesture.
                return
            # Materials: NATIVE node QDrag (not the black system) so
            # Houdini's own viewport handler fires its Drop Actions menu.
            if self._drag_section == "material":
                if moved >= QtWidgets.QApplication.startDragDistance():
                    self._drag_start = None
                    if (
                        self._drag_panel is not None
                        and self._drag_index is not None
                        and self._drag_index.isValid()
                    ):
                        self._drag_panel._run_material_drag(self._drag_index)
                return
            # Unified self-managed drag for every non-real-path section.
            if not self._dragging and moved >= (
                QtWidgets.QApplication.startDragDistance()
            ):
                self._begin_drag()
            if self._dragging:
                self._move_preview()
                # Same accent-purple drop-target feedback as the material
                # drag gets via the sidebar filter - these gestures have no
                # Qt drag events, so drive it from the cursor position.
                if self._drag_panel is not None:
                    self._drag_panel._update_category_drag_hover_global()
            return
        super().mouseMoveEvent(event)

    def _begin_drag(self) -> None:
        """Start the shared self-managed drag: a floating name tag that
        follows the cursor. Look is temporary (a restyle is planned) -
        the point right now is one gesture for every non-real-path
        section instead of four hand-rolled ones."""
        self._dragging = True
        name = ""
        if self._drag_index is not None and self._drag_index.isValid():
            name = (
                self._drag_index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
            )
        label = QtWidgets.QLabel(str(name))
        label.setWindowFlags(
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        label.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        # Shared style with the native drags' pixmap (see ui_helpers)
        # so every drag tag looks identical.
        label.setStyleSheet(ui_helpers.DRAG_TAG_STYLE)
        label.adjustSize()
        self._preview = label
        self._move_preview()
        label.show()

    def _move_preview(self) -> None:
        if self._preview is not None:
            pos = QtGui.QCursor.pos()
            # Offset below-right of the cursor, like the native
            # file-drag tag sits.
            self._preview.move(pos.x() + 12, pos.y() + 14)

    def _end_preview(self) -> None:
        if self._preview is not None:
            self._preview.close()
            self._preview.deleteLater()
            self._preview = None

    def _run_texture_file_drag(self) -> None:
        """Drags the pressed texture's file path as real file mime data
        (QUrl + plain text, matching what dragging a file from Finder
        provides), so Houdini's parm fields accept it natively.

        The DRAG PICTURE is the shared black name tag (setPixmap), same as
        every other section - "native" is only the drop mechanism; the
        picture is an independent choice, and the design calls for ONE
        look everywhere. macOS caveat: a file/URL drag sometimes insists on its
        own file badge over a custom pixmap, so confirm the tag actually
        shows on the live test."""
        index = self.currentIndex()
        if not index.isValid():
            return
        panel = _find_panel(self)
        if panel is None:
            return
        # PathRole is the same role number on TextureFiles and GeoFiles
        # (by design), and index.data() resolves through the index's own
        # model - so this one lookup serves both sections' drags.
        path = index.data(panel.texture_files_model.PathRole)
        if not path:
            return
        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()
        mime_data.setUrls([QtCore.QUrl.fromLocalFile(path)])
        mime_data.setText(path)
        drag.setMimeData(mime_data)
        name = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        drag.setPixmap(ui_helpers.name_tag_pixmap(name))
        drag.exec(QtCore.Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            # Always tear the preview down first, on every exit path -
            # a floating always-on-top label that survives the gesture
            # would be the stuck-cursor bug class all over again.
            self._end_preview()
            self._dragging = False
            panel = self._drag_panel
            idx = self._drag_index
            if panel is not None:
                panel._set_drag_hover_row(-1)   # clear the drop-target glow
            if panel is not None and idx is not None:
                section = self._drag_section
                # Released over a sidebar category? Recategorise the
                # selection (Materials/Cop/Code/Colors) - checked first,
                # since it's inside the panel and takes precedence over a
                # node target. Returns None for folder sections and for
                # the "All" row, so those fall through unchanged.
                category = panel._category_under_cursor()
                if category is not None:
                    panel.assign_category_active(category)
                # Network-context sections resolve where they landed
                # themselves; node-target sections need the node under
                # the cursor - resolved ONCE here (no per-frame polling).
                elif section == "cop":
                    panel.drop_cop_at_release(idx)
                elif section == "geometry":
                    panel.drop_geo_at_release(idx)
                elif section in self.NODE_TARGET_SECTIONS:
                    node = panel._node_under_cursor()
                    if node is not None:
                        if section == "gradient":
                            panel.apply_gradient_to_node(idx, node)
                        elif section == "code":
                            panel.drop_code_at_release(idx, node)
                # A release over nothing is silent - a miss is a normal
                # drag outcome, not an error.
        # Clearing the press state also covers the plain-click case: if
        # left uncleared, a later hover move (mouseMoveEvent can fire
        # without a button held under mouse tracking) would measure a
        # large "moved" distance against a stale start point and
        # spuriously launch a drag with no button pressed.
        self._drag_start = None
        self._drag_section = None
        self._drag_panel = None
        super().mouseReleaseEvent(event)


class DragDropCentralWidget(QtWidgets.QWidget):
    """Receives a node dragged IN from a network editor (drop = save to
    the library). The legacy leave-the-panel OUTBOUND import that used
    to live here (and on DragDropListView) is gone: every section's
    drag-out is now a self-managed or mime-based gesture in
    DragDropListView, so the native model drag that fed those handlers
    never starts - and during the material mime drag the leftover
    handler actively double-imported (DRAGTEST log, 2026-07-19)."""

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        # A material dragged OUT of our own grid carries a valid node path
        # (its /mat copy), so dropping it back on the panel used to fire
        # the save-node flow ("... already exists in the library"). Ignore
        # any drop that ORIGINATED inside the panel; only a node dragged in
        # from a Houdini network editor (external, source() is None or a
        # non-panel widget) is a "save this".
        src = event.source()
        if src is not None and _find_panel(src) is not None:
            return
        node_path = event.mimeData().text()
        # Check if valid data:
        node = hou.node(node_path)
        if not node:
            return
        node.setSelected(True)  # Select node for save script
        panel = _find_panel(self)
        if panel is None:
            return
        # Route to the ACTIVE section's save flow (Section.save_node) so
        # the right dialog - with that section's own categories - opens:
        # a wrangle dropped in the Code section saves a snippet, not a
        # material. Before setup() has built the registry (no library
        # configured) fall back to the material flow, whose own guard
        # explains that a library must be set first.
        section = panel._section()
        if section is None:
            panel.save_asset()
        else:
            section.save_node(node)


class CategoryDropFilter(QtCore.QObject):
    """Makes the category sidebar a real DROP TARGET: dragging assets from
    the grid onto a category recategorises them (same gesture as a node
    drop, but aimed at a category). Installed as an event filter on the
    sidebar so no .ui subclassing is needed; it accepts only drags that
    started in our own grid (so a node dragged in from Houdini still falls
    through to the save handler), and consumes the drop so it never
    reaches the central widget's save-node flow."""

    def __init__(self, cat_list, panel) -> None:
        super().__init__(cat_list)
        self._list = cat_list
        self._panel = panel
        cat_list.setAcceptDrops(True)
        cat_list.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et in (
            QtCore.QEvent.Type.DragEnter,
            QtCore.QEvent.Type.DragMove,
        ):
            if self._panel._can_drop_category(event):
                # Highlight the category under the cursor in the accent
                # purple, so it's clear which one the drop will hit.
                self._panel._update_category_drag_hover(
                    event.position().toPoint()
                )
                event.acceptProposedAction()
                return True
            return False
        if et == QtCore.QEvent.Type.DragLeave:
            self._panel._set_drag_hover_row(-1)
            return False
        if et == QtCore.QEvent.Type.Drop:
            handled = self._panel._handle_category_drop(event)
            self._panel._set_drag_hover_row(-1)
            if handled:
                event.acceptProposedAction()
                return True
            return False
        return False
