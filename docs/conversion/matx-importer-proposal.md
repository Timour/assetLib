# MaterialX online importer — proposal

Status: **proposal.** Design settled; not built.

Goal: browse free MaterialX libraries (starting with AMD GPUOpen, 454
materials, MIT Public Domain) inside AssetLib and import one as a
first-class library material.

## 1. The online browser IS the existing grid

Not a separate window. The Materials section already swaps which models
feed the same sidebar + grid + search; the browser is one more model.

**Entry point: the View (eye) menu → "Online Materials".**
Clicking it swaps the grid's model to
`MatxOnlineLibrary`, which serves rows from the GPUOpen API instead of
`library.json`. Everything downstream works unchanged: tiles, list mode,
search box, scroll, section memory, the thumbnail engine.

### Why the View menu, and not the Renderer menu

An earlier draft put it in the Renderer menu, which forced an ugly
special case: the entry had to be excluded from "All" and needed a
divider to explain that it wasn't really a filter.

The revised design removes the exception entirely:

- **Browsing online is a VIEW mode** — a different way of looking, not a
  filter on what you own. So it belongs in the View menu.
- **MaterialX becomes a real RENDERER** (below), so the Renderer menu
  stays a pure filter — All / Karma / Redshift / Octane / MaterialX —
  and MaterialX *does* belong in "All", because once downloaded those
  materials genuinely are in the library.

No divider, no exception, no "special" entry.

## 1b. MaterialX is its own renderer

Downloaded materials are registered with **`renderer = "MtlX"`**,
alongside Karma / Redshift / Octane / Mantra. That means:

- They appear in the Renderer filter like any other renderer, and are
  included in **All**.
- The tile subtitle and the list-mode Type column read `MtlX`, so
  their origin stays visible at a glance.
- Preferences gains a `renderer_mtlx` visibility flag, exactly like the
  existing per-renderer toggles.

Technically they *are* Karma-renderable MaterialX materials — but
labelling them by origin is more useful than labelling them `Karma`,
because it tells you where the material came from and that its textures
live in `matX/`.

## 2. The model

`core/matx_library.py` → `MatxOnlineLibrary(QAbstractListModel)`, using
the **same role numbers** as `MaterialLibrary` so the existing delegate
and proxy work untouched:

| Role | Value |
|---|---|
| Display | material title |
| Subtitle | author + resolution actually available |
| Decoration | GPUOpen preview render (see §3) |
| Category | GPUOpen category |
| Tag | GPUOpen tags (feeds the existing `:tag` search) |

Paginated and lazy — 454 materials, fetched in pages as the user
scrolls, never all at once. All HTTP happens on a worker thread; the UI
thread never blocks. (`requests` ships with Houdini — verified.)

## 3. Thumbnails cost nothing extra

GPUOpen serves preview renders per material. Download them into the
existing **unified thumbnail engine** as a new provider — it already
handles lazy loading, the RAM budget and eviction. Cache to
`~/Library/Caches/AssetLib/matx_previews/`, so a second visit is instant.

**Bonus:** the same preview becomes the imported material's thumbnail —
no shaderball render needed on import.

## 4. Resolution preference + fallback

New pref **`matx_resolution`** (default `2k`). Packages carry labels like
`1k 8b`, `2k 8b`, `4k 16b`.

Rule:
1. Exact match on the preferred resolution → use it.
2. Otherwise the **next highest** available.
3. If nothing higher exists, the highest available below.

So a preference is a floor you'd like, never a hard failure. Shown in
the tile subtitle so you can see what you'd actually get.

## 5. Import pipeline — Houdini does the conversion

Uses Houdini's own `.mtlx` → VOP converter (the `editmaterial` LOP), so
we write no MaterialX translator:

```
1. resolve package        (resolution rule above)
2. download + unzip    →  <library>/matX/<Material_Name>/
                            ├── <Name>.mtlx
                            └── textures/*.png
3. reference the .mtlx into a temp stage      → USD material prim
4. editmaterial LOP, matpath1 = that prim     → EDITABLE VOP NETWORK
5. save through the normal material pipeline  → a normal library material
6. thumbnail = the GPUOpen preview already downloaded
7. destroy the temp LOP nodes
```

