"""
Material Import Dialog attached to the MatLibPanel
"""

import os

from PySide6 import QtWidgets, QtCore, QtUiTools

from matlib import branding


class UsdDialog(QtWidgets.QDialog):
    """
    Material Import Dialog attached to the MatLibPanel
    """

    def __init__(
        self,
        cat_list: list[str],
        default_cat: str = "",
        name: str | None = None,
    ) -> None:
        """name: when given, the dialog shows an editable Name row
        prefilled with it (COP saves need both: pick category AND set
        name). Materials pass nothing and keep their node-derived
        naming, since a single name field can't serve their
        multi-selection saves."""
        super(UsdDialog, self).__init__()
        self.script_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

        self.categories = ""
        self.tags = ""
        self.fav = False
        self.name = name or ""
        self.canceled = False

        loader = QtUiTools.QUiLoader()
        file = QtCore.QFile(self.script_path + "/ui/material_dialog.ui")
        file.open(QtCore.QFile.ReadOnly)
        self.ui = loader.load(file)
        file.close()

        mainlayout = QtWidgets.QVBoxLayout()
        mainlayout.addWidget(self.ui)
        # Padding between the dialog border and its content/buttons,
        # like native dialogs have (~10px rendered) - the old zeroed
        # margins left everything flush against the edges once the
        # dialog started hugging its content.
        mainlayout.setContentsMargins(5, 5, 5, 5)

        self.setLayout(mainlayout)
        self.setWindowTitle("Save to " + branding.APP_NAME)

        # The .ui's root is a full QMainWindow (with a status bar and a
        # 350x200 minimum) embedded as a plain widget - that's where the
        # dialog's odd empty space and loose sizing came from. Neutralize
        # at runtime rather than editing the .ui: drop the minimum, hide
        # the status bar, and let the dialog hug its content at a fixed
        # size like a normal save dialog.
        self.ui.setMinimumSize(0, 0)
        statusbar = self.ui.findChild(QtWidgets.QStatusBar)
        if statusbar is not None:
            statusbar.setVisible(False)
        mainlayout.setSizeConstraint(
            QtWidgets.QLayout.SizeConstraint.SetFixedSize
        )

        # Favorite doesn't belong in the save dialog - hide the whole
        # row; cb_fav stays alive (hidden, unchecked) so confirm()
        # keeps working unchanged and self.fav is always False here.
        fav_label = self.ui.findChild(QtWidgets.QLabel, "label_3")
        if fav_label is not None:
            fav_label.setVisible(False)
        fav_row = self.ui.findChild(QtWidgets.QHBoxLayout, "horizontalLayout_3")
        parent_layout = self.ui.findChild(QtWidgets.QVBoxLayout, "verticalLayout_2")
        if fav_row is not None and parent_layout is not None:
            parent_layout.removeItem(fav_row)

        # Optional Name row, inserted above Category - built in code
        # (not the .ui, standing practice) mirroring the .ui rows'
        # label-plus-field QHBoxLayout shape.
        self.line_name = None
        if name is not None and parent_layout is not None:
            self.line_name = QtWidgets.QLineEdit()
            self.line_name.setText(name)
            name_row = QtWidgets.QHBoxLayout()
            name_row.addWidget(QtWidgets.QLabel("Name"))
            name_row.addWidget(self.line_name)
            parent_layout.insertLayout(0, name_row)

        self.combo_cats = self.ui.findChild(QtWidgets.QComboBox, "combo_categories")
        for cat in cat_list:
            self.combo_cats.addItem(cat)
        self.combo_cats.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.InsertAtTop)

        # Pre-select the category currently active in the panel so a
        # material dropped while browsing e.g. "Fabric" defaults to it.
        if default_cat:
            default_index = self.combo_cats.findText(default_cat)
            if default_index >= 0:
                self.combo_cats.setCurrentIndex(default_index)

        self.line_tags = self.ui.findChild(QtWidgets.QLineEdit, "line_tags")
        # Keeps the content-hugging fixed-size dialog from coming out
        # cramped - the input fields set the resulting width.
        self.line_tags.setMinimumWidth(280)
        self.cb_fav = self.ui.findChild(QtWidgets.QCheckBox, "cb_fav")
        self.cb_fav.setVisible(False)

        self.buttons = self.ui.findChild(QtWidgets.QDialogButtonBox, "buttonBox")
        self.buttons.accepted.connect(self.confirm)
        self.buttons.rejected.connect(self.destroy)

    def confirm(self) -> None:
        """
        Confirm material Creation

        :param self: Description
        """
        # self.categories = self.line_cats.text()
        self.categories = self.combo_cats.currentText()
        self.tags = self.line_tags.text()
        self.fav = self.cb_fav.isChecked()
        if self.line_name is not None:
            self.name = self.line_name.text().strip()
        self.accept()

    def destroy(self, destroyWindow=True, destroySubWindows=True) -> None:
        """
        Cancel material Creation

        :param self: Description
        :param destroyWindow: Description
        :param destroySubWindows: Description
        """
        self.canceled = True
        self.close()
