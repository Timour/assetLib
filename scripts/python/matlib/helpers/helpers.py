"""
Module with helpful utility functions used in and around houdini
"""

import re
import html

import hou


def tooltip_html(text: str, width: int = 250) -> str:
    """Rich-text tooltip that ADAPTS to its content: short text gets a
    snug box (Qt sizes the tooltip to the content), long text wraps inside
    a fixed max-width box instead of stretching to the screen edge.

    The previous version always pinned `width`, so even a two-word tooltip
    rendered in a big ~250px box (the Colors-hover bug). `width` is
    now a CEILING: only text whose longest line would overflow it gets the
    fixed-width wrapping table; anything shorter is returned as plain rich
    text and Qt fits the box to it. Width is logical px (~2x on Retina).
    Qt only word-wraps rich text, hence the HTML. Shared by every tab that
    uses this helper, so the fix is modular."""
    safe = html.escape(text).replace("\n", "<br>")
    longest = max((len(line) for line in text.splitlines()), default=0)
    # ~7 logical px per character at the tooltip font - a rough upper bound,
    # only used to decide box-vs-content-sized (not exact layout).
    if longest * 7 <= width:
        return "<div>%s</div>" % safe
    return (
        '<table width="%d" cellspacing="0" cellpadding="0">'
        "<tr><td>%s</td></tr></table>" % (width, safe)
    )


def find_file_parm(node: hou.Node) -> hou.Parm | None:
    """Returns the first file-reference parm on the node, or None if it
    has none.

    Detects it generically via Houdini's own parm-type system (any
    string parm whose stringType() is hou.stringParmType.FileReference -
    the same "does this parm hold a file path" mechanism Houdini's own
    tooling uses, e.g. for dependency collection) instead of a hardcoded
    per-node-type lookup table. Covers any node with a file-browse parm
    - Karma (mtlximage), Redshift, Octane, Copernicus/COP file nodes,
    future renderers, custom HDAs - without needing to know about that
    node type in advance. If a node exposes more than one file parm, the
    first one in parm-definition order wins; there's no attempt to guess
    which is "the" primary one beyond that.
    """
    for parm in node.parms():
        template = parm.parmTemplate()
        if (
            template.type() == hou.parmTemplateType.String
            and template.stringType() == hou.stringParmType.FileReference
        ):
            return parm
    return None


# Code-parm names, most common first: a wrangle's VEX snippet, an
# OpenCL kernel, a Python SOP's script, a Gas/DOP snippet. Detection is
# by KNOWN name rather than parm-type (a code parm is just a multiline
# String with no distinguishing template flag) - covers the nodes a
# "save a wrangle snippet" library actually cares about.
CODE_PARM_NAMES = ("snippet", "vexpression", "kernelcode", "python", "code")

CODE_PARM_LANGUAGE = {
    "snippet": "VEX",
    "vexpression": "VEX",
    "kernelcode": "OpenCL",
    "python": "Python",
    "code": "Code",
}


def find_code_parm(node: hou.Node) -> hou.Parm | None:
    """The node's code/snippet parm (a wrangle's `snippet`, an OpenCL
    `kernelcode`, a Python SOP's `python`, ...), or None. Checked in
    CODE_PARM_NAMES order so a node exposing several picks the most
    snippet-like first."""
    for name in CODE_PARM_NAMES:
        parm = node.parm(name)
        if parm is not None:
            return parm
    return None


def code_parm_language(parm: hou.Parm) -> str:
    """Best-effort language label ('VEX'/'OpenCL'/'Python'/'Code') for a
    code parm, from its name."""
    if parm is None:
        return "Code"
    return CODE_PARM_LANGUAGE.get(parm.name(), "Code")


