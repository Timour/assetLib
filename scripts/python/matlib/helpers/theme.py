"""Houdini 22 theme (Pluto) auto-follow.

Houdini 22 stores its UI theme as three roles - base, primary (the
accent), highlight - as hue/chroma/tone triplets in the
pluto_ui.themeValues preference, readable through the documented
hou.getPreference call. AssetLib derives its own palette from those
roles so the panel follows whatever theme is active automatically -
no preference toggle, and like Houdini's own UI the
accent is used as a FAMILY of shades (chroma/tone variants), not one
flat color.

Every derived color is anchored so the DEFAULT theme reproduces the
panel's hand-tuned palette exactly: the tone/chroma factors below were
computed from the real shipped hexes and round-trip verified against
Houdini's own OkLCH math (hutil.oklch - the same module the theme
editor uses; it ships wherever the theme system does, so its absence
simply means "no theme"). When no theme is readable (Houdini 21, or
any failure), every color falls back to the original constant,
byte-identical to the pre-theme build.
"""

import json

from PySide6 import QtGui

import hou

# The hand-tuned palette: the fallback AND the anchor the derivations
# reproduce under Houdini 22's default theme.
_DEFAULTS = {
    "surface_low": "#262626",  # tab tray, sidebar backdrop, thumb bg
    "surface": "#2d2d2d",  # toolbar row, tab strip, line_tags
    "surface_high": "#313131",  # grid + details bg, star stamped hole
    "field": "#434343",  # filter box fill, toolbar divider
    "text_dim": "#696969",  # grid tile subtitle
    "text": "#a6a6a6",  # grid tile name, unselected tab text
    "text_bright": "#dddddd",  # selected tab text, "Filter" label
    "tab_chip": "#3e4765",  # section tab selected fill
    "tab_ring": "#43506d",  # section tab selected ring
    "star": "#fcb900",  # favorite badge (Yellow mode)
}

# Base-family surfaces: a tone on the theme base's own hue/chroma
# (the default base is chroma 0, which lands exactly on the greys
# above; a tinted base tints every surface coherently).
_BASE_TONES = {
    "surface_low": 14.9,
    "surface": 18.2,
    "surface_high": 20.0,
    "field": 28.1,
    "text_dim": 44.1,
    "text": 67.9,
    "text_bright": 88.0,
}

# Accent-family shades: (chroma factor vs the accent's own, tone) -
# the "different opacities of the accent" Houdini's own example panel
# shows. Factors/tones solved from the shipped chip colors against the
# default accent #7082b9.
_ACCENT_DERIVED = {
    "tab_chip": (0.60, 30.4),
    "tab_ring": (0.59, 33.8),
}

_theme = "unread"
_derived = {}


def _read():
    global _theme
    if _theme != "unread":
        return _theme
    _theme = None
    try:
        raw = hou.getPreference("pluto_ui.themeValues")
        if raw:
            values = json.loads(raw)
            from hutil import oklch

            _theme = {
                "oklch": oklch,
                "base": _to_qcolor(oklch, values["base"]),
                "accent": _to_qcolor(oklch, values["primary"]),
                "highlight": _to_qcolor(oklch, values["highlight"]),
            }
    except Exception as exc:
        print(
            "Amaze: Houdini theme not readable, using built-in "
            "colors (" + str(exc) + ")"
        )
    return _theme


def _to_qcolor(oklch, triplet):
    hue, chroma, tone = (float(v) for v in triplet[:3])
    ok = oklch.OkLCH(
        oklch.tone_to_lightness(tone),
        chroma / 100.0 * oklch.max_chroma,
        hue,
    )
    return _rgb_qcolor(oklch, ok)


def _rgb_qcolor(oklch, ok):
    rgb = oklch.chromaClamp(ok)
    return QtGui.QColor(int(rgb.red), int(rgb.green), int(rgb.blue))


def _lch(oklch, qcolor):
    return oklch.RGB(qcolor.red(), qcolor.green(), qcolor.blue()).to_OKLCH()


def is_active() -> bool:
    """True when a Houdini theme was read - the panel is following it."""
    return _read() is not None


def accent(fallback_hex: str) -> QtGui.QColor:
    """The theme's accent; the manual accent preference when no theme."""
    theme = _read()
    if theme is not None:
        return QtGui.QColor(theme["accent"])
    return QtGui.QColor(fallback_hex)


def color(name: str) -> QtGui.QColor:
    """A named AssetLib color, theme-derived when a theme is active."""
    theme = _read()
    if theme is None:
        return QtGui.QColor(_DEFAULTS[name])
    if name not in _derived:
        _derived[name] = _derive(theme, name)
    return QtGui.QColor(_derived[name])


def color_hex(name: str) -> str:
    return color(name).name()


def _derive(theme, name):
    oklch = theme["oklch"]
    try:
        if name == "star":
            return QtGui.QColor(theme["highlight"])
        if name in _BASE_TONES:
            base = _lch(oklch, theme["base"])
            return _rgb_qcolor(
                oklch,
                oklch.OkLCH(
                    oklch.tone_to_lightness(_BASE_TONES[name]),
                    base.chroma,
                    base.hue,
                ),
            )
        if name in _ACCENT_DERIVED:
            factor, tone = _ACCENT_DERIVED[name]
            acc = _lch(oklch, theme["accent"])
            return _rgb_qcolor(
                oklch,
                oklch.OkLCH(
                    oklch.tone_to_lightness(tone),
                    acc.chroma * factor,
                    acc.hue,
                ),
            )
    except Exception as exc:
        print(
            "Amaze: theme derivation failed for " + name + ": " + str(exc)
        )
    return QtGui.QColor(_DEFAULTS[name])
