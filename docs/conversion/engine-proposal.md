# Conversion engine — proposal

Status: **proposal, awaiting approval.** Nothing here is built.

Grounded in the [catalogue](catalogue.md) and the 422-material survey.

## Why the current converter needs replacing

It works, but it's shaped wrong for the data:

| Problem | Evidence |
|---|---|
| **Only 2 procedural nodes mapped** (`TextureSampler`, `MaxonNoise`) | The library is overwhelmingly procedural — `TextureSampler` appears in only **26 of 422** materials. |
| **Biggest node is unhandled** | `RSColorLayer` — **173** materials. |
| **Free 1:1 wins left on the table** | `RSMathRange` → `mtlxrange` (**155**), `RSColorCorrection` → `mtlxcolorcorrect` (**56**), `RSMathAbs*` → `mtlxabsval` (**49**). |
| **Converters are hand-written functions** | Each new node type = a new bespoke function, even when the mapping is a pure rename. |
| **Shader maps are hardcoded per model** | Two near-duplicate tables; a third shader means a third. |

Roughly **2/3 of the library's node instances** are currently either
skipped or approximated.

## Proposed architecture: declarative mappings + a small engine

Replace "a function per node type" with **data** the engine executes.
Hand-written converters remain possible, but become the exception.

### 1. A mapping is a record, not a function

```python
NodeMapping(
    rs_type   = "redshift::RSMathRange",
    mtlx_type = "mtlxrange",
    signature = "color3",              # or "float", or inferred
    parms     = {"old_min": "inlow", "old_max": "inhigh",
                 "new_min": "outlow", "new_max": "outhigh",
                 "clamp":   "doclamp"},
    inputs    = {"input": "in"},       # connected inputs, recursed
    grade     = Grade.EXACT,
    notes     = [],                    # emitted into the report
)
```
`RSMathRange`, `RSColorCorrection`, `RSMathAbs*`, `TextureSampler` and
both shader models all collapse to records like this. **The two shader
parm maps become two records, not two code paths.**

### 2. The engine does four things

1. **Resolve** the RS node type → mapping (or `None` → report, never
   guess).
2. **Create** the MaterialX node, set `signature` first, then parms via
   the signature-aware setter.
3. **Recurse** each connected input, wiring the result.
4. **Report** with a grade — exact / structural / approximated / skipped.

### 3. Structural mappings get a builder hook

Some conversions aren't one node (`RSColorLayer` → chained mixes,
`Fresnel` → facingratio+mix, `RSRamp` → ramp+UV driver). Those declare a
`builder=` callable and reuse the same recursion/reporting. Everything
already learned stays: the UV driver, the shared texcoord chain, the
bump/displacement handling.

### 4. Grades drive an honest report

Every conversion returns a per-node grade, so the dialog can say
"12 exact, 3 structural, 2 approximated, 1 skipped" instead of a vague
pass/fail. **Nothing unmapped is ever silently substituted.**

## Rollout (each stage independently useful)

| Stage | Work | Gain |
|---|---|---|
| **1** | Engine + records for the existing converters (no behaviour change) | Foundation; proves parity |
| **2** | Add the three 1:1 wins: `RSMathRange`, `RSColorCorrection`, `RSMathAbs*` | ~**260** material-instances, tiny effort |
| **3** | `RSColorLayer` builder (chained mixes) | **173** materials — the biggest single gap |
| **4** | `Fresnel`, `SurfaceTangent`, upgrade `MaxonNoise` → `mtlxunifiednoise3d` | ~**330** instances, better fidelity |
| **5** | Hair2 → `kma_hair`, `State`, `Material` | Long tail |

Stage 2 alone is a couple of hours for the biggest ratio of value to risk.

## Deliberately out of scope

`rsOSL` (130), `ToonMaterial` (92), `Contour` (43), `TonemapPattern` (42),
`Flakes` (25) — **no equivalent exists.** The engine reports them
clearly. Faking them would be worse than not converting them, because a
material that looks *nearly* right is harder to spot than one that
obviously didn't convert.

Optional later: a **bake fallback** — render an unconvertible branch to a
texture and wire an `mtlximage`. Renders correctly, but yields a file
instead of an editable graph, so it should be opt-in per material.

## Open questions

1. **Is a fully-graded report what you want**, or just a short "these
   didn't convert" list?
2. **`RSColorLayer` blend modes** — only "normal" maps cleanly. Convert
   normal-mode layers and report the rest, or attempt math-node
   equivalents for multiply/screen?
3. **Bake fallback** — worth having at all, or does it violate
   "editable node graph or nothing"?
