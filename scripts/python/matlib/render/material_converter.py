"""Redshift -> Karma/MaterialX material conversion (test/v0).

Best-effort node-graph translation, not a faithful 1:1 transpiler - no
converter can reproduce every Redshift shading feature in MaterialX, and
this doesn't try to. It walks a reconstructed Redshift material's shading
network and rebuilds the closest MaterialX equivalent it can find,
node type by node type. Anything without a mapping (custom OSL, Toon
shading, most of Redshift's procedural utility nodes - RSRamp,
RSColorLayer, RSMathRange, etc.) is left unconverted and reported, never
silently dropped or guessed at - the caller decides what to do with a
partial result.

Maxon Noise (redshift::MaxonNoise) specifically has no MaterialX
equivalent at all - it's a large proprietary noise library (Alligator,
Displaced Turbulence, Wavy Turbulence, ...) with dozens of algorithms;
MaterialX's own noise nodes are a much smaller standard set (Perlin-family
noise3d, cellnoise3d, worleynoise3d, fractal3d). convert_maxon_noise()
substitutes a generic MaterialX fractal noise as a stand-in - it will not
look identical, and every use is flagged in the ConversionReport so that's
never mistaken for a faithful reproduction.
"""

import hou

from matlib.core import debug

from matlib.helpers import helpers


class ConversionReport:
    """Collects what happened during a single material's conversion, so
    the caller can show the user an honest summary instead of a bare
    pass/fail."""

    def __init__(self, mat_name: str) -> None:
        self.mat_name = mat_name
        self.skipped: list[str] = []
        self.approximated: list[str] = []

    def skip(self, msg: str) -> None:
        self.skipped.append(msg)

    def approximate(self, msg: str) -> None:
        self.approximated.append(msg)

    def is_clean(self) -> bool:
        return not self.skipped and not self.approximated

    def summary_lines(self) -> list[str]:
        lines = [f'"{self.mat_name}":']
        for msg in self.approximated:
            lines.append("  [approximated] " + msg)
        for msg in self.skipped:
            lines.append("  [skipped] " + msg)
        if self.is_clean():
            lines.append("  fully converted, no skipped inputs")
        return lines


def _redshift_type_available() -> bool:
    """True if the Redshift plugin is loaded (redshift_vopnet is a
    creatable node type). Category-agnostic - scans all node type
    categories rather than assuming which one owns it."""
    for cat in hou.nodeTypeCategories().values():
        if hou.nodeType(cat, "redshift_vopnet") is not None:
            return True
    return False


def find_redshift_shader(vopnet: hou.Node) -> hou.Node | None:
    """Find the actual surface shader node inside a reconstructed
    redshift_vopnet.

    Preferred: whatever is wired into the redshift_material output
    node's Surface input (input 0) - that's the shader actually driving
    the material, by definition, regardless of how many other
    "Material"-named utility nodes (MaterialBlender, MaterialLayer, ...)
    happen to exist in the network or in what order children() returns
    them. Falls back to the name-based scan shaderball_scene.py already
    uses (find "whatever material node exists") for networks with no
    output node or an unwired Surface input."""
    for child in vopnet.children():
        if child.type().name() == "redshift_material":
            inputs = child.inputs()
            if inputs and inputs[0] is not None:
                tname = inputs[0].type().name()
                # Only trust it if it actually looks like a surface
                # shader - guards against inputs() returning a compacted
                # tuple in some cases (e.g. only displacement wired),
                # where index 0 wouldn't be the Surface input at all.
                if "Material" in tname or "PBR" in tname:
                    return inputs[0]
            break
    for child in vopnet.children():
        tname = child.type().name()
        if tname == "redshift_material":
            continue
        if "Material" in tname or "PBR" in tname:
            return child
    return None


# redshift::StandardMaterial parm name -> mtlxstandard_surface parm name.
# Verified against Houdini's own bxdf/standard_surface.mtlx nodedef.
# Uncertain entries (emission/opacity - not directly confirmed against a
# real saved material in this pass) are included as best-effort; a wrong
# or missing name just no-ops via the try/except in _copy_constant_parm
# rather than raising.
STANDARD_MATERIAL_PARM_MAP = [
    ("base_color", "base_color"),
    ("base_color_weight", "base"),
    ("diffuse_roughness", "diffuse_roughness"),
    ("metalness", "metalness"),
    ("refl_color", "specular_color"),
    ("refl_weight", "specular"),
    ("refl_roughness", "specular_roughness"),
    ("refl_ior", "specular_IOR"),
    ("refl_aniso", "specular_anisotropy"),
    ("refl_aniso_rotation", "specular_rotation"),
    ("refr_weight", "transmission"),
    ("refr_color", "transmission_color"),
    ("refr_roughness", "transmission_extra_roughness"),
    ("ms_amount", "subsurface"),
    ("ms_color", "subsurface_color"),
    ("ms_radius", "subsurface_radius"),
    # subsurface_radius is a DISTANCE (mean free path); ms_radius_scale is
    # its scalar multiplier. Without carrying it across, a converted
    # subsurface material renders at scale 1 and washes out - the same
    # scale issue the PhysicallyBased import hit. OpenPBR already maps its
    # equivalent (subsurface_radius_scale) below.
    ("ms_radius_scale", "subsurface_scale"),
    ("sheen_weight", "sheen"),
    ("sheen_color", "sheen_color"),
    ("sheen_roughness", "sheen_roughness"),
    ("coat_weight", "coat"),
    ("coat_color", "coat_color"),
    ("coat_roughness", "coat_roughness"),
    ("coat_ior", "coat_IOR"),
    ("coat_aniso", "coat_anisotropy"),
    ("coat_aniso_rotation", "coat_rotation"),
    # thin film handled separately (gate + unit) by _convert_thin_film.
    ("emission_color", "emission_color"),
    ("emission_weight", "emission"),
    ("opacity_color", "opacity"),
]