def pick_cop_display_child(
    net: hou.Node, children: list | None = None
) -> hou.Node | None:
    """The child that best represents a COP network's picture - shared
    by the live pick at save time (render/nodes.py) and the loaded-copy
    fallback at render time (render/thumbs.py) so the two can't drift.
    `children` restricts the pick to a subset (a selection save): the
    network's display node only wins if it is IN that subset.

    Order, shaped by three misses hit in live testing:
    1. The display-flagged child. displayNode() first, but that call
       has not proven reliable on Copernicus networks - so each child
       is also asked directly via the generic flag API
       (isGenericFlagSet(hou.nodeFlag.Display)).
    2. Among OUT_*-named children, the one named like COLOR - a fabric
       setup carries OUT_color/OUT_normal/OUT_height siblings, and
       first-out*-wins rendered the normal map.
    3. Any OUT_*-named child, then an output-TYPE child.
    4. A terminal child (nothing downstream), then the last child.
    """
    kids = list(children) if children is not None else list(net.children())
    if not kids:
        return None
    display = None
    try:
        display = net.displayNode()
    except AttributeError:
        display = None
    if display is not None and display not in kids:
        display = None
    if display is None:
        for child in kids:
            try:
                if child.isGenericFlagSet(hou.nodeFlag.Display):
                    display = child
                    break
            except (AttributeError, TypeError, hou.OperationFailed):
                break
    if display is not None:
        return display
    outs = [c for c in kids if c.name().lower().startswith("out")]
    for child in outs:
        lowered = child.name().lower()
        if "color" in lowered or "rgb" in lowered:
            return child
    if outs:
        return outs[0]
    for child in kids:
        if "output" in child.type().name().lower():
            return child
    terminals = [c for c in kids if not c.outputConnections()]
    if terminals:
        return terminals[-1]
    return kids[-1]


def find_color_ramp_parm(node: hou.Node) -> hou.Parm | None:
    """Returns the first COLOR ramp parm on the node, or None.

    Same generic-detection philosophy as find_file_parm above: any parm
    whose template is a color RampParmTemplate qualifies, so this covers
    MaterialX/Karma ramps, Redshift ramps, COP ramps and custom HDAs
    without a per-node-type table. First in parm-definition order wins.
    """
    for parm in node.parms():
        template = parm.parmTemplate()
        if (
            template.type() == hou.parmTemplateType.Ramp
            and template.parmType() == hou.rampParmType.Color
        ):
            return parm
    return None


def find_color_parm_tuple(node: hou.Node) -> hou.ParmTuple | None:
    """Returns the first color parm tuple (3-component float with a
    color-square look, e.g. a material's base color) on the node, or
    None. Generic via the parm template's look/naming - no per-node
    knowledge."""
    for parm_tuple in node.parmTuples():
        template = parm_tuple.parmTemplate()
        if (
            template.type() == hou.parmTemplateType.Float
            and template.numComponents() == 3
            and (
                template.look() == hou.parmLook.ColorSquare
                or template.namingScheme() == hou.parmNamingScheme.RGBA
            )
        ):
            return parm_tuple
    return None


_RAMP_BASIS = {
    "Constant": hou.rampBasis.Constant,
    "Linear": hou.rampBasis.Linear,
    "CatmullRom": hou.rampBasis.CatmullRom,
    "MonotoneCubic": hou.rampBasis.MonotoneCubic,
    "Bezier": hou.rampBasis.Bezier,
    "BSpline": hou.rampBasis.BSpline,
    "Hermite": hou.rampBasis.Hermite,
}


def ramp_to_data(ramp: hou.Ramp) -> dict:
    """Serializes a hou.Ramp to plain JSON-able data (basis names, key
    positions, values) so a saved gradient re-applies exactly as it was
    on the source node."""
    reverse = {v: k for k, v in _RAMP_BASIS.items()}
    return {
        "bases": [reverse.get(b, "Linear") for b in ramp.basis()],
        "keys": list(ramp.keys()),
        "values": [list(v) for v in ramp.values()],
    }


