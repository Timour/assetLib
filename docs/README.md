# AssetLib reference wiki

Durable **reference knowledge** — how Houdini actually behaves, verified
against its own source/docs/forums. Written once, reused forever.

## What goes where

| File | Contains |
|---|---|
| **`docs/`** (here) | *How Houdini works.* Node types, parm names, mime formats, APIs, renderer limits. Timeless facts. Organised by topic. |
| **`docs/architecture/`** | *How Amaze is built.* The system map + shared **terminology** (`overview.md`), and **all user-facing text** (`ui-text.md`) — edit copy in one place. |
| **`MANUAL.md`** | *How to use the plugin.* End-user docs. |
| **`README.md`** | Project overview. |
| *(private, outside the repo)* | *What we did and why.* A chronological dated development log kept outside this repo — deliberately **not** committed, so working notes stay private. |

The split exists because the chronological log is append-only and
1000+ lines: real research gets buried in it and re-derived later.
Anything expensive to find out — and still true next month — belongs
here instead.

## Rules

1. **Verify, don't assume.** Every claim should be traceable: a quote
   from SideFX docs, a path into Houdini's own source, or an actual
   hython test. Note *how* it was confirmed.
2. **Cite the source** (doc URL, forum thread, or the file path inside
   `$HFS`).
3. **Record what's uncertain**, and say so plainly. A flagged unknown is
   worth more than a confident guess.
4. **Correct entries in place** when something turns out wrong — and
   leave a note saying it was wrong, so the mistake isn't repeated.
5. **Check here before researching.** If it's already answered, use it.

## Index

### Houdini — materials
- [How a Redshift material is built](houdini/redshift-materials.md) —
  containers, shader models, the utility layer, real usage stats.
- [How a Karma/MaterialX material is built](houdini/karma-materials.md) —
  the builder, house conventions, node families, the signature trap.
- [**Best practice: a system for well-built Karma materials**](houdini/karma-material-best-practice.md)
  — patterns derived from 3 author groups (MaterialX project, SideFX
  production materials, this library). Rules checklist + anti-patterns.
- [Karma Material Builder](houdini/karma-material-builder.md) — how to
  build a *real* one, and the Tab-menu masks.
- [MaterialX ramps](houdini/materialx-ramps.md) — which ramp node to use
  in Karma vs portable MaterialX, and their real limits.

### Houdini — other
- [Drag and drop](houdini/drag-and-drop.md) — mime formats, Houdini's own
  drop handlers, and why some drags must be self-managed.

### Conversion (Redshift → Karma)
- [Conversion catalogue](conversion/catalogue.md) — every node worth
  mapping, ranked by real-library frequency, with verified parm names
  and honest "no equivalent" calls.
- [Conversion engine proposal](conversion/engine-proposal.md) — proposed
  declarative architecture and staged rollout. **Not built yet.**
- [MaterialX online importer proposal](conversion/matx-importer-proposal.md)
  — browse/import free MaterialX libraries (GPUOpen) as first-class
  library materials. **Not built yet.**
