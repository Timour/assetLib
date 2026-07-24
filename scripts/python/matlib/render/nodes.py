"""
Handles all Node Interaction with Houdini
"""

import os
import re
import hou
import voptoolutils

import importlib

from matlib.render import thumbs
from matlib.core import material
from matlib.prefs import prefs
from matlib.core import debug
from matlib.helpers import helpers

importlib.reload(thumbs)


def make_karma_builder(parent: hou.Node, name: str) -> hou.Node:
    """Create a MaterialX Material Builder subnet matching the
    KARMA_REF reference material EXACTLY.

    A plain "subnet" is NOT a MaterialX builder: its Tab menu doesn't
    offer mtlx* nodes and its network isn't picked up as a material.
    This calls voptoolutils._setupMtlXBuilderSubnet - the same function
    Houdini's own shelf tools use.

    **Two flavours exist, and this uses the one KARMA_REF uses** (read
    from the saved reference, 2026-07-20):

    | | render_context | output nodes |
    |---|---|---|
    | Karma Material Builder | `kma` | one `suboutput` + `kma_material_properties` |
    | **MaterialX Material Builder** (KARMA_REF, this) | `mtlx` | two `subnetconnector`s: `surface_output` / `displacement_output` |

    The mtlx flavour is not just a style choice - it's also more
    ROBUST. Its output terminals are separate `subnetconnector` nodes
    each carrying its own `parmname` ("surface" / "displacement"), so
    destroying the starter shader can't wipe the terminal names the way
    it wipes a `suboutput`'s `name1`/`name2` parms (the pitch-black bug).
    `kma_rampconst` and other `kma_*` nodes still create fine here via
    createNode - the tab-menu mask only limits the interactive Tab menu,
    not programmatic creation (verified).

    Starter `mtlxstandard_surface`/`mtlxdisplacement` are removed (real
    content is built/loaded right after); the `subnetconnector` output
    nodes are KEPT. Wire the shader in with `wire_builder_output()`.

    Shared by the import path AND the Redshift->Karma converter, so a
    converted material is structurally identical to a hand-built one."""
    builder = parent.createNode("subnet")
    builder.setName(helpers.sanitize_usd_path(name), unique_name=True)
    builder = voptoolutils._setupMtlXBuilderSubnet(
        subnet_node=builder,
        name=name,
        mask=voptoolutils.MTLX_TAB_MASK,
        folder_label="MaterialX Builder",
        render_context="mtlx",
    )
    for child in builder.children():
        if child.type().name() in (
            "mtlxstandard_surface",
            "mtlxdisplacement",
            "kma_material_properties",
        ):
            child.destroy()
    return builder


def wire_builder_output(builder, surface_node, displacement_node=None):
    """Wire a shader (and optional displacement) into a builder's output
    terminals, for EITHER builder flavour.

    - MaterialX flavour (KARMA_REF, what make_karma_builder now makes):
      two `subnetconnector` nodes, matched by `parmname` surface /
      displacement; the shader goes into the connector's input 0.
    - Karma flavour (`suboutput`): surface = input 0, displacement =
      input 1. Kept so SAVED materials of the older flavour still wire
      correctly on load.

    Returns True if a surface terminal was found and wired."""
    connectors = {}
    suboutput = None
    for child in builder.children():
        tname = child.type().name()
        if tname == "subnetconnector":
            kind = child.parm("connectorkind")
            if kind is not None and kind.eval() == 1:      # output
                pn = child.parm("parmname")
                connectors[pn.eval() if pn else ""] = child
        elif tname == "suboutput":
            suboutput = child

    try:
        if connectors:
            wired = False
            if surface_node is not None and "surface" in connectors:
                connectors["surface"].setInput(0, surface_node)
                wired = True
            if displacement_node is not None and "displacement" in connectors:
                connectors["displacement"].setInput(0, displacement_node)
            return wired
        if suboutput is not None:
            if surface_node is not None:
                suboutput.setInput(0, surface_node)
            if displacement_node is not None:
                suboutput.setInput(1, displacement_node)
            return surface_node is not None
    except hou.Error as exc:
        # hou.Error, not just OperationFailed: a wrong-type source (e.g. a
        # collect node into the surface terminal) raises hou.InvalidInput,
        # a sibling - which crashed the whole conversion instead of being
        # reported. The material engine's surface_terminal_wired() check
        # then flags the unwired result.
        debug.event("karma", "could not wire builder output", error=str(exc))
        print("Amaze: could not wire builder output: %s" % exc)
    return False


def surface_terminal_wired(builder) -> bool:
    """Does the builder have a NAMED surface terminal with something
    wired into it? The single invariant every Karma material must hold -
    a material without it renders pitch black, and the whole network can
    otherwise look perfect (see karma-material-builder.md)."""
    for child in builder.children():
        tname = child.type().name()
        if tname == "subnetconnector":
            kind = child.parm("connectorkind")
            pn = child.parm("parmname")
            if (kind is not None and kind.eval() == 1
                    and pn is not None and pn.eval() == "surface"):
                return any(child.inputs())
        elif tname == "suboutput":
            inputs = child.inputs()
            return bool(inputs) and inputs[0] is not None
    return False


def activate_shader_inputs(builder) -> int:
    """Turn ON every editmaterial `__activate__*` input toggle in the
    builder, RECURSIVELY.

    editmaterial (the online-import path) gives every node input an
    `__activate__<input>` toggle that defaults to **0 = off**, and a
    deactivated input is DROPPED from the USD export. So an image node
    whose `__activate__file` is 0 exports with no `inputs:file` at all -
    it reads nothing and the material renders **pitch black on
    everything**, even though the file parm holds a perfectly valid path
    at the VOP level.

    This was the real cause of the black online-imported materials. The
    thumbnail path tried to patch it but only looped over TOP-LEVEL
    nodes, so the images nested inside the `NG_` nodegraph were never
    reached. This does the whole tree, once, at build time, so the SAVED
    material is correct everywhere - thumbnail and a real object in LOP
    alike.

    Harmless where there are no such parms (the Redshift converter and
    the values path build fresh mtlx nodes that have none). Returns the
    count activated, for logging."""
    count = 0
    for node in builder.allSubChildren():
        for parm in node.parms():
            if "__activate__" in parm.name():
                try:
                    if parm.eval() != 1:
                        parm.set(1)
                        count += 1
                except hou.Error:
                    pass
    return count


