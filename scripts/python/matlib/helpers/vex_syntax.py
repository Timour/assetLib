"""Shared VEX/C-like syntax coloring - the one palette + tokenizer used
by BOTH the Code section's tile preview (core/code_library.py) and the
editor dialog (dialogs/code_dialog.py), so the two always match.

Colors are a best-effort match of Houdini's own wrangle VEXpression
editor (black background, blue types, blue functions, green strings,
gold numbers, teal @bindings). Every value is a named constant here -
tune freely.
"""

import re

from PySide6 import QtGui

# --- palette (Houdini wrangle editor, best-effort) --------------------
BACKGROUND = QtGui.QColor("#000000")
DEFAULT = QtGui.QColor("#d4d4d4")
COMMENT = QtGui.QColor("#6a9955")
STRING = QtGui.QColor("#9cdb6a")   # green, brighter than comments
NUMBER = QtGui.QColor("#d7a35b")   # gold / orange
TYPE = QtGui.QColor("#8f9fff")     # vector / int / float - blue-violet
KEYWORD = QtGui.QColor("#c586d9")  # if / for / return - purple
FUNCTION = QtGui.QColor("#5e9cea") # point / addpoint / addprim - blue
ATTRIB = QtGui.QColor("#56c2b0")   # @P / v@center - teal

KEYWORDS = {
    "if", "else", "for", "foreach", "while", "do", "return", "break",
    "continue", "function", "struct", "export", "const", "in",
    "import", "def", "class", "elif", "and", "or", "not",
    "None", "True", "False", "kernel", "__kernel", "__global",
}
TYPES = {
    "int", "float", "vector", "vector2", "vector4", "matrix", "matrix2",
    "matrix3", "string", "void", "array", "dict", "bsdf", "surface",
    "displacement", "light", "shadow", "fog", "material", "shader",
    "char", "double", "bool", "long", "unsigned", "global", "constant",
}

_TOKEN_RE = re.compile(
    r"""
    (?P<comment>//[^\n]*|\#[^\n]*|/\*.*?\*/) |
    (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*') |
    (?P<attrib>[vfipsu2349]?@[A-Za-z_][A-Za-z0-9_]*) |
    (?P<number>\b\d+\.?\d*\b) |
    (?P<word>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<other>\s+|.)
    """,
    re.VERBOSE | re.DOTALL,
)


def spans(line: str):
    """Yield (start, length, QColor) runs for one line of code. A word
    immediately followed by '(' is coloured as a function call (that's
    how the wrangle editor tells point()/addpoint() from plain idents)."""
    for m in _TOKEN_RE.finditer(line):
        kind = m.lastgroup
        color = DEFAULT
        if kind == "comment":
            color = COMMENT
        elif kind == "string":
            color = STRING
        elif kind == "attrib":
            color = ATTRIB
        elif kind == "number":
            color = NUMBER
        elif kind == "word":
            text = m.group()
            if text in KEYWORDS:
                color = KEYWORD
            elif text in TYPES:
                color = TYPE
            elif line[m.end():].lstrip().startswith("("):
                color = FUNCTION
            else:
                color = DEFAULT
        else:
            color = DEFAULT
        yield m.start(), len(m.group()), color


class VexHighlighter(QtGui.QSyntaxHighlighter):
    """Applies the shared palette to a QPlainTextEdit, line by line."""

    def highlightBlock(self, text: str) -> None:
        for start, length, color in spans(text):
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(color)
            self.setFormat(start, length, fmt)