def data_to_ramp(data: dict) -> hou.Ramp:
    """Inverse of ramp_to_data - unknown basis names degrade to
    Linear rather than failing."""
    bases = [
        _RAMP_BASIS.get(name, hou.rampBasis.Linear)
        for name in data.get("bases", [])
    ]
    values = [tuple(v) for v in data.get("values", [])]
    return hou.Ramp(bases, list(data.get("keys", [])), values)


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return (
        int(h[0:2], 16) / 255.0,
        int(h[2:4], 16) / 255.0,
        int(h[4:6], 16) / 255.0,
    )


def build_stepped_ramp(hex_colors: list) -> hou.Ramp:
    """A constant-basis (stepped) color ramp from a list of hex strings
    - discrete bands, so the palette's colors stay readable (the
    presentation used for the Sanzo Wada combinations)."""
    n = max(len(hex_colors), 1)
    bases = [hou.rampBasis.Constant] * n
    keys = [i / n for i in range(n)]
    values = [_hex_to_rgb(c) for c in hex_colors]
    return hou.Ramp(bases, keys, values)


def build_linear_ramp(hex_colors: list) -> hou.Ramp:
    """A linear-basis color ramp from a list of hex strings - the same
    palette as build_stepped_ramp but blending smoothly, keys spread
    evenly from 0 to 1."""
    n = len(hex_colors)
    if n <= 1:
        bases = [hou.rampBasis.Linear]
        keys = [0.0]
        values = [_hex_to_rgb(hex_colors[0])] if hex_colors else [(0.0, 0.0, 0.0)]
        return hou.Ramp(bases, keys, values)
    bases = [hou.rampBasis.Linear] * n
    keys = [i / (n - 1) for i in range(n)]
    values = [_hex_to_rgb(c) for c in hex_colors]
    return hou.Ramp(bases, keys, values)


def get_connected_nodes(node: hou.Node) -> list[hou.Node]:
    """
    Get all connected nodes for the given node
    Returns Input and Output Nodes in a single list

    Both get_connected_input_nodes and get_connected_output_nodes include
    the starting node itself in their result, so the raw concatenation
    would contain it twice - dedupe by path, preserving discovery order.

    :param node: Description
    :type node: hou.Node
    :return: Description
    :rtype: list[Node]
    """
    in_nodes = get_connected_input_nodes([node], selected=[])
    out_nodes = get_connected_output_nodes([node], selected=[])
    seen = set()
    unique = []
    for n in in_nodes + out_nodes:
        path = n.path()
        if path not in seen:
            seen.add(path)
            unique.append(n)
    return unique


def get_connected_input_nodes(
    nodes: list[hou.Node], selected: list[hou.Node]
) -> list[hou.Node]:
    """
    Get all connected Input nodes for the given node
    Returns only Input Nodes in a single list

    :param nodes: Description
    :type nodes: list[hou.Node]
    :param selected: Description
    :type selected: list[hou.Node]
    :return: Description
    :rtype: list[Node]
    """
    for node in nodes:
        if node is not None:
            selected.append(node)
            get_connected_input_nodes(node.inputs(), selected)
    return selected


def get_connected_output_nodes(
    nodes: list[hou.Node], selected: list[hou.Node]
) -> list[hou.Node]:
    """
    Get all connected Input nodes for the given node
    Returns only Input Nodes in a single list

    :param nodes: Description
    :type nodes: list[hou.Node]
    :param selected: Description
    :type selected: list[hou.Node]
    :return: Description
    :rtype: list[Node]
    """
    for node in nodes:
        if node is not None:
            selected.append(node)
            get_connected_output_nodes(node.outputs(), selected)
    return selected
def sanitize_usd_path(path: str) -> str:
    """
    Sanitze String for usage in .usd files and Solaris

    :param path: Description
    :type path: str
    :return: Description
    :rtype: str
    """
    clean = re.sub("[^a-zA-Z0-9]", "_", path)
    # Node names and USD prim names may not START with a digit -
    # "01_ball.obj"-style file names hit this constantly in the
    # Geometry section.
    if clean and clean[0].isdigit():
        clean = "_" + clean
    return clean
