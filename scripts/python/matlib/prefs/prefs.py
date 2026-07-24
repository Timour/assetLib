"""
Holds and loads the Preferences for the Matlib
"""

import os
import json
import hou


class Prefs:
    """
    Holds and loads the Preferences for the Matlib
    """

    def __init__(self) -> None:
        self.path: str = hou.getenv("ASSETLIB")
        self._directory = ""
        self.data = {}
        self._renderer_matx_enabled = False
        self._renderer_mantra_enabled = False
        self._renderer_arnold_enabled = False
        self._renderer_redshift_enabled = False
        self._renderer_octane_enabled = False
        # Panel view state: category list shown, details hidden by default
        self._show_categories = True
        self._show_details = False
        self._last_renderer = "All"
        self._view_mode = "grid"
        # Per-view-mode icon sizes: thumbsize = grid (legacy key),
        # thumbsize_list = list. Both match ClickSlider.DEFAULT_VALUE.
        self._thumbsize = 256
        self._thumbsize_list = 256
        # v2: registered folder pointers for the Textures section
        self._texture_folders: list[str] = []
        # v2: favorited texture files, stored as full absolute paths since
        # texture folders are arbitrary external directories with no
        # MatLib-owned id the way a material asset has
        self._texture_favorites: list[str] = []
        # v2: Geometry section - registered folder pointers, favorites
        # (full paths, same reasoning as textures) and the last-selected
        # folder, mirroring the texture trio exactly.
        self._geometry_folders: list[str] = []
        self._geometry_favorites: list[str] = []
        self._last_geometry_folder = ""
        # v2: per-section "Include Subfolders" toggles (sidebar
        # right-click) - default off, matching the original flat-scan
        # design; recursion is opt-in.
        self._texture_include_subfolders = False
        self._geometry_include_subfolders = False
        # v2: geometry thumbnail shading mode (flipbook ROP shadingmode
        # menu token). Default = wire over shaded - shaded alone looks
        # too flat without wires.
        self._geometry_shading_mode = "smoothwireshaded"
        # v2: geometry thumbnail background - "black"/"white" swap the
        # flipbook's grey sky for a solid bgimage (for contrast);
        # "default" keeps the flipbook's own look. White is the
        # default, together with the smoothwireshaded shading default
        # above.
        self._geometry_bg = "white"
        # v2: favorite-star badge color - "background" (stamped-hole
        # look, grid bg), "yellow" (amber sticker) or "custom" (the
        # hex in star_custom_color).
        self._star_color_mode = "background"
        # v2: show entry counts on INDIVIDUAL sidebar categories/folders
        # ("All" always shows its total regardless).
        self._sidebar_counts = True
        # v2: RAM budget (MB) for the shared thumbnail image cache -
        # past it, least-recently-viewed thumbnails drop from memory
        # and reload from disk when scrolled back into view.
        self._ram_cache_mb = 256
        # v2: hide sidebar categories with zero visible assets (for
        # Materials, "visible" respects the active renderer filter).
        # OFF = always show every category, the pre-hiding behavior.
        self._hide_empty_categories = True
        # v2: which section tabs are shown (order fixed elsewhere) - so
        # a user who only wants Materials + Code can hide the rest.
        self._enabled_sections = [
            "material", "texture", "gradient", "cop", "geometry", "code",
        ]
        self._star_custom_color = "#fcb900"
        # v2: favorited CURATED gradient combinations, as "<set>:<id>"
        # keys (e.g. "wada:132", "klee:7"). User gradients store their
        # favorite flag inline in gradients.json instead - they have no
        # stable id to key on here.
        self._gradient_favorites: list[str] = []
        # v2: last-selected folder in the Textures section - a real
        # folder path, TextureFolders.ALL_LABEL ("All") if that was
        # selected, or "" if nothing's ever been selected yet. Restored
        # both across Houdini sessions and when switching between the
        # Mat/Tex/COP tabs within one session.
        self._last_texture_folder = ""
        # v2: how many iconvert conversions run at once (1-8, default 4)
        self._texture_parallel_conversions = 4
        # v2: skip the native OS decoder (sips on macOS) and always use
        # iconvert - escape hatch in case the native path's color/tone
        # handling ever looks wrong compared to iconvert's
        self._texture_force_iconvert = False
        # v2: accent color for the size slider / progress bar, "#rrggbb".
        # Default matches ClickSlider.LEFT_COLOR.
        self._accent_color = "#5d7abd"
        # v2: Karma thumbnail samples, separate from rendersamples (which
        # is the Redshift thumbnail dial - wired into the Redshift ROP's
        # UnifiedMaxSamples). Karma renders thumbnails on the CPU engine
        # and needs far fewer; 9 is Karma's own default.
        self._karma_rendersamples = 9
        # v2: wheel scroll speed factor for the thumbnail grid/list
        # (DragDropListView applies trackpad pixel deltas scaled by
        # this). 0.75 is the default settled on after live tuning
        # (1.0 scrolled roughly twice as fast as it should); shown as
        # a percent in Preferences.
        self._scroll_speed = 0.75
        self._debug_mode = False
        self._matx_parallel_downloads = 8
        # Online MaterialX browser: preferred download resolution, and
        # whether the MtlX renderer shows in the Renderer filter.
        self._matx_resolution = "2k"
        self._renderer_mtlx_enabled = True

    def save(self) -> None:
        """
        Sanitize and Save the Preferences to disk as json
        """
        # Sanitize Filepath
        if not self._directory.endswith("/"):
            self._directory = self._directory + "/"
        self._directory.replace("\\", "/")

        self.data["directory"] = self._directory
        self.data["extension"] = self._ext
        self.data["img_extension"] = self._img_ext
        self.data["done_file"] = self.done_file
        self.data["img_dir"] = self._img_dir
        self.data["asset_dir"] = self._asset_dir
        self.data["rendersize"] = self._rendersize
        self.data["thumbsize"] = self._thumbsize
        self.data["thumbsize_list"] = self._thumbsize_list
        self.data["rendersamples"] = self._rendersamples
        self.data["render_on_import"] = self._render_on_import
        self.data["renderer_materialx"] = self._renderer_matx_enabled
        self.data["renderer_mantra"] = self._renderer_mantra_enabled
        self.data["renderer_redshift"] = self._renderer_redshift_enabled
        self.data["renderer_octane"] = self._renderer_octane_enabled
        self.data["renderer_arnold"] = self._renderer_arnold_enabled
        self.data["ballmode"] = self._ballmode
        self.data["show_categories"] = self._show_categories
        self.data["show_details"] = self._show_details
        self.data["last_renderer"] = self._last_renderer
        self.data["view_mode"] = self._view_mode
        self.data["texture_folders"] = self._texture_folders
        self.data["texture_favorites"] = self._texture_favorites
        self.data["gradient_favorites"] = self._gradient_favorites
        self.data["star_color_mode"] = self._star_color_mode
        self.data["sidebar_counts"] = self._sidebar_counts
        self.data["ram_cache_mb"] = self._ram_cache_mb
        self.data["hide_empty_categories"] = self._hide_empty_categories
        self.data["enabled_sections"] = self._enabled_sections
        self.data["star_custom_color"] = self._star_custom_color
        self.data["geometry_folders"] = self._geometry_folders
        self.data["geometry_favorites"] = self._geometry_favorites
        self.data["last_geometry_folder"] = self._last_geometry_folder
        self.data["texture_include_subfolders"] = self._texture_include_subfolders
        self.data["geometry_include_subfolders"] = self._geometry_include_subfolders
        self.data["geometry_shading_mode"] = self._geometry_shading_mode
        self.data["geometry_bg"] = self._geometry_bg
        self.data["last_texture_folder"] = self._last_texture_folder
        self.data["texture_parallel_conversions"] = self._texture_parallel_conversions
        self.data["texture_force_iconvert"] = self._texture_force_iconvert
        self.data["accent_color"] = self._accent_color
        self.data["karma_rendersamples"] = self._karma_rendersamples
        self.data["scroll_speed"] = self._scroll_speed
        self.data["debug_mode"] = self._debug_mode
        self.data["matx_parallel_downloads"] = self._matx_parallel_downloads
        self.data["matx_resolution"] = self._matx_resolution
        self.data["renderer_mtlx"] = self._renderer_mtlx_enabled

        with open(self.path + ("/settings.json"), "w", encoding="utf-8") as lib_json:
            json.dump(self.data, lib_json, indent=4)

    def load(self) -> bool:
        """
        Load the Preferences from disk as json
        """
        with open(self.path + ("/settings.json"), encoding="utf-8") as lib_json:
            data = json.load(lib_json)
            self._directory = data["directory"]
            self._ext = data["extension"]
            self._img_ext = data["img_extension"]
            self.done_file = data["done_file"]
            self._img_dir = data["img_dir"]
            self._asset_dir = data["asset_dir"]
            self._rendersize = data["rendersize"]
            # .get() with a default matching ClickSlider.DEFAULT_VALUE, in
            # case settings.json predates this key (thumbsize used to be
            # required here)
            self._thumbsize = data.get("thumbsize", 256)
            # Grid and list view each remember their own icon size
            # (e.g. grid at 128 and list at 32 should coexist).
            # thumbsize stays the grid size for backward compatibility;
            # the list size defaults to the grid size the first time so
            # nothing changes visually until it's adjusted in list mode.
            self._thumbsize_list = data.get("thumbsize_list", self._thumbsize)
            self._render_on_import = data["render_on_import"]
            self._renderer_matx_enabled = data["renderer_materialx"]
            self._renderer_mantra_enabled = data["renderer_mantra"]
            self._renderer_redshift_enabled = data["renderer_redshift"]
            self._renderer_octane_enabled = data["renderer_octane"]
            self._renderer_arnold_enabled = data["renderer_arnold"]
            self._rendersamples = data["rendersamples"]
            self._ballmode = data["ballmode"]
            # .get() so existing settings.json without these keys still loads
            self._show_categories = data.get("show_categories", True)
            self._show_details = data.get("show_details", False)
            self._last_renderer = data.get("last_renderer", "All")
            self._view_mode = data.get("view_mode", "grid")
            self._texture_folders = data.get("texture_folders", [])
            self._texture_favorites = data.get("texture_favorites", [])
            self._gradient_favorites = data.get("gradient_favorites", [])
            self._star_color_mode = data.get("star_color_mode", "background")
            self._sidebar_counts = data.get("sidebar_counts", True)
            self._ram_cache_mb = data.get("ram_cache_mb", 256)
            self._hide_empty_categories = data.get(
                "hide_empty_categories", True
            )
            self._enabled_sections = data.get(
                "enabled_sections",
                ["material", "texture", "gradient", "cop", "geometry", "code"],
            )
            self._star_custom_color = data.get("star_custom_color", "#fcb900")
            self._geometry_folders = data.get("geometry_folders", [])
            self._geometry_favorites = data.get("geometry_favorites", [])
            self._last_geometry_folder = data.get("last_geometry_folder", "")
            self._texture_include_subfolders = data.get(
                "texture_include_subfolders", False
            )
            self._geometry_include_subfolders = data.get(
                "geometry_include_subfolders", False
            )
            self._geometry_shading_mode = data.get(
                "geometry_shading_mode", "smoothwireshaded"
            )
            self._geometry_bg = data.get("geometry_bg", "white")
            self._last_texture_folder = data.get("last_texture_folder", "")
            self._texture_parallel_conversions = data.get(
                "texture_parallel_conversions", 4
            )
            self._texture_force_iconvert = data.get("texture_force_iconvert", False)
            self._accent_color = data.get("accent_color", "#5d7abd")
            self._karma_rendersamples = data.get("karma_rendersamples", 9)
            self._scroll_speed = data.get("scroll_speed", 0.75)
            self._debug_mode = bool(data.get("debug_mode", False))
            self._matx_parallel_downloads = int(
                data.get("matx_parallel_downloads", 8)
            )
            self._matx_resolution = data.get("matx_resolution", "2k")
            self._renderer_mtlx_enabled = data.get("renderer_mtlx", True)

            if os.path.exists(self._directory):
                return True
            return False
            # return self.get_dir_from_user(True)

    def get_dir_from_user(self) -> bool:
        """Get Directory from User and write into prefs"""
        count = 0
        while count < 3:
            if not os.path.exists(self._directory) or count < 1:
                if not os.path.exists(self._directory) and count < 1:
                    hou.ui.displayMessage("It looks like your library is not set up yet. Please choose a directory to store the library data")  # type: ignore
                elif count > 0:
                    hou.ui.displayMessage("Invalid Path selected. Please try again")
                path = hou.ui.selectFile(file_type=hou.fileType.Directory)
                if path == "":  # Canceled
                    return False
                self._directory = hou.expandString(path)
            else:
                print(f"Amaze: Library set successfully to {self._directory}")
                self.save()
                return True
            count += 1
        return False

    @property
    def dir(self) -> str:
        return self._directory

    @dir.setter
    def dir(self, val: str) -> None:
        self._directory = val

    @property
    def rendersize(self) -> int:
        return self._rendersize

    @rendersize.setter
    def rendersize(self, val: int) -> None:
        self._rendersize = val

    @property
    def rendersamples(self) -> int:
        return self._rendersamples

    @rendersamples.setter
    def rendersamples(self, val: int) -> None:
        self._rendersamples = val

    @property
    def ballmode(self) -> int:
        return self._ballmode

    @ballmode.setter
    def ballmode(self, val: int) -> None:
        self._ballmode = val

    @property
    def show_categories(self) -> bool:
        return self._show_categories

    @show_categories.setter
    def show_categories(self, val: bool) -> None:
        self._show_categories = val

    @property
    def show_details(self) -> bool:
        return self._show_details

    @show_details.setter
    def show_details(self, val: bool) -> None:
        self._show_details = val

    @property
    def last_renderer(self) -> str:
        return self._last_renderer

    @last_renderer.setter
    def last_renderer(self, val: str) -> None:
        self._last_renderer = val if val else "All"

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @view_mode.setter
    def view_mode(self, val: str) -> None:
        self._view_mode = val if val in ("grid", "list") else "grid"

    @property
    def thumbsize(self) -> int:
        """Icon size for GRID view (kept under the legacy 'thumbsize'
        key for backward compatibility)."""
        return self._thumbsize

    @thumbsize.setter
    def thumbsize(self, val: int) -> None:
        self._thumbsize = val

    @property
    def thumbsize_list(self) -> int:
        """Icon size for LIST view - independent of the grid size."""
        return self._thumbsize_list

    @thumbsize_list.setter
    def thumbsize_list(self, val: int) -> None:
        self._thumbsize_list = val

    @property
    def render_on_import(self) -> int:
        return self._render_on_import

    @render_on_import.setter
    def render_on_import(self, val: int) -> None:
        self._render_on_import = val

    @property
    def img_dir(self) -> str:
        return self._img_dir

    @property
    def asset_dir(self) -> str:
        return self._asset_dir

    @property
    def img_ext(self) -> str:
        return self._img_ext

    @property
    def ext(self) -> str:
        return self._ext

    @property
    def renderer_matx_enabled(self) -> bool:
        return self._renderer_matx_enabled

    @renderer_matx_enabled.setter
    def renderer_matx_enabled(self, val: bool) -> None:
        self._renderer_matx_enabled = val

    @property
    def renderer_mtlx_enabled(self) -> bool:
        """Visibility of the MtlX renderer (materials imported from the
        online MaterialX libraries) in the Renderer filter."""
        return self._renderer_mtlx_enabled

    @renderer_mtlx_enabled.setter
    def renderer_mtlx_enabled(self, val: bool) -> None:
        self._renderer_mtlx_enabled = bool(val)

    @property
    def matx_resolution(self) -> str:
        """Preferred texture resolution for online MaterialX downloads.
        A FLOOR, not a hard requirement: if a material lacks it, the
        importer takes the next highest, else the highest below (see
        matx_sources.pick_resolution)."""
        return self._matx_resolution

    @matx_resolution.setter
    def matx_resolution(self, val: str) -> None:
        self._matx_resolution = str(val or "2k")

    @property
    def renderer_mantra_enabled(self) -> bool:
        return self._renderer_mantra_enabled

    @renderer_mantra_enabled.setter
    def renderer_mantra_enabled(self, val: bool) -> None:
        self._renderer_mantra_enabled = val

    @property
    def renderer_arnold_enabled(self) -> bool:
        return self._renderer_arnold_enabled

    @renderer_arnold_enabled.setter
    def renderer_arnold_enabled(self, val: bool) -> None:
        self._renderer_arnold_enabled = val

    @property
    def renderer_redshift_enabled(self) -> bool:
        return self._renderer_redshift_enabled

    @renderer_redshift_enabled.setter
    def renderer_redshift_enabled(self, val: bool) -> None:
        self._renderer_redshift_enabled = val

    @property
    def renderer_octane_enabled(self) -> bool:
        return self._renderer_octane_enabled

    @renderer_octane_enabled.setter
    def renderer_octane_enabled(self, val: bool) -> None:
        self._renderer_octane_enabled = val

    @property
    def texture_folders(self) -> list[str]:
        return self._texture_folders

    def add_texture_folder(self, path: str) -> None:
        if path and path not in self._texture_folders:
            self._texture_folders.append(path)
            self.save()

    def remove_texture_folder(self, path: str) -> None:
        if path in self._texture_folders:
            self._texture_folders.remove(path)
            self.save()

    @property
    def texture_favorites(self) -> list[str]:
        return self._texture_favorites

    def add_texture_favorite(self, path: str) -> None:
        if path and path not in self._texture_favorites:
            self._texture_favorites.append(path)
            self.save()

    def remove_texture_favorite(self, path: str) -> None:
        if path in self._texture_favorites:
            self._texture_favorites.remove(path)
            self.save()

    @property
    def geometry_folders(self) -> list[str]:
        return self._geometry_folders

    def add_geometry_folder(self, path: str) -> None:
        if path and path not in self._geometry_folders:
            self._geometry_folders.append(path)
            self.save()

    def remove_geometry_folder(self, path: str) -> None:
        if path in self._geometry_folders:
            self._geometry_folders.remove(path)
            self.save()

    @property
    def geometry_favorites(self) -> list[str]:
        return self._geometry_favorites

    def add_geometry_favorite(self, path: str) -> None:
        if path and path not in self._geometry_favorites:
            self._geometry_favorites.append(path)
            self.save()

    def remove_geometry_favorite(self, path: str) -> None:
        if path in self._geometry_favorites:
            self._geometry_favorites.remove(path)
            self.save()

    @property
    def texture_include_subfolders(self) -> bool:
        return self._texture_include_subfolders

    @texture_include_subfolders.setter
    def texture_include_subfolders(self, val: bool) -> None:
        self._texture_include_subfolders = bool(val)

    @property
    def geometry_shading_mode(self) -> str:
        return self._geometry_shading_mode

    @geometry_shading_mode.setter
    def geometry_shading_mode(self, val: str) -> None:
        self._geometry_shading_mode = str(val or "smoothwireshaded")

    @property
    def geometry_bg(self) -> str:
        return self._geometry_bg

    @geometry_bg.setter
    def geometry_bg(self, val: str) -> None:
        self._geometry_bg = str(val or "white")

    @property
    def geometry_include_subfolders(self) -> bool:
        return self._geometry_include_subfolders

    @geometry_include_subfolders.setter
    def geometry_include_subfolders(self, val: bool) -> None:
        self._geometry_include_subfolders = bool(val)

    @property
    def last_geometry_folder(self) -> str:
        return self._last_geometry_folder

    @last_geometry_folder.setter
    def last_geometry_folder(self, val: str) -> None:
        self._last_geometry_folder = str(val or "")

    @property
    def sidebar_counts(self) -> bool:
        return self._sidebar_counts

    @sidebar_counts.setter
    def sidebar_counts(self, val: bool) -> None:
        self._sidebar_counts = bool(val)

    @property
    def ram_cache_mb(self) -> int:
        return self._ram_cache_mb

    @ram_cache_mb.setter
    def ram_cache_mb(self, val: int) -> None:
        self._ram_cache_mb = min(4096, max(64, int(val)))

    @property
    def hide_empty_categories(self) -> bool:
        return self._hide_empty_categories

    @hide_empty_categories.setter
    def hide_empty_categories(self, val: bool) -> None:
        self._hide_empty_categories = bool(val)

    @property
    def enabled_sections(self) -> list:
        return self._enabled_sections

    @enabled_sections.setter
    def enabled_sections(self, val) -> None:
        # Never leave the panel with no tabs - fall back to Materials.
        val = [str(k) for k in val] if val else []
        self._enabled_sections = val or ["material"]

    @property
    def star_color_mode(self) -> str:
        return self._star_color_mode

    @star_color_mode.setter
    def star_color_mode(self, val: str) -> None:
        self._star_color_mode = str(val or "background")

    @property
    def star_custom_color(self) -> str:
        return self._star_custom_color

    @star_custom_color.setter
    def star_custom_color(self, val: str) -> None:
        self._star_custom_color = str(val or "#fcb900")

    @property
    def gradient_favorites(self) -> list[str]:
        return self._gradient_favorites

    def add_gradient_favorite(self, key: str) -> None:
        if key and key not in self._gradient_favorites:
            self._gradient_favorites.append(key)
            self.save()

    def remove_gradient_favorite(self, key: str) -> None:
        if key in self._gradient_favorites:
            self._gradient_favorites.remove(key)
            self.save()

    @property
    def last_texture_folder(self) -> str:
        return self._last_texture_folder

    @last_texture_folder.setter
    def last_texture_folder(self, val: str) -> None:
        self._last_texture_folder = val or ""

    @property
    def texture_parallel_conversions(self) -> int:
        return self._texture_parallel_conversions

    @texture_parallel_conversions.setter
    def texture_parallel_conversions(self, val: int) -> None:
        self._texture_parallel_conversions = max(1, min(8, int(val)))

    @property
    def texture_force_iconvert(self) -> bool:
        return self._texture_force_iconvert

    @texture_force_iconvert.setter
    def texture_force_iconvert(self, val: bool) -> None:
        self._texture_force_iconvert = bool(val)

    @property
    def karma_rendersamples(self) -> int:
        return self._karma_rendersamples

    @karma_rendersamples.setter
    def karma_rendersamples(self, val: int) -> None:
        self._karma_rendersamples = max(1, int(val))

    @property
    def scroll_speed(self) -> float:
        return self._scroll_speed

    @scroll_speed.setter
    def scroll_speed(self, val: float) -> None:
        self._scroll_speed = max(0.1, min(3.0, float(val)))

    @property
    def debug_mode(self) -> bool:
        """Write a structured session log for deep analysis. OFF by
        default - it is a diagnostic tool, not a normal running mode."""
        return self._debug_mode

    @debug_mode.setter
    def debug_mode(self, val: bool) -> None:
        self._debug_mode = bool(val)

    @property
    def matx_parallel_downloads(self) -> int:
        """Concurrent preview downloads in the online browser.

        These are latency-bound, not bandwidth-bound (a 40KB thumbnail
        takes ~470ms from GPUOpen), so concurrency scales almost
        linearly: measured over 32 PolyHaven previews, 1 -> 220ms each,
        8 -> 42ms, 16 -> 18ms. Capped at 16 to stay a polite client of
        free public APIs."""
        return self._matx_parallel_downloads

    @matx_parallel_downloads.setter
    def matx_parallel_downloads(self, val: int) -> None:
        self._matx_parallel_downloads = max(1, min(16, int(val)))

    @property
    def accent_color(self) -> str:
        return self._accent_color

    @accent_color.setter
    def accent_color(self, val: str) -> None:
        self._accent_color = val if val else "#5d7abd"

    def get_done_file(self) -> str:
        """Get Extension for done_file for singaling rendering process within houdini"""
        return self.done_file
