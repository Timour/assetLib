# Building a real Karma Material Builder

A plain `subnet` is **not** a MaterialX builder. Getting this wrong cost
us a long detour (see the ramp saga) because Karma-context nodes silently
don't exist outside a proper one.

## Symptoms of a fake builder

- The Tab menu inside offers no `mtlx*` nodes.
- `kma_*` nodes fail to create: *"Invalid node type name"* — including
  `kma_rampconst`, the ramp you actually want for Karma.
- Karma's viewport material binding doesn't pick the network up.

## How to make a real one

Use Houdini's own function — the same one its *Karma Material Builder*
shelf tool calls (`$HFS/houdini/toolbar/ExtraTools.shelf`, tool
`vop_karmamtlxsubnet`). Don't hand-roll the tabmenumask /
shader-language / render-context setup.

```python
import voptoolutils

builder = parent.createNode("subnet")
builder = voptoolutils._setupMtlXBuilderSubnet(
    subnet_node=builder,
    name=name,
    mask=voptoolutils.KARMAMTLX_TAB_MASK,
    folder_label="Karma Material Builder",
    render_context="kma",
)
```

It creates starter placeholders as a side effect —
`mtlxstandard_surface`, `mtlxdisplacement`, `kma_material_properties` —
destroy those if you're loading/building real content, and **keep the
`subinput` / `suboutput` connectors**.

### Two flavours — and this project uses the robust one

`_setupMtlXBuilderSubnet` builds two different structures depending on
`render_context` (verified by reading the KARMA_REF reference,
2026-07-20):

| | `render_context` | mask | output |
|---|---|---|---|
| **Karma** Material Builder | `kma` | `KARMAMTLX_TAB_MASK` | one `suboutput` (+ `kma_material_properties`) |
| **MaterialX** Material Builder | `mtlx` | `MTLX_TAB_MASK` | two `subnetconnector` nodes: `surface_output` / `displacement_output` |

**`make_karma_builder` uses the `mtlx` flavour**, because it's what
KARMA_REF (the house reference) uses and it's structurally more robust:
the output terminals are separate `subnetconnector` nodes, each carrying
its own `parmname` (`surface` / `displacement`) — so destroying the
starter shader **cannot** wipe the terminal names the way it wipes a
`suboutput`'s `name1`/`name2` (below). The pitch-black bug is
structurally impossible in this flavour.

`kma_*` nodes (e.g. `kma_rampconst`) still `createNode` fine inside an
`mtlx`-context builder — the tab-menu mask only restricts the
interactive Tab menu, not programmatic creation.

Wire the shader in with `nodes.wire_builder_output(builder, surface,
displacement)`, which handles **both** flavours (subnetconnector by
`parmname`, or `suboutput` by input index) so saved materials of either
kind load correctly.

### ⚠️ Destroying the starters wipes the output connector NAMES (kma flavour)

This cost a full debugging session (2026-07-20). An earlier version of
this page said "wire your shader into `suboutput` input 0 (surface),
input 1 (displacement)" — which is wrong once the starters are gone,
because the connector names go with them:

```
pristine builder   name1="surface"  name2="displacement"
                   inputNames ('surface','displacement','properties','next')

after destroying   name1=""         name2=""
the starter nodes  inputNames ('next',)
```

With the names blank, wiring a shader in makes Houdini invent a generic
connector called `out`. The USD material then has **no surface
terminal** — and a material with no surface terminal renders **pitch
black on everything**: thumbnails, viewport, a real object in LOP.

Nothing about the network looks wrong. The shader, the nodegraph, the
textures, the parameter values and every connection are all correct and
compare byte-identical against a known-good material. The defect is one
level up, in the *container's* output definition, which is exactly why
it survived several rounds of structural comparison.

**Capture the connector names before destroying and restore them
after:**

```python
output = next(c for c in builder.children()
              if c.type().name() == "suboutput")
names = []
i = 1
while output.parm("name%d" % i) is not None:
    names.append((output.parm("name%d" % i).eval(),
                  output.parm("label%d" % i).eval()))
    i += 1

# ... destroy the starter nodes ...

for position, (name, label) in enumerate(names, start=1):
    if name:
        output.parm("name%d" % position).set(name)
        output.parm("label%d" % position).set(label)
```