def build_karma_material(parent, name, produce):
    """THE Karma material engine - one funnel every input goes through.

    Redshift conversion, online MaterialX import and re-import of a saved
    material are ADAPTERS: each only knows how to produce a shader network
    inside a builder. Everything universal - the KARMA_REF container, the
    output wiring, the layout, and the one invariant check - lives here,
    so a new input type is a new `produce` callback and nothing else, and
    a structural bug is fixed once for every input.

    `produce(builder)` builds the shading network inside `builder` and
    returns the surface shader node, or a `(shader, displacement)` tuple.
    Returns `(builder, shader)`; `shader` is None if the adapter produced
    nothing (the caller decides how to report that)."""
    builder = make_karma_builder(parent, name)
    result = produce(builder)
    if isinstance(result, tuple):
        shader, displacement = (result + (None,))[:2]
    else:
        shader, displacement = result, None

    if shader is not None:
        wire_builder_output(builder, shader, displacement)

    # Activate every input toggle, recursively - without this,
    # editmaterial-derived materials export with their texture `file`
    # inputs stripped and render pitch black (see activate_shader_inputs).
    activated = activate_shader_inputs(builder)
    if activated:
        debug.event("karma", "activated shader inputs",
                    material=name, count=activated)

    builder.layoutChildren()

    if shader is not None and not surface_terminal_wired(builder):
        # The check that would have caught the pitch-black bug on day one.
        debug.event(
            "karma", "material has no wired surface terminal",
            material=name, builder=builder.path(),
            children=[c.type().name() for c in builder.children()],
        )
        print(
            "Amaze: WARNING - '%s' has no wired surface terminal and "
            "will render black (see karma-material-builder.md)" % name
        )
    return builder, shader