# redshift::OpenPBRMaterial parm name -> mtlxstandard_surface parm name.
# OpenPBR uses the spec's own names (base_weight/base_metalness/...),
# which differ from Standard Surface's Arnold-style names - names read
# from a real saved OpenPBR material's .mat block (Blue_Acrylic), mapped
# to the Standard Surface nodedef. Targets mtlxstandard_surface (not
# mtlxopen_pbr_surface) to stay consistent with the library's existing
# Karma materials. Inputs handled elsewhere (geometry_normal ->
# bump/normal, tangents, thin_walled) are excluded here and listed in
# _OPENPBR_HANDLED_INPUTS so they don't report as "unmapped".
OPENPBR_MATERIAL_PARM_MAP = [
    ("base_weight", "base"),
    ("base_color", "base_color"),
    ("base_diffuse_roughness", "diffuse_roughness"),
    ("base_metalness", "metalness"),
    ("specular_weight", "specular"),
    ("specular_color", "specular_color"),
    ("specular_roughness", "specular_roughness"),
    ("specular_ior", "specular_IOR"),
    ("specular_roughness_anisotropy", "specular_anisotropy"),
    ("transmission_weight", "transmission"),
    ("transmission_color", "transmission_color"),
    ("transmission_depth", "transmission_depth"),
    ("transmission_scatter", "transmission_scatter"),
    ("transmission_scatter_anisotropy", "transmission_scatter_anisotropy"),
    ("transmission_dispersion_abbe_number", "transmission_dispersion"),
    ("subsurface_weight", "subsurface"),
    ("subsurface_color", "subsurface_color"),
    ("subsurface_radius", "subsurface_radius"),
    ("subsurface_radius_scale", "subsurface_scale"),
    ("subsurface_scatter_anisotropy", "subsurface_anisotropy"),
    ("fuzz_weight", "sheen"),
    ("fuzz_color", "sheen_color"),
    ("fuzz_roughness", "sheen_roughness"),
    ("coat_weight", "coat"),
    ("coat_color", "coat_color"),
    ("coat_roughness", "coat_roughness"),
    ("coat_ior", "coat_IOR"),
    ("coat_roughness_anisotropy", "coat_anisotropy"),
    # thin film handled separately (gate + unit) by _convert_thin_film.
    ("emission_color", "emission_color"),
    # OpenPBR emission is a luminance (nits), Standard Surface's is a
    # 0-1 weight - a direct copy is right for the common non-emissive
    # case (0 stays 0); genuinely emissive materials may need the weight
    # adjusted by hand (flagged in the report).
    ("emission_luminance", "emission"),
    ("geometry_opacity", "opacity"),
]

# OpenPBR shader inputs converted OUTSIDE the parm map (bump/normal via
# the geometry_normal input, tangents, thin-walled) - excluded from the
# "connected but unmapped" report.
_OPENPBR_HANDLED_INPUTS = {
    "geometry_normal",
    "geometry_coat_normal",
    "geometry_tangent",
    "geometry_coat_tangent",
    "geometry_thin_walled",
    "bump_input",
}

def _convert_thin_film(rs_node: hou.Node, mtlx: hou.Node, report) -> None:
    """Convert Redshift thin film, respecting the GATE and the UNIT that a
    naive copy ignored (which baked a warm iridescent tint onto every
    converted metal - #179; the root cause was indeed a scaling
    problem):

    * OpenPBR gates thin film with `thin_film_weight` (default 0), and its
      `thin_film_thickness` is in MICROMETRES (parm range 0-1, default
      0.5um = 500nm). mtlxstandard_surface has NO weight gate (thickness
      >0 switches it on) and wants NANOMETRES - so apply only when the
      weight is on, and convert um -> nm (x1000).
    * StandardMaterial has no weight (thickness>0 IS the gate) and its
      `thinfilm_thickness` is already in NANOMETRES (parm range 0-1000) ->
      copy straight across.

    So a metal that genuinely has thin film converts correctly; one that
    doesn't (weight 0, or thickness 0) leaves mtlx's thin film off."""
    def _v(name):
        p = rs_node.parm(name)
        try:
            return p.eval() if p is not None else None
        except hou.Error:
            return None

    def _put(name, value):
        p = mtlx.parm(name)
        if p is not None:
            try:
                p.set(value)
            except hou.Error:
                pass

    weight = _v("thin_film_weight")          # OpenPBR only
    if weight is not None:                    # -> this is an OpenPBR shader
        if weight > 1e-6:
            _put("thin_film_thickness", (_v("thin_film_thickness") or 0.0) * 1000.0)
            ior = _v("thin_film_ior")
            if ior is not None:
                _put("thin_film_IOR", ior)
        return
    thickness_nm = _v("thinfilm_thickness")   # StandardMaterial, in nm
    if thickness_nm is not None and thickness_nm > 1e-6:
        _put("thin_film_thickness", thickness_nm)
        ior = _v("thinfilm_ior")
        if ior is not None:
            _put("thin_film_IOR", ior)


