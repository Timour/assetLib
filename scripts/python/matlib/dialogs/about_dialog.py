"""
About Info Widget attached to the MatLibPanel
"""

import os

from PySide6 import QtWidgets, QtCore, QtUiTools

from matlib import branding


class AboutDialog(QtWidgets.QDialog):
    """
    About Info Widget attached to the MatLibPanel
    """

    def __init__(self):
        super(AboutDialog, self).__init__()
        self.script_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

        # Load UI from ui.file
        loader = QtUiTools.QUiLoader()
        file = QtCore.QFile(self.script_path + "/ui/about.ui")
        file.open(QtCore.QFile.ReadOnly)
        self.ui = loader.load(file)
        file.close()

        # set main layout and attach to widget
        mainlayout = QtWidgets.QVBoxLayout()
        mainlayout.addWidget(self.ui)
        mainlayout.setContentsMargins(0, 0, 0, 0)  # Remove Margins

        self.setLayout(mainlayout)
        self.setWindowTitle("About " + branding.APP_NAME)

        # Rewrite the upstream About text at runtime (the .ui stays
        # untouched, standing practice): app branding, fork lineage and
        # license. The name comes from branding.APP_NAME.
        text = self.ui.findChild(QtWidgets.QTextEdit, "textEdit")
        if text is not None:
            text.setHtml(
                "<h2>" + branding.APP_NAME + "</h2>"
                "<p><i>" + branding.APP_TAGLINE + "</i></p>"
                "<p>An asset library for Houdini: materials, textures, "
                "COP networks, color palettes, geometry and code - browse, "
                "save, drag and assign.</p>"
                "<p>By Fredrik Timour, built with Claude (Anthropic).<br>"
                "<a href='https://github.com/Timour/assetLib'>"
                "github.com/Timour/assetLib</a></p>"
                "<p>Based on egMatLib by Elmar Glaubauf<br>"
                "<a href='https://github.com/eglaubauf/egMatLib'>"
                "github.com/eglaubauf/egMatLib</a></p>"
                "<p>Code and assets released under GPLv3 - free to use, "
                "modify and embed as stated in the license. Selling or "
                "reselling of the code is not permitted.</p>"
                "<p>Additional thanks: slayerk (Redshift forums), "
                "Sanzo Wada / Paul Klee / Josef Albers / Johannes Itten "
                "(color palette sources, public domain).</p>"
            )
