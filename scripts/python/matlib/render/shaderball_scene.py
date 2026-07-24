"""
Generates a ShaderBall Scene and allows for Rendering Material Preview
"""

import hou


class ShaderBallSetup:
    """
    Generates a ShaderBall Scene and allows for Rendering Material Preview
    """

    def __init__(
        self,
        renderer: str = "Mantra",
        parent: hou.Node = hou.node("/obj"),
        ball_mode: int = 0,
    ) -> None:

        self.ballmode = ball_mode
        self.geo_node = parent.createNode("geo")
        self.filecache = self.geo_node.createNode("filecache::2.0")

        self.split = self.geo_node.createNode("split")
        self.split.setInput(0, self.filecache, 0)

        self.mat_plane = self.geo_node.createNode("material")
        self.mat_ball = self.geo_node.createNode("material")

        self.mat_plane.setInput(0, self.split, 0)
        self.mat_ball.setInput(0, self.split, 1)

        self.merge = self.geo_node.createNode("merge")

        self.merge.setNextInput(self.mat_plane, 0)
        self.merge.setNextInput(self.mat_ball, 0)

        self.switch = self.geo_node.createNode("switch")
        self.switch.setNextInput(self.merge, 0)
        self.switch.setNextInput(self.mat_ball, 0)

        self.out = self.geo_node.createNode("null")
        self.out.setName("OUT", True)
        self.out.setInput(0, self.switch, 0)

        self.geo_node.setName("ShaderBallScene", True)

        data_template = hou.StringParmTemplate(
            "mat_ball",
            "ShaderBall Material",
            1,
            string_type=hou.stringParmType.NodeReference,
        )
        self.geo_node.addSpareParmTuple(data_template)

        toggle_template = hou.ToggleParmTemplate(
            "do_show_ball_only", "Show Ball Only", 1
        )
        self.geo_node.addSpareParmTuple(toggle_template)

        self.matnet = self.geo_node.createNode("matnet")
        self.apply_initial_materials(renderer)

        # Set Parms
        self.filecache.parm("loadfromdisk").set(1)
        if not self.ballmode:
            self.filecache.parm("file").set(
                "$ASSETLIB/scripts/python/matlib/res/geo/ShaderBallScene_Simple.bgeo.sc"
            )
        else:
            self.filecache.parm("file").set(
                "$ASSETLIB/scripts/python/matlib/res/geo/ShaderBallScene.bgeo.sc"
            )
        self.filecache.parm("filemethod").set(1)
        self.filecache.parm("timedependent").set(0)

        self.split.parm("group").set("Plane")
        self.split.parm("grouptype").set(4)

        self.mat_plane.parm("shop_materialpath1").set("../matnet1/Plane")

        self.mat_ball.parm("shop_materialpath1").set(self.geo_node.parm("mat_ball"))
        self.switch.parm("input").set(self.geo_node.parm("do_show_ball_only"))
        self.geo_node.parm("do_show_ball_only").set(0)

        self.out.setGenericFlag(hou.nodeFlag.Display, True)
        self.out.setGenericFlag(hou.nodeFlag.Render, True)

        self.geo_node.layoutChildren()

    def apply_initial_materials(self, renderer: str) -> None:
        """
        Apply Default Materials for the given Renderer
        """
        if "Mantra" in renderer:
            self.mat = self.matnet.createNode("principledshader::2.0")

            self.mat.parm("basecolorr").set(1)
            self.mat.parm("basecolorg").set(1)
            self.mat.parm("basecolorb").set(1)
            self.mat.parm("basecolor_usePointColor").set(0)
            self.mat.parm("basecolor_useTexture").set(1)
            self.mat.parm("basecolor_texture").set(
                "$ASSETLIB/scripts/python/matlib/res/img/FloorTexture.rat"
            )

            self.mat.parm("rough").set(0)
            self.mat.parm("reflect").set(0)

            self.mat.setName("Plane", True)

        elif "Redshift" in renderer:

            self.mat = self.matnet.createNode("redshift_vopnet")

            # Locate the auto-created surface material. The default child
            # node differs between Redshift versions: older builds create
            # StandardMaterial1, newer builds create an OpenPBR material.
            rsmat = self.mat.node("StandardMaterial1")
            if rsmat is None:
                for child in self.mat.children():
                    tname = child.type().name()
                    if tname == "redshift_material":
                        continue
                    if "Material" in tname or "PBR" in tname:
                        rsmat = child
                        break
            if rsmat is None:
                rsmat = self.mat.createNode("redshift::StandardMaterial")
                out = None
                for child in self.mat.children():
                    if child.type().name() == "redshift_material":
                        out = child
                        break
                if out is not None:
                    out.setInput(0, rsmat, 0)

            # Kill specular on the floor. Parameter names differ:
            # StandardMaterial uses refl_weight, OpenPBR uses specular_weight.
            for parm_name in ("refl_weight", "specular_weight"):
                parm = rsmat.parm(parm_name)
                if parm is not None:
                    parm.set(0)
                    break

            tex = self.mat.createNode("redshift::TextureSampler")
            tex.parm("tex0").set(
                "$ASSETLIB//scripts/python/matlib/res/img/FloorTexture.exr"
            )

            # Prefer the named input so this works on both material types;
            # input index 0 is only guaranteed on StandardMaterial.
            try:
                rsmat.setNamedInput("base_color", tex, 0)
            except (hou.OperationFailed, AttributeError):
                rsmat.setInput(0, tex, 0)

            self.mat.setName("Plane", True)

        elif "Arnold" in renderer:

            self.mat = self.matnet.createNode("arnold_materialbuilder")
            # The auto-created output node's name can differ between
            # versions - same class of gap that crashed Redshift (bug #3)
            # and Octane (#4): search generically instead of assuming
            # "OUT_material", and degrade to an unwired floor material
            # (with a console note) rather than crashing the thumbnail.
            out = self.mat.node("OUT_material")
            if out is None:
                for child in self.mat.children():
                    if "arnold_material" in child.type().name():
                        out = child
                        break

            amat = self.mat.createNode("arnold::standard_surface")
            specular = amat.parm("specular")
            if specular is not None:
                specular.set(0)

            tex = self.mat.createNode("arnold::image")
            tex.parm("filename").set(
                "$ASSETLIB//scripts/python/matlib/res/img/FloorTexture.tx"
            )

            amat.setInput(0, tex, 0)
            if out is not None:
                out.setInput(0, amat, 0)
            else:
                print(
                    "Amaze: Arnold floor material output node not "
                    "found - floor left unwired"
                )

            self.mat.setName("Plane", True)

        elif "Octane" in renderer:

            self.mat = self.matnet.createNode("octane_vopnet")

            # Locate the auto-created surface material. As with Redshift
            # above, the default child's name can differ between Octane
            # versions - search generically instead of assuming
            # "Standard_Surface" and crashing when it isn't there.
            omat = self.mat.node("Standard_Surface")
            if omat is None:
                for child in self.mat.children():
                    tname = child.type().name()
                    if "Surface" in tname or "Material" in tname:
                        omat = child
                        break

            if omat is not None:
                specular = omat.parm("specular")
                if specular is not None:
                    specular.set(0)

                tex = self.mat.createNode("octane::NT_TEX_IMAGE")
                tex.parm("A_FILENAME").set(
                    "$ASSETLIB/scripts/python/matlib/res/img/FloorTexture.exr"
                )
                omat.setInput(1, tex, 0)
            else:
                print(
                    "Amaze: could not find the default Octane material "
                    "node on a fresh octane_vopnet - floor texture/specular "
                    "skipped (renderer version difference)."
                )

            self.mat.setName("Plane", True)

    def get_geo_node(self) -> hou.Node:
        """
        Get the currently attached GeoNode
        """
        return self.geo_node