def _copy_constant_parm(
    src_node: hou.Node, src_name: str, dst_node: hou.Node, dst_name: str
) -> None:
    """Copy a plain value across, WIDTH-AWARE: OpenPBR and Standard
    Surface don't always agree on a parm's tuple width (e.g. a color on
    one side, a scalar on the other), and a raw set() of a mismatched
    width raises hou.InvalidSize - which is a sibling of hou.Error, NOT
    caught by the old (OperationFailed, TypeError, ValueError) clause,
    so it aborted the whole OpenPBR conversion ("aluminum: crashed -
    Invalid size" in the error dialog). A scalar broadcasts to a vector, a
    vector collapses to its first component; anything still off no-ops
    rather than raising."""
    src_parm = src_node.parmTuple(src_name)
    dst_parm = dst_node.parmTuple(dst_name)
    if src_parm is None or dst_parm is None:
        return
    try:
        src_vals = list(src_parm.eval())
        dst_len = len(dst_parm)
        if len(src_vals) != dst_len:
            if len(src_vals) == 1:
                src_vals = src_vals * dst_len          # scalar -> vector
            elif dst_len == 1:
                src_vals = src_vals[:1]                # vector -> scalar
            else:
                src_vals = (src_vals + [src_vals[-1]] * dst_len)[:dst_len]
        dst_parm.set(tuple(src_vals))
    except (hou.Error, TypeError, ValueError):
        pass


_UV_SCALE_TAG = "matlib_uv_scale"


def _named_inputs(node: hou.Node) -> dict:
    """{input_name: connected_node} for a VOP node, via the None-padded
    inputs()/inputNames() positional zip (padding confirmed against H21's
    own HOM docs - see convert_standard_material's history note for why
    inputConnections() isn't used instead)."""
    input_nodes = node.inputs()
    input_names = node.inputNames()
    result = {}
    for i, name in enumerate(input_names):
        if i < len(input_nodes) and input_nodes[i] is not None:
            result[name] = input_nodes[i]
    return result


def _set_poly_parm(node: hou.Node, base_name: str, values, signature: str) -> bool:
    """Set a parm on a signature-polymorphic MaterialX node
    (mtlxmultiply, mtlxremap, ...), safely. These nodes carry one parm
    variant per signature (in2, in2_color3, in2_vector2, ... - confirmed
    from a saved multiply's spare-parm block in the real library), the
    parm's tuple width varies accordingly, and a blind set of the wrong
    width raises hou.InvalidSize - which is a hou.Error, NOT a
    ValueError, so a careless except clause misses it (that combination
    crashed every conversion once). Nudges the signature parm first,
    then tries the suffixed variant BEFORE the plain base name: after a
    signature switch the suffixed parm is the one that actually renders,
    and setting only the plain variant succeeds silently while changing
    nothing visible (that exact miss shipped once too - textures came
    out unscaled with the scale correctly read)."""
    if not isinstance(values, (tuple, list)):
        values = (values,)
    try:
        sig = node.parm("signature")
        if sig is not None:
            sig.set(signature)
    except hou.Error:
        pass
    for parm_name in (base_name + "_" + signature, base_name):
        parm = node.parmTuple(parm_name)
        if parm is None:
            continue
        vals = (tuple(float(v) for v in values) + (float(values[-1]),) * len(parm))[
            : len(parm)
        ]
        try:
            parm.set(vals)
            return True
        except (hou.Error, TypeError, ValueError):
            continue
    return False


def _set_multiply_in2(multiply: hou.Node, scale: tuple) -> bool:
    return _set_poly_parm(multiply, "in2", scale, "vector2")


def _get_or_create_uv_chain(
    dest_parent: hou.Node, scale: tuple, report: ConversionReport
) -> hou.Node:
    """UV chain for converted textures: one shared mtlxtexcoord per
    material, feeding one mtlxmultiply per *distinct* UV scale value -
    every mtlximage wires its texcoord input from the multiply matching
    its source sampler's Redshift `scale` parm. Samplers in production
    materials usually channel-reference one shared scale value, so the
    common case is a single multiply serving every image - which then
    doubles as the one dial that scales all the material's textures
    together (the original ask). A material mixing different scales gets
    one multiply per value, still correct per image.

    The multiply doubles as the record of which scale it carries via
    node user data - matching on that (not on parm values, whose width
    varies by signature) is what makes reuse detection reliable."""
    tag = f"{scale[0]:.6g},{scale[1]:.6g}"
    for child in dest_parent.children():
        if (
            child.type().name() == "mtlxmultiply"
            and child.userData(_UV_SCALE_TAG) == tag
        ):
            return child
    texcoord = None
    for child in dest_parent.children():
        if child.type().name() == "mtlxtexcoord":
            texcoord = child
            break
    if texcoord is None:
        texcoord = dest_parent.createNode("mtlxtexcoord")
    multiply = dest_parent.createNode("mtlxmultiply")
    multiply.setNamedInput("in1", texcoord, 0)
    multiply.setUserData(_UV_SCALE_TAG, tag)
    if not _set_multiply_in2(multiply, scale) and tuple(scale) != (1.0, 1.0):
        # A failed set on a non-default scale means the converted
        # texture would render at the wrong tiling - worth surfacing.
        # (A failed set on 1,1 is harmless: multiply's in2 defaults to 1.)
        report.approximate(
            f"couldn't set UV scale {tag} on the texture-scale multiply - "
            "set its second input by hand"
        )
    return multiply


