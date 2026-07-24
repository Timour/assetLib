# Redshift → Karma/MaterialX conversion catalogue

Built 2026-07-20 from three sources, not guesswork:
1. **Every** node type in H21.0.778 (165 `redshift::*`, 231 `mtlx*`, 19
   `hmtlx*`, 45 `kma_*`).
2. **A real production library** — 422 Redshift + 44 Karma materials scanned
   for which node types actually appear.
3. **Live hython inspection** of each pair's inputs/outputs/parms.

Frequency = number of materials in the library containing that node, so
the list is ordered by what actually matters, not by what exists.

## Priority table

Legend — **1:1** exact · **Struct** faithful but needs several nodes ·
**Approx** closest available, will not match · **None** no equivalent.

| # mats | Redshift node | → MaterialX / Karma | Grade | Status |
|---:|---|---|---|---|
| 197 | `OpenPBRMaterial` | `mtlxstandard_surface` | Struct | **done** |
| 173 | `RSColorLayer` | chained `mtlxmix` | Struct | **TODO — biggest gap** |
| 173 | `MaxonNoise` | `mtlxunifiednoise3d` + `mtlxmix` | Approx | partial (uses `mtlxfractal3d`) |
| 155 | `RSMathRange` | `mtlxrange` | **1:1** | **TODO — easy win** |
| 141 | `RSRamp` | `kma_rampconst` | Struct | **done** |
| 130 | `rsOSL` | — | **None** | report |
| 92 | `ToonMaterial` | — | **None** | report |
| 88 | `SurfaceTangent` | `mtlxtangent` (+ `kma_tangentrotate`) | Approx | TODO |
| 82 | `StandardMaterial` | `mtlxstandard_surface` | Struct | **done** |
| 71 | `Fresnel` | `mtlxfacingratio` + `mtlxmix` | Struct | TODO |
| 57 | `BumpMap` | `mtlxnormalmap` | Struct | **done** |
| 56 | `RSColorCorrection` | `mtlxcolorcorrect` | **1:1**\* | **TODO — easy win** |
| 43 | `Contour` | — | **None** | report |
| 42 | `TonemapPattern` | — | **None** | report |
| 27 | `RSMathAbsColor` | `mtlxabsval` | **1:1** | TODO |
| 26 | `TextureSampler` | `mtlximage` | Struct | **done** |
| 25 | `Flakes` | — | **None** | report |
| 22 | `RSMathAbs` | `mtlxabsval` | **1:1** | TODO |
| 20 | `Displacement` | `mtlxdisplacement` | Struct | **done** |
| 14 | `Hair2` | `kma_hair` | Approx | TODO |
| 13 | `Material` | `mtlxstandard_surface` | Struct | TODO |
| 11 | `State` | `mtlxposition` / `mtlxnormal` / … | Approx | TODO |

\* everything but `level` maps exactly — see below.

## Verified mappings (parm names confirmed in hython)

### `RSMathRange` → `mtlxrange` — exact, highest-value easy win
```
RS   in: input, old_min, old_max, new_min, new_max   parm: clamp
mtlx in: in,    inlow,   inhigh,  outlow,  outhigh   parm: doclamp (+ gamma)
```
Straight rename, including the clamp toggle. 155 materials.

### `RSColorCorrection` → `mtlxcolorcorrect`
```
RS   in: input, gamma, contrast, hue, saturation, level
mtlx in: in,    gamma, contrast, hue, saturation, lift, gain
```
Four map exactly. RS `level` has no direct twin — closest is `gain`
(report as approximated).

### `RSMathAbsColor` / `RSMathAbs` → `mtlxabsval`
`input` → `in`. Exact. (Watch RS's `math_op` parm — it can make the node
do more than abs; check before assuming.)

### `RSColorLayer` → chained `mtlxmix` — the biggest gap
```
RS in: base_color, layer1_color, layer1_mask, layer2_color, layer2_mask,
       layer3_color, layer3_mask, ...   (+ per-layer enable/blend parms)
```
Structural mapping: start from `base_color`, then per enabled layer
`mtlxmix(bg=accumulated, fg=layerN_color, mix=layerN_mask)`. **Set
`signature=color3`** or it silently evaluates greyscale.
Caveat: RS per-layer **blend modes** (multiply/screen/…) have no single
MaterialX node — only "normal" blending maps cleanly; others need math
nodes or a report.

