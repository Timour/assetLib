"""
Preferences Dialog attached to the MatLibPanel
"""

import os
import subprocess

import hou

from matlib.helpers import theme
from PySide6 import QtWidgets, QtCore, QtGui, QtUiTools
from PySide6.QtGui import QCloseEvent

from matlib.core import debug, texture_library
from matlib.helpers import ui_helpers


class PrefsDialog(QtWidgets.QDialog):
    """
    Preferences Dialog attached to the MatLibPanel
    """

    def __init__(self, prefs, texture_files_model=None) -> None:
        super(PrefsDialog, self).__init__()
        self.script_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

        self._prefs = prefs
        self._texture_files_model = texture_files_model

        loader = QtUiTools.QUiLoader()
        file = QtCore.QFile(self.script_path + "/ui/prefs.ui")
        file.open(QtCore.QFile.ReadOnly)
        self.ui = loader.load(file)
        file.close()

        # self.ui (loaded from prefs.ui) is used purely as a widget
        # factory below - its own group-box/layout structure is never
        # shown. Every visible row in this dialog is instead rebuilt in
        # code as a QFormLayout matching the main panel's "details view"
        # exactly (see details_form in matlib.ui: right-aligned label
        # column, field column to the right) so the whole dialog reads as
        # one consistent set of rows instead of a mix of layouts.
        # prefs.ui itself is never edited - same standing practice as
        # every other v2 UI change in this project: the .ui file is
        # maintained externally in Qt Designer and must never be edited
        # from code.

        self.line_workdir = self.ui.findChild(QtWidgets.QLineEdit, "line_workdir")
        self.line_workdir.setDisabled(True)

        self.line_rendersize = self.ui.findChild(QtWidgets.QSpinBox, "line_rendersize")
        self.line_rendersize.valueChanged.connect(self.set_rendersize)

        self.line_rendersamples = self.ui.findChild(
            QtWidgets.QSpinBox, "line_rendersamples"
        )
        self.line_rendersamples.valueChanged.connect(self.set_rendersamples)

        self._combo_ballmode = self.ui.findChild(
            QtWidgets.QComboBox, "combo_shaderball"
        )
        self._combo_ballmode.addItem("Simple")
        self._combo_ballmode.addItem("Complex (V1)")
        self._combo_ballmode.currentIndexChanged.connect(self.set_ballmode)

        self.cbx_render_on_import = self.ui.findChild(
            QtWidgets.QCheckBox, "cbx_renderOnImport"
        )
        # prefs.ui sets this checkbox to RightToLeft (the old trick for
        # putting the box on the widget's right side) - that fights the
        # shared form column and parks this one box out of line with
        # every other tick box. Runtime property change, not a .ui edit.
        self.cbx_render_on_import.setLayoutDirection(
            QtCore.Qt.LayoutDirection.LeftToRight
        )
        # Houdini's checkbox convention (as in its own dialogs):
        # the BOX sits at the field column with its text to the box's
        # RIGHT, and the label column stays empty - so every checkbox
        # row is addRow("", box) with the text on the checkbox itself.
        self.cbx_render_on_import.setText("Render Thumbs on Import")
        self.cbx_render_on_import.stateChanged.connect(self.set_render_on_import)

        # The renderer checkboxes already carry their text ("Karma",
        # "Mantra", ...) in prefs.ui - exactly what the box-then-text
        # convention needs, so it is kept as-is.
        self._cbx_matx = self.ui.findChild(QtWidgets.QCheckBox, "cbx_matx")
        self._cbx_matx.toggled.connect(self.toggle_matx)
        self._cbx_mantra = self.ui.findChild(QtWidgets.QCheckBox, "cbx_mantra")
        self._cbx_mantra.toggled.connect(self.toggle_mantra)
        self._cbx_redshift = self.ui.findChild(QtWidgets.QCheckBox, "cbx_redshift")
        self._cbx_redshift.toggled.connect(self.toggle_redshift)
        self._cbx_octane = self.ui.findChild(QtWidgets.QCheckBox, "cbx_octane")
        self._cbx_octane.toggled.connect(self.toggle_octane)

        # Load Config from settings
        self.fill_values()

        # Flat sections divided by 1px lines (same color as the line
        # under the toolbar), not QGroupBoxes - Houdini's stylesheet
        # renders those as light-grey rounded boxes, which this dialog
        # deliberately avoids. ONE QFormLayout for the whole dialog
        # (sections are spanning header rows), so every field and tick
        # box shares a single column - aligned with the RenderSize
        # field's left edge.
        form = self._make_form()

        self._add_section_header(form, "Library Settings", first=True)
        form.addRow("Working Directory", self.line_workdir)

        self._add_section_header(form, "Render Settings")
        # The three numeric render settings read like Houdini's own
        # parameter rows: number field + slider, kept in sync
        # (reference: the parameter-edit dialog's Size row).
        form.addRow(
            "RenderSize", self._field_slider_row(self.line_rendersize, 64, 1024)
        )
        # The pre-existing samples spinbox (prefs.ui) is the Redshift
        # thumbnail dial now - its pref (rendersamples) drives the
        # Redshift ROP's UnifiedMaxSamples. Karma gets its own,
        # code-created control on a separate pref (karma_rendersamples,
        # default 9 = Karma's own default) since Karma CPU thumbnails
        # need a completely different sample scale than Redshift.
        form.addRow(
            "RenderSamples (Redshift)",
            self._field_slider_row(self.line_rendersamples, 1, 1024),
        )
        self.spin_karma_samples = QtWidgets.QSpinBox()
        self.spin_karma_samples.setValue(self._prefs.karma_rendersamples)
        self.spin_karma_samples.valueChanged.connect(self.set_karma_rendersamples)
        form.addRow(
            "RenderSamples (Karma)",
            self._field_slider_row(self.spin_karma_samples, 1, 256),
        )
        # v2: shared thumbnail RAM budget - past it, least-recently-
        # viewed thumbnails drop from memory and reload from disk when
        # scrolled back into view (Materials now; Textures/Geometry to
        # join; Cop always stays fully resident).
        self.spin_ram_cache = QtWidgets.QSpinBox()
        self.spin_ram_cache.setRange(64, 4096)
        self.spin_ram_cache.setValue(self._prefs.ram_cache_mb)
        self.spin_ram_cache.valueChanged.connect(self.set_ram_cache_mb)
        form.addRow(
            "RAM Cache (MB)",
            self._field_slider_row(self.spin_ram_cache, 64, 2048),
        )
        form.addRow("ShaderBall", self._combo_ballmode)
        # v2: geometry thumbnail shading mode (flipbook shadingmode menu
        # tokens - the wire variants keep low-poly meshes from reading
        # as flat silhouettes). Takes effect on newly rendered
        # thumbnails; each mode keeps its own disk cache.
        self._combo_geo_shading = QtWidgets.QComboBox()
        for label, token in (
            ("Smooth Wire Shaded", "smoothwireshaded"),
            ("Smooth Shaded", "smoothshaded"),
            ("Flat Wire Shaded", "flatwireshaded"),
            ("Flat Shaded", "flatshaded"),
            ("Wireframe", "wireframe"),
            ("Hidden Line Ghost", "hiddenlineghost"),
            ("Hidden Line Invisible", "hiddenlineinvisible"),
        ):
            self._combo_geo_shading.addItem(label, token)
        current_index = self._combo_geo_shading.findData(
            self._prefs.geometry_shading_mode
        )
        self._combo_geo_shading.setCurrentIndex(max(current_index, 0))
        self._combo_geo_shading.currentIndexChanged.connect(
            self.set_geometry_shading_mode
        )
        form.addRow("Geometry Shading", self._combo_geo_shading)
        self._combo_geo_bg = QtWidgets.QComboBox()
        for label, token in (
            ("White", "white"),
            ("Black", "black"),
            ("Default (grey sky)", "default"),
        ):
            self._combo_geo_bg.addItem(label, token)
        bg_index = self._combo_geo_bg.findData(self._prefs.geometry_bg)
        self._combo_geo_bg.setCurrentIndex(max(bg_index, 0))
        self._combo_geo_bg.currentIndexChanged.connect(self.set_geometry_bg)
        form.addRow("Geometry Background", self._combo_geo_bg)
        form.addRow("", self.cbx_render_on_import)

        self._add_section_header(form, "Enabled Renderers")
        form.addRow("", self._cbx_matx)
        form.addRow("", self._cbx_mantra)
        form.addRow("", self._cbx_redshift)
        form.addRow("", self._cbx_octane)

        # v2: which section tabs are shown - a user who only wants
        # Materials + Code can hide the rest.
        self._add_section_header(form, "Sections")
        self._section_boxes = {}
        for key, label in (
            ("material", "Materials"),
            ("texture", "Textures"),
            ("gradient", "Colors"),
            ("cop", "Cop"),
            ("geometry", "Geometry"),
            ("code", "Code"),
        ):
            box = QtWidgets.QCheckBox(label)
            box.setChecked(key in self._prefs.enabled_sections)
            box.toggled.connect(self._on_section_toggled)
            self._section_boxes[key] = box
            form.addRow("", box)

        # v2: Texture thumbnail cache controls. Texture thumbnails
        # generate at RenderSize above (shared with materials' shaderball
        # render resolution, not a separate hidden setting), so the cache
        # path label depends on it and is kept up to date if RenderSize
        # changes while this dialog is open.
        self._add_section_header(form, "Texture Cache")
        self._cache_label = QtWidgets.QLabel()
        self._cache_label.setWordWrap(True)
        form.addRow("Cached Thumbnails", self._cache_label)
        clear_cache_btn = QtWidgets.QPushButton("Clear Thumbnail Caches (Textures + Geometry)")
        clear_cache_btn.clicked.connect(self.clear_texture_cache)
        form.addRow("", clear_cache_btn)
        self._update_cache_label()

        # v2: how many iconvert conversions run at once, plus the
        # force-iconvert escape hatch. Each iconvert call pays a fixed
        # Houdini-process startup cost regardless of file size, so
        # running several concurrently cuts wall-clock time roughly
        # proportionally on folders with many EXR/HDR files.
        self._add_section_header(form, "Texture Generation")
        # Same number-field + slider row as RenderSize - the
        # old live "(N)" label is redundant now that the field shows the
        # value directly.
        self.spin_parallel = QtWidgets.QSpinBox()
        self.spin_parallel.setValue(self._prefs.texture_parallel_conversions)
        self.spin_parallel.valueChanged.connect(self.set_texture_parallel)
        form.addRow(
            "Parallel Conversions", self._field_slider_row(self.spin_parallel, 1, 8)
        )

        self._cbx_force_iconvert = QtWidgets.QCheckBox("Force iconvert only")
        self._cbx_force_iconvert.setChecked(self._prefs.texture_force_iconvert)
        self._cbx_force_iconvert.toggled.connect(self.set_texture_force_iconvert)
        form.addRow("", self._cbx_force_iconvert)

        # v2: accent color for the size slider / texture progress bar.
        # Plain QColorDialog rather than Houdini's own hou.qt.ColorField -
        # the latter's docs don't specify what signal it emits on a color
        # change, and that can't be verified without live-testing in
        # Houdini, so a fully-documented Qt widget is the safer bet here.
        self._add_section_header(form, "Appearance")
        self._accent_swatch = QtWidgets.QPushButton()
        self._accent_swatch.setFixedSize(60, 24)
        self._set_accent_swatch(QtGui.QColor(self._prefs.accent_color))
        self._accent_swatch.clicked.connect(self.pick_accent_color)
        match_houdini_btn = QtWidgets.QPushButton("Match Houdini Accent Color")
        match_houdini_btn.clicked.connect(self.match_houdini_accent_color)
        if not theme.is_active():
            # The panel follows the Houdini 22 theme automatically
            # (accent = the theme's own accent role, no toggle), so the
            # manual picker only appears when no theme is readable
            # (e.g. Houdini 21).
            form.addRow("Accent Color", self._accent_swatch)
            form.addRow("", match_houdini_btn)

        self._cbx_sidebar_counts = QtWidgets.QCheckBox(
            "Show Counts on Categories"
        )
        self._cbx_sidebar_counts.setChecked(self._prefs.sidebar_counts)
        self._cbx_sidebar_counts.toggled.connect(self.set_sidebar_counts)
        form.addRow("", self._cbx_sidebar_counts)

        self._cbx_hide_empty = QtWidgets.QCheckBox("Hide Empty Categories")
        self._cbx_hide_empty.setChecked(self._prefs.hide_empty_categories)
        self._cbx_hide_empty.toggled.connect(self.set_hide_empty_categories)
        form.addRow("", self._cbx_hide_empty)

        # v2: favorite-star badge color: Background (stamped-hole look),
        # Yellow (amber sticker) or Custom (swatch button below).
        self._combo_star = QtWidgets.QComboBox()
        for label, token in (
            ("Background (stamped hole)", "background"),
            ("Yellow", "yellow"),
            ("Custom", "custom"),
        ):
            self._combo_star.addItem(label, token)
        star_index = self._combo_star.findData(self._prefs.star_color_mode)
        self._combo_star.setCurrentIndex(max(star_index, 0))
        self._combo_star.currentIndexChanged.connect(self.set_star_color_mode)
        form.addRow("Favorite Star", self._combo_star)

        self._star_swatch = QtWidgets.QPushButton()
        self._star_swatch.setFixedSize(60, 24)
        self._set_star_swatch(QtGui.QColor(self._prefs.star_custom_color))
        self._star_swatch.clicked.connect(self.pick_star_color)
        form.addRow("Custom Star Color", self._star_swatch)

        # v2: wheel scroll speed for the thumbnail grid/list, shown as a
        # percent (the pref itself is a float factor; 75% = the default
        # settled on after live tuning). Applied live -
        # DragDropListView reads the pref fresh on every wheel event.
        self.spin_scroll_speed = QtWidgets.QSpinBox()
        self.spin_scroll_speed.setRange(10, 300)
        self.spin_scroll_speed.setValue(round(self._prefs.scroll_speed * 100))
        self.spin_scroll_speed.valueChanged.connect(self.set_scroll_speed)
        form.addRow(
            "Scroll Speed (%)",
            self._field_slider_row(self.spin_scroll_speed, 10, 300),
        )

        # --- Online Materials --------------------------------------
        self._add_section_header(form, "Online Materials")
        self.cbb_matx_res = QtWidgets.QComboBox()
        for label in ("1k", "2k", "4k", "8k"):
            self.cbb_matx_res.addItem(label)
        current = self.cbb_matx_res.findText(self._prefs.matx_resolution)
        self.cbb_matx_res.setCurrentIndex(current if current >= 0 else 1)
        self.cbb_matx_res.currentTextChanged.connect(self.set_matx_resolution)
        self.cbb_matx_res.setToolTip(
            "Texture resolution to download. A floor, not a hard match: "
            "the next highest available is used, or the highest below."
        )
        form.addRow("Download Resolution", self.cbb_matx_res)

        self.spin_matx_parallel = QtWidgets.QSpinBox()
        self.spin_matx_parallel.setRange(1, 16)
        self.spin_matx_parallel.setValue(self._prefs.matx_parallel_downloads)
        self.spin_matx_parallel.valueChanged.connect(
            self.set_matx_parallel_downloads
        )
        self.spin_matx_parallel.setToolTip(
            "Preview downloads at once. These wait on network latency "
            "rather than bandwidth, so more is markedly faster."
        )
        form.addRow(
            "Parallel Downloads",
            self._field_slider_row(self.spin_matx_parallel, 1, 16),
        )

        # --- Debug ------------------------------------------------
        self._add_section_header(form, "Debug")
        self._cbx_debug = QtWidgets.QCheckBox("Debug Mode")
        self._cbx_debug.setChecked(self._prefs.debug_mode)
        self._cbx_debug.setToolTip(
            "Write a structured session log for diagnosing problems. "
            "Off by default."
        )
        self._cbx_debug.toggled.connect(self.set_debug_mode)
        form.addRow("", self._cbx_debug)

        self._debug_path_label = QtWidgets.QLabel(debug.log_path())
        self._debug_path_label.setWordWrap(True)
        self._debug_path_label.setEnabled(False)
        form.addRow("Log File", self._debug_path_label)

        debug_buttons = QtWidgets.QWidget()
        debug_row = QtWidgets.QHBoxLayout(debug_buttons)
        debug_row.setContentsMargins(0, 0, 0, 0)
        reveal_btn = QtWidgets.QPushButton("Show Log in Finder")
        reveal_btn.clicked.connect(self.reveal_debug_log)
        clear_btn = QtWidgets.QPushButton("Clear Log")
        clear_btn.clicked.connect(self.clear_debug_log)
        debug_row.addWidget(reveal_btn)
        debug_row.addWidget(clear_btn)
        debug_row.addStretch()
        form.addRow("", debug_buttons)

        # The form has outgrown the screen (Debug sits at the bottom and
        # was unreachable), so it lives in a scroll area. Interim, ahead
        # of a proper re-architecture of Preferences.
        #
        # Deliberately UNSTYLED: a stylesheet on this scroll area would
        # put the whole form subtree on Qt's CSS rendering path and knock
        # the fields off their native look - the documented details-panel
        # regression, and the cat_list scrollbar one.
        page = QtWidgets.QWidget()
        page.setLayout(form)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)          # form follows the width
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        mainlayout = QtWidgets.QVBoxLayout()
        mainlayout.setContentsMargins(12, 12, 12, 12)
        mainlayout.addWidget(scroll)
        self.setLayout(mainlayout)

        # Cap the window at most of the screen so it SCROLLS instead of
        # growing past the edge; short screens get a scrollbar, tall ones
        # still show everything at once.
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().height()
            self.setMaximumHeight(int(available * 0.85))
            self.resize(
                self.width(),
                min(page.sizeHint().height() + 40, int(available * 0.85)),
            )
        # ~300px (rendered) wider than the dialog's old natural width -
        # gives the new sliders real travel room.
        self.setMinimumWidth(480)

    def _add_section_header(
        self,
        form: QtWidgets.QFormLayout,
        title: str,
        first: bool = False,
    ) -> None:
        """Adds a flat section header as a spanning row in the shared
        form: 1px divider line above it (except the first, same color as
        the toolbar's bottom divider) and a grey title. Spanning rows in
        ONE form (instead of one form per section) are what keep every
        field/tick box aligned to a single column dialog-wide."""
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0 if first else 8, 0, 2)
        v.setSpacing(8)
        if not first:
            divider = QtWidgets.QWidget()
            divider.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True
            )
            divider.setStyleSheet("background-color: #434343;")
            divider.setFixedHeight(1)
            v.addWidget(divider)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("color: #999999;")
        v.addWidget(title_label)
        form.addRow(box)

    def _field_slider_row(
        self, spinbox: QtWidgets.QSpinBox, lo: int, hi: int
    ) -> QtWidgets.QWidget:
        """Houdini-style numeric parameter row: narrow number field +
        slider, kept in sync both ways (the mutual setValue connections
        terminate because setValue with an unchanged value emits
        nothing). The slider is the project's own ClickSlider so it
        matches the toolbar's size slider exactly."""
        spinbox.setRange(lo, hi)
        spinbox.setFixedWidth(64)
        spinbox.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        slider = ui_helpers.ClickSlider()
        slider.setOrientation(QtCore.Qt.Orientation.Horizontal)
        # No tick dots / snap magnets here - those belong to
        # the toolbar's thumbnail-size slider only.
        slider.snap_marks = ()
        slider.setRange(lo, hi)
        slider.setValue(spinbox.value())
        slider.set_accent_color(QtGui.QColor(self._prefs.accent_color))
        spinbox.valueChanged.connect(slider.setValue)
        slider.valueChanged.connect(spinbox.setValue)
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(spinbox)
        h.addWidget(slider, 1)
        return row

    def _set_accent_swatch(self, color: QtGui.QColor) -> None:
        self._accent_swatch.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #1a1a1a;"
        )

    def pick_accent_color(self) -> None:
        current = QtGui.QColor(self._prefs.accent_color)
        color = QtWidgets.QColorDialog.getColor(current, self, "Pick Accent Color")
        if not color.isValid():
            return
        self._prefs.accent_color = color.name()
        self._set_accent_swatch(color)

    def match_houdini_accent_color(self) -> None:
        """Reads Houdini's own selection/highlight color (the resource
        used throughout its UI for progress bars, selected list rows,
        active handles, etc. - see UIDark.hcs/UILight.hcs in
        $HFS/houdini/config) so the plugin can match whatever color
        scheme is currently active, including Houdini 22's own
        customizable UI colors."""
        try:
            color = hou.qt.getColor("ListEntrySelected")
        except Exception as exc:
            hou.ui.displayMessage(f"Could not read Houdini's accent color: {exc}")  # type: ignore
            return
        self._prefs.accent_color = color.name()
        self._set_accent_swatch(color)

    @staticmethod
    def _make_form() -> QtWidgets.QFormLayout:
        """A QFormLayout configured exactly like the main panel's details
        view (details_form in matlib.ui) - right-aligned label column,
        fields grow to fill the rest - so every group in this dialog
        reads as the same kind of row instead of a mix of layouts."""
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignTrailing
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        return form

    def set_texture_parallel(self, value: int) -> None:
        self._prefs.texture_parallel_conversions = value

    def set_matx_resolution(self, label: str) -> None:
        self._prefs.matx_resolution = label

    def set_matx_parallel_downloads(self, value: int) -> None:
        """Read fresh on every dispatch, so it applies to the next batch
        without a restart - same as the texture conversion count."""
        self._prefs.matx_parallel_downloads = value

    def set_debug_mode(self, checked: bool) -> None:
        """Takes effect immediately - the engine is reconfigured here as
        well as when the dialog closes, so a session can be captured
        without restarting Houdini."""
        self._prefs.debug_mode = checked
        debug.configure(checked)
        if checked:
            debug.prefs_snapshot(self._prefs)

    def reveal_debug_log(self) -> None:
        path = debug.log_path()
        folder = os.path.dirname(path)
        try:
            os.makedirs(folder, exist_ok=True)
            if os.path.exists(path):
                subprocess.call(["open", "-R", path])
            else:
                subprocess.call(["open", folder])
        except Exception as exc:
            print("Amaze: could not reveal the log: %s" % exc)

    def clear_debug_log(self) -> None:
        path = debug.log_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            print("Amaze: could not clear the log: %s" % exc)

    def set_texture_force_iconvert(self, checked: bool) -> None:
        self._prefs.texture_force_iconvert = checked

    def _update_cache_label(self) -> None:
        # Guarded: set_rendersize() can fire (via valueChanged) during
        # fill_values(), which runs before _cache_label is constructed.
        if not hasattr(self, "_cache_label"):
            return
        cache_dir = texture_library._cache_dir_for(self._prefs.rendersize)
        self._cache_label.setText(cache_dir)

    def clear_texture_cache(self) -> None:
        """Delete all cached texture thumbnails from disk (every
        resolution, not just the current RenderSize - see
        ThumbnailCache.clear()). They regenerate automatically next time
        each folder is browsed."""
        if not hou.ui.displayConfirmation(
            "This deletes all cached texture thumbnails from disk. They "
            "will regenerate automatically next time each folder is "
            "browsed. Continue?"
        ):
            return
        if self._texture_files_model is not None:
            self._texture_files_model.clear_cache()
        else:
            texture_library.ThumbnailCache(self._prefs.rendersize).clear()
        hou.ui.displayMessage("Texture thumbnail cache cleared.")

    def toggle_matx(self):
        """
        Docstring for toggle_matx

        :param self: Description
        """
        self._prefs.renderer_matx_enabled = (
            True if self._cbx_matx.isChecked() else False
        )

    def toggle_mantra(self):
        """
        En/Disable Renderer Mantra

        :param self: Description
        """
        self._prefs.renderer_mantra_enabled = (
            True if self._cbx_mantra.isChecked() else False
        )

    def toggle_redshift(self):
        """
        En/Disable Renderer Mantra

        :param self: Description
        """
        self._prefs.renderer_redshift_enabled = (
            True if self._cbx_redshift.isChecked() else False
        )

    def toggle_octane(self):
        """
        En/Disable Renderer Mantra

        :param self: Description
        """
        self._prefs.renderer_octane_enabled = (
            True if self._cbx_octane.isChecked() else False
        )

    def set_rendersize(self):
        """
        Set Rendersize (Disk) - also the resolution texture thumbnails
        generate at, so the cache path label needs to stay in sync.

        :param self: Description
        """
        self._prefs.rendersize = self.line_rendersize.value()
        self._update_cache_label()

    def set_rendersamples(self):
        """
        Set RenderSamples (Disk)

        :param self: Description
        """
        self._prefs.rendersamples = self.line_rendersamples.value()

    def set_scroll_speed(self, value: int) -> None:
        self._prefs.scroll_speed = value / 100.0

    def set_ram_cache_mb(self, value: int) -> None:
        self._prefs.ram_cache_mb = value
        self._prefs.save()

    def set_sidebar_counts(self, checked: bool) -> None:
        self._prefs.sidebar_counts = checked
        self._prefs.save()

    def _on_section_toggled(self, _checked: bool) -> None:
        """Rebuild enabled_sections from the checked boxes, in the fixed
        ALL_SECTIONS order. Never leave zero enabled - if the user
        unticks the last one, Materials is forced back on."""
        order = ("material", "texture", "gradient", "cop", "geometry", "code")
        enabled = [k for k in order if self._section_boxes[k].isChecked()]
        if not enabled:
            self._section_boxes["material"].blockSignals(True)
            self._section_boxes["material"].setChecked(True)
            self._section_boxes["material"].blockSignals(False)
            enabled = ["material"]
        self._prefs.enabled_sections = enabled
        self._prefs.save()

    def set_hide_empty_categories(self, checked: bool) -> None:
        self._prefs.hide_empty_categories = checked
        self._prefs.save()

    def set_star_color_mode(self, index: int) -> None:
        token = self._combo_star.itemData(index)
        if token:
            self._prefs.star_color_mode = token
            self._prefs.save()

    def _set_star_swatch(self, color: QtGui.QColor) -> None:
        self._star_swatch.setStyleSheet(
            "background-color: %s; border: 1px solid #222222;" % color.name()
        )

    def pick_star_color(self) -> None:
        current = QtGui.QColor(self._prefs.star_custom_color)
        color = QtWidgets.QColorDialog.getColor(current, self)
        if not color.isValid():
            return
        self._prefs.star_custom_color = color.name()
        self._prefs.save()
        self._set_star_swatch(color)

    def set_geometry_shading_mode(self, index: int) -> None:
        token = self._combo_geo_shading.itemData(index)
        if token:
            self._prefs.geometry_shading_mode = token
            self._prefs.save()

    def set_geometry_bg(self, index: int) -> None:
        token = self._combo_geo_bg.itemData(index)
        if token:
            self._prefs.geometry_bg = token
            self._prefs.save()

    def set_karma_rendersamples(self):
        """Set the Karma-specific thumbnail sample count"""
        self._prefs.karma_rendersamples = self.spin_karma_samples.value()

    def set_ballmode(self):
        """
        Set Chosen Shaderball

        :param self: Description
        """
        self._prefs.ballmode = self._combo_ballmode.currentIndex()

    def set_render_on_import(self):
        """
        Set if Thumbnails should be rendered on import to MatLib

        :param self: Description
        """
        self._prefs.render_on_import = int(self.cbx_render_on_import.isChecked())

    def closeEvent(self, arg__1: QCloseEvent) -> None:
        """
        Save Preferences on Close

        :param self: Description
        :param arg__1: Description
        :type arg__1: QCloseEvent
        """
        self._prefs.save()

    # Fill UI
    def fill_values(self) -> None:
        """
        Fill UI

        :param self: Description
        """
        self.directory = self._prefs.dir
        self.rendersize = self._prefs.rendersize
        self.render_on_import = self._prefs.render_on_import
        self.rendersamples = self._prefs.rendersamples
        self.ballmode = self._prefs.ballmode

        self.line_workdir.setText(self.directory)
        self.line_rendersize.setValue(self.rendersize)
        self.line_rendersamples.setValue(self.rendersamples)

        self._combo_ballmode.setCurrentIndex(self.ballmode)

        self.cbx_render_on_import.setChecked(self.render_on_import)

        self._cbx_matx.setChecked(self._prefs.renderer_matx_enabled)
        self._cbx_mantra.setChecked(self._prefs.renderer_mantra_enabled)
        self._cbx_redshift.setChecked(self._prefs.renderer_redshift_enabled)
        self._cbx_octane.setChecked(self._prefs.renderer_octane_enabled)
