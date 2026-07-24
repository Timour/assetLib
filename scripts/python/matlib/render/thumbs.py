import os
import time
import hou

from matlib.core import debug
from matlib.core import material
from matlib.render import nodes, thumbnail_scene
from matlib.prefs import prefs
from matlib.helpers import helpers

import importlib

importlib.reload(nodes)
importlib.reload(thumbnail_scene)


def _node_errors(node):
    """Houdini's own cook errors/warnings for a node - the message that
    actually explains a failed render, which never reaches a print()."""
    if node is None:
        return {}
    out = {}
    for label, call in (("errors", "errors"), ("warnings", "warnings")):
        try:
            out[label] = list(getattr(node, call)())
        except Exception:
            pass
    return out


class ThumbNailRenderer:
    def __init__(
        self, preferences: prefs.Prefs, mat: material.Material | None = None
    ) -> None:
        self._mat = mat
        self._preferences = preferences
        self._builder = None
        self._preferences.load()

    def create_thumbnail(self) -> None:
        node_handler = nodes.NodeHandler(self._preferences)
        if self._mat:
            node_handler.import_asset_to_scene(self._mat)

        try:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                if material.is_karma_renderer(self._mat.renderer):
                    self.create_thumb_mtlx(node_handler.builder_node, self._mat.mat_id)
                elif self._mat.renderer == "Mantra":
                    self.create_thumb_mantra(node_handler.builder_node, self._mat.mat_id)
                elif self._mat.renderer == "Redshift":
                    self.create_thumb_redshift(node_handler.builder_node, self._mat.mat_id)
                elif self._mat.renderer == "Octane":
                    self.create_thumb_octane(node_handler.builder_node, self._mat.mat_id)
                elif self._mat.renderer == "Arnold":
                    self.create_thumb_arnold(node_handler.builder_node, self._mat.mat_id)

                else:
                    pass
        finally:
            # Runs even if the user interrupts the render (ESC) so the
            # imported material copy never lingers in /mat.
            node_handler.cleanup()

    def build_karma_scaffold(self):
        """Build the reusable Karma thumbnail scaffold ONCE: the lopnet,
        the shaderball USD reference (the expensive part - a full stage
        composition), the floor material, the Karma render properties
        and the ROP. Everything here is identical for every material -
        only the material library and output paths change per render
        (see render_karma_into). Returns a scaffold dict, or None if no
        Scene Viewer is open (OCIO display/view come from it).

        Render All builds this once and reuses it across the whole
        batch instead of paying the USD stage load per material; a
        single render (create_thumb_mtlx) builds and destroys its own.
        """
        viewer = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.SceneViewer)
        if not viewer:
            return None

        display = viewer.getOCIODisplay()
        view = viewer.getOCIOView()

        space = "ACEScg"
        for s in hou.Color.ocio_spaces():
            if "acescg" in s.lower():
                space = s
                break

        net = hou.node("/obj").createNode("lopnet")
        try:
            ref = net.createNode("reference::2.0")
            if self._preferences.ballmode:
                ref.parm("filepath1").set(
                    hou.getenv("ASSETLIB")
                    + "/scripts/python/matlib/res/usd/shaderBallScene2.usd"
                )
            else:
                ref.parm("filepath1").set(
                    hou.getenv("ASSETLIB")
                    + "/scripts/python/matlib/res/usd/shaderBallScene2_Simple.usd"
                )

            ref.parm("primpath1").set("/shaderBallScene")
            lib1 = net.createNode("materiallibrary")
            lib1.setFirstInput(ref)
            surf = lib1.createNode("mtlxstandard_surface")
            tex = lib1.createNode("mtlxtiledimage")
            tex.parm("file").set("color3")
            tex.parm("file").set("$ASSETLIB/scripts/python/matlib/res/img/FloorTexture.rat")
            surf.setInput(1, tex, 0)
            surf.setGenericFlag(hou.nodeFlag.Material, True)
            lib1.parm("materials").set(1)
            lib1.parm("matnode1").set("mtlxstandard_surface1")
            lib1.parm("matpath1").set("/thumb/bg_material")
            lib1.parm("geopath1").set("/shaderBallScene/geo/plane/mesh_0")
            lib1.parm("assign1").set(1)

            preferences = net.createNode("karmarenderproperties")
            preferences.parm("camera").set("/shaderBallScene/cameras/RenderCam")
            preferences.parm("res_mode").set("manual")
            preferences.parm("res_mode").pressButton()
            preferences.parm("resolutionx").set(self._preferences.rendersize)
            preferences.parm("resolutiony").deleteAllKeyframes()
            preferences.parm("resolutiony").set(self._preferences.rendersize)
            # CPU engine, not XPU - much faster for small images like
            # these thumbnails (XPU's device startup overhead dominates
            # at this size). Samples from the Karma-specific pref
            # (default 9, Karma's own default) - deliberately NOT
            # prefs.rendersamples, which is the Redshift thumbnail dial
            # and lives at a very different scale (256).
            preferences.parm("engine").set("cpu")
            preferences.parm("engine").pressButton()
            preferences.parm("pathtracedsamples").set(
                self._preferences.karma_rendersamples
            )
            preferences.parm("enabledof").set(0)
            preferences.parm("enablemblur").set(0)

            rop = net.createNode("usdrender_rop")
            rop.parm("renderer").set("BRAY_HdKarma")  # Karma CPU
            rop.setFirstInput(preferences)
            rop.parm("soho_foreground").set(1)
        except Exception:
            net.destroy()
            raise
        return {
            "net": net,
            "lib1": lib1,
            "preferences": preferences,
            "rop": rop,
            "display": display,
            "view": view,
            "space": space,
        }

    def render_karma_into(self, scaffold, node, asset_id: str) -> bool:
        """Render one material into a pre-built scaffold. Creates and
        destroys ONLY the per-material nodes (the material library and
        the exr->png copnet); the scaffold's lopnet/reference/floor/
        render-properties/ROP persist for the next material. Identical
        pixels to the old single-shot create_thumb_mtlx - same wiring,
        same version branch, same cache-busting timestamped EXR."""
        net = scaffold["net"]
        lib1 = scaffold["lib1"]
        preferences = scaffold["preferences"]
        rop = scaffold["rop"]
        display = scaffold["display"]
        view = scaffold["view"]
        space = scaffold["space"]

        # Build path. UNIQUE per render (timestamp suffix): Houdini
        # caches images by file path, so a rerender writing to the same
        # intermediate EXR name could have its EXR->PNG conversion
        # served the PREVIOUS render's cached pixels - a fresh-looking
        # PNG with stale content, which is exactly what rerender/
        # overwrite produced (a stale leftover EXR from an interrupted
        # run causes the same). A never-reused name defeats both.
        path = (
            self._preferences.dir
            + self._preferences.img_dir
            + str(asset_id)
            + "."
            + str(int(time.time() * 1000))
            + ".acescg.exr"
        )

        lib = None
        copnet = None
        try:
            lib = net.createNode("materiallibrary")
            lib.setFirstInput(lib1)

            curr_items = node
            # print(curr_items)  # TODO: Fails if is list
            if not isinstance(node, list):
                if curr_items.type().name() == "subnet":
                    curr_items = (node,)
                elif "mtlxopen_pbr_surface" in curr_items.type().name():
                    curr_items = (node,)
                else:
                    curr_items = node.children()

            with debug.timed("batch", "copy nodes into lib",
                             asset_id=str(asset_id)):
                curr_nodes = hou.copyNodesTo(curr_items, lib)  # type: ignore
            curr_nodes[0].setSelected(True)

            # The render-time __activate__/opacity patch that used to live
            # here is gone: the material engine (nodes.activate_shader_inputs)
            # activates every input RECURSIVELY at build time, so the saved
            # material is already correct - and this loop only ever reached
            # TOP-LEVEL nodes anyway (the images are nested), which is why
            # it never actually fixed anything. Materials built now (clean
            # translator / converter output) carry no __activate__ parms at
            # all.
            mat_node = None
            for n in curr_nodes:
                if n.type().name() == "collect":
                    n.setGenericFlag(hou.nodeFlag.Material, True)
                    mat_node = n

            if mat_node is None:
                for n in curr_nodes:
                    if n.type().name() == "mtlxstandard_surface":
                        n.setGenericFlag(hou.nodeFlag.Material, True)
                        mat_node = n
                        break
                    elif "subnet" in n.type().name():
                        n.setGenericFlag(hou.nodeFlag.Material, True)
                        mat_node = n
                        break

            # One explicit material entry (same pattern the floor library
            # lib1 above already uses), not fillmaterials - auto-fill
            # created an entry per material-ish node in the copied network
            # (shader, displacement, collect...), and the extra entries'
            # prims never generate (they're all part of the ONE material),
            # producing a yellow "Ignoring missing explicit primitive:
            # /materials/<name>" node error per extra entry on every
            # single thumbnail render.
            if debug.is_on():
                debug.event(
                    "thumb", "karma material content",
                    asset_id=str(asset_id),
                    ocio_display=scaffold.get("display"),
                    ocio_view=scaffold.get("view"),
                    textures=debug.texture_snapshot(curr_nodes[0]),
                )
            debug.event(
                "thumb", "karma material node chosen",
                asset_id=str(asset_id),
                mat_node=mat_node.name() if mat_node else None,
                mat_type=mat_node.type().name() if mat_node else None,
                candidates=[(n.name(), n.type().name()) for n in curr_nodes],
            )
            if mat_node is not None:
                lib.parm("materials").set(1)
                lib.parm("matnode1").set(mat_node.name())
                lib.parm("matpath1").set("/materials/" + mat_node.name())
            else:
                # No recognisable material node - fall back to auto-fill
                # rather than render nothing.
                lib.parm("fillmaterials").pressButton()
            lib.parm("assign1").set(1)
            lib.parm("geopath1").set("/shaderBallScene/geo/ball")

            preferences.parm("picture").set(path)
            preferences.setFirstInput(lib)

            with debug.timed("batch", "husk render (rop execute)",
                             asset_id=str(asset_id)):
                rop.parm("execute").pressButton()

            if not os.path.exists(path):
                # The render produced nothing - fail loudly instead of
                # letting the conversion step write a PNG from stale
                # data (the old PNG stays, honestly old).
                debug.event(
                    "thumb", "karma render produced no EXR",
                    asset_id=str(asset_id), expected=path,
                    rop=rop.path(),
                    rop_errors=_node_errors(rop),
                    lib_errors=_node_errors(lib),
                )
                print(
                    "Amaze: Karma thumbnail render produced no EXR "
                    "for " + str(asset_id) + " - keeping the old thumbnail"
                )
                return False

            if hou.applicationVersion()[0] > 20:
                # Copnet Setup
                copnet = net.createNode("copnet")
                copnet.setName("exr_to_png", unique_name=True)

                cop_file = copnet.createNode("file")

                cop_file.parm("filename").set(path)
                cop_file.parm("aovs").set(1)
                cop_file.parm("aov1").set("C")
                cop_out = copnet.createNode("rop_image")
                cop_out.parm("trange").set(0)

                cop_out.setInput(0, cop_file)
                cop_out.parm("colorconversion").set(1)  # Set to Bake OpenColorIO
                cop_out.parm("ociodisplay").set(display)
                cop_out.parm("ocioview").set(view)

                newpath = (
                    self._preferences.dir
                    + self._preferences.img_dir
                    + str(asset_id)
                    + self._preferences.img_ext
                )

                cop_out.parm("copoutput").set(newpath)
                with debug.timed("batch", "exr->png conversion",
                                 asset_id=str(asset_id)):
                    cop_out.parm("execute").pressButton()

            else:  # Use Old COPs with restricted OCIO Capabilities
                # Copnet Setup
                copnet = net.createNode("cop2net")
                copnet.setName("exr_to_png", unique_name=True)

                cop_file = copnet.createNode("file")
                cop_file.parm("nodename").set(0)
                cop_file.parm("filename1").set(path)
                cop_file.parm("colorspace").set(3)  # Set to OpenColorIO
                cop_file.parm("ocio_space").set(space)
                cop_out = copnet.createNode("rop_comp")
                cop_out.parm("trange").set(0)

                cop_out.setInput(0, cop_file)
                cop_out.parm("convertcolorspace").set(3)
                cop_out.parm("ocio_display").set(display)
                cop_out.parm("ocio_view").set(view)

                newpath = (
                    self._preferences.dir
                    + self._preferences.img_dir
                    + str(asset_id)
                    + self._preferences.img_ext
                )

                cop_out.parm("copoutput").set(newpath)
                cop_out.parm("execute").pressButton()
        finally:
            # Destroy ONLY the per-material nodes so the scaffold is
            # clean for the next material (single-shot then destroys the
            # whole net anyway). Runs on interrupt too - no orphaned
            # material lib / copnet left behind.
            if copnet is not None:
                copnet.destroy()
            if lib is not None:
                lib.destroy()

        if os.path.exists(path):
            os.remove(path)

        # Measure the PNG that was actually written. "It looks black" is
        # ambiguous at tile size - an all-zero render, a transparent
        # image and a stale file are different bugs, and this tells them
        # apart without another round-trip.
        if debug.is_on():
            png = (
                self._preferences.dir
                + self._preferences.img_dir
                + str(asset_id)
                + self._preferences.img_ext
            )
            debug.event("thumb", "karma thumbnail written",
                        asset_id=str(asset_id), **debug.image_stats(png))

        return True

    def create_thumb_mtlx(self, node: hou.Node, asset_id: str) -> bool:
        """Single Karma thumbnail: build a throwaway scaffold, render
        one material into it, destroy it. Render All uses the scaffold
        across the whole batch instead (build_karma_scaffold +
        render_karma_into)."""
        scaffold = self.build_karma_scaffold()
        if scaffold is None:
            return False
        try:
            return self.render_karma_into(scaffold, node, asset_id)
        finally:
            # Runs even if the render is interrupted so no orphaned
            # lopnet (with live ROP) stays in /obj.
            scaffold["net"].destroy()

    def create_thumb_mantra(self, node: hou.Node, asset_id: str) -> bool:
        # Create Thumbnail
        sc = thumbnail_scene.ThumbNailScene("Mantra", self._preferences.ballmode)
        thumb = sc.get_node()
        # thumb.setself._preferences.ballmode
        thumb.parm("mat").set(node.path())

        # Build path. Intermediate EXR name is UNIQUE per render - same
        # image-cache staleness hazard as create_thumb_mtlx above.
        path = self._preferences.dir + self._preferences.img_dir + str(asset_id)
        exr_path = path + "." + str(int(time.time() * 1000)) + ".exr"

        try:
            #  Set Renderpreferences and Object Exclusions for Thumbnail Rendering
            thumb.parm("path").set(exr_path)

            thumb.parm("cop_out_img").set(path + self._preferences.img_ext)
            exclude = "* ^" + thumb.name()
            thumb.parm("obj_exclude").set(exclude)
            lights = thumb.name() + "/*"
            thumb.parm("lights").set(lights)
            thumb.parm("resx").set(self._preferences.rendersize)
            thumb.parm("resy").set(self._preferences.rendersize)

            # Render Frame
            thumb.parm("render").pressButton()
        finally:
            # CleanUp - runs even if the render is interrupted so no
            # orphaned thumbnail scene (with live ROP) stays in /obj.
            thumb.destroy()
        if os.path.exists(exr_path):
            os.remove(exr_path)
        return True

    @staticmethod
    def _pick_cop_thumb_source(temp: hou.Node) -> hou.Node | None:
        """FALLBACK-ONLY picker for assets saved before the source-node
        name was recorded at save time (which is the reliable path -
        see create_thumb_cop). Shares the exact logic of the live pick
        via helpers.pick_cop_display_child so the two can't drift."""
        return helpers.pick_cop_display_child(temp)

    def create_thumb_geo_file(
        self, file_path: str, out_path: str, size: int
    ) -> bool:
        """Thumbnail for a geometry FILE (the v2 Geometry section):
        temp geo + the right loader SOP for the extension, orthographic
        camera fitted on the (container-rotated) bounding box, env
        light, rendered through the FLIPBOOK ROP (Vulkan viewport
        renderer; Karma CPU fallback) with the retina-quadrant
        compensation - the verified end state of an eleven-round
        debugging saga. Everything is destroyed in finally. Returns
        False (with an AssetLib-prefixed console reason) on any failure
        - callers treat that as "no thumbnail", never an error."""
        lowered = file_path.lower()
        if lowered.endswith(".abc"):
            loader_type = "alembic"
        elif lowered.endswith((".usd", ".usda", ".usdc")):
            loader_type = "usdimport"
        else:
            loader_type = "file"

        def _set(node, name, value):
            parm = node.parm(name)
            if parm is not None:
                try:
                    parm.set(value)
                except hou.OperationFailed:
                    pass

        base = os.path.basename(file_path)
        geo = None
        cam = None
        light = None
        rop = None
        try:
            geo = hou.node("/obj").createNode("geo")
            try:
                loader = geo.createNode(loader_type)
            except hou.OperationFailed:
                print(
                    "Amaze: geometry thumbnail - no '%s' SOP available"
                    " for %s" % (loader_type, base)
                )
                return False
            file_parm = helpers.find_file_parm(loader)
            if file_parm is None:
                print(
                    "Amaze: geometry thumbnail - no file parm on the "
                    + loader_type
                    + " SOP"
                )
                return False
            file_parm.set(file_path)
            loader.setDisplayFlag(True)
            try:
                loader.setRenderFlag(True)
            except AttributeError:
                pass

            bbox = None
            try:
                geometry = loader.geometry()
                if geometry is not None:
                    bbox = geometry.boundingBox()
            except hou.Error:
                bbox = None
            if bbox is None or bbox.sizevec().length() == 0:
                print(
                    "Amaze: geometry thumbnail - could not cook "
                    + base
                    + " (unsupported format or empty file), skipped"
                )
                return False
            # THE step-back fix after the "still not centered" round:
            # the fit numbers and resolution were verified correct in
            # a live console capture, leaving exactly one suspect -
            # the hand-built lookat rotation matrix, the only hand math
            # left in the chain. It is now GONE ENTIRELY: the camera
            # keeps its DEFAULT orientation (identity looks down -Z, by
            # Houdini's own definition) and just gets translated to
            # straight in front of the geometry. The 3/4 view comes
            # from rotating the GEO CONTAINER instead, via plain rotate
            # parms, and the bbox corners are transformed by
            # hou.hmath.buildRotate - Houdini's own matrix builder,
            # Houdini's own conventions, nothing hand-rolled anywhere.
            _set(geo, "rx", -20.0)
            _set(geo, "ry", 35.0)
            rot = hou.hmath.buildRotate(hou.Vector3(-20.0, 35.0, 0.0))

            cam = hou.node("/obj").createNode("cam")
            _set(cam, "resx", size)
            _set(cam, "resy", size)
            # ORTHOGRAPHIC framing (the "zoom out and find it" round):
            # perspective framing under the Vulkan rasterizer was too
            # tight by a large factor even though the IDENTICAL fit
            # math framed perfectly under Karma - meaning the two
            # renderers interpret the camera's perspective attributes
            # (aperture/focal/aspect/res-override crop semantics)
            # differently, and that interpretation isn't pinned down
            # anywhere scriptable. Ortho removes the entire question:
            # the visible width IS one number (orthowidth), no focal,
            # no aperture, no fov derivation - and it's the classic
            # product-shot look for asset thumbnails anyway. Fit: the
            # largest per-axis extent of any bbox corner projected onto
            # the camera's own x/y axes, plus margin.
            # Transform the LOCAL bbox corners into world space with
            # Houdini's own rotation matrix, then fit axis-aligned - the
            # camera has no rotation, so world x/y ARE the image axes.
            min_vec = bbox.minvec()
            max_vec = bbox.maxvec()
            world_min = None
            world_max = None
            for cx in (min_vec[0], max_vec[0]):
                for cy in (min_vec[1], max_vec[1]):
                    for cz in (min_vec[2], max_vec[2]):
                        corner = hou.Vector3(cx, cy, cz) * rot
                        if world_min is None:
                            world_min = hou.Vector3(corner)
                            world_max = hou.Vector3(corner)
                        else:
                            for axis in range(3):
                                world_min[axis] = min(world_min[axis], corner[axis])
                                world_max[axis] = max(world_max[axis], corner[axis])
            world_center = (world_min + world_max) * 0.5
            half_x = (world_max[0] - world_min[0]) * 0.5
            half_y = (world_max[1] - world_min[1]) * 0.5
            half_z = (world_max[2] - world_min[2]) * 0.5
            ortho_width = max(half_x, half_y, 0.0005) * 2.0 * 1.1
            distance = half_z * 2.0 + ortho_width

            # Straight down +Z in front of the rotated geometry -
            # translation only, orientation stays identity.
            _set(cam, "tx", world_center[0])
            _set(cam, "ty", world_center[1])
            _set(cam, "tz", world_center[2] + distance)
            _set(cam, "projection", "ortho")
            _set(cam, "orthowidth", ortho_width)
            # The Vulkan rasterizer honors near/far clip planes strictly
            # (raytracers effectively don't) - scale them to the fitted
            # distance so no model scale can clip.
            _set(cam, "near", max(distance * 0.001, 0.00001))
            _set(cam, "far", distance + half_z * 4.0 + ortho_width)
            light = hou.node("/obj").createNode("envlight")

            # Renderer: the FLIPBOOK ROP, per the H22 manual (standing
            # project rule) - opengl is "scheduled to be deleted",
            # flipbook is its designated replacement and has been
            # accepted by the strict camera check in an earlier round.
            # Its documented pages don't enumerate parms, so the node
            # ANNOUNCES its own relevant parm names once per session
            # (the "flipbook ROP parms" console line) - future
            # adjustments get made from that ground truth, not doc
            # archaeology. Karma CPU (a previously proven material
            # combination) is the fallback if flipbook is missing or
            # camera-less. NOTE: flipbook renders with the VIEWPORT's
            # configured renderer - with the viewport on the plain
            # Vulkan rasterizer it's fast; a viewport parked on a Karma
            # delegate makes every thumbnail a viewport-quality Karma
            # render (the earlier ~5min/file round).
            rop = None
            renderer_used = ""
            try:
                candidate = hou.node("/out").createNode("flipbook")
                if candidate.parm("camera") is not None:
                    rop = candidate
                    renderer_used = "flipbook ROP (viewport renderer)"
                    # EMPIRICAL RETINA-QUADRANT COMPENSATION. A full
                    # parm dump + screenshots decoded to: the
                    # flipbook's output is the LOWER-LEFT QUADRANT of a
                    # double-size framebuffer (visible span = exactly
                    # half the set orthowidth, subject displaced up-
                    # right by a quarter frame - the 2x Retina backing
                    # store, this project's most-confirmed platform
                    # constant, inside the Vulkan capture). Compensate:
                    # double the orthowidth and shift the camera center
                    # up-right by half the INTENDED span, so the
                    # captured quadrant IS the intended frame. Flipbook
                    # branch only - the Karma fallback renders the
                    # camera faithfully and must stay uncompensated. If
                    # SideFX fixes the capture (or on a non-HiDPI
                    # display) this overcorrects in the exact opposite
                    # signature - instantly recognizable and worth a
                    # bug report to SideFX either way.
                    _set(cam, "orthowidth", ortho_width * 2.0)
                    _set(cam, "tx", world_center[0] + ortho_width * 0.5)
                    _set(cam, "ty", world_center[1] + ortho_width * 0.5)
                    # Scope the render to OUR nodes - the dump showed
                    # vobjects=* / alights=*, which pulls the user's
                    # whole /obj scene (and its lights) into every
                    # thumbnail.
                    _set(rop, "vobjects", geo.path())
                    _set(rop, "alights", light.path())
                    # Shading mode from Preferences (default: wire over
                    # shaded; plain shaded reads too flat).
                    # Set by menu token, verified by reading it back.
                    # Runs BEFORE the background block deliberately.
                    shading = rop.parm("shadingmode")
                    wanted = getattr(
                        self._preferences,
                        "geometry_shading_mode",
                        "smoothwireshaded",
                    )
                    if shading is not None:
                        # Resolve against the parm's OWN menu instead of
                        # guessing token spellings: live testing showed
                        # 'smoothwireshaded' rejected with the default
                        # landing on 'smooth' - the real tokens are the
                        # short forms ('smooth'/'smoothwire'/...). The
                        # pref keeps its long descriptive value; this
                        # maps it onto whatever this build actually
                        # offers. (Historical note: the round where
                        # wires WORKED did so via a set(9) index
                        # fallback that a later edit replaced with the
                        # same broken string token - that, not
                        # lighting, is when wires died.)
                        try:
                            menu_items = shading.parmTemplate().menuItems()
                        except hou.Error:
                            menu_items = ()
                        resolved = wanted if wanted in menu_items else None

                        def _norm(token_string):
                            for junk in ("shaded", "frame", "line", "_"):
                                token_string = token_string.replace(junk, "")
                            return token_string

                        if resolved is None:
                            normalized = _norm(wanted)
                            for item in menu_items:
                                if _norm(item) == normalized:
                                    resolved = item
                                    break
                        if resolved is None:
                            normalized = _norm(wanted)
                            for item in menu_items:
                                item_normalized = _norm(item)
                                if (
                                    item_normalized in normalized
                                    or normalized in item_normalized
                                ):
                                    resolved = item
                                    break
                        if resolved is not None:
                            try:
                                shading.set(resolved)
                            except hou.OperationFailed:
                                resolved = None
                        if resolved is None:
                            print(
                                "Amaze: geometry thumbnail - no shading "
                                "menu token matches '" + wanted + "'; this "
                                "build offers: " + ", ".join(menu_items)
                            )
                    # Full-strength wires: the flipbook default is
                    # wireblend=0.5 (wires half-faded toward the geo
                    # color). That read fine against the bright grey-sky
                    # renders, but killing the sky for the solid
                    # background also removed its LIGHT - the mesh
                    # renders darker/flatter and half-blended wires
                    # disappear into it ("wireframe not respected").
                    _set(rop, "wireblend", 1.0)
                    _set(rop, "wirewidth", 1.0)
                    # Background from Preferences: the flipbook's own
                    # backdrop is its procedural sky (the washed grey =
                    # skyground 0.2 in the parm dump); a solid bgimage
                    # replaces it deterministically for contrast.
                    bg_mode = getattr(
                        self._preferences, "geometry_bg", "white"
                    )
                    if bg_mode in ("black", "white"):
                        bg_file = (
                            hou.getenv("ASSETLIB")
                            + "/scripts/python/matlib/res/img/geo_bg_"
                            + bg_mode
                            + ".png"
                        )
                        if os.path.exists(bg_file):
                            _set(rop, "bgimage", bg_file)
                            _set(rop, "skyusesky", 0)
                        else:
                            print(
                                "Amaze: geometry thumbnail - bg image "
                                "missing at " + bg_file
                            )
                    # Positive in-effect report - re-announces whenever
                    # the LOOK changes (a mid-session Preferences flip
                    # of mode/bg included), so the console always names
                    # what the newest renders used.
                    look_key = (wanted, bg_mode)
                    if look_key != getattr(
                        ThumbNailRenderer, "_geo_look_announced", None
                    ):
                        ThumbNailRenderer._geo_look_announced = look_key
                        try:
                            in_effect = shading.evalAsString() if shading else "?"
                        except hou.Error:
                            in_effect = "?"
                        print(
                            "Amaze: geometry look in effect - shading '%s', "
                            "wireblend %s, bg '%s'"
                            % (
                                in_effect,
                                rop.parm("wireblend").eval()
                                if rop.parm("wireblend")
                                else "?",
                                bg_mode,
                            )
                        )
                else:
                    candidate.destroy()
            except hou.OperationFailed:
                rop = None
            if rop is None:
                try:
                    rop = hou.node("/out").createNode("karma")
                    renderer_used = "karma ROP (CPU)"
                except hou.OperationFailed:
                    print(
                        "Amaze: geometry thumbnail - neither flipbook "
                        "nor karma ROP available, skipped"
                    )
                    return False
                _set(rop, "engine", "cpu")
                _set(rop, "samplesperpixel", 9)
            if renderer_used != getattr(
                ThumbNailRenderer, "_geo_rop_announced", None
            ):
                ThumbNailRenderer._geo_rop_announced = renderer_used
                print(
                    "Amaze: geometry thumbnails rendering via "
                    + renderer_used
                )
                # Ground truth: the COMPLETE parm list with current
                # values, wrapped in short lines (the previous single-
                # line dump got display-truncated exactly before the
                # interesting region). One full paste closes every
                # which-parm question for good.
                names = sorted(p.name() for p in rop.parms())
                print("Amaze: " + rop.type().name() + " ROP parms:")
                for start in range(0, len(names), 6):
                    chunk = []
                    for name in names[start:start + 6]:
                        try:
                            value = rop.parm(name).eval()
                        except hou.Error:
                            value = "?"
                        chunk.append("%s=%s" % (name, value))
                    print("Amaze:   " + "  ".join(chunk))
            # Square resolution override, tried across the common parm
            # namings - whichever exists on this build wins; the parm
            # list printed above shows which landed.
            _set(rop, "tres", 1)
            _set(rop, "res1", size)
            _set(rop, "res2", size)
            _set(rop, "res_overridex", size)
            _set(rop, "res_overridey", size)
            _set(rop, "aspect", 1.0)
            _set(rop, "trange", 0)
            cam_parm = rop.parm("camera")
            if cam_parm is None:
                print(
                    "Amaze: geometry thumbnail - ROP has no camera "
                    "parm, skipped"
                )
                return False
            cam_parm.set(cam.path())
            picture_parm = rop.parm("picture") or helpers.find_file_parm(rop)
            if picture_parm is None:
                print(
                    "Amaze: geometry thumbnail - no output picture parm "
                    "found on the " + rop.type().name() + " ROP, skipped"
                )
                return False
            picture_parm.set(out_path)
            rop.render()
            if not os.path.exists(out_path):
                print(
                    "Amaze: geometry thumbnail - render finished but "
                    "wrote no image for " + base
                )
                return False
            return True
        finally:
            for node in (rop, light, cam, geo):
                if node is not None:
                    try:
                        node.destroy()
                    except hou.ObjectWasDeleted:
                        pass

    def create_thumb_cop(self, asset_id: str, source_name: str = "") -> bool:
        """Thumbnail for a standalone COP-network asset (the v2 Cop
        section): the network's own display/output image IS the
        thumbnail - no shaderball/lights/camera. Works on a temporary
        copy loaded from the just-saved asset file (never the scene
        node), writes the image via a rop_image COP created inside it,
        and always destroys the copy. Every failure path prints an
        AssetLib-prefixed reason - callers treat False as
        "registered without a thumbnail", never as a save failure."""
        out_path = (
            self._preferences.dir
            + self._preferences.img_dir
            + str(asset_id)
            + self._preferences.img_ext
        )
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )
        temp = None
        try:
            temp = hou.node("/obj").createNode("copnet")
            temp.loadItemsFromFile(file_name, ignore_load_warnings=True)

            # The node whose image gets written. The RECORDED name wins:
            # save_node_cop reads the display flag off the LIVE network
            # at save time and persists the chosen node's name, because
            # flag state doesn't reliably survive the items-file
            # round-trip - two live tests picked wrong nodes in opposite
            # directions when heuristics ran on the loaded copy. The
            # heuristic chain below is only the fallback for assets
            # saved before the name was recorded.
            out = None
            if source_name:
                out = temp.node(source_name)
                if out is None:
                    print(
                        "Amaze: COP thumbnail - recorded source node '"
                        + source_name
                        + "' not found in the loaded copy, falling back"
                    )
            if out is None:
                out = self._pick_cop_thumb_source(temp)
            if out is None:
                print("Amaze: COP thumbnail - network is empty, skipped")
                return False

            rop = temp.createNode("rop_image")
            # Multi-output nodes (sim blocks etc.): prefer the output
            # actually named like a color image over a blind index 0.
            out_index = 0
            out_name = ""
            try:
                names = list(out.outputNames())
                for i, name in enumerate(names):
                    lowered = name.lower()
                    if "color" in lowered or "rgb" in lowered or lowered == "c":
                        out_index = i
                        out_name = name
                        break
                if not out_name and names:
                    out_name = names[0]
            except AttributeError:
                pass
            print(
                "Amaze: COP thumbnail rendering "
                + out.name()
                + (" output '" + out_name + "'" if out_name else "")
            )
            try:
                rop.setInput(0, out, out_index)
            except hou.InvalidInput:
                print(
                    "Amaze: COP thumbnail - rop_image would not accept "
                    "the output node as input, skipped"
                )
                return False
            # The output-picture parm found generically (FileReference
            # string parm) rather than by a hardcoded name - same
            # mechanism as texture load-to-node (helpers.find_file_parm).
            picture_parm = helpers.find_file_parm(rop)
            if picture_parm is None:
                print(
                    "Amaze: COP thumbnail - no file parm found on "
                    "rop_image, skipped"
                )
                return False
            picture_parm.set(out_path)

            # Render the single current frame: rop_image is ROP-like, so
            # prefer the real render() call, falling back to pressing
            # its execute button if it isn't a hou.RopNode here.
            if isinstance(rop, hou.RopNode):
                rop.render()
            else:
                execute_parm = rop.parm("execute")
                if execute_parm is None:
                    print(
                        "Amaze: COP thumbnail - rop_image has no "
                        "execute parm to press, skipped"
                    )
                    return False
                execute_parm.pressButton()

            if not os.path.exists(out_path):
                print(
                    "Amaze: COP thumbnail - render finished but wrote "
                    "no image at " + out_path
                )
                return False
            return True
        finally:
            if temp is not None:
                temp.destroy()

    def create_thumb_redshift(self, node: hou.Node, asset_id: str) -> bool:

        # Create Thumbnail
        sc = thumbnail_scene.ThumbNailScene("Redshift", self._preferences.ballmode)
        thumb = sc.get_node()
        try:
            thumb.parm("mat").set(node.path())

            # Build path
            path = (
                self._preferences.dir
                + self._preferences.img_dir
                + str(asset_id)
                + self._preferences.img_ext
            )

            #  Set Rendersettings and Object Exclusions for Thumbnail Rendering
            thumb.parm("path").set(path)
            exclude = "* ^" + thumb.name()
            thumb.parm("obj_exclude").set(exclude)
            lights = thumb.name() + "/*"
            thumb.parm("lights").set(lights)
            thumb.parm("resx").set(self._preferences.rendersize)
            thumb.parm("resy").set(self._preferences.rendersize)

            # Sampling quality from the Redshift-specific pref. This ROP
            # previously set no sampling parms at all (rendered on ROP
            # defaults) - prefs.rendersamples' only consumer used to be
            # the Karma path, which now has its own karma_rendersamples,
            # so this pref is the Redshift dial now. Max only: the min
            # stays at the ROP default so Redshift's adaptive sampling
            # still decides how much of the budget each pixel needs.
            # safe_set so a renamed parm on some RS version degrades
            # gracefully, like the rest of the RS ROP setup.
            thumbnail_scene.safe_set(
                sc.rop, "UnifiedMaxSamples", self._preferences.rendersamples
            )

            # Render Frame
            thumb.parm("render").pressButton()
        finally:
            # CleanUp - runs even if the render is interrupted so no
            # orphaned thumbnail scene (with live ROP) stays in /obj.
            thumb.destroy()
        return True

    def create_thumb_octane(self, node: hou.Node, asset_id: str) -> bool:
        # Create Thumbnail
        sc = thumbnail_scene.ThumbNailScene("Octane", self._preferences.ballmode)
        thumb = sc.get_node()
        # Build path
        path = (
            self._preferences.dir
            + self._preferences.img_dir
            + str(asset_id)
            + self._preferences.img_ext
        )

        try:
            thumb.parm("mat").set(node.path())

            # Set Rendersettings and Object Exclusions for Thumbnail Rendering
            thumb.parm("path").set(path)
            exclude = "* ^" + thumb.name()
            thumb.parm("obj_exclude").set(exclude)
            lights = thumb.name() + "/*"
            thumb.parm("lights").set(lights)
            thumb.parm("resx").set(self._preferences.rendersize)
            thumb.parm("resy").set(self._preferences.rendersize)

            # Render Frame
            thumb.parm("render").pressButton()
        finally:
            # CleanUp - runs even if the render is interrupted so no
            # orphaned thumbnail scene (with live ROP) stays in /obj.
            thumb.destroy()
        return True

    def create_thumb_arnold(self, node: hou.Node, asset_id: str) -> bool:
        # Create Thumbnail
        sc = thumbnail_scene.ThumbNailScene("Arnold", self._preferences.ballmode)
        thumb = sc.get_node()

        # Build path. Intermediate EXR name is UNIQUE per render - same
        # image-cache staleness hazard as create_thumb_mtlx above.
        path = self._preferences.dir + self._preferences.img_dir + str(asset_id)
        exr_path = path + "." + str(int(time.time() * 1000)) + ".exr"

        try:
            thumb.parm("mat").set(node.path())

            #  Set Rendersettings and Object Exclusions for Thumbnail Rendering
            thumb.parm("path").set(exr_path)
            thumb.parm("cop_out_img").set(path + self._preferences.img_ext)

            exclude = "* ^" + thumb.name()
            thumb.parm("obj_exclude").set(exclude)
            lights = thumb.name() + "/*"
            thumb.parm("lights").set(lights)
            thumb.parm("resx").set(self._preferences.rendersize)
            thumb.parm("resy").set(self._preferences.rendersize)
            thumb.parm("render").pressButton()

            # WaitForRender - A really bad hack
            done_path = hou.getenv("ASSETLIB") + "/lib/done.txt"
            mustend = time.time() + 60.0
            while time.time() < mustend:
                if os.path.exists(done_path):
                    os.remove(done_path)
                    time.sleep(2)
                    break
                time.sleep(1)
        finally:
            # CleanUp - runs even if the render is interrupted so no
            # orphaned thumbnail scene (with live ROP) stays in /obj.
            thumb.destroy()

        if os.path.exists(exr_path):
            os.remove(exr_path)

        return True