#: Standard-surface inputs that carry COLOUR (need an sRGB read and a
#: color3 signature). Everything else a texture can feed - roughness,
#: metalness, specular, opacity, coat weight, ... - is scalar DATA and
#: must be read RAW/linear. This is the wiki's per-map colour-space rule
#: (karma-material-best-practice.md §12), verified against SideFX's own
#: StandardSurface .mtlx: the colour image carries colorspace
#: "srgb_texture", the roughness image carries none.
_COLOUR_INPUTS = frozenset({
    "base_color",
    "specular_color",
    "coat_color",
    "emission_color",
    "subsurface_color",
    "sheen_color",
    "transmission_color",
})


def _apply_image_colorspace(
    image_node: hou.Node, role: str, report: ConversionReport, label: str
) -> None:
    """Set an mtlximage's signature + colour space by semantic role.

    role: "color" (sRGB, color3), "data" (Raw, float) or "normal" (Raw,
    vector3). A texture read in the wrong space isn't obviously broken -
    it looks *subtly* wrong - which is exactly why it's worth forcing
    rather than leaving to the node default (color3/auto)."""
    if "image" not in image_node.type().name():
        return
    signature, colorspace = {
        "color": ("color3", "srgb_texture"),
        "data": ("default", "Raw"),
        "normal": ("vector3", "Raw"),
    }.get(role, ("color3", "srgb_texture"))
    for parm_name, value in (
        ("signature", signature),
        ("filecolorspace", colorspace),
    ):
        parm = image_node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.set(value)
        except hou.Error:
            report.approximate(
                f'{label}: could not set the texture {parm_name}={value} '
                "- it may be read in the wrong colour space"
            )


def convert_texture_sampler(
    rs_node: hou.Node,
    dest_parent: hou.Node,
    report: ConversionReport,
    target_input: str = "",
) -> hou.Node:
    """redshift::TextureSampler -> mtlximage (+ the material's shared
    texcoord->multiply UV chain wired into its texcoord input, carrying
    the sampler's own Redshift `scale` value - confirmed as a 2-float
    parm from real saved library data, where production materials
    genuinely use it, e.g. scale 5,5).

    `target_input` is the standard-surface input this texture feeds, used
    to pick the colour space: a colour input reads sRGB, everything else
    reads Raw (see _COLOUR_INPUTS). Normal maps go through convert_bump_map,
    which sets the Vector3/Raw combination itself."""
    mtlx = dest_parent.createNode("mtlximage")
    path_parm = rs_node.parm("tex0")
    if path_parm is not None:
        mtlx.parm("file").set(path_parm.unexpandedString())
    role = "color" if target_input in _COLOUR_INPUTS else "data"
    _apply_image_colorspace(mtlx, role, report, f'"{rs_node.name()}"')
    scale = (1.0, 1.0)
    scale_parm = rs_node.parmTuple("scale")
    if scale_parm is not None:
        try:
            vals = scale_parm.eval()
            if len(vals) >= 2:
                scale = (float(vals[0]), float(vals[1]))
            elif len(vals) == 1:
                scale = (float(vals[0]), float(vals[0]))
        except hou.Error:
            pass
    try:
        mtlx.setNamedInput(
            "texcoord", _get_or_create_uv_chain(dest_parent, scale, report), 0
        )
    except hou.OperationFailed:
        report.skip(
            f'"{rs_node.name()}": couldn\'t wire a texcoord node into its '
            "mtlximage - UVs may need connecting by hand"
        )
    return mtlx


