"""Save/edit dialog for the Code section. Name + Language + editable
Category + Tags + a code editor styled like Houdini's wrangle
VEXpression field (black background, line-number gutter, the shared VEX
syntax colours). Used for New Snippet (empty), Edit (prefilled), and
Save from Node (code + language prefilled)."""

from PySide6 import QtWidgets, QtGui, QtCore

from matlib import branding
from matlib.helpers import vex_syntax

LANGUAGES = ("VEX", "OpenCL", "Python", "Code")


class _LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QtCore.QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint(event)


class CodeEditor(QtWidgets.QPlainTextEdit):
    """A wrangle-style code editor: black background, monospace, a grey
    line-number gutter, and the shared VEX syntax highlighter - the
    standard Qt CodeEditor pattern, coloured to match Houdini."""

    GUTTER_BG = QtGui.QColor("#1a1a1a")
    GUTTER_FG = QtGui.QColor("#7a7a7a")
    BG = vex_syntax.BACKGROUND
    FG = vex_syntax.DEFAULT

    def __init__(self, text="", read_only=False, parent=None):
        super().__init__(parent)
        self.setReadOnly(read_only)
        font = QtGui.QFont("Courier New")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPixelSize(14)
        self.setFont(font)
        self.setTabStopDistance(
            4 * QtGui.QFontMetricsF(font).horizontalAdvance(" ")
        )
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        # Black field, light default text, teal-ish selection - the
        # wrangle editor look. Scrollbars stay native (no ancestor
        # stylesheet, so no scrollbar-rendering regression).
        self.setStyleSheet(
            "QPlainTextEdit { background-color: %s; color: %s;"
            " border: 1px solid #2b2b2b; selection-background-color:"
            " #264f78; }"
            % (self.BG.name(), self.FG.name())
        )
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width(0)
        self._highlighter = vex_syntax.VexHighlighter(self.document())
        self.setPlainText(text)

    # -- gutter ---------------------------------------------------------

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(
                0, rect.y(), self._gutter.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QtCore.QRect(
                cr.left(), cr.top(), self.line_number_area_width(), cr.height()
            )
        )

    def line_number_area_paint(self, event):
        painter = QtGui.QPainter(self._gutter)
        painter.fillRect(event.rect(), self.GUTTER_BG)
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(
            self.contentOffset()
        ).top()
        bottom = top + self.blockBoundingRect(block).height()
        painter.setPen(self.GUTTER_FG)
        width = self._gutter.width() - 6
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, int(top), width, h,
                    QtCore.Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            number += 1
        painter.end()


class CodeDialog(QtWidgets.QDialog):
    def __init__(
        self,
        categories: list,
        name: str = "",
        language: str = "VEX",
        category: str = "",
        tags: str = "",
        code: str = "",
        description: str = "",
        title: str = "Save Code to " + branding.APP_NAME,
    ) -> None:
        super().__init__()
        self.name = ""
        self.language = ""
        self.category = ""
        self.tags = ""
        self.code = ""
        self.description = ""
        self.canceled = True

        self.setWindowTitle(title)

        form = QtWidgets.QFormLayout()

        self._line_name = QtWidgets.QLineEdit(name)
        self._line_name.setMinimumWidth(360)
        form.addRow("Name", self._line_name)

        self._combo_lang = QtWidgets.QComboBox()
        for lang in LANGUAGES:
            self._combo_lang.addItem(lang)
        if language in LANGUAGES:
            self._combo_lang.setCurrentText(language)
        form.addRow("Language", self._combo_lang)

        self._combo_category = QtWidgets.QComboBox()
        self._combo_category.setEditable(True)
        for cat in categories:
            self._combo_category.addItem(cat)
        self._combo_category.setCurrentText(
            category or (categories[0] if categories else "")
        )
        form.addRow("Category", self._combo_category)

        self._line_tags = QtWidgets.QLineEdit(tags)
        form.addRow("Tags", self._line_tags)

        # Shown on hover over the tile - a short note on what the snippet
        # does / how to use it (the curated starter snippets ship one).
        self._text_desc = QtWidgets.QPlainTextEdit(description)
        self._text_desc.setPlaceholderText(
            "Optional - shown on hover (what it does, sliders to add...)"
        )
        self._text_desc.setFixedHeight(56)
        form.addRow("Description", self._text_desc)

        self._editor = CodeEditor(code)
        self._editor.setMinimumSize(560, 320)
        form.addRow("Code", self._editor)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.setContentsMargins(8, 8, 8, 8)
        self.setLayout(layout)

    def _confirm(self) -> None:
        self.name = self._line_name.text().strip()
        self.language = self._combo_lang.currentText().strip()
        self.category = self._combo_category.currentText().strip()
        self.tags = self._line_tags.text().strip()
        self.description = self._text_desc.toPlainText().strip()
        self.code = self._editor.toPlainText()
        if not self.code.strip():
            QtWidgets.QMessageBox.warning(
                self, "Empty snippet", "There is no code to save."
            )
            return
        self.canceled = False
        self.accept()


class CodeViewDialog(QtWidgets.QDialog):
    """Read-only view of a snippet with a Copy button - the wrangle-
    style editor, read-only, for the 'read it back' path."""

    def __init__(self, name: str, code: str) -> None:
        super().__init__()
        self.setWindowTitle(name or "Snippet")

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        editor = CodeEditor(code, read_only=True)
        editor.setMinimumSize(560, 360)
        layout.addWidget(editor)

        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        copy_btn = QtWidgets.QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(
            lambda: QtGui.QGuiApplication.clipboard().setText(code)
        )
        row.addWidget(copy_btn)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.setLayout(layout)