class NodeHandler:
    """
    Handles all Node Interaction with Houdini
    """

    def __init__(self, preferences: prefs.Prefs) -> None:
        self._preferences = preferences
        self._builder_node = hou.node("/stage")
        self._builder = 0
        self._renderer = ""
        self._import_path = None
        self._hou_parent = None
        self._use_existing_node = False
        self._cop_info = {}
        # Optional explicit network context (a materiallibrary node or
        # an editor's pwd) that overrides the active-editor lookup -
        # set per-import by import_asset_to_scene(context_node=...),
        # used by the material drag where the destination is the editor
        # under the RELEASE POINT, not the active one.
        self._context_override = None

    def get_active_network_editor(self):
        """Return the NetworkEditor pane the user is most likely looking at.

        Prefers a pane that is the visible (current) tab in its split,
        falling back to any open NetworkEditor. Returns None if none exist.
        Replaces the old "first NetworkEditor in paneTabs()" behaviour, which
        picked an arbitrary editor when several were open and made
        context-aware import land in the wrong network."""
        editors = [
            pt
            for pt in hou.ui.paneTabs()  # type: ignore
            if pt.type() == hou.paneTabType.NetworkEditor
        ]
        if not editors:
            return None
        visible = [e for e in editors if e.isCurrentTab()]
        return (visible or editors)[0]

    def get_current_network_node(self) -> None | hou.Node:
        """Return the network node currently displayed in the active editor.

        Uses the editor's pwd() (the network whose children are shown)
        instead of currentNode().parent(), which crashed when nothing was
        current in the picked pane. An explicit per-import context
        (self._context_override, e.g. the release point of a drag) wins
        over the active-editor lookup."""
        if self._context_override is not None:
            return self._context_override
        editor = self.get_active_network_editor()
        if editor is None:
            return None
        return editor.pwd()

    @property
    def builder_node(self) -> hou.Node:
        """
        Docstring for builder_node

        :param self: Description
        :return: Description
        :rtype: Node
        """
        return self._builder_node

    @property
    def builder(self) -> int:
        """
        Docstring for builder

        :param self: Description
        :return: Description
        :rtype: int
        """
        return self._builder

    @property
    def renderer(self) -> str:
        """
        Docstring for renderer

        :param self: Description
        :return: Description
        :rtype: str
        """
        return self._renderer

    def get_renderer_from_node(self, node: hou.Node) -> str:
        """
        Get the renderer based on the node type of the given node

        :param self: Description
        :param node: Description
        :type node: hou.Node
        :return: Description
        :rtype: str
        """
        if node.type().name() == "redshift_vopnet":
            self._renderer = "Redshift"
            self._builder = 1
        elif "rs_usd_material_builder" in node.type().name():
            self._renderer = "Redshift"
            self._builder = 1
        elif node.type().name() == "materialbuilder":
            self._renderer = "Mantra"
            self._builder = 1
        elif node.type().name() == "principledshader::2.0":
            self._renderer = "Mantra"
            self._builder = 0
        elif node.type().name() == "arnold_materialbuilder":
            self._renderer = "Arnold"
            self._builder = 1
        elif node.type().name() == "octane_vopnet":
            self._renderer = "Octane"
            self._builder = 1
        elif "octane_solaris_material_builder" in node.type().name():
            self._renderer = "Octane"
            self._builder = 1
        elif "mtlxopen_pbr_surface" in node.type().name():
            self._renderer = "Karma"
            self._builder = 0
        elif "mtlxstandard_surface" in node.type().name():
            self._renderer = "Karma"
            self._builder = 0
        elif node.type().name() == "subnet":
            self._builder = 1
            for n in node.children():
                if "mtlx" in n.type().name():
                    self._renderer = "Karma"
        elif node.type().name() == "collect":
            self._renderer = "Karma"
            self._builder = 0
        return self._renderer

    # --- Import target capability ------------------------------------------
    # Node types that CAN live in a LOP/Solaris context (in addition to
    # Karma/MaterialX, which are recognised by renderer, not by type name).
    LOP_CAPABLE_NODE_TYPES = (
        "rs_usd_material_builder",  # Redshift USD material builder
        "octane_solaris_material_builder",  # Octane Solaris material builder
    )

    def get_saved_node_type(self, mat: material.Material) -> str:
        """Return the true builder node type recorded in a material's
        .interface file. asCode() writes a createNode("<type>", ...) call, so
        the first such type is the builder that was saved. Returns "" if the
        file is missing or unreadable."""
        iface = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + ".interface"
        )
        try:
            with open(iface, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return ""
        match = re.search(r'createNode\(\s*[\'"]([^\'"]+)[\'"]', text)
        return match.group(1) if match else ""

    # Redshift shader/material node type -> short display label, checked
    # in this order (most specific first) against the .mat file's raw
    # text. Not a rigorous graph walk - the same "good enough for a
    # label" text-search approach get_saved_node_type() already uses for
    # the outer builder type, just applied to the inner shader.
    SHADER_TYPE_LABELS = (
        ("redshift::ToonMaterial", "Toon"),
        ("redshift::OpenPBRMaterial", "PBR"),
        ("redshift::StandardMaterial", "Standard"),
        ("redshift::MaterialBlender", "Blend"),
        ("redshift::Hair", "Hair"),
        ("redshift::Volume", "Volume"),
        ("redshift::rsOSL", "OSL"),
    )

    def get_shader_type_label(self, mat: material.Material) -> str:
        """Best-effort label for the material's actual shader type
        (Standard/PBR/Toon/...) - the .mat file (saveItemsToFile format)
        is a Houdini "item file": mostly binary framing with real ASCII
        text segments in between, including literal "type = redshift::X"
        node-type declarations, so a plain text search works without
        needing to parse the format properly. Returns "" if the file is
        missing/unreadable or no known shader type is found."""
        mat_path = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + self._preferences.ext
        )
        try:
            with open(mat_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return ""
        text = data.decode("latin-1")
        for needle, label in self.SHADER_TYPE_LABELS:
            if ("type = " + needle) in text:
                return label
        return ""

    def import_targets(self, mat: material.Material) -> set:
        """Return the contexts a material can be imported into: a subset of
        {"mat", "lop"}.

        - Karma/MaterialX mix freely -> both.
        - Redshift USD builder (and any LOP_CAPABLE_NODE_TYPES) -> both.
        - Classic redshift_vopnet / octane_vopnet / Arnold / Mantra -> "mat"
          only; importing them into a LOP context is impossible.
        """
        if material.is_karma_renderer(mat.renderer):
            return {"mat", "lop"}
        if self.get_saved_node_type(mat) in self.LOP_CAPABLE_NODE_TYPES:
            return {"mat", "lop"}
        return {"mat"}

    def import_asset_to_scene(
        self,
        mat: material.Material,
        target: str = "auto",
        context_node: hou.Node | None = None,
    ):
        """Import a Material to the Network Editor/Scene.

        target: "auto" derives the destination from the active network editor
        (double-click behaviour); "mat" forces /mat; "lop" forces a LOP
        materiallibrary. context_node optionally overrides the active-editor
        lookup with an explicit destination context (the material drag's
        release point). Returns (ok, reason): if the material cannot live in
        the requested context, ok is False, reason explains why, and nothing
        is imported."""
        self._context_override = context_node
        ok, reason = self.update_context(mat, target)
        if not ok:
            self._context_override = None
            return (False, reason)

        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + ".interface"
        )

        self.restore_cop_companion(mat)
        self._hou_parent = hou.node("/obj").createNode("matnet")
        try:
            debug.event(
                "import", "routing by renderer",
                material=mat.name, renderer=mat.renderer,
                karma_family=material.is_karma_renderer(mat.renderer),
                targets=sorted(self.import_targets(mat)),
                saved_node_type=self.get_saved_node_type(mat),
            )
            if material.is_karma_renderer(mat.renderer):
                self.load_interface_mtlx(parms_file_name, mat)
                self.load_items_file_mtlx(mat)
            elif mat.renderer == "Mantra":
                self.load_interface_mantra(parms_file_name, mat)
                self.load_items_file(mat)
            elif "Redshift" in mat.renderer:
                self.load_interface_other(parms_file_name, mat, "redshift_vopnet")
                self.load_items_file(mat, move_builder=True)
            elif "Octane" in mat.renderer:
                self.load_interface_other(parms_file_name, mat, "octane_vopnet")
                self.load_items_file(mat, move_builder=True)
            elif mat.renderer == "Arnold":
                self.load_interface_other(parms_file_name, mat, "arnold_materialbuilder")
                self.load_items_file(mat, move_builder=True)

            # Setup MaterialLibrary if Import Context was such
            if self._import_path.type().name() == "materiallibrary":
                self._import_path.parm("materials").set(0)
                self._import_path.parm("fillmaterials").pressButton()
                i = 0
                while i < self._import_path.parm("materials").evalAsInt():
                    self._import_path.parm("".join(["assign", str(i + 1)])).set(0)
                    i += 1

            # Stamp the imported builder with its library id so a later
            # "Save to AssetLib" on this node can offer update-instead-
            # of-duplicate. Best-effort: a failed stamp only means the
            # save flow falls back to name matching.
            try:
                if (
                    self._builder_node is not None
                    and self._builder_node.path() != "/stage"
                ):
                    self._builder_node.setUserData(
                        "assetlib_id", str(mat.mat_id)
                    )
            except (hou.OperationFailed, hou.ObjectWasDeleted) as e:
                print("Amaze: could not stamp imported node: " + str(e))
        finally:
            # Runs even on failure so the temporary staging matnet never
            # lingers in /obj (same class of leak as the thumbnail
            # interrupt-safety hardening, applied here to the import path).
            self._context_override = None
            self._hou_parent.destroy()
        return (True, "")

    def cleanup(self):

        if self._import_path:
            if self._use_existing_node:
                # print("Import_Path: ", self._import_path)
                # print("Builder: ", self._builder_node)
                self._builder_node.destroy()
            else:
                self._import_path.destroy()

    def update_context(self, mat: material.Material, target: str = "auto"):
        """Resolve where a material should import to and set self._import_path.

        target:
          "auto" - derive the destination from the active network editor
                   (double-click "let MatLib decide" behaviour);
          "mat"  - force /mat;
          "lop"  - force a LOP materiallibrary.

        Returns (ok, reason). If the resolved context is LOP and the material
        cannot live there (classic redshift_vopnet / octane_vopnet / Arnold /
        Mantra), ok is False and nothing is created; otherwise ok is True and
        self._import_path is set. /mat and SOP contexts accept every material
        MatLib handles, so only the LOP case can fail."""
        allowed = self.import_targets(mat)

        if target == "mat":
            world = "mat"
        elif target == "lop":
            world = "lop"
        else:  # auto
            world = self._auto_world()

        if world == "lop" and "lop" not in allowed:
            return (
                False,
                '"'
                + mat.name
                + '" is a '
                + mat.renderer
                + " VOP material and cannot be imported into a LOP/Solaris "
                + "context. Use Import to MAT.",
            )

        if world == "lop":
            self._set_lop_import_path()
        elif world == "sop":
            self._set_sop_import_path()
        else:  # "mat" (and any fallback)
            self._import_path = hou.node("/mat")
            self._use_existing_node = True
        return (True, "")

    def _auto_world(self) -> str:
        """Classify the active network editor's context as 'lop', 'sop', or
        'mat' (the default for anything that holds VOP-style materials)."""
        curr = self.get_current_network_node()
        if curr is None:
            return "mat"
        typename = curr.type().name()
        try:
            child_cat = curr.childTypeCategory().name().lower()
        except Exception:
            child_cat = ""
        if (
            "stage" in typename
            or "lopnet" in typename
            or "materiallibrary" in typename
            or "lop" in child_cat
        ):
            return "lop"
        if "geo" in typename or "sop" in child_cat:
            return "sop"
        return "mat"

    def _set_lop_import_path(self) -> None:
        """Point the import path at a LOP materiallibrary: reuse the one the
        editor is inside, else create one under the current stage/lopnet, else
        create one under /stage (forced LOP from a non-LOP context)."""
        curr = self.get_current_network_node()
        if curr is not None and "materiallibrary" in curr.type().name():
            self._import_path = curr
            self._use_existing_node = True
            return
        if curr is not None and (
            "stage" in curr.type().name() or "lopnet" in curr.type().name()
        ):
            self._import_path = curr.createNode("materiallibrary")
            return
        self._import_path = hou.node("/stage").createNode("materiallibrary")

    def _set_sop_import_path(self) -> None:
        """Create a matnet inside the current geo/SOP network for the import."""
        curr = self.get_current_network_node()
        if curr is not None:
            self._import_path = curr.createNode("matnet")
        else:
            self._import_path = hou.node("/mat")
            self._use_existing_node = True

    def load_interface_mtlx(self, parms_file_name, mat: material.Material) -> None:
        """
        Loads the Interface File from disk

        :param self: Description
        :param parms_file_name: Description
        :param mat: Description
        """
        if os.path.exists(parms_file_name):
            with open(parms_file_name, "r", encoding="utf-8") as interface_file:
                code = interface_file.read()
            hou_parent = self._hou_parent  # needed for exec
            exec(code)

            # Same MaterialX Material Builder every other path uses -
            # matches the KARMA_REF reference (mtlx render context,
            # subnetconnector outputs). Was an inline duplicate of
            # make_karma_builder's setup, drifting from it; now shared,
            # so the flavour can't diverge between build and load. The
            # starter shader/displacement are removed inside it; the
            # subnetconnector outputs are kept and load_items_file_mtlx()
            # wires the loaded shader into them via wire_builder_output.
            self._builder_node = make_karma_builder(
                self._import_path, mat.name
            )

        else:
            return

    def load_interface_mantra(self, parms_file_name, mat: material.Material) -> None:
        """
        Loads the Interface File from disk

        :param self: Description
        :param parms_file_name: Description
        :param mat: Description
        """
        # Create temporary storage of nodes
        # tmp_matnet = hou.node("obj").createNode("matnet")
        # hou_parent = tmp_matnet  # needed for the code script below

        # self._import_path = hou.node("/stage").createNode("materiallibrary")
        builder = None
        if os.path.exists(parms_file_name):
            # Only load parms if MatBuilder
            if mat.builder:
                with open(parms_file_name, "r", encoding="utf-8") as interface_file:
                    code = interface_file.read()
                hou_parent = self._hou_parent  # needed for exec
                exec(code)

                builder = hou.selectedNodes()[0]
            # Selection will be empty if not a MaterialBuilder
            else:
                builder = self._import_path.createNode("materialbuilder")
        else:
            builder = hou.node(self._import_path).createNode("materialbuilder")

        builder.setName(mat.name, unique_name=True)
        builder.setGenericFlag(hou.nodeFlag.Material, True)
        # Delete Default children in MaterialBuilder
        for node in builder.children():
            node.destroy()

        self._builder_node = builder

    def load_interface_other(
        self, parms_file_name: str, mat: material.Material, builder_name: str
    ) -> None:
        """
        Loads the Interface File Configuration from Disk

        :param self: Description
        :param parms_file_name: Description
        :type parms_file_name: str
        :param mat: Description
        :type mat: material.Material
        :param builder_name: Description
        :type builder_name: str
        """
        # Create temporary storage of nodes
        # tmp_matnet = hou.node("obj").createNode("matnet")
        # hou_parent = tmp_matnet  # needed for the code script below

        if os.path.exists(parms_file_name):
            with open(parms_file_name, "r", encoding="utf-8") as interface_file:
                code = interface_file.read()
            hou_parent = self._hou_parent  # needed for exec
            exec(code)
            builder = hou_parent.children()[0]
            # Delete auto-created default children (newer Redshift builds
            # create an OpenPBR material + output in a fresh vopnet) so the
            # saved items don't collide or duplicate on load.
            for node in builder.children():
                node.destroy()
        else:
            # _import_path is already a hou.Node (every assignment sets it
            # to one), so hou.node() - which wants a STRING path - raised
            # "argument 1 of type char const *" and crashed the Redshift
            # converter whenever this fallback was reached.
            builder = self._import_path.createNode(builder_name)

        builder.setName(mat.name, unique_name=True)
        builder.setGenericFlag(hou.nodeFlag.Material, True)
        # Delete Default children in RS-VopNet
        # for node in builder.children():
        #     node.destroy()

        self._builder_node = builder

    def load_items_file(self, mat: material.Material, move_builder: bool = False) -> None:
        """
        Loads the actual Node Configuration from Disk.
        move_builder=True moves the rebuilt builder node itself to the import
        context (Redshift/Octane/Arnold, whose .mat files store the builder's
        children); False moves the loaded children (MaterialX/Mantra, whose
        .mat files store the material node itself).

        :param self: Description
        :param mat: Description
        """
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + self._preferences.ext
        )

        try:
            self._builder_node.loadItemsFromFile(file_name, ignore_load_warnings=True)
        except OSError:
            hou.ui.displayMessage("Failure on Import. Please Check Files.")  # type: ignore
            return None

        if move_builder:
            new_mat = hou.moveNodesTo((self._builder_node,), self._import_path)  # type: ignore
            self._builder_node = new_mat[0]
            self._builder_node.moveToGoodPosition()
        else:
            new_mat = hou.moveNodesTo(self._builder_node.children(), self._import_path)  # type: ignore
            self.builder_node.destroy()
            self._builder_node = new_mat[0]

    def load_items_file_mtlx(self, mat: material.Material) -> None:
        """MaterialX/Karma equivalent of load_items_file(move_builder=False),
        but keeps the loaded shader+texture network wrapped in the subnet
        load_interface_mtlx() already created there, instead of flattening
        it out to the destination and discarding the subnet.

        That flattening (the shared load_items_file() behaviour) is why a
        Karma material imported into a LOP materiallibrary was unusable
        there: a materiallibrary's own material-list UI (fillmaterials)
        specifically looks for subnet-type children as "one material
        each" - a loose, unwrapped shader node sitting directly in its
        network isn't recognised as a material at all, even though the
        node itself exists and renders fine. Left load_items_file() and
        its move_builder parameter completely untouched rather than
        changed in place, since Mantra shares that same code path and
        this fix has only been verified for MaterialX/Karma."""
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + self._preferences.ext
        )
        pre_existing = set(self._builder_node.children())
        try:
            self._builder_node.loadItemsFromFile(file_name, ignore_load_warnings=True)
        except OSError:
            hou.ui.displayMessage("Failure on Import. Please Check Files.")  # type: ignore
            return None
        loaded = [
            c for c in self._builder_node.children() if c not in pre_existing
        ]

        # Two different .mat layouts exist in one library, saved by two
        # different paths, indistinguishable from the .interface alone:
        # - save_node_collect (bare mtlxstandard_surface + connected
        #   nodes, e.g. converter output): loose shader/texture items.
        # - save_node_mtlx (a whole builder-subnet material, e.g. every
        #   hand-built Karma Material Builder): ONE subnet item carrying
        #   its own complete internal network, output connectors, and
        #   builder spare-parm config (item files preserve full node
        #   state).
        # The single-subnet case must NOT be wrapped again - that would
        # nest a complete builder inside our scaffolding builder. Unwrap
        # it instead: it already IS the material.
        if len(loaded) == 1 and loaded[0].type().name() == "subnet":
            inner = hou.moveNodesTo((loaded[0],), self._import_path)[0]  # type: ignore
            outer = self._builder_node
            self._builder_node = inner
            outer.destroy()
            inner.setName(helpers.sanitize_usd_path(mat.name), unique_name=True)
            inner.setGenericFlag(hou.nodeFlag.Material, True)
            inner.moveToGoodPosition()
            return

        # Loose-items case: self._builder_node is the Karma Material
        # Builder scaffolding created in load_interface_mtlx(), sitting at
        # the real destination - find the actual shader among the loaded
        # children and wire it into the builder's own output connector
        # (kept around instead of destroyed, specifically for this) -
        # without that the material would be correctly *shaped* like a
        # Karma Material Builder but not connected to anything usable
        # from outside the subnet.
        shader_node = None
        displacement_node = None
        collect_nodes = []
        for child in self._builder_node.children():
            tname = child.type().name()
            if tname == "collect":
                collect_nodes.append(child)
            elif tname == "mtlxdisplacement" and displacement_node is None:
                displacement_node = child
            elif shader_node is None and (
                tname == "mtlxstandard_surface"
                or "mtlxopen_pbr_surface" in tname
                or tname == "subnet"
            ):
                shader_node = child
                child.setGenericFlag(hou.nodeFlag.Material, True)
        wire_builder_output(self._builder_node, shader_node, displacement_node)
        # The load path is two-phase (build the builder, load items, wire)
        # so it can't go through build_karma_material's single funnel - but
        # it holds the SAME invariant, checked the same way.
        if shader_node is not None and not surface_terminal_wired(
            self._builder_node
        ):
            debug.event(
                "karma", "loaded material has no wired surface terminal",
                material=mat.name, builder=self._builder_node.path(),
            )
            print(
                "Amaze: WARNING - loaded '%s' has no wired surface "
                "terminal and will render black" % mat.name
            )
        # A loaded collect node is the flat save format's stand-in for
        # "surface + displacement belong together" (see the converter) -
        # inside a real builder the suboutput just wired above carries
        # that role, so the collect is redundant clutter here. Destroyed
        # after the wiring so the builder's contents match a hand-built
        # Karma Material Builder exactly.
        for collect in collect_nodes:
            collect.destroy()
        # Auto-arrange the loaded nodes - loadItemsFromFile() restores
        # each node's originally-saved position, which were relative to
        # wherever they lived at save time and can land overlapping here.
        # Same as pressing "L" in the network editor.
        self._builder_node.layoutChildren()
        self._builder_node.moveToGoodPosition()

    COP_LIB_ROOT = "/obj/MatLib"

    @property
    def cop_info(self) -> dict:
        """COP companion info from the last save ({} if none)"""
        return self._cop_info

    def _sanitize_net_name(self, name: str) -> str:
        return re.sub(r"[^\w]", "_", name)

    def _find_cop_container(self, cop_node):
        """Walk up from a COP node to the network node containing it"""
        n = cop_node
        while n is not None:
            try:
                cat = n.type().category().name().lower()
            except AttributeError:
                return None
            if cat not in ("cop2", "cop"):
                return n
            n = n.parent()
        return None

    def _collect_cop_refs(self, nodes) -> list:
        """Scan the given nodes (and their descendants) for op: string
        parms referencing COP nodes. Returns (parm, cop_node, container)."""
        refs = []
        scan = []
        for node in nodes:
            scan.append(node)
            scan.extend(node.allSubChildren())
        for n in scan:
            for parm in n.parms():
                try:
                    raw = parm.unexpandedString()
                except hou.OperationFailed:
                    continue
                if "op:" not in raw:
                    continue
                for p in re.findall(r"op:(/[\w/\.\-]+)", raw):
                    target = hou.node(p)
                    if target is None:
                        continue
                    try:
                        cat = target.type().category().name().lower()
                    except AttributeError:
                        continue
                    if cat not in ("cop2", "cop"):
                        continue
                    container = self._find_cop_container(target)
                    if container is None:
                        continue
                    refs.append((parm, target, container))
        return refs

    def prepare_cop_companion(self, nodes, asset_id: str, net_name: str) -> dict:
        """If the material node(s) reference COP networks via op: paths,
        save those networks as a companion file next to the material and
        return an {old op: path -> new op: path} rewrite map. Also sets
        self._cop_info for the library database. Returns {} if the
        material has no COP references."""
        self._cop_info = {}
        refs = self._collect_cop_refs(nodes)
        if not refs:
            return {}

        net_name = self._sanitize_net_name(net_name)
        containers = []
        for _parm, _target, container in refs:
            if container not in containers:
                containers.append(container)

        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + "_cop"
            + self._preferences.ext
        )

        staging_parent = hou.node("/obj").createNode("subnet")
        rename_map = {}
        net_type = containers[0].type().name()
        try:
            try:
                staging = staging_parent.createNode(net_type)
            except hou.OperationFailed:
                net_type = "copnet"
                staging = staging_parent.createNode(net_type)
            for container in containers:
                # Copy ITEMS, not just child nodes - network dots are
                # not children, and copying without them drops every
                # wire routed through one (same bug class as the
                # standalone COP save, caught live in Houdini). Falls
                # back to the old node-only copy if copyItems is
                # unavailable in this Houdini build.
                items = container.allItems()
                try:
                    copies = staging.copyItems(items)
                except (AttributeError, hou.OperationFailed):
                    items = container.children()
                    copies = hou.copyNodesTo(items, staging)
                for orig, copy in zip(items, copies):
                    # Dots/boxes/notes carry no rename-relevant name -
                    # the op: path rewrite map only ever needs real nodes.
                    if isinstance(orig, hou.Node):
                        rename_map[orig.path()] = copy.name()
            staging.saveItemsToFile(
                staging.allItems(), file_name, save_hda_fallbacks=False
            )
        finally:
            staging_parent.destroy()

        path_map = {}
        for _parm, target, container in refs:
            rel = target.path()[len(container.path()) + 1 :]
            parts = rel.split("/")
            top_orig = container.path() + "/" + parts[0]
            parts[0] = rename_map.get(top_orig, parts[0])
            path_map["op:" + target.path()] = (
                "op:" + self.COP_LIB_ROOT + "/" + net_name + "/" + "/".join(parts)
            )

        self._cop_info = {"name": net_name, "type": net_type}
        print(
            "Amaze: saved "
            + str(len(containers))
            + " COP network(s) with material -> "
            + self.COP_LIB_ROOT
            + "/"
            + net_name
        )
        return path_map

    def rewrite_cop_refs(self, nodes, path_map: dict) -> None:
        """Rewrite op: references in all string parms of the given nodes.
        Only ever called on temporary save copies, never on scene nodes."""
        if not path_map:
            return
        keys = sorted(path_map.keys(), key=len, reverse=True)
        scan = []
        for node in nodes:
            scan.append(node)
            scan.extend(node.allSubChildren())
        for n in scan:
            for parm in n.parms():
                try:
                    raw = parm.unexpandedString()
                except hou.OperationFailed:
                    continue
                if "op:" not in raw:
                    continue
                new = raw
                for key in keys:
                    new = new.replace(key, path_map[key])
                if new != raw:
                    try:
                        parm.set(new)
                    except (hou.OperationFailed, hou.PermissionError):
                        pass

    def restore_cop_companion(self, mat: material.Material) -> None:
        """Recreate the material's saved COP network under /obj/MatLib on
        import. An existing network with the same name is reused."""
        info = getattr(mat, "cop_net", {}) or {}
        if not info or not info.get("name"):
            return
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + "_cop"
            + self._preferences.ext
        )
        if not os.path.exists(file_name):
            print(
                "Amaze: COP companion file missing for " + mat.name + " - skipped"
            )
            return
        root = hou.node(self.COP_LIB_ROOT)
        if root is None:
            root = hou.node("/obj").createNode("subnet")
            try:
                root.setName("MatLib")
            except hou.OperationFailed:
                print("Amaze: could not create /obj/MatLib - COP restore skipped")
                root.destroy()
                return
        if root.node(info["name"]) is not None:
            print(
                "Amaze: COP network '"
                + info["name"]
                + "' already exists in /obj/MatLib - reusing it"
            )
            return
        try:
            copnet = root.createNode(info.get("type", "copnet"))
        except hou.OperationFailed:
            copnet = root.createNode("copnet")
        try:
            copnet.setName(info["name"])
        except hou.OperationFailed:
            pass
        try:
            copnet.loadItemsFromFile(file_name, ignore_load_warnings=True)
        except OSError:
            print("Amaze: failed to load COP companion for " + mat.name)
            copnet.destroy()
            return
        copnet.moveToGoodPosition()
        print(
            "Amaze: restored COP network -> "
            + self.COP_LIB_ROOT
            + "/"
            + info["name"]
        )

    def save_node_cop(
        self,
        node: hou.Node,
        asset_id: str,
        update: bool = False,
        items: list | None = None,
    ) -> bool:
        """Save a COP network as a standalone library asset (the v2 Cop
        section). Two modes:
        - node is a copnet CONTAINER (items None): the whole network -
          allItems() -> <id>.mat, the container's own asCode ->
          <id>.interface (the first createNode(...) call records the
          real network type for import, same contract as materials).
        - items given: a SELECTION of nodes/dots inside a Copernicus
          network (node = the clicked child, its parent is the net) -
          only those items are saved; the interface records the PARENT
          network's type so a container can still be reconstructed
          when importing outside any COP network.
        The scene is never modified - saveItemsToFile reads in place."""
        if items is not None:
            net = node.parent()
            selection_nodes = [i for i in items if isinstance(i, hou.Node)]
            if not selection_nodes:
                hou.ui.displayMessage(  # type: ignore
                    "No COP nodes selected - nothing to save."
                )
                return False
        else:
            net = node
            selection_nodes = None
            if not node.children():
                hou.ui.displayMessage(  # type: ignore
                    "The COP network is empty - nothing to save."
                )
                return False
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )
        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + ".interface"
        )
        with open(parms_file_name, "w", encoding="utf-8") as interface_file:
            interface_file.write(net.asCode())
        # allItems(), NOT children(): network DOTS route one output into
        # several inputs, and children() excludes them - saving without
        # the dots silently drops every wire that runs through one
        # (caught live in Houdini: re-imported networks came back with
        # missing inputs, and thumbnails failed on those broken copies).
        # allItems() carries children + dots + network boxes + notes.
        # A selection save stores exactly the user's items instead.
        net.saveItemsToFile(
            items if items is not None else net.allItems(),
            file_name,
            save_hda_fallbacks=False,
        )

        # Record which child IS the picture while the LIVE network is
        # in front of us: the display flag doesn't reliably survive the
        # items-file round-trip, which is why heuristics run on the
        # loaded temp copy kept picking wrong nodes (two live misses in
        # opposite directions). Node NAMES do survive, so the name is
        # what gets persisted (via the asset's cop_net field) and looked
        # up again at render time - including rerenders long after this
        # scene is gone.
        self._cop_info = {}
        source = helpers.pick_cop_display_child(net, children=selection_nodes)
        if source is not None:
            self._cop_info = {"thumb_node": source.name()}
            print(
                "Amaze: COP save - thumbnail source recorded: "
                + source.name()
            )
        else:
            print("Amaze: COP save - no thumbnail source found to record")

        if not update and not self._preferences.render_on_import:
            return True
        # Thumbnail failure never blocks registration (same rule as
        # materials).
        thumber = thumbs.ThumbNailRenderer(self._preferences)
        try:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                thumber.create_thumb_cop(
                    str(asset_id), self._cop_info.get("thumb_node", "")
                )
        except Exception as exc:
            print(
                "Amaze: COP thumbnail failed for "
                + node.name()
                + " (asset saved anyway): "
                + str(exc)
            )
        return True

    def import_cop_asset(
        self,
        mat: material.Material,
        context_node: hou.Node | None = None,
    ):
        """Recreate a saved COP asset (Cop section import). Context-
        aware, like materials: if the destination network
        already holds Copernicus nodes - the user is inside a COP
        network, or the drag released on/into a copnet - the saved
        nodes load DIRECTLY into it, no new container. Anywhere else a
        container of the saved network type is created (in the
        destination if possible, else /obj) and loaded into.
        context_node overrides the active-editor lookup (drag release
        point). Returns (ok, reason) - same contract as
        import_asset_to_scene."""
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + mat.mat_id
            + self._preferences.ext
        )
        if not os.path.exists(file_name):
            return (False, '"%s": asset file is missing on disk.' % mat.name)
        net_type = self.get_saved_node_type(mat) or "copnet"

        dest = context_node
        if dest is None:
            editor = self.get_active_network_editor()
            dest = editor.pwd() if editor is not None else None

        if dest is not None:
            try:
                is_cop_net = dest.childTypeCategory().name() == "Cop"
            except (AttributeError, hou.OperationFailed):
                is_cop_net = False
            if is_cop_net:
                before = set(dest.children())
                try:
                    dest.loadItemsFromFile(
                        file_name, ignore_load_warnings=True
                    )
                except (OSError, hou.OperationFailed) as exc:
                    return (
                        False,
                        '"%s": failed to load into %s (%s).'
                        % (mat.name, dest.path(), exc),
                    )
                # Saved positions came from a different network and can
                # land on top of existing nodes - lay out just the new
                # arrivals (best-effort; the L key fixes any residue).
                new_children = [
                    c for c in dest.children() if c not in before
                ]
                if new_children:
                    try:
                        dest.layoutChildren(items=new_children)
                    except (TypeError, hou.OperationFailed):
                        pass
                return (True, "")

        copnet = None
        if dest is not None:
            try:
                copnet = dest.createNode(net_type)
            except hou.OperationFailed:
                copnet = None
        if copnet is None:
            try:
                copnet = hou.node("/obj").createNode(net_type)
            except hou.OperationFailed:
                return (
                    False,
                    '"%s": could not create a %s node in the current '
                    "network or /obj." % (mat.name, net_type),
                )
        try:
            try:
                copnet.setName(
                    helpers.sanitize_usd_path(mat.name), unique_name=True
                )
            except hou.OperationFailed:
                pass
            copnet.loadItemsFromFile(file_name, ignore_load_warnings=True)
        except (OSError, hou.OperationFailed) as exc:
            copnet.destroy()
            return (
                False,
                '"%s": failed to load the saved network (%s).' % (mat.name, exc),
            )
        copnet.moveToGoodPosition()
        try:
            copnet.setUserData("assetlib_id", str(mat.mat_id))
        except hou.OperationFailed:
            pass
        return (True, "")

    def save_node(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Save Node wrapper for different Material Types"""
        if hou.getenv("OCIO") is None:
            hou.ui.displayMessage("Please set $OCIO first")  # type: ignore
            return False
        val = False

        if "Redshift" in self._renderer:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                val = self.save_node_redshift(node, asset_id, update)
        elif "Mantra" in self._renderer:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                val = self.save_node_mantra(node, asset_id, update)
        elif "Arnold" in self._renderer:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                val = self.save_node_arnold(node, asset_id, update)
        elif "Octane" in self._renderer:
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                val = self.save_node_octane(node, asset_id, update)
        elif material.is_karma_renderer(self._renderer):
            if (
                node.type().name() == "collect"
                or "mtlxopen_pbr_surface" in node.type().name()
                or "mtlxstandard_surface" in node.type().name()
            ):
                with hou.InterruptableOperation(
                    "Rendering", "Performing Tasks", open_interrupt_dialog=True
                ):
                    val = self.save_node_collect(node, asset_id, update)
            else:
                with hou.InterruptableOperation(
                    "Rendering", "Performing Tasks", open_interrupt_dialog=True
                ):
                    val = self.save_node_mtlx(node, asset_id, update)
        else:
            hou.ui.displayMessage("Selected Node is not a Material Builder")  # type: ignore
        return val

    def save_node_collect(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Saves the attached network from a collect node to disk - does not add to library"""
        # Filepath where to save stuff
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )
        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + ".interface"
        )

        nodetree = helpers.get_connected_nodes(node)

        sub_tmp = nodetree[0].parent().createNode("subnet")
        try:
            children = sub_tmp.children()
            for n in children:
                n.destroy()
            hou.copyNodesTo((nodetree), sub_tmp)  # type: ignore
            children = sub_tmp.children()

            path_map = self.prepare_cop_companion(
                tuple(nodetree), str(asset_id), node.name()
            )
            if path_map:
                self.rewrite_cop_refs((sub_tmp,), path_map)

            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(sub_tmp.asCode())

            sub_tmp.saveItemsToFile(children, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary save copy never
            # lingers in the scene.
            sub_tmp.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        try:
            thumber = thumbs.ThumbNailRenderer(self._preferences)
            ok = thumber.create_thumb_mtlx(nodetree, asset_id)
        except Exception as exc:
            debug.exception("Karma thumbnail", exc, asset_id=asset_id,
                            node=node.path())
            print(
                "Amaze: Karma thumbnail failed ("
                + str(exc)
                + ") - material saved and registered without thumbnail."
            )
            return True
        if not ok:
            # A thumbnail that merely RETURNS False must not lose the
            # material either: save_node()'s result is what add_asset()
            # gates registration on, so returning it directly meant a
            # failed render silently discarded the whole asset.
            debug.event("save", "Karma thumbnail returned False",
                        asset_id=asset_id, node=node.path())
            print(
                "Amaze: Karma thumbnail did not render - material "
                "saved and registered without one."
            )
        return True

    def save_node_mtlx(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Saves the MtlX node to disk - does not add to library"""
        # Filepath where to save stuff
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + asset_id
            + self._preferences.ext
        )

        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + asset_id
            + ".interface"
        )

        builder = hou.node("/obj").createNode("matnet")
        try:
            copied = hou.copyNodesTo((node,), builder)  # type: ignore

            path_map = self.prepare_cop_companion((node,), str(asset_id), node.name())
            if path_map:
                self.rewrite_cop_refs((copied[0],), path_map)

            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(node.asCode())

            builder.saveItemsToFile(copied, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary save copy never
            # lingers in the scene.
            builder.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        try:
            thumber = thumbs.ThumbNailRenderer(self._preferences)
            ok = thumber.create_thumb_mtlx(node, asset_id)
        except Exception as exc:
            debug.exception("Karma thumbnail", exc, asset_id=asset_id,
                            node=node.path())
            print(
                "Amaze: Karma thumbnail failed ("
                + str(exc)
                + ") - material saved and registered without thumbnail."
            )
            return True
        if not ok:
            # A thumbnail that merely RETURNS False must not lose the
            # material either: save_node()'s result is what add_asset()
            # gates registration on, so returning it directly meant a
            # failed render silently discarded the whole asset.
            debug.event("save", "Karma thumbnail returned False",
                        asset_id=asset_id, node=node.path())
            print(
                "Amaze: Karma thumbnail did not render - material "
                "saved and registered without one."
            )
        return True

    def save_node_mantra(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Saves the Mantra node to disk - does not add to library"""
        # Filepath where to save stuff
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )

        # COP companion: same pattern as save_node_redshift - compute on
        # the real scene node before any copying, then rewrite the paths
        # only on a temporary copy (forcing one even if node is already
        # a materialbuilder) so the scene material is never touched.
        path_map = self.prepare_cop_companion((node,), str(asset_id), node.name())

        orig_node = node
        builder = None
        if node.type().name() != "materialbuilder" or path_map:
            builder = hou.node("/mat").createNode("materialbuilder")
            for c in builder.children():
                c.destroy()
            hou.copyNodesTo((node,), builder)  # type: ignore
            node = builder
            if path_map:
                self.rewrite_cop_refs((node,), path_map)

        try:
            # interface-stuff
            parms_file_name = (
                self._preferences.dir
                + self._preferences.asset_dir
                + str(asset_id)
                + ".interface"
            )
            children = node.children()

            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(node.asCode())

            node.saveItemsToFile(children, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary save copy never
            # lingers in the scene.
            node = orig_node
            if builder is not None:
                builder.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        try:
            thumber = thumbs.ThumbNailRenderer(self._preferences)
            ok = thumber.create_thumb_mantra(node, asset_id)
        except Exception as exc:
            debug.exception("Mantra thumbnail", exc, asset_id=asset_id,
                            node=node.path())
            print(
                "Amaze: Mantra thumbnail failed ("
                + str(exc)
                + ") - material saved and registered without thumbnail."
            )
            return True
        if not ok:
            # A thumbnail that merely RETURNS False must not lose the
            # material either: save_node()'s result is what add_asset()
            # gates registration on, so returning it directly meant a
            # failed render silently discarded the whole asset.
            debug.event("save", "Mantra thumbnail returned False",
                        asset_id=asset_id, node=node.path())
            print(
                "Amaze: Mantra thumbnail did not render - material "
                "saved and registered without one."
            )
        return True

    def save_node_redshift(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Saves the Redshift node to disk - does not add to library"""
        # Filepath where to save stuff
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )

        # interface-stuff
        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + ".interface"
        )
        # COP companion: if the material references COP networks via op:
        # paths, save them alongside and rewrite the paths on a temporary
        # copy so the scene material is never touched.
        path_map = self.prepare_cop_companion((node,), str(asset_id), node.name())
        tmp_parent = None
        save_node = node
        if path_map:
            tmp_parent = hou.node("/obj").createNode("matnet")
            save_node = hou.copyNodesTo((node,), tmp_parent)[0]
            self.rewrite_cop_refs((save_node,), path_map)

        try:
            children = save_node.children()

            # interface_file.write(node.parmTemplateGroup().asCode())
            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(save_node.asCode())

            save_node.saveItemsToFile(children, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary COP-rewrite copy never
            # lingers in the scene.
            if tmp_parent is not None:
                tmp_parent.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        try:
            thumber = thumbs.ThumbNailRenderer(self._preferences)
            return thumber.create_thumb_redshift(node, asset_id)
        except Exception as e:
            print(
                "Amaze: Redshift thumbnail failed ("
                + str(e)
                + ") - material saved and registered without thumbnail."
            )
            return True

    def save_node_octane(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """
        Saves a node for octane renderer to disk

        :param self: Description
        :param node: Description
        :type node: hou.Node
        :param asset_id: Description
        :type asset_id: str
        :param update: Description
        :type update: bool
        :return: Description
        :rtype: bool
        """

        # Filepath where to save stuff
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )

        # interface-stuff
        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + ".interface"
        )
        # COP companion: same pattern as save_node_redshift - if the
        # material references COP networks via op: paths, save them
        # alongside and rewrite the paths on a temporary copy so the
        # scene material is never touched.
        path_map = self.prepare_cop_companion((node,), str(asset_id), node.name())
        tmp_parent = None
        save_node = node
        if path_map:
            tmp_parent = hou.node("/obj").createNode("matnet")
            save_node = hou.copyNodesTo((node,), tmp_parent)[0]
            self.rewrite_cop_refs((save_node,), path_map)

        try:
            children = save_node.children()

            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(save_node.asCode())

            save_node.saveItemsToFile(children, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary COP-rewrite copy
            # never lingers in the scene.
            if tmp_parent is not None:
                tmp_parent.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        try:
            thumber = thumbs.ThumbNailRenderer(self._preferences)
            return thumber.create_thumb_octane(node, asset_id)
        except Exception as e:
            print(
                "Amaze: Octane thumbnail failed ("
                + str(e)
                + ") - material saved and registered without thumbnail."
            )
            return True

    def save_node_arnold(self, node: hou.Node, asset_id: str, update: bool) -> bool:
        """Saves the Arnold node to disk - does not add to library"""
        file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + self._preferences.ext
        )

        parms_file_name = (
            self._preferences.dir
            + self._preferences.asset_dir
            + str(asset_id)
            + ".interface"
        )
        # COP companion: same pattern as save_node_redshift - if the
        # material references COP networks via op: paths, save them
        # alongside and rewrite the paths on a temporary copy so the
        # scene material is never touched.
        path_map = self.prepare_cop_companion((node,), str(asset_id), node.name())
        tmp_parent = None
        save_node = node
        if path_map:
            tmp_parent = hou.node("/obj").createNode("matnet")
            save_node = hou.copyNodesTo((node,), tmp_parent)[0]
            self.rewrite_cop_refs((save_node,), path_map)

        try:
            children = save_node.children()

            with open(parms_file_name, "w", encoding="utf-8") as interface_file:
                interface_file.write(save_node.asCode())

            save_node.saveItemsToFile(children, file_name, save_hda_fallbacks=False)
        finally:
            # Runs even on failure so the temporary COP-rewrite copy
            # never lingers in the scene.
            if tmp_parent is not None:
                tmp_parent.destroy()

        # If this is not a manual update and render_on_import is off, finish here
        if not update:
            if not self._preferences.render_on_import:
                return True

        thumber = thumbs.ThumbNailRenderer(self._preferences)
        return thumber.create_thumb_arnold(node, asset_id)