def convert_maxon_noise(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node:
    """redshift::MaxonNoise -> a generic MaterialX fractal noise stand-in.
    See the module docstring - this is deliberately not claimed to be a
    faithful reproduction, just the closest available substitute."""
    position = dest_parent.createNode("mtlxposition")
    fractal = dest_parent.createNode("mtlxfractal3d")
    fractal.setNamedInput("position", position, 0)
    report.approximate(
        f'"{rs_node.name()}" (redshift::MaxonNoise) has no MaterialX equivalent - '
        "substituted a generic fractal noise; review visually, it will not match exactly"
    )
    return fractal


def convert_bump_map(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node | None:
    """The RS output node's "Bump Map" input -> mtlxnormalmap feeding the
    shader's normal input (project convention: everything bump goes to
    the normal map). Real parm/input names confirmed from saved library
    data: redshift::BumpMap takes its texture on "input" and has
    inputType (0 = height field, 1 = tangent-space normal) + scale.

    The bump texture goes STRAIGHT into mtlxnormalmap's "in" input, no
    conversion node in between - an earlier version inserted
    mtlxheighttonormal whenever the RS inputType parm said height-field,
    but library content wires real normal-map textures through BumpMap
    nodes regardless of that setting, so trusting it produced a
    nonsensical normal->normal "conversion". The library's hand-built
    materials settle the wiring convention: all 67 normal-map textures
    in the library feed mtlxnormalmap's "in" input directly
    (normal/tangent inputs always unconnected). The RS scale is still
    only copied when inputType explicitly says tangent-space normal
    (value 1) - a height-style scale like 0.001 copied onto a
    normal-map strength would flatten it to nothing."""
    nm = dest_parent.createNode("mtlxnormalmap")
    if rs_node.type().name() == "redshift::BumpMap":
        tex_src = _named_inputs(rs_node).get("input")
        input_type = rs_node.parm("inputType")
        if input_type is not None and input_type.eval() == 1:
            _copy_constant_parm(rs_node, "scale", nm, "scale")
    else:
        # Something else wired straight into the output's Bump Map input.
        tex_src = rs_node
    if tex_src is None:
        report.skip(
            f'"{rs_node.name()}": bump map has no texture input to convert'
        )
        return nm
    converted = convert_node(tex_src, dest_parent, report)
    if converted is not None:
        # A normal map is DATA, not colour: Vector3 signature + Raw colour
        # space, so no sRGB transform is applied. SideFX's guidance is
        # explicit about this, and it matches their own chess_set .mtlx
        # (image type vector3, no colorspace attr). Wrong here looks
        # subtly wrong, not obviously broken - worth forcing.
        _apply_image_colorspace(
            converted, "normal", report, f'"{rs_node.name()}"'
        )
        try:
            nm.setNamedInput("in", converted, 0)
        except hou.OperationFailed:
            report.skip(
                f'"{rs_node.name()}": converted bump texture couldn\'t be '
                "wired into mtlxnormalmap"
            )
    return nm


def convert_displacement(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node | None:
    """The RS output node's "Displacement" input -> mtlxdisplacement.
    Real parm/input names confirmed from saved library data:
    redshift::Displacement takes its texture on "texMap" and has a float
    scale (plus a Change Range remap, which mtlxdisplacement has no
    equivalent for - reported when it's actually in use)."""
    disp = dest_parent.createNode("mtlxdisplacement")
    remap_values = None
    if rs_node.type().name() == "redshift::Displacement":
        _copy_constant_parm(rs_node, "scale", disp, "scale")
        ranges = {}
        for pname, default in (
            ("oldrange_min", 0.0),
            ("oldrange_max", 1.0),
            ("newrange_min", 0.0),
            ("newrange_max", 1.0),
        ):
            parm = rs_node.parm(pname)
            ranges[pname] = parm.eval() if parm is not None else default
        if any(
            abs(ranges[p] - d) > 1e-6
            for p, d in (
                ("oldrange_min", 0.0),
                ("oldrange_max", 1.0),
                ("newrange_min", 0.0),
                ("newrange_max", 1.0),
            )
        ):
            remap_values = ranges
        tex_src = _named_inputs(rs_node).get("texMap")
    else:
        # Something else wired straight into the output's Displacement
        # input - convert it directly as the displacement source.
        tex_src = rs_node
    if tex_src is None:
        return disp
    converted = convert_node(tex_src, dest_parent, report)
    if converted is None:
        return disp
    # A non-default Change Range on the RS node has a faithful MaterialX
    # equivalent after all: mtlxremap (inlow/inhigh -> outlow/outhigh
    # maps exactly onto RS's oldrange -> newrange). Inserted between the
    # converted texture and the displacement node only when actually in
    # use. Parms set via the polymorphic-safe helper (same
    # signature-variant parm layout as mtlxmultiply); a failed set
    # falls back to the old honest "adjust by hand" note rather than
    # silently producing wrong displacement levels.
    if remap_values is not None:
        remap = dest_parent.createNode("mtlxremap")
        ok = True
        for base, key in (
            ("inlow", "oldrange_min"),
            ("inhigh", "oldrange_max"),
            ("outlow", "newrange_min"),
            ("outhigh", "newrange_max"),
        ):
            ok = _set_poly_parm(remap, base, remap_values[key], "color3") and ok
        try:
            remap.setNamedInput("in", converted, 0)
            converted = remap
        except hou.OperationFailed:
            ok = False
        if not ok:
            report.approximate(
                f'"{rs_node.name()}" uses a Change Range remap that '
                "couldn't be fully rebuilt as mtlxremap - check the remap "
                "node's values (or displacement levels) by hand"
            )
    try:
        disp.setNamedInput("displacement", converted, 0)
    except hou.OperationFailed:
        report.skip(
            f'"{rs_node.name()}": converted displacement texture '
            "couldn't be wired into mtlxdisplacement"
        )
    return disp


#: RSRamp inputMapping -> which UV channel drives the ramp, expressed as
#: the mtlxseparate2 output index (outx = 0 = U, outy = 1 = V). Menu
#: values/labels confirmed by inspecting the real parm in Houdini:
#: 0 Vertical, 1 Horizontal, 2 Diagonal, 3 Radial, 4 Circular.
_RAMP_MAPPING_OUTPUT = {"0": 1, "1": 0}  # Vertical -> V, Horizontal -> U
_RAMP_MAPPING_LABELS = {
    "0": "Vertical",
    "1": "Horizontal",
    "2": "Diagonal",
    "3": "Radial",
    "4": "Circular",
}


def _build_ramp_uv_driver(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
):
    """The value that drives an RSRamp when nothing is wired into its
    "input": Redshift derives it from the UV map according to
    inputMapping (Vertical -> V, Horizontal -> U). Rebuilt in MaterialX
    as mtlxtexcoord -> mtlxseparate2 -> the matching channel, honouring
    inputInvert. Without this the converted ramp has no driving value at
    all and reads flat. Returns (node, output_index), or None if the
    chain couldn't be built."""
    mapping = ""
    parm = rs_node.parm("inputMapping")
    if parm is not None:
        try:
            mapping = str(parm.eval())
        except hou.Error:
            mapping = ""
    out_index = _RAMP_MAPPING_OUTPUT.get(mapping)
    if out_index is None:
        report.approximate(
            f'"{rs_node.name()}" (RSRamp) uses a '
            f"{_RAMP_MAPPING_LABELS.get(mapping, mapping)} input mapping - "
            "MaterialX has no direct equivalent, so the ramp is driven by "
            "V instead; adjust by hand if it matters"
        )
        out_index = 1
    try:
        texcoord = dest_parent.createNode("mtlxtexcoord")
        separate = dest_parent.createNode("mtlxseparate2")
        separate.setNamedInput("in", texcoord, 0)
    except hou.Error:
        report.skip(
            f'"{rs_node.name()}" (RSRamp): could not build the UV driver - '
            "wire the ramp's input by hand"
        )
        return None
    driver, driver_out = separate, out_index
    invert = rs_node.parm("inputInvert")
    try:
        if invert is not None and invert.eval():
            inv = dest_parent.createNode("mtlxinvert")
            inv.setNamedInput("in", separate, out_index)
            driver, driver_out = inv, 0
    except hou.Error:
        report.approximate(
            f'"{rs_node.name()}" (RSRamp) has Invert Input enabled - it '
            "couldn't be reproduced; flip the ramp by hand"
        )
    return (driver, driver_out)


def convert_ramp(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node | None:
    """redshift::RSRamp -> kma_rampconst ("Karma Ramp Const"), Houdini's
    real Karma ramp node - a ramp stays a ramp.

    It takes the lookup position on its "t" input (float) and holds the
    gradient in an ordinary Houdini ramp parm ("vramp" for colour,
    "framp" for float), so the whole gradient copies across as a
    hou.Ramp with its colours intact.

    Two earlier attempts were wrong and are worth remembering:
    hmtlxrampc is EXCLUDED from the Karma context (voptoolutils'
    KARMAMTLX_TAB_MASK lists "^hmtlxramp*") so Karma degrades it to a
    float evaluation - colour knots in, greyscale out; and rebuilding
    the gradient out of mtlxremap/clamp/mix worked but wasn't a ramp any
    more. kma_rampconst only exists inside a real Karma Material
    Builder context, which is why the converter now builds there
    (nodes.make_karma_builder)."""
    ramp = None
    src_parm = rs_node.parm("ramp")
    if src_parm is not None:
        try:
            ramp = src_parm.evalAsRamp()
        except hou.Error:
            ramp = None

    try:
        node = dest_parent.createNode("kma_rampconst")
    except hou.Error as exc:
        report.skip(
            f'"{rs_node.name()}" (RSRamp): the Karma ramp node could not '
            f"be created here ({exc}) - rebuild the ramp by hand"
        )
        return None

    # Colour ramps live on "vramp", float ramps on "framp".
    is_color = True
    if ramp is not None:
        try:
            values = ramp.values()
            if values:
                is_color = isinstance(values[0], (tuple, list))
        except hou.Error:
            pass
    if ramp is not None:
        target = "vramp" if is_color else "framp"
        parm = node.parm(target)
        if parm is not None:
            try:
                parm.set(ramp)
            except hou.Error:
                report.approximate(
                    f'"{rs_node.name()}" (RSRamp): the gradient could not '
                    "be copied - rebuild the ramp by hand"
                )
        # Both Houdini MaterialX ramp nodes are LINEAR-ONLY (SideFX docs
        # for kma_rampconst and hmtlxrampc both say so), so any other
        # knot interpolation can't be reproduced faithfully.
        try:
            if any(b != hou.rampBasis.Linear for b in ramp.basis()):
                report.approximate(
                    f'"{rs_node.name()}" (RSRamp) uses non-linear knot '
                    "interpolation - the Karma ramp only supports Linear, "
                    "so the gradient is rebuilt as linear"
                )
        except (hou.Error, AttributeError):
            pass

    # Whatever drives the lookup. A wired input converts directly;
    # otherwise Redshift derives it from the UV map per inputMapping,
    # which we rebuild as texcoord -> separate -> U/V. Without a driver
    # the ramp has nothing to look up and reads flat.
    src_in = _named_inputs(rs_node).get("input")
    driver = None
    if src_in is not None:
        converted = convert_node(src_in, dest_parent, report)
        if converted is not None:
            driver = (converted, 0)
    else:
        driver = _build_ramp_uv_driver(rs_node, dest_parent, report)
    if driver is not None:
        try:
            node.setNamedInput("t", driver[0], driver[1])
        except hou.OperationFailed:
            report.skip(
                f'"{rs_node.name()}" (RSRamp): the driving value could not '
                "be wired into the ramp's t input"
            )
    else:
        report.approximate(
            f'"{rs_node.name()}" (RSRamp): nothing drives the gradient - '
            "wire the ramp's t input by hand or it reads flat"
        )

    # "Alt" input source has no MaterialX equivalent (UV Map / Auto both
    # mean the UV-driven chain above).
    source = rs_node.parm("inputSource")
    try:
        if source is not None and str(source.eval()) == "1":
            report.approximate(
                f'"{rs_node.name()}" (RSRamp) uses the "Alt" input source '
                "- no Karma equivalent, so it is driven by UV instead"
            )
    except hou.Error:
        pass
    return node


# Node type name -> converter function. Anything not listed here is
# reported as skipped rather than guessed at.
NODE_CONVERTERS = {
    "redshift::TextureSampler": convert_texture_sampler,
    "redshift::MaxonNoise": convert_maxon_noise,
    "redshift::RSRamp": convert_ramp,
}


def convert_node(
    rs_node: hou.Node,
    dest_parent: hou.Node,
    report: ConversionReport,
    target_input: str = "",
) -> hou.Node | None:
    """Dispatch a single connected (procedural) input node to its
    converter, or report it as unsupported. Returns None on no mapping -
    callers must handle that by leaving the destination input unwired
    rather than crashing.

    `target_input` is the standard-surface input the result will feed;
    passed through so a texture sampler can pick its colour space from
    what it drives (colour vs data). Only convert_texture_sampler uses
    it; the other converters accept and ignore it via **kwargs-free
    signature matching below."""
    conv = NODE_CONVERTERS.get(rs_node.type().name())
    if conv is None:
        report.skip(
            f'"{rs_node.name()}" ({rs_node.type().name()}) has no conversion '
            "mapping yet - left at the MaterialX default"
        )
        return None
    if conv is convert_texture_sampler:
        return conv(rs_node, dest_parent, report, target_input)
    return conv(rs_node, dest_parent, report)


def convert_standard_material(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node:
    """redshift::StandardMaterial -> mtlxstandard_surface."""
    return _convert_uber_shader(
        rs_node, dest_parent, report,
        STANDARD_MATERIAL_PARM_MAP, {"bump_input"},
    )


def convert_openpbr_material(
    rs_node: hou.Node, dest_parent: hou.Node, report: ConversionReport
) -> hou.Node:
    """redshift::OpenPBRMaterial -> mtlxstandard_surface (OpenPBR's
    spec parm names translated to Standard Surface's, per
    OPENPBR_MATERIAL_PARM_MAP)."""
    return _convert_uber_shader(
        rs_node, dest_parent, report,
        OPENPBR_MATERIAL_PARM_MAP, _OPENPBR_HANDLED_INPUTS,
    )


def _convert_uber_shader(
    rs_node: hou.Node,
    dest_parent: hou.Node,
    report: ConversionReport,
    parm_map: list,
    handled_inputs: set,
) -> hou.Node:
    """Build an mtlxstandard_surface node (under dest_parent) equivalent
    to rs_node (a Redshift Standard or OpenPBR material) using parm_map.
    For each mapped parameter: if the Redshift input has a live
    connection, recursively convert whatever feeds it; otherwise just
    copy the constant value across. handled_inputs are connected inputs
    dealt with elsewhere (bump/normal), so they aren't reported as
    unmapped."""
    mtlx = dest_parent.createNode("mtlxstandard_surface")
    # inputConnections()/NodeConnection.outputNode() was tried first and
    # gave back nonsense for this node type - outputNode() kept reporting
    # rs_node itself, with output-sounding names ("outColor", "out"), not
    # the upstream texture nodes actually feeding it. Rather than keep
    # guessing at that API's exact semantics, switched to the plain
    # inputs()/inputNames() pairing this codebase already uses
    # successfully elsewhere for the same kind of lookup
    # (helpers.get_connected_nodes, and shaderball_scene.py's own
    # setNamedInput(name, tex, 0) wiring - which is also why the output
    # index below is hardcoded to 0: every proven wiring call in this
    # codebase assumes a texture/utility node's primary output is index 0,
    # and there's no simple way to recover a specific output index from
    # inputs() alone).
    name_to_node = _named_inputs(rs_node)
    debug.event("convert", "shader connected inputs",
                node=rs_node.path(), inputs=list(name_to_node.keys()))
    for rs_name, mtlx_name in parm_map:
        src_node = name_to_node.get(rs_name)
        if src_node is None:
            _copy_constant_parm(rs_node, rs_name, mtlx, mtlx_name)
            continue
        converted = convert_node(src_node, dest_parent, report, mtlx_name)
        if converted is not None:
            try:
                mtlx.setNamedInput(mtlx_name, converted, 0)
            except hou.OperationFailed:
                report.skip(
                    f'"{rs_name}" -> "{mtlx_name}": conversion built '
                    "successfully but couldn't be wired to the shader input"
                )
    # A connected input the parm map doesn't cover used to vanish without
    # a trace (the skip-notes above only fire for MAPPED inputs) -
    # a real conversion showed a live "bump_input" connection converting
    # to nothing, silently. bump/normal inputs are handled separately in
    # convert_redshift_material; everything else unmapped at least gets
    # reported now.
    mapped = {rs_name for rs_name, _ in parm_map}
    for name in name_to_node:
        if name in mapped or name in handled_inputs:
            continue
        report.skip(
            f'shader input "{name}" is connected but has no conversion '
            "mapping yet - left at the MaterialX default"
        )
    _convert_thin_film(rs_node, mtlx, report)
    return mtlx


def convert_redshift_material(
    node_handler,
    source_mat,
    prefs_dir_parent: hou.Node,
) -> tuple[hou.Node | None, ConversionReport]:
    """Reconstructs source_mat (a Redshift material.Material) at a scratch
    location, converts its shader network, and returns the converted
    (shader, displacement, report): the mtlxstandard_surface, an
    mtlxdisplacement (or None), and a report of what couldn't be
    converted - the SAME adapter API as the online translator, so the
    engine wires each into the builder's own terminal. Both live under
    prefs_dir_parent; the caller registers via add_asset() and destroys
    the scratch node.

    redshift::StandardMaterial and redshift::OpenPBRMaterial (the two
    largest groups in a real-world library) are both handled, each via
    its own parm map to mtlxstandard_surface; any other shader type is
    reported and skipped, returning (None, report)."""
    report = ConversionReport(source_mat.name)
    debug.event("convert", "start", material=source_mat.name,
                renderer=source_mat.renderer, mat_id=source_mat.mat_id)

    # Preflight: the converter must RECONSTRUCT the Redshift material to
    # read its parameters, which needs the Redshift plugin loaded. When
    # it isn't (e.g. the Houdini/Redshift version mismatch keeps it from
    # loading), createNode("redshift_vopnet") raises "Invalid node type
    # name" mid-reconstruction - a cryptic crash. Report it clearly and
    # skip instead.
    if not _redshift_type_available():
        report.skip(
            "Redshift isn't loaded this session, so the source material "
            "can't be read (the converter reconstructs the Redshift "
            "network to read its parameters). Load Redshift and retry - "
            "check the Houdini/Redshift plugin versions match."
        )
        return None, None, report

    scratch = hou.node("/obj").createNode("matnet")
    try:
        node_handler._hou_parent = scratch
        node_handler._import_path = scratch
        node_handler._use_existing_node = True

        iface_path = (
            node_handler._preferences.dir
            + node_handler._preferences.asset_dir
            + source_mat.mat_id
            + ".interface"
        )
        node_handler.load_interface_other(iface_path, source_mat, "redshift_vopnet")
        node_handler.load_items_file(source_mat, move_builder=True)
        vopnet = node_handler.builder_node

        shader = find_redshift_shader(vopnet)
        if shader is None:
            report.skip("no surface shader node found inside the saved network")
            return None, None, report
        shader_type = shader.type().name()
        if shader_type == "redshift::StandardMaterial":
            mtlx = convert_standard_material(shader, prefs_dir_parent, report)
        elif shader_type == "redshift::OpenPBRMaterial":
            mtlx = convert_openpbr_material(shader, prefs_dir_parent, report)
        else:
            report.skip(
                f"shader is {shader_type}, not a Standard or OpenPBR "
                "material - not handled"
            )
            return None, None, report

        # Bump and displacement live on the redshift_material OUTPUT
        # node's own named inputs in RS networks, never on the shader -
        # walk them from there. Bump becomes mtlxnormalmap -> the
        # shader's normal input; displacement becomes mtlxdisplacement,
        # tied to the surface via a collect node so the whole
        # displacement branch is reachable from the single node the
        # save path walks connections from (get_connected_nodes only
        # recurses through inputs of the node it's handed - a
        # displacement chain not wired to anything shared with the
        # shader would silently not be saved at all).
        out_node = None
        for child in vopnet.children():
            if child.type().name() == "redshift_material":
                out_node = child
                break
        out_inputs = _named_inputs(out_node) if out_node is not None else {}
        # Bump can arrive two ways in RS networks: wired into the
        # shader's own bump_input, or into the output node's "Bump Map"
        # input - real production libraries show both patterns (a real
        # material used bump_input, which the output-node-only handling
        # silently dropped). Shader-level wins when both exist (it's the
        # same BumpMap node in every observed case) - only one normal
        # input to feed either way.
        # StandardMaterial wires bump into bump_input or the output's
        # "Bump Map"; OpenPBR wires its normal/bump into the shader's
        # own geometry_normal input. Check all three.
        shader_inputs = _named_inputs(shader)
        bump_src = (
            shader_inputs.get("geometry_normal")
            or shader_inputs.get("bump_input")
            or out_inputs.get("Bump Map")
        )
        if bump_src is not None:
            nm = convert_bump_map(bump_src, prefs_dir_parent, report)
            if nm is not None:
                try:
                    mtlx.setNamedInput("normal", nm, 0)
                except hou.OperationFailed:
                    report.skip(
                        "converted bump couldn't be wired to the "
                        "shader's normal input"
                    )
        mtlx_disp = None
        if out_node is not None:
            disp_src = out_inputs.get("Displacement")
            if disp_src is not None:
                mtlx_disp = convert_displacement(disp_src, prefs_dir_parent, report)

        # Sanitized: node names can't contain spaces/dashes etc., and
        # library material names can - an unsanitized setName() would
        # raise hou.OperationFailed and abort the whole conversion for
        # any such material. Same helper the import path uses.
        mtlx.setName(
            helpers.sanitize_usd_path(source_mat.name), unique_name=True
        )
        # Return (shader, displacement) - the SAME adapter API the online
        # translator uses, so the engine wires each into its own builder
        # terminal (surface_output / displacement_output). This replaced a
        # collect node bundling the two, which the KARMA_REF subnetconnector
        # builder rejects (its surface terminal won't accept a collect's
        # output - hou.InvalidInput, the converter crash). Displacement is
        # saved as part of the whole builder now, so it no longer needs a
        # collect to stay reachable from one node.
        return mtlx, mtlx_disp, report
    finally:
        # The reconstructed Redshift copy is only ever scratch scaffolding
        # for reading values/connections - never left in the scene, same
        # discipline as every other temp-node use in this codebase.
        scratch.destroy()
