# MaterialX ramps in Houdini (20.5+)

Researched 2026-07-20 while fixing the Redshift→Karma converter, which
was dropping `redshift::RSRamp` entirely.

## Short answer

| Node | Label | Use when | Limits |
|---|---|---|---|
| **`kma_rampconst`** | Karma Ramp Const | Rendering with **Karma** — SideFX's recommended choice | **Karma only**, **linear interpolation only** |
| `hmtlxrampc` / `hmtlxrampf` | MtlX Color / Float Ramp | Material must be **portable** to other MaterialX renderers | **Linear only**, **max 10 control points** |
| `hmtlxcubicrampc` / `hmtlxcubicrampf` | MtlX Color / Float Cubic Ramp | You need **spline/cubic** interpolation | Portable MtlX node (slower in Karma than `kma_*`) |
| `mtlxramplr` / `mtlxramptb` / `mtlxramp4` | MtlX Ramplr / Ramptb / Ramp4 | Simple UV gradients (left-right, top-bottom, 4-corner) | No arbitrary knots |

SideFX on `kma_rampconst`:
> "This node is faster than the MtlX Color Ramp node, but this node only
> works with Karma, while the MaterialX node works with any MaterialX
> capable renderer."

SideFX on `hmtlxrampc`:
> "For Karma use only it is advised to use 'Karma Float/Color Ramp' for
> better performance and experience."

## Node details (verified in hython, H21.0.778)

### `kma_rampconst` — the Karma one
```
inputs  = ('t',)     types ('float',)   <- 0..1 lookup position
outputs = ('out',)   types ('vector',)
parms   : signature, vramp (COLOUR ramp), framp (FLOAT ramp),
          vramp1pos/vramp1cr/cg/cb/vramp1interp, framp1pos/framp1value/...
```
- `signature` switches colour vs float; the gradient lives in **`vramp`**
  (colour) or **`framp`** (float).
- A `hou.Ramp` can be assigned straight to `vramp` — colours survive
  intact (verified: set `((1,.2,0),(0,.3,1))`, read back identical).
- Wires directly into `mtlxstandard_surface.base_color`.
- **Only creatable inside a real Karma Material Builder context.** In a
  bare `matnet` it fails with *"Invalid node type name"* — see
  [karma-material-builder.md](karma-material-builder.md).

### `hmtlxrampc` — the portable one
```
inputs  = ('input',) float      outputs = ('out',) color
parms   : ramp (RampParmTemplate), ramp1pos, ramp1cr/cg/cb, ramp1interp, ...
```

## Driving a ramp

All of them want a **0–1 float**. There is no built-in "ramp direction"
control. The standard way to feed one from UVs:

```
mtlxtexcoord  ->  mtlxseparate2  ->  outx (U) / outy (V)  ->  ramp input
```
`mtlxseparate2` outputs `('outx','outy')` — index 0 is U, index 1 is V.

For **radial / circular / directional** ramps there is **no node**. Per
SideFX forums you must build the 2D transform math yourself out of
MaterialX math nodes.

## Community reality (forums)

- MtlX ramps are widely called *"bare bones and not very useable"*.
- The **10-key limit** on MtlX ramps is confirmed by users.
- **The most common workaround is to use a texture instead of a ramp**
  for anything beyond a simple gradient.
- MaterialX is reported as lacking radial ramp, facing ratio, wire
  shader, slope/curve/edges, and having limited noise.
- Cubic ramps were added around **19.0.419** ("Add MtlX ramp support for
  cubic and linear float/color ramps").
- Unresolved: a [thin white seam between adjacent ramps in Karma XPU](https://www.sidefx.com/forum/topic/94502/)
  (H20, no SideFX reply). If banding shows at ramp boundaries in XPU,
  suspect this rather than your own network.

## Corrected mistake — don't repeat it

I first concluded Karma **cannot render** `hmtlxramp*` because
`KARMAMTLX_TAB_MASK` excludes it (`^hmtlxramp*`), and that this caused a
greyscale render. **That was wrong.** The docs are explicit that the MtlX
ramp works with any MaterialX-capable renderer; the Tab-menu exclusion is
SideFX *steering* Karma users to the faster Karma node, not a
can't-render. The real cause of that greyscale was never pinned down.

Also: **`mtlxmix` defaults to a FLOAT signature** (float in, float out).
Mixing colours with it without setting `signature = color3` silently
yields greyscale — same visible symptom, different cause.

## How this is used here

`render/material_converter.py` → `convert_ramp()`:
`redshift::RSRamp` → `kma_rampconst`, gradient copied into `vramp`,
driver wired into `t`. Redshift's `inputMapping` decides the channel
(Vertical → V, Horizontal → U); Diagonal/Radial/Circular have no
equivalent and are reported. Non-linear knots are reported (both ramp
nodes are linear-only).

## Sources
- [Karma Ramp Constant VOP node](https://www.sidefx.com/docs/houdini/nodes/vop/kma_rampconst.html)
- [MtlX Color Ramp VOP node](https://www.sidefx.com/docs/houdini/nodes/vop/hmtlxrampc.html)
- [MaterialX ramps? (forum)](https://www.sidefx.com/forum/topic/81355/?page=1)
- [Karma — Controlling Ramp direction/pattern (forum)](https://www.sidefx.com/forum/topic/95177/)
- [Karma — curious ramp constant approximation (forum)](https://www.sidefx.com/forum/topic/94502/)
- [Using MaterialX in Solaris](https://www.sidefx.com/docs/houdini/solaris/materialx.html)
