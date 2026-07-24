"""The PhysicallyBased material icon.

Value-only sources ship no textures and therefore no preview render, so
their tile is DRAWN from the material's own measured numbers, using
the template file (ui/physicallybased_thumb.svg):

    +---------------------------+
    |     PHYSICALLY BASED      |   fixed header (text is curves,
    |    BY ANTON PALMQVIST     |   so no font dependency)
    +-------------+-------------+
    | base colour | transparency|   two live swatches
    +-------------+-------------+

* **base colour** - the material's `color`, converted linear -> sRGB.
* **transparency** - black over a checkerboard. `transmission` in this
  dataset is strictly boolean (21 of 86 materials have it, always 1;
  `transmissionDepth` exists on only two), so there is no continuous
  value to ramp: opaque materials get solid black, transmissive ones
  25% black - present enough to read as a material, sheer
  enough to show the checker through it.

Rendered by string-substituting two fills in the SVG, so the template
stays the single source of truth for the layout - re-exporting it from
Pixelmator is all that's needed to change the design.
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtSvg

import hou

#: Alpha of the black transparency swatch for a TRANSMISSIVE material.
#: Opaque materials use 1.0 (the checker is fully covered).
TRANSMISSIVE_ALPHA = 0.25

_SVG_CACHE = None


def _template() -> str:
    """The SVG source, read once."""
    global _SVG_CACHE
    if _SVG_CACHE is None:
        path = os.path.join(
            hou.getenv("ASSETLIB") or "",
            "scripts", "python", "matlib", "ui",
            "physicallybased_thumb.svg",
        )
        with open(path, "r") as handle:
            _SVG_CACHE = handle.read()
    return _SVG_CACHE


def _srgb_hex(color) -> str:
    """Linear colour -> an sRGB hex string.

    PhysicallyBased stores LINEAR values, and some exceed 1 (gold is
    [1.059, 0.773, 0.307]), so clamping and the sRGB transfer function
    are both required - scaling straight to 0-255 renders every swatch
    far too dark."""
    if not color:
        return "#808080"
    out = []
    for component in list(color)[:3]:
        c = max(0.0, min(1.0, float(component)))
        if c <= 0.0031308:
            c = c * 12.92
        else:
            c = 1.055 * (c ** (1.0 / 2.4)) - 0.055
        out.append(int(round(c * 255)))
    while len(out) < 3:
        out.append(out[-1] if out else 128)
    return "#%02x%02x%02x" % tuple(out)


def icon_svg(values: dict) -> str:
    """The template with both swatches filled in for one material."""
    svg = _template()

    svg = svg.replace(
        'id="basecolor" fill="#f9e231"',
        'id="basecolor" fill="%s"' % _srgb_hex(values.get("color")),
    )

    alpha = TRANSMISSIVE_ALPHA if values.get("transmission") else 1.0
    svg = svg.replace(
        'id="transperacytransmission" fill="#74fbea" fill-rule="evenodd"'
        ' stroke="none" visibility="hidden"',
        'id="transperacytransmission" fill="#000000" fill-rule="evenodd"'
        ' stroke="none" fill-opacity="%s"' % alpha,
    )
    return svg


def render(values: dict, size: int) -> QtGui.QImage:
    """A square icon for one material's measured values.

    Rendered straight from the SVG onto a transparent image via
    QSvgRenderer - deliberately NOT through QIcon, whose internal engine
    has lost alpha for us before (the filter-icon black-box saga)."""
    image = QtGui.QImage(
        size, size, QtGui.QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(QtCore.Qt.GlobalColor.transparent)

    renderer = QtSvg.QSvgRenderer(
        QtCore.QByteArray(icon_svg(values).encode("utf-8"))
    )
    painter = QtGui.QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return image