### `Fresnel` → `mtlxfacingratio` + `mtlxmix`
```
RS   in: facing_color, perp_color, ior, user_curve   out: outColor
mtlx facingratio in: viewdirection, normal, faceforward, invert  out: FLOAT
```
RS outputs a *colour*; MaterialX gives a *ratio*. Rebuild as
`mix(bg=facing_color, fg=perp_color, mix=facingratio)`. RS's IOR-based
curve ≠ geometric facing ratio → approximation. `user_curve` is a ramp →
`kma_rampconst`.

### `MaxonNoise` → `mtlxunifiednoise3d` (+ `mtlxmix`)
RS outputs *colourised* noise (`color1`/`color2` + lacunarity/gain/
exponent). MaterialX: `mtlxunifiednoise3d` (position, freq, offset,
jitter, outmin, outmax) then `mix(color1, color2, noise)`.
**Better than the current `mtlxfractal3d`** — unified noise exposes
matching controls. Still an approximation: Maxon's noise family
(Alligator, Displaced Turbulence, …) is proprietary and unmatched.

### `SurfaceTangent` → `mtlxtangent`
`space`/`index` vs RS `source`/`tspace_id`. RS `rotation` has no twin on
the MaterialX node — **`kma_tangentrotate` exists** and is the right
partner for it in Karma.

## Not convertible — report, never fake

| Node | Why |
|---|---|
| `rsOSL` (130) | Arbitrary OSL source. Cannot be translated. |
| `ToonMaterial` (92) | No NPR/toon shading model in MaterialX. |
| `Contour` (43) | Outline shading, no equivalent. |
| `TonemapPattern` (42) | Redshift-specific. |
| `Flakes` (25) | No flake BRDF; a normal-perturbation fake is *not* the same. |

These are ~30% of the library by node presence. **An honest converter
reports them; it does not substitute lookalikes silently.**

## Karma-only nodes worth knowing

No Redshift source, but useful when hand-finishing a converted material:
`kma_curvature`, `kma_roundededge`, `kma_hextiled_triplanar` (breaks tiling
repetition), `kma_voronoinoise2d/3d`, `kma_melanin`, `kma_hair`,
`kma_rayswitch`, `kma_aov`, `kma_ocio_transform`.

## Traps (each cost real time)

1. **Polymorphic signatures.** `mtlxmix`, `mtlxrange`, `mtlxcolorcorrect`,
   `mtlxabsval` etc. default to a **float** signature. Mixing/adjusting
   colour without `signature=color3` yields **greyscale**. Use
   `_set_poly_parm()`, which sets the signature *and* the suffixed parm.
2. **`hou.InvalidSize`** is a `hou.Error`, not `ValueError` — a careless
   `except` misses it and aborts the whole conversion.
3. **Build inside a real Karma Material Builder** or `kma_*` nodes don't
   exist — see [karma-material-builder.md](../houdini/karma-material-builder.md).
4. **Thin film**: Redshift stores thickness in µm and gates the effect;
   MaterialX is thickness-gated only, so copying the value paints
   iridescence on every metal. Don't copy it.
5. **Colour space is per-map, and the shader input decides it.** A
   texture feeding a colour input (`base_color`, `*_color`) reads
   `srgb_texture` + `signature color3`; everything else it can feed
   (roughness, metalness, specular, opacity, …) is scalar DATA and reads
   `Raw` + `signature default`; a normal map reads `Raw` + `signature
   vector3`. Verified against SideFX's StandardSurface `.mtlx` (colour
   image carries `srgb_texture`, roughness carries none) — see
   [best-practice §12](../houdini/karma-material-best-practice.md). The
   converter sets this from the target input via `_apply_image_colorspace`;
   a roughness map read as sRGB is *subtly* wrong, not obviously broken,
   which is why it has to be forced rather than left to the node default
   (color3/auto).
6. **The builder must have a NAMED surface terminal.** The converter
   builds through `make_karma_builder`, which now preserves the
   `surface` / `displacement` connector names that destroying the
   starter nodes would otherwise wipe. A material wired to a generic
   `out` connector renders **pitch black on everything**. Check terminal
   names first on any all-black converted material —
   [karma-material-builder.md](../houdini/karma-material-builder.md#-destroying-the-starters-wipes-the-output-connector-names).