**How to verify** — after wiring, the terminals must be *named*:

```python
{n: (x.name() if x else None)
 for n, x in zip(out.inputNames(), out.inputs())}
# GOOD: {'surface': 'SR_wood', 'displacement': 'Displacement'}
# BAD:  {'out': 'SR_wood', 'out_2': 'Displacement'}
```

If you ever see a material that is uniformly black while its network
looks perfect, **check the terminal names first**. It is the cheapest
possible test and it would have been the first move here.

In this project: `render/nodes.py` → `make_karma_builder(parent, name)`,
shared by **both** the import path and the Redshift→Karma converter — so
this defect hit every material either of them produced.

## Tab menu masks

`$HFS/houdini/python3.13libs/voptoolutils.py`:

```python
MTLX_TAB_MASK        = 'MaterialX {UTILITY_NODES} {SUBNET_NODES}'
KARMAMTLX_TAB_MASK   = "karma USD ^mtlxUsd* ^mtlxramp* ^hmtlxramp* ^hmtlxcubicramp* {MTLX_TAB_MASK}"
USDPREVIEW_TAB_MASK  = "USD {UTILITY_NODES} {SUBNET_NODES}"
```

`^` entries are **exclusions**. So a *Karma* builder's Tab menu hides all
the ramp variants while a *generic MaterialX* subnet shows them.

⚠️ **Do not read that exclusion as "Karma can't render it."** It's SideFX
steering Karma users toward the faster `kma_*` ramps. The MtlX ramp does
work in Karma — its own docs say so. (I got this wrong once; see
[materialx-ramps.md](materialx-ramps.md).)

## Saving / re-importing

A material built inside a real builder saves and re-imports as a **single
subnet**, which `load_items_file_mtlx()` handles via its *unwrap* path —
identical to a hand-built Karma material. Loose items instead need
scaffolding wrapped around them on the way back in. Building in a real
builder from the start avoids that whole branch.

## Sources
- `$HFS/houdini/python3.13libs/voptoolutils.py`
- `$HFS/houdini/toolbar/ExtraTools.shelf` (tool `vop_karmamtlxsubnet`)
- [Karma User Guide — Materials](https://www.sidefx.com/docs/houdini/solaris/kug/materials.html)

## Importing an existing .mtlx as an EDITABLE VOP network

**Houdini already converts MaterialX → VOP nodes. Use it; don't write a
translator.** (An earlier conclusion that no such path existed was
wrong — the Material Linker's *Edit Material Network* is that path.)

The mechanism is the **`editmaterial` LOP**:

```python
editor = loptoolutils.createLopNode(parent, 'editmaterial', [input_lop], 'editmaterial1')
editor.parm('matpath1').set('/path/to/material/prim')
```

Verified in H21.0.778:
- `editmaterial` **is a VOP network** — `childTypeCategory() == Vop`,
  `isNetwork() == True`, so the material's shading network becomes real,
  editable VOP nodes inside it.
- Key parms: `materials` (count), `matpath1` (the USD material prim),
  `basematpath1`, `matnode1`, `load1`, `usebasemat1`.

Two places in Houdini's UI drive it, both just creating this node:
- `MaterialLinkerMaterialMenu.xml` → `h.pane.materiallinker.edit_material_network`
- `UsdStagePrimMenu.xml` → `h.pane.scenegraphtree.menu.edit_material_network`
  (gated by `loputils.canHaveEditMaterialProperties()`)

### Why this matters for importing third-party materials

A `.mtlx` file is **not** a VOP network — SideFX's guidance is to
*reference* external `.mtlx` files, which USD ingests as material prims
via the UsdMaterialX plugin. That alone would make an imported material a
different asset kind from ours (VOP archives saved as `.mat`/`.interface`).

`editmaterial` closes that gap:

```
.mtlx on disk
  → reference into a stage        (USD material prim)
  → editmaterial LOP (matpath1)   (EDITABLE VOP NETWORK)
  → save through the normal pipeline
```

So downloaded MaterialX materials can become first-class library
materials — **one system**, not a bolted-on second asset type.