Result: a **first-class Karma material** — categories, favourites,
drag-drop, `/mat` and LOP import all work. One system, no second asset
type. See [karma-material-builder.md](../houdini/karma-material-builder.md#importing-an-existing-mtlx-as-an-editable-vop-network).

## 6. `matX/` is permanent, not staging

New folder beside `img/` and `mat/`:

```
<library>/
├── mat/     node archives (.mat/.interface)
├── img/     thumbnails
└── matX/    downloaded MaterialX sources + their textures
    └── Indigo_Palm_Wallpaper/
        ├── Indigo_Palm_Wallpaper.mtlx
        └── textures/*.png
```

**This answers the staging-vs-permanent question definitively: it must
be permanent.** The imported VOP network's `mtlximage` nodes point at
those texture files. Delete `matX/`, and every imported material loses
its textures. Keeping the `.mtlx` alongside also allows re-importing at a
different resolution later.

⚠️ **Size**: ~6 MB per material at 1k, more at 4k. The library folder is
Jottacloud-synced — so importing 100 materials syncs ~1 GB. Worth
knowing; an argument for browse-and-pick over bulk download (already
your call).

## 7. Reused vs new

**Reused (most of it):** grid, tiles, delegate, list mode, search,
sidebar, scroll, section memory, thumbnail engine, the whole save/import
pipeline, categories/favourites.

**New:** `core/matx_library.py` (model + API client), a thumbnail-engine
provider for remote previews, `matx_resolution` pref + Preferences row,
the Renderer-menu entry with separator, and the import routine in §5.

**Not needed:** a MaterialX→VOP translator (Houdini's), a new asset type,
a separate browser window, third-party dependencies.

## 7b. Multi-source (use more than GPUOpen)

All four APIs were probed live and all work with plain HTTP+JSON:

| Source | Size | Licence | Kind | Notes |
|---|---:|---|---|---|
| **AMD GPUOpen** | 454 | MIT Public Domain | **Package** | `.mtlx` + textures, resolution variants. Single-author (AMD). |
| **PolyHaven** | 783 textures | CC0 | **Package** | `max_resolution`, categories, tags, authors, `thumbnail_url`. |
| **ambientCG** | ~2000 | CC0 | **Texture-set** | **ZIP of maps only, no `.mtlx`.** `api/v2/full_json?type=Material`, no key. See §7c. |
| **PhysicallyBased** | 86 | — | **Values** | **No textures at all.** |

### The important distinction: three kinds of source

**Package sources** (GPUOpen, PolyHaven) ship a zip of a `.mtlx` plus
texture maps → the §5 pipeline (download → `matX/` → reference →
`editmaterial`/translate → save).

**Texture-set sources** (ambientCG) ship a zip of *conventionally-named
maps only, no `.mtlx`* → we build the material ourselves from the maps.
See §7c; not yet built (deferred).

**Value sources** (PhysicallyBased) ship *measured reference values* —
`color`, `metalness`, `roughness`, `ior`, `complexIor`, `specularColor`,
`density`, plus a literature `reference`. No textures, no download, no
unzip; the entire dataset is ~69 KB.

These map exactly onto **tier A "preset" materials** from the
[best-practice study](../houdini/karma-material-best-practice.md) — a
shader with constants, ~2 nodes. So a PhysicallyBased import is simply:
*create `mtlxstandard_surface`, set the measured parameters, done.*
No `matX/` entry, no textures, instant, and physically accurate.

That's a genuinely useful second mode: **GPUOpen/PolyHaven/ambientCG for
textured surfaces; PhysicallyBased for correct base values** (real
aluminium, real gold) to build on.

### Architecture: one interface, per-source adapters

```python
class MatxSource:                     # each source implements this
    name, licence
    def list(search, offset, limit) -> [record]     # title/author/category/tags/preview
    def resolutions(record)          -> [label]     # [] for value sources
    def fetch(record, resolution, dest) -> Result   # .mtlx path, or parameter values
```
`MatxOnlineLibrary` holds one adapter at a time; the sidebar lists
**sources** (and, within one, its categories). Adding a source later is
one class, no model or UI change.

### On [kwokcb/materialxMaterials](https://github.com/kwokcb/materialxMaterials)

Apache-2.0, maintained, and it wraps all four of these sources. Its
dependencies (`requests`, `PIL`, MaterialX ≥1.39) are **already present
in Houdini 21** — verified — so there is no version conflict.

Honest assessment either way:
- **For it**: per-source download/extract quirks are handled and tracked
  upstream; if an API changes, someone else fixes it.
- **Against it**: AssetLib currently has **zero** third-party
  dependencies and installs as a folder copy — adding one means a `pip`
  step on both machines (or vendoring, which means tracking upstream).
  We'd wrap it regardless, to fit `matX/`, the resolution rule,
  thumbnails and library registration. And its biggest single win —
  converting PhysicallyBased values into MaterialX — we don't need,
  because building a `mtlxstandard_surface` VOP directly from those
  values is *simpler* than generating a `.mtlx` and importing it.

**DECIDED: implement the adapters directly.** Each is ~30–60
lines against these APIs; the repo stays a **reference** for endpoint
details only. AssetLib keeps zero third-party dependencies and its
folder-copy install.

## 7c. ambientCG texture-set adapter — READY TO BUILD (deferred)

**Status: not built (deferred), but the design calls are locked, so
it's a quick add — no re-investigation.**

ambientCG is CC0 (public domain — the freest of all four), with a clean
keyless API. The only reason it isn't already in is that it ships **no
`.mtlx`** — just a ZIP of maps named by a fixed convention
(`<AssetId>_<Res>-<FMT>_<MapType>.png`, e.g.
`Bricks075A_2K-PNG_Color.png`, `_Roughness`, `_Metalness`, `_NormalGL`,
`_NormalDX`, `_Displacement`, `_AmbientOcclusion`, `_Opacity`,
`_Emission`).

**The build:** a new **Texture-set adapter** conforming to the Material
Engine's `produce(builder) → (shader, displacement)` contract — reusing
the mtlximage/`mtlxstandard_surface` node-building the Redshift converter
and translator already have:

| ambientCG map | → mtlx wiring | colorspace |
|---|---|---|
| Color | base_color | srgb_texture |
| Roughness | specular_roughness | raw |
| Metalness | metalness | raw |
| **NormalGL** | → `mtlxnormalmap` → normal | raw |
| Displacement | → `mtlxdisplacement` (builder disp out) | raw |
| Opacity | opacity | raw |
| Emission | emission_color | srgb_texture |
| **AmbientOcclusion** | **ignored** | — |

**Locked decisions:**
- **AO map → ignored.** Karma path-traces its own occlusion; a baked AO
  map double-darkens crevices.
- **Format → PNG** (lossless; normal/displacement stay artifact-free).
  Resolution still follows the existing Download Resolution pref +
  `pick_resolution` fallback.
- **Normal → NormalGL** (Y+, what mtlxnormalmap expects), not NormalDX.

**Plumbing:** `AmbientCG(MatxSource)` in `matx_sources.py` (catalogue via
`api/v2/full_json?type=Material&include=downloadData,imageData,tagData`,
`kind="textureset"`), one row in the View submenu group, and a
`kind == "textureset"` branch in `matx_import.import_record` (download →
unzip into `matX/<Name>/` → texture-set adapter → funnel through
`build_karma_material`). The imported material's thumbnail = the API
preview, same as the others. This adapter is also the foundation for any
future maps-only source.

### Categories are per-source and survive import

Every source exposes categories, and they feed the **normal category
sidebar** — no special UI.

**Naming (implemented 2026-07-21):** the source is chosen from **View →
Online Materials → `<source>`** (a submenu, one checkable entry per
source), and the sidebar then shows only *that* source's categories,
**capitalised and unsuffixed** — `Brick`, `Wood`, `Metal`. The source is
the submenu you came in through, so it no longer rides on every category
name. On import, the material takes that plain category, so the grouping
persists in the local library.

*(This supersedes the earlier `<Category>-<Source>` suffix scheme, which
existed only because the old single all-sources view needed the suffix to
tell sources apart in one flat list. The per-source submenu removed that
need — `_cat()` in `matx_sources.py`.)*

## 8. Deliberately not in v1

- Bulk "download everything" — 2.8 GB at 1k, and hard to undo.
- Re-import at a different resolution (the `.mtlx` is kept, so it's a
  later feature, not a redesign).

## 9. Decisions & remaining questions

**Decided:**
1. **Entry point** — View (eye) menu → "Online Materials".
2. **Renderer** — imported materials get their own renderer,
   `MaterialX`; it is a normal renderer and *is* part of "All".
3. **Offline** — show an **empty grid**. No dialog, no hidden menu
   entry; a console line explains why. (Consistent with the house rule
   that dialogs confirm actions, they don't announce outcomes.)

4. **Sources** — **built:** GPUOpen, PolyHaven, PhysicallyBased, via
   **direct per-source adapters** (no third-party dependency).
   **ambientCG deferred** — ready-to-build spec in §7c.
5. **Categories** — each source's own categories, exposed in the normal
   sidebar as `<Category>-<Source>`, and written onto the material on
   import so the grouping persists.

6. **Renderer string** — **`MtlX`**.
7. **Category order** — **`<Category>-<Source>`** (`Wallpaper-GPUOpen`).

**Nothing open.** The design is fully specified.
