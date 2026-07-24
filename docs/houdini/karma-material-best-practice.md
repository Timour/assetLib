# A system for well-built Karma materials

Derived by studying **three independent author groups** — not opinion:

| Source | Count | Who |
|---|---:|---|
| `$HFS/houdini/materialx_resources/Materials/Examples` | 49 | The MaterialX project (Autodesk/ILM et al.) |
| `$HFS/houdini/usd/materials/basic_materials.usd` | 27 | **SideFX**, production materials with maps |
| This studio's library | 44 | This studio's house style |
| [Using MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx.html) | — | SideFX's own written guidance |
| Practitioner workflow write-ups (see §10) | — | Working artists, outside SideFX |

Where all agree, it's a rule. Where they differ, it's noted.

**Wider material libraries** (worth mining when the local sets are too
narrow — all free, all MaterialX):
[GPUOpen MaterialX Library](https://matlib.gpuopen.com/main/materials/all)
(**454** materials — *all authored by AMD*, not contributor-diverse as
often described; MIT Public Domain, MaterialX 1.38.7),
[PhysicallyBased](https://physicallybased.info/),
[Poly Haven](https://polyhaven.com/),
[AmbientCG](https://ambientcg.com/), and
[kwokcb/materialxMaterials](https://github.com/kwokcb/materialxMaterials)
— a Python utility that queries all of them and emits MaterialX.

---

## 1. Materials are bimodal — build the simplest tier that works

Across the MaterialX examples the **median material is 2 nodes**. The
distribution isn't a bell curve, it's two clusters:

| Tier | Shape | When |
|---|---|---|
| **A — Preset** | shader + material, all values constant. No network. | Gold, glass, plastic, car paint. *Most materials.* |
| **B — Textured** | shader + a texture chain (image/normal/roughness) | Scanned/authored surfaces |
| **C — Procedural** | shader + a computed chain (noise/ramp/math) | Marble, wood, concrete |

`standard_surface_gold` = 2 nodes. `standard_surface_chess_set` = 134.
Nothing in between is common. **Don't build tier C when tier A renders
the same** — the commonest real-world mistake is over-networking a
material that is fundamentally four constants.

## 2. The canonical skeleton

```
Karma Material Builder (subnet, render_context="kma")
├── subinput
├── <the network>
├── mtlxstandard_surface          <- the shader
└── suboutput                     <- input 0 surface, input 1 displacement
```

- **`mtlxstandard_surface` is the default shader.** All 44 of this
  library's materials and 33/49 MaterialX examples use it. Use `mtlxopen_pbr_surface` only
  deliberately (10/49 examples; nothing in this library).
- Displacement goes to **suboutput input 1**, never into the surface.
- Build it in a **real** builder — [karma-material-builder.md](karma-material-builder.md).

**The output terminal is load-bearing, and it must be NAMED.** SideFX's
own docs call the output the *MtlX Surface Material* node ("analogous to
Collect VOP") with a `surfaceshader` input and a `displacement` input;
inside a Karma builder the `suboutput`'s named connectors — `surface`
and `displacement` — are that terminal. A material whose terminals are
the generic `out` / `out_2` (which is what you get if the connector
names were wiped) has **no surface output** and renders pitch black on
everything. This is not cosmetic — it is the single hardest-to-see way
to break a material, because the whole network below it can be perfect.
See [karma-material-builder.md](karma-material-builder.md#-destroying-the-starters-wipes-the-output-connector-names).

## 3. Provide a UsdPreviewSurface too (SideFX's strongest convention)

**24 of SideFX's 27 production materials declare BOTH outputs:**

```
outputs:mtlx:surface   -> ND_standard_surface_surfaceshader   (Karma)
outputs:surface        -> UsdPreviewSurface                   (everything else)
```

The MaterialX shader renders in Karma; the UsdPreviewSurface makes the
material display correctly in the viewport, in other DCCs, and in any
USD tool that isn't Karma. Neither the MaterialX examples nor this
library do this — **it's the single biggest gap between our materials
and SideFX's.**

## 4. Encapsulate the network

MaterialX's own convention is a named `<nodegraph>` per material —
`standard_surface_chess_set` is 15 materials, each with its own
`NG_<Piece>` holding 4 images + 1 normalmap. Same shape, repeated.

In Houdini VOP-land the builder subnet *is* that encapsulation, so
flat-inside-the-builder (this library's style) is fine. The transferable
part is the discipline: **one material = one self-contained network**,
no cross-wiring between materials.

## 5. The standard chains

**UV / tiling** — three valid approaches, all in use, verified by
reading the files (2026-07-20):

| Approach | Where | Shape |
|---|---|---|
| **`tiledimage` + `uvtiling`** | SideFX `.mtlx` **Examples** (StandardSurface) | tiling baked into the image node — *simplest*, no separate chain |
| `texcoord → separate2 → multiply → image` | SideFX production `.usd` (`basic_materials`, 10× texcoord / 16× separate2) | explicit UV chain |
| `mtlxtexcoord → mtlxmultiply → mtlximage.texcoord` | this library, all 44 | one shared chain, multiply = tiling control |

None is "more correct" — the Examples set proves the simplest
(`tiledimage.uvtiling`) is idiomatic SideFX. The importer copies whatever
the source `.mtlx` used, so it carries any of these through faithfully.

**Normal maps** — confirmed against SideFX's own `standard_surface_chess_set`:
```
mtlximage (type vector3) → mtlxnormalmap.in → NG output → standard_surface.normal
```
This is **exactly** what the importer and converter produce. `normal`
and `tangent` on the normalmap stay unconnected. The image is type
`vector3` (VOP-side: **signature Vector3**) — *that* is what suppresses
the colour-space transform, and in the `.mtlx` text form the image node
carries **no `colorspace` attribute at all** (unlike the colour map,
which carries `srgb_texture`). Height-style bump uses `ND_bump_vector3`
instead. Note: **none of the 49 Examples uses a normal map on a plain
textured surface** — only the 15-piece chess set and the glTF boombox
do, so this is a less-exercised path in SideFX's own set than scanned
libraries (PolyHaven/GPUOpen) make it.

**Displacement**:
```
mtlximage → (mtlxremap) → mtlxdisplacement → suboutput input 1
```
⚠️ **Zero of the 49 SideFX Examples use displacement.** This chain comes
from the scanned-library and Redshift-conversion side, not from SideFX's
reference set — so it is the *least* validated chain against first-party
material, and worth extra scrutiny.

**Ramps**: `kma_rampconst` — **SideFX's own materials use the Karma ramp
(`kma_ramp_color`, 14 instances)**, confirming it over the portable MtlX
ramp for Karma work. See [materialx-ramps.md](materialx-ramps.md).

### 5b. Skin / subsurface — the one tier-C recipe worth writing down

Skin is the case where the tier-A "just set constants" rule breaks down,
and the numbers are unintuitive enough to be worth recording. From a
practitioner walkthrough (Andreas KJ, see §10):

| `mtlxstandard_surface` parm | Value | Why |
|---|---|---|
| `subsurface` | **0.7** | Blend, not a switch. 1.0 is pure SSS and reads waxy. |
| `subsurface_radius` | **(1.0, 0.35, 0.2)** | RGB = deep/mid/shallow scattering. Red travels furthest through flesh — this ratio *is* the skin look. |
| `subsurface_scale` | **0.001** | Scene-scale multiplier on the radius. Wrong scale is the usual reason SSS "does nothing". |
| `specular_IOR` | **1.44** | Skin, not the 1.5 default. |

The chains around it, all from one diffuse/albedo map:

```
albedo → mtlxmultiply (1, 0.975, 0.975)   → subsurface_color   # adds redness
albedo → mtlxcolorcorrect (saturation 0.5) → base_color        # desaturates
spec   → mtlxremap (inhigh 0.07 → 0..1)    → specular_color
rough  → mtlxremap (lifts the low end)     → specular_roughness
disp   → mtlxseparate3 (take one channel)  → mtlxdisplacement (scale ~0.00165)
```

Two general lessons hide in that, useful well beyond skin:

- **`mtlxremap` is the workhorse for map ranges.** Scanned maps rarely
  use 0–1; remapping the actual range beats grading inside the shader.
- **A single albedo map legitimately drives several inputs** through
  different corrections. Re-using one texture is normal, not a shortcut.

**Displacement needs geometry-side setup too** — the material alone does
nothing. On the mesh prim (not its parent group): a **Mesh Edit** LOP to
set the Subdivision Scheme, and **Render Geometry Settings** to set
Dicing Quality (≈2.0 for crisp displacement; it is micro-polygons per
pixel, so it is view-dependent and a render-cost dial).

## 6. The professional node vocabulary

What SideFX actually reaches for, by frequency in their 27 materials —
this is the shortlist worth knowing:

| Node | n | Used for |
|---|---:|---|
| `switch` | 65 | **Switchable variants/modes inside one material** |
| `multiply` | 71 | Scaling values and UVs |
| `geompropvalue` | 27 | **Reading geometry attributes** for per-object variation |
| `range` | 26 | Remapping ranges (with clamp) |
| `mix` | 24 | Blending/layering colours |
| `image` | 30 | Textures |
| `separate2` | 16 | Splitting UV into U/V |
| `kma_ramp_color` | 14 | Gradients |
| `bump` | 14 | Height bump |
| `unifiednoise3d` | 12 | Procedural noise |
| `colorcorrect` | 12 | Hue/sat/gamma/contrast |
| `contrast` | 11 | Contrast |
| `texcoord` | 10 | UVs |

Two patterns worth stealing:
- **`switch`** — build one material with switchable modes rather than
  five near-duplicate materials.
- **`geompropvalue`** — drive variation from geometry attributes so one
  material serves many objects. Nothing in this library does this.

## 6b. Signatures are not cosmetic — SideFX's two hard rules

From SideFX's own MaterialX guidance, and **not visible from reading
finished materials** (which is why no amount of example-mining surfaces
it):

1. **`mtlxtexcoord` → signature `Vector2`**, then into image/tiledimage/
   triplanar.
2. **Normal-map images → signature `Vector3`** — *specifically to stop a
   colour-space transform being applied to the texture.* A normal map
   read as colour is wrong data, and it won't look obviously broken —
   it'll look subtly wrong. This is the highest-value rule on the page.

Plus the trap from our own experience: every polymorphic node carrying
colour needs `signature = color3`, or it silently evaluates as float →
greyscale.

## 6c. Pick the right builder

SideFX ship **two**:

| Builder | Use |
|---|---|
| **Karma MaterialX Builder** | Karma-only work. Tab menu filtered to Karma + Preview Surface + MaterialX-compatible nodes. Better experience. |
| **USD MaterialX Builder** | Pure-USD portability across renderers. |

We build with the Karma one (`render_context="kma"`).

**Karma's documented limitations** — these decide portability:
- No MaterialX **light shaders**
- **String inputs aren't allowed**, even for primvar names
- **Compositing nodes not supported**
- No Surface and Volume materials on the same prim

## 6d. Tiling, seams and UDIMs

MaterialX "lacks ready-made nodes" for procedural patterns and expects
low-level math assembly — so use the purpose-built nodes where they exist:

| Node | For |
|---|---|
| `mtlxtriplanarprojection` | Projection without UVs — kills seams |
| `kma_hextiled_triplanar` / `kma_hextiled_texture` | **Hexagonal tiling** with random scale/rotation per patch — kills *visible repetition*, not just seams |
| `mtlxtiledimage` | Straightforward tiling |
| `mtlxplace2d`, `mtlxrotate2d`, `mtlxUsdTransform2d` | 2D UV transforms |
| `hmtlxudimoffset`, `hmtlxudimpatch` | UDIM handling |

`<UDIM>` tokens are supported directly in texture filenames.

## 6e. Separation of concerns

Practitioner consensus (and the reason SideFX's own materials read
cleanly): identify the core functions — **base colour, roughness, normal,
displacement** — and keep each one's chain distinct rather than
interleaved. It makes a shader debuggable and lets you swap one
component without disturbing the rest. MaterialX expresses this with a
named `<nodegraph>` per material; in a Houdini builder it's a matter of
discipline and layout.

Also: **reference external `.mtlx` files rather than sublayering them**,
for cleaner scene organisation.

## 7. Rules checklist

1. Build inside a **real Karma Material Builder**.
2. Start at **tier A**; add a network only when constants can't do it.
3. `mtlxstandard_surface` unless you have a reason.
4. **Add a UsdPreviewSurface output** for anything shared or shipped.
5. One shared **texcoord→multiply** chain per material, not one per image.
6. **`mtlxtexcoord` signature = Vector2.**
7. **Normal-map images signature = Vector3** (stops colour-space being
   applied), into `mtlxnormalmap.**in**`.
8. Displacement into **suboutput input 1**.
9. **Set `signature=color3`** on every polymorphic node carrying colour —
   they default to float and silently render greyscale.
10. Ramps: `kma_rampconst` for Karma; remember **linear-only**.
11. Prefer `switch`/`geompropvalue` over duplicating materials.
12. Reach for `mtlxtriplanarprojection` / `kma_hextiled_triplanar` before
    hand-building anti-seam math.
13. Keep base-colour / roughness / normal / displacement chains
    **separate**, not interleaved.

## 8. Anti-patterns

- **Over-networking a preset.** If it's four constants, it's two nodes.
- **A texcoord per image** instead of one shared chain.
- **Karma-only material with no UsdPreviewSurface**, then wondering why
  it's black in another tool.
- **Colour through a float-signature node** → greyscale (the #1 trap).
- **A plain `subnet`** instead of a real builder → `kma_*` nodes missing.
- **Assuming the ramp keeps your interpolation** — they're linear-only.

## 9. What this means for conversion

The [catalogue](../conversion/catalogue.md) mappings are independently
validated by SideFX's own usage: they lean on `range` (26), `mix` (24),
`colorcorrect` (12), `unifiednoise3d` (12) and `kma_ramp_color` (14) —
precisely the targets proposed for `RSMathRange`, `RSColorLayer`,
`RSColorCorrection`, `MaxonNoise` and `RSRamp`. A converted material
built to this system will look like a hand-built one, not like machine
output.

## 10. Sources

**Material sets studied** (read directly, not just cited)
- `$HFS/houdini/materialx_resources/Materials/Examples` — 49 `.mtlx`,
  MaterialX 1.39, SideFX-authored (StandardSurface / OpenPbr /
  DisneyPrincipled / GltfPbr / UsdPreviewSurface / SimpleHair).
  StandardSurface set analysed node-by-node in §12.
- `$HFS/houdini/usd/materials/basic_materials/basic_materials.usd` —
  SideFX production, the `texcoord → separate2 → multiply` tiling set.
- This library's 44 Karma materials

**Online libraries to widen the sample**
- [GPUOpen MaterialX Library](https://matlib.gpuopen.com/main/materials/all) — 290+ materials, many contributors
- [PhysicallyBased](https://physicallybased.info/) · [Poly Haven](https://polyhaven.com/) · [AmbientCG](https://ambientcg.com/)
- [kwokcb/materialxMaterials](https://github.com/kwokcb/materialxMaterials) — queries all of the above, emits MaterialX
- [AcademySoftwareFoundation/MaterialX](https://github.com/AcademySoftwareFoundation/MaterialX)

**Guidance**
- [Using MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx.html) — SideFX, **authoritative**. The [How to](https://www.sidefx.com/docs/houdini/solaris/materialx.html#how_to) section is the canonical build recipe; §2, §6b and the normal-map chain in §5 all conform to it. Confirms: image node signature **Color** for textures / **Vector3** for normals (stops colour-space transform), texcoord signature **Vector2**, `MtlX Surface Material` = the named output terminal, `MtlX Place2D` for UV placement. Also its stated limits: no string inputs, no Surface+Volume on one prim, no compositing nodes in Karma.
- [Karma User Guide — Materials](https://www.sidefx.com/docs/houdini/solaris/kug/materials.html)
- [Karma Material Builder: complex shaders from scratch](https://www.artivoxa.com/houdini-karma-material-builder-creating-complex-shaders-from-scratch/) — practitioner workflow
- [Shading in Karma — skin and SSS](https://www.andreaskj.com/shading-in-karma-skin-and-sss/) — Andreas KJ; source for §5b. The only detailed subsurface walkthrough found, and the only source giving concrete skin values rather than "tune to taste".

**Still-narrow areas** (flagged honestly): every set analysed is authored
by a *vendor* — SideFX, AMD, the MaterialX project — or by this studio.
**None is broad community output.** AMD's 454 are consistent and
well-built but single-author and predominantly scanned PBR
(ORM + basecolor + normal → standard_surface), so they broaden *texture*
patterns, not *procedural* ones. For procedural technique the
practitioner write-ups and the `kma_*` node set remain the better guide.

## 11. Anatomy of a production material (AMD / GPUOpen, analysed)

Downloaded and parsed `Indigo_Palm_Wallpaper` (MIT Public Domain,
MaterialX 1.38). It is a textbook build and worth copying wholesale.

**Top level — three prims, a clear naming convention:**
```
<nodegraph        name="NG_Indigo_Palm_Wallpaper">   the network
<standard_surface name="SR_Indigo_Palm_Wallpaper">   the shader
<surfacematerial  name="Indigo_Palm_Wallpaper">      the material
```
`NG_` = nodegraph, `SR_` = shader, bare name = material.

**Inside the nodegraph — five patterns worth stealing:**

1. **Exposed parameters as named `constant` nodes** — `UVScale`,
   `RoughnessMin`, `RoughnessMax`. The material is *tweakable* without
   digging through the graph. This is what makes it feel authored rather
   than generated.
2. **One shared UV chain**: `texcoord → multiply (× UVScale)` feeding
   **every** image. Exactly this library's convention.
3. **ORM packing**: a single `image` (type **vector3**) holds
   Occlusion/Roughness/Metalness, then `extract index=1` → roughness,
   `extract index=2` → metalness. One texture fetch instead of three.
4. **Roughness remap via `mix`**: `mix(bg=RoughnessMin, fg=RoughnessMax,
   mix=RoughnessExtract)` — rescales the map into an artist-controlled
   range instead of using it raw.
5. **Named graph outputs matching shader inputs**:
   `base_color_output`, `specular_roughness_output`,
   `specular_metalness_output`, `normal_output`, `tangent_output`,
   `coat_normal_output`.

**Signature confirmation:** the normal-map and ORM images are
`type="vector3"`; only base colour is `type="color3"`. This is exactly
SideFX's Vector3 rule, observed in the wild — and it's why we now force
`signature=vector3` on converted normal-map textures.

**Texture set**: `_basecolor`, `_normal`, `_ORM`, `_height` — 4 maps,
1k 8-bit ≈ 6.2 MB per material.

## 12. Anatomy of the SideFX StandardSurface Examples (first-party)

`$HFS/houdini/materialx_resources/Materials/Examples/StandardSurface/`
— 21 `.mtlx` files, MaterialX 1.39, SideFX-authored. **The closest
reference to what the importer produces**, because it is the same
`.mtlx` text form editmaterial reconstructs from. Read directly
2026-07-20 — previously they had only been cited, not actually
opened.

**Every material's three-prim skeleton is identical** (`gold` shown, the
tier-A minimum):
```xml
<standard_surface name="SR_gold" type="surfaceshader">
  <input name="base_color" type="color3" value="0.944, 0.776, 0.373" />
  <input name="metalness"  type="float"  value="1" />
  ...
</standard_surface>
<surfacematerial name="Gold" type="material">
  <input name="surfaceshader" type="surfaceshader" nodename="SR_gold" />
</surfacematerial>
```

**`<surfacematerial type="material">` with a `surfaceshader` input is
the terminal — present in 47 of 49 files, no exceptions in
StandardSurface.** This is the `.mtlx` form of the named `surface`
connector on a builder's suboutput, and it is what the black-material
bug destroyed. Reading these first would have caught it: *every*
reference material has an explicit, named surface terminal; ours had
`out`.

Other confirmations from the raw files:
- **Colour space is per-map, not per-material**: the colour `tiledimage`
  carries `colorspace="srgb_texture"`; the roughness/data image carries
  **none** (it's linear/raw). The importer must preserve this — a data
  map read as sRGB is wrong.
- **Metals**: `base_color` is white (`brass`) or the metal's own colour
  (`gold`) with `metalness=1`; the *texture* often drives `coat_color`,
  not `base_color` (see `brass_tiled`).
- **Tiling** is `tiledimage.uvtiling` (a `vector2`), no texcoord node —
  the simplest of the three approaches in §5.
- **Procedural** (`brick_procedural`, `marble_solid`) and **the one big
  textured build** (`chess_set`, 15 materials / 134 nodes) bracket the
  bimodal distribution from §1 exactly: 2-node presets or a full graph,
  nothing between.
