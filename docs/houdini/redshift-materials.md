# How a Redshift material is built

Verified against this studio's library (422 Redshift materials scanned)
and live node inspection in H21.0.778.

## Two container forms

| Container | # materials | Notes |
|---|---:|---|
| `redshift_usd_material` | 241 | The **USD/Solaris** builder. Wrapped in a `subnet` with `subinput`/`suboutput`. LOP-capable. |
| `redshift_material` | 181 | The **classic** output node inside a `redshift_vopnet`. `/mat`-only, not LOP-capable. |

That split is why AssetLib routes imports by the builder type recorded in
the `.interface` file: a classic `redshift_vopnet` cannot live in a LOP
context, an `rs_usd_material_builder` can.

## Anatomy

```
redshift_vopnet  (or subnet, for the USD builder)
└── redshift_material / redshift_usd_material      <- OUTPUT node
    ├─ Surface        <- the shader (OpenPBR / Standard / Toon / Hair2)
    ├─ Displacement   <- redshift::Displacement
    └─ Bump Map       <- redshift::BumpMap
```

The output node's **Surface** input (input 0) is the authoritative way to
find the shader. Scanning children by name is a fallback only — utility
types like `MaterialBlender`/`MaterialLayer` also contain "Material" and
will match a naive name scan.

**Bump/displacement usually connect to the OUTPUT node, not the shader** —
though OpenPBR also accepts a normal on its own `geometry_normal` input.
Check all three places.

## Shader models in use

| Shader | # mats | |
|---|---:|---|
| `OpenPBRMaterial` | 197 | The modern default. OpenPBR-spec parm names (`base_weight`, `base_metalness`, `specular_ior`, `fuzz_*`). |
| `ToonMaterial` | 92 | NPR. No MaterialX equivalent. |
| `StandardMaterial` | 82 | Older, Arnold-style names (`refl_color`, `refl_weight`, `ms_amount`). |
| `Hair2` | 14 | Hair BSDF. |
| `Material` | 13 | Legacy. |

**The two main shaders use completely different parm vocabularies** —
that's why the converter needs two maps, not one.

## The utility layer (what makes RS materials complex)

Most materials aren't just a shader — the real texture work happens in
these, in order of how often they appear:

- `RSColorLayer` (173) — multi-layer colour compositor (`base_color` +
  N × `layerN_color`/`layerN_mask`, per-layer enable and blend mode).
  **This is the workhorse.**
- `MaxonNoise` (173) — the Maxon procedural noise library.
- `RSMathRange` (155) — remap old→new range, with clamp.
- `RSRamp` (141) — gradient lookup, driven by UV (`inputMapping`:
  Vertical=V, Horizontal=U, Diagonal/Radial/Circular) or a wired input.
- `rsOSL` (130) — arbitrary OSL source.
- `SurfaceTangent` (88), `Fresnel` (71), `RSColorCorrection` (56),
  `Contour` (43), `TonemapPattern` (42), `Flakes` (25),
  `RSMathAbs`/`AbsColor` (49 combined), `TextureSampler` (26),
  `State` (11).

**`TextureSampler` appears in only 26 of 422** — these materials are
overwhelmingly **procedural**, not texture-mapped. That's the single most
important fact for conversion: you cannot get far by only handling
image nodes.

## Conventions worth copying

- `TextureSampler.scale` is a **2-float** UV scale; materials commonly
  channel-reference one sampler's scale from another
  (`ch("../color/scale1")`) so a whole material shares one tiling value.
- `BumpMap.inputType`: 0 = height field, 1 = tangent-space normal — but
  **don't trust it**; production content wires real normal maps through
  BumpMap with inputType=height. Treat the texture as a normal map and
  only copy `scale` when inputType says tangent-space.
- Thin film thickness is in **micrometres** (0.5 = 500 nm) and is gated
  so it stays invisible — see the trap in
  [the catalogue](../conversion/catalogue.md).

## Reading a material without Redshift loaded

The saved `.mat` is a Houdini item file — mostly binary with real ASCII
segments, including literal `type = redshift::X` declarations. A plain
text search over the bytes is enough to identify node types (that's how
the 422-material survey above was done) **without** the Redshift plugin.

Reconstructing a material to read its *parameters*, though, **does**
require the plugin — `createNode("redshift_vopnet")` fails with
*"Invalid node type name"* otherwise. The converter preflights this.
