# How a Karma/MaterialX material is built

Verified against this studio's 44 hand-built Karma materials and live
node inspection in H21.0.778.

## The container

A **real Karma Material Builder** — a `subnet` configured by
`voptoolutils._setupMtlXBuilderSubnet(..., render_context="kma")`. A plain
subnet is not one, and `kma_*` nodes won't even create inside it. Full
recipe: [karma-material-builder.md](karma-material-builder.md).

```
subnet  (Karma Material Builder)
├── subinput
├── suboutput          <- input 0 = surface, input 1 = displacement
├── mtlxstandard_surface
└── ...the shading network
```

SideFX's own guidance: **start from the Karma Material Builder** when
building materials for Karma.

## What real materials actually use

All 44 use `mtlxstandard_surface`. Nothing uses `mtlxopen_pbr_surface` —
which is why the converter targets standard_surface.

| Node | # mats | Role |
|---|---:|---|
| `mtlxstandard_surface` | 44 | The shader. Always. |
| `mtlxdisplacement` | 19 | Displacement, into suboutput input 1. |
| `mtlxnormalmap::2` | 18 | Normal maps. Texture goes into its **`in`** input. |
| `mtlxtexcoord` | 17 | UVs. |
| `mtlximage` | 16 | Textures. |
| `mtlxmultiply` | 16 | UV scaling (texcoord → multiply → image.texcoord). |
| `mtlxremap` | 16 | Range remapping. |
| `collect` | 15 | Ties surface + displacement together. |
| `mtlxcolorcorrect`, `mtlxinvert`, `mtlxcombine3`, `mtlxseparate2`, `mtlxtiledimage`, `kma_rampconst` | 1–2 | Occasional. |

**The established house convention** (visible in all 44):
```
mtlxtexcoord → mtlxmultiply → mtlximage.texcoord     (one shared UV chain
                                                      per material, the
                                                      multiply = tiling)
mtlximage → mtlxnormalmap.in → standard_surface.normal
```
All 67 normal-map textures feed `mtlxnormalmap`'s **`in`** — `normal` and
`tangent` are always left unconnected.

## The node families available

- **Shading**: `mtlxstandard_surface`, `mtlxopen_pbr_surface`, plus BSDF
  layer nodes (`mtlxlayer`, `mtlxLamaLayer`).
- **Texture**: `mtlximage`, `mtlxtiledimage`, `kma_hextiled_texture`,
  `kma_hextiled_triplanar` (breaks visible tiling repetition).
- **Colour**: `mtlxcolorcorrect`, `mtlxhsvadjust`, `mtlxsaturate`,
  `mtlxcontrast`, `mtlxrange`, `mtlxremap`, `mtlxclamp`, `mtlxinvert`.
- **Math**: `mtlxadd/subtract/multiply/divide/power/sqrt/abs/min/max/
  modulo/floor/round/sign/dot/magnitude/normalize`.
- **Mix/logic**: `mtlxmix`, `mtlxswitch`, `mtlxifgreater(eq)`.
- **Geometry**: `mtlxtexcoord`, `mtlxposition`, `mtlxnormal`,
  `mtlxtangent`, `mtlxbitangent`, `mtlxgeomcolor`, `mtlxviewdirection`,
  `mtlxfacingratio`.
- **Noise**: `mtlxnoise2d/3d`, `mtlxfractal3d`, `mtlxcellnoise2d/3d`,
  `mtlxworleynoise2d/3d`, `mtlxunifiednoise2d/3d`, `kma_voronoinoise2d/3d`.
- **Ramps**: see [materialx-ramps.md](materialx-ramps.md).
- **Karma extras** (`kma_*`, Karma-only): `curvature`, `roundededge`,
  `melanin`, `hair`, `fur`, `rayswitch`, `aov`, `ocio_transform`,
  `tangentrotate`, `nesteddielectrics`, the whole pyro set.

## Known gaps vs Redshift

MaterialX has **no**: radial ramp, toon/NPR shading, contour/outline,
flake BRDF, or arbitrary OSL. Noise is a small standard set versus
Maxon's large proprietary library. Karma partly compensates with the
`kma_*` nodes — which is exactly SideFX's stated approach: supplement
missing MaterialX functionality with Karma/USD Preview nodes.

## Gotchas

1. **Polymorphic signatures default to FLOAT.** `mtlxmix`, `mtlxrange`,
   `mtlxcolorcorrect`, `mtlxabsval`, `mtlxmultiply` … all carry one parm
   variant per signature (`fg`, `fg_color3`, `fg_vector2`, …). Feed them
   colour without setting `signature=color3` and you get **greyscale**,
   silently. This is the single most common MaterialX trap.
2. **Set the suffixed parm**, not just the base name — after a signature
   switch, `fg_color3` is what renders; setting `fg` succeeds and changes
   nothing visible.
3. **Ramps are linear-only** (both `kma_rampconst` and `hmtlxrampc`);
   MtlX ramps additionally cap at **10 control points**.
4. `mtlxnormalmap::2.0` is the current version — plain `mtlxnormalmap` is
   the 1.38 legacy node.
5. **A material with no named surface terminal renders pitch black.** The
   builder's `suboutput` connectors must be *named* `surface` /
   `displacement`, not the generic `out` / `out_2` Houdini invents when
   the names are blank. Nothing in the network looks wrong — see
   [karma-material-builder.md](karma-material-builder.md#-destroying-the-starters-wipes-the-output-connector-names).
   **Check this first** on any uniformly-black material.
