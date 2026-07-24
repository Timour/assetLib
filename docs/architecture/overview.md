# AssetLib architecture & terminology

This is the **shared vocabulary** for the project. When a term here is
written in **Bold Caps** it is a *named part* — say "update the **Material
Engine**" or "the **Colors Section** is broken" and we both know exactly
what is meant. Rename a term in this file and the new name is the one to
use from then on.

Read this before a big change. Keep it current when the architecture
moves (this is the *what exists now* map; a private development log,
kept outside this repo, is the *what we did and why* log).

---

## 1. The one-paragraph picture

AssetLib is a single Houdini **Python Panel** — one window with a
**Toolbar**, a **Section Tab Strip**, a **Sidebar**, and a **Grid**.
Metadata is edited in floating **Dialogs** (see §6) — Materials has the
**Edit Info Dialog**, Code its editor — one per section, not a docked
panel.

The same widgets are reused for every kind of
asset; switching tabs just repoints them at different data. Behind the
panel are three standalone **Engines** (thumbnails, Karma materials,
debug) that the rest of the code talks to through small, stable APIs. Each
tab is a **Section** object — a node-type-like plug-in that tells the
panel how to drive the shared widgets. Data is read/written through
**Models** backed by a **Library** folder on disk.

---

## 2. The shell — what you see

| Term | What it is | In code |
|---|---|---|
| **Panel** | The whole window. One class, built once. | `panel/panel.py` → `MatLibPanel` |
| **Toolbar** | Top strip: menu buttons (Library/View/Renderer), Filter box, Favourites star, Grid/List toggle, size slider. | built in `setup()` |
| **Section Tab Strip** | The Mat/Tex/Colors/Cop/Geo/Code tab bar. | `ui_helpers.SectionTabBar` |
| **Sidebar** | Left list: categories (asset sections) or folders (file sections). | the `cat_list` widget |
| **Grid** | The main thumbnail area (grid or list mode). | the `thumblist` widget |
| **Tile** | One item in the Grid (thumbnail + name + subtitle). Painted by the **Tile Delegate**. | `AssetItemDelegate` |
| **Filter Box** | The search field in the Toolbar. | `line_filter` |

---

## 3. Sections — the node-types of the panel

A **Section** is one tab. It is a small object that tells the **Panel**
how to drive the shared widgets for its kind of asset. The Panel owns the
widgets and the data; a Section only says *what to do with them*.

**File:** `panel/sections.py`. **Registry:** `SECTION_CLASSES`. Adding a
section = one new class + one line in the registry, nothing else.

The six sections, grouped by **Archetype** (they share machinery):

| Section (term) | Archetype | Key | Stores |
|---|---|---|---|
| **Materials Section** | Asset | `material` | Karma/Redshift/Octane materials |
| **Cop Section** | Asset | `cop` | Copernicus networks |
| **Code Section** | Asset | `code` | VEX/Python snippets |
| **Textures Section** | Folder | `texture` | image files in registered folders |
| **Geometry Section** | Folder | `geometry` | geo files in registered folders |
| **Colors Section** | Gradient | `gradient` | curated + user colour palettes |

Plus one *view mode*, not a section:

- **Online Browser** — a mode over the **Materials Section** (View menu →
  Online Materials) that swaps the Grid to browse remote MaterialX
  libraries. `panel.online_mode`, guarded by `panel._is_online()`.

### The three Archetypes

- **Asset Archetype** (`AssetSection`) — a **Library Model** over a JSON
  file + a **Categories Model** in the Sidebar, filtered by the **Filter
  Proxy**. Materials, Cop, Code.
- **Folder Archetype** (`FolderSection`) — a **Folders Model** (pointers
  to real directories) + a **Files Model** listing what's inside.
  Textures, Geometry.
- **Gradient Archetype** (`GradientSection`) — the read-only palette
  library. Colors.

### The Section API (what every Section implements)

| Method | Called when | 
|---|---|
| `activate()` | its tab is selected — point the widgets at its Models |
| `stack()` | returns the **Asset Stack** (model, proxy, selection, categories) or None |
| `filter_text(text)` | the **Filter Box** changes |
| `filter_favorites(on)` | the **Favourites Star** toggles |
| `select_category(index)` | a **Sidebar** row is clicked |
| `double_click(index)` | a **Tile** is double-clicked (the *primary action*) |
| `rc_menu()` | right-click on the **Grid** |
| `catlist_menu()` | right-click on the **Sidebar** |
| `edit_dialog()` | open the section's edit **Dialog** (see §6), if any |

The Panel's shared handlers dispatch to `panel._section().<method>()`
instead of branching on the section key.

---

## 4. The Engines — standalone, API-driven

An **Engine** is a self-contained subsystem the rest of the code talks to
through a small stable API. There are three.

### 4a. The **Thumbnail Engine**

One byte-budgeted image cache + loader for *every* section. Keyed by
asset identity, never by row, so reloads/reorders can't misroute an
image.

- **File:** `core/thumbnails.py` · **Singleton:** `thumbnails.engine`
- **API:** `request_file(key, path)` · `deposit(key, image)` ·
  `peek(key)` · `discard(key)` · `is_missing(key)` · `clear()`
- **Providers** (how an image is produced): **FILE** (materials/cop —
  load a PNG), **CONVERT** (textures — sips/iconvert EXR→PNG), **RENDER**
  (geometry — Houdini flipbook), **PAINT** (colors/code — drawn in
  memory).
- **RAM budget:** the `ram_cache_mb` pref; evicted images reload from
  disk on demand.

### 4b. The **Material Engine**

The single funnel every Karma material is built through. The engine owns
the container, the wiring, activation and verification; each input is an
**Adapter** that only produces a shader network.

- **File:** `render/nodes.py` · **Entry point:**
  `build_karma_material(parent, name, produce)`
- **Adapter API:** `produce(builder) -> (shader, displacement)`
- **Adapters:**
  - **MaterialX Translator** (`core/matx_translate.py`) — online `.mtlx`
    → clean VOP nodes, via Houdini's MaterialX Python API.
  - **Redshift Converter** (`render/material_converter.py`) — a Redshift
    material → equivalent Karma nodes.
  - **Values Adapter** (`matx_import._values_to_standard_surface`) —
    PhysicallyBased measured values → a preset shader.
- **The Builder** — the container the engine makes:
  `make_karma_builder()` → a MaterialX Material Builder subnet matching
  **KARMA_REF** (see §8). Wired via `wire_builder_output()`; verified by
  `surface_terminal_wired()`.

### 4c. The **Debug Engine**

Structured session logging, JSON Lines. **Two tiers:**

- **Crash recorder — always on, only a real crash.** An *uncaught*
  exception (via the hook `install()` arms at panel construction) is
  written *even with Debug Mode off* — carrying the environment header
  (Houdini version, renderer plugins loaded). Nothing else is always-on.
  A quiet session writes nothing; a crash starts the log.
- **Verbose tier — Debug Mode gated** (Preferences → Debug): `event()` /
  `note()` / handled `exception()` / `prefs_snapshot()`. Debug Off means
  off. Development sessions run with it on.

- **File:** `core/debug.py` · **Log:**
  `~/Library/Logs/AssetLib/assetlib_debug.jsonl`
- **API:** `install()` · `configure(on)` · `exception(where)` ·
  `event(cat, msg)` · `note(...)` · `timed(cat, msg)` · snapshots:
  `node_snapshot()` · `image_stats()` · `material_snapshot()` ·
  `texture_snapshot()`

---

## 5. Models & storage

| Term | What it is | In code |
|---|---|---|
| **Library** | The on-disk folder holding an asset section's data: `library.json` (index) + `mat/` (node archives) + `img/` (thumbnails) + `matX/` (downloaded MaterialX). Path in `settings.json`. | — |
| **Library Model** | The Qt model over a **Library**'s JSON. Materials/Cop/Code each have one (Cop/Code subclass it over their own JSON). | `core/library.py` → `MaterialLibrary`; `cop_library.py`; `code_library.py` |
| **Material** | One asset record (id, name, categories, tags, favourite, renderer, …). | `core/material.py` |
| **Categories Model** | The Sidebar list for an Asset section. | `core/category.py` → `Categories` |
| **Sidebar Proxy** | Sorts categories and hides empty ones (renderer-aware). | `category.CategoriesSidebarProxy` |
| **Filter Proxy** | The Grid's search/renderer/favourite/tag filter (Asset sections). | `core/multifilterproxy_model.py` |
| **Asset Stack** | The 4-tuple `(Library Model, Filter Proxy, selection model, Categories Model)` an Asset section works through. | `section.stack()` |
| **Folders Model / Files Model** | The Folder-archetype pair (registered dirs / files inside). | `texture_library.py`, `geo_library.py` |
| **Prefs** | Settings, one shared instance injected into every model. | `prefs/prefs.py` → `Prefs`, from `settings.json` |
| **Database** | The JSON read/write layer, one connector per JSON filename. | `core/database.py` |

---

## 6. Dialogs — a convention, not an Engine

A **Dialog** is a modal form (save, edit, preferences, about). Dialogs
are *not* an Engine — they have no runtime pipeline, they're just forms —
but they share one house style, so that style is a **base class**, not
copied per dialog.

- **AssetDialog** — the shared base: a `QFormLayout` with right-aligned
  labels + fields right, native 5px margins, content-hugging fixed size,
  OK/Cancel. Helpers `add_line` / `add_combo` / `add_row` / `finish`. A
  new dialog is a few `add_*` calls, identical by construction.
  `dialogs/base_dialog.py`.
- **Section-owned** — a Section provides its own dialog through the
  Section API's `edit_dialog()` hook, like it owns its menu. Materials →
  **Edit Info Dialog**; Code → its editor. Textures/Geometry/Cop can get
  one the same way ("make dialogs for the others").

| Dialog | Section | In code |
|---|---|---|
| **Edit Info Dialog** | Materials | `edit_material_info` / `details_dialog` |
| **Code Dialog** | Code | `dialogs/code_dialog.py` (AssetDialog: pending) |
| **Save Dialog** | Materials / Cop | `dialogs/usd_dialog.py` (AssetDialog: pending) |
| **Gradient / Category Dialog** | Colors | `dialogs/gradient_dialog.py` ✓ AssetDialog |
| **Preferences** | — (app-wide) | `dialogs/prefs_dialog.py` (AssetDialog: pending) |
| **About** | — | `dialogs/about_dialog.py` |

*Adoption is incremental* — GradientDialog/CategoryDialog use AssetDialog;
the others migrate one at a time (low-risk, not force-retrofitted).

---

## 7. The Renderer terms

- **Renderer** — a material's engine label: `Karma`, `Redshift`,
  `Octane`, `Mantra`, `MtlX` (online imports), `COP`. Drives the Tile
  subtitle and the Renderer filter.
- **Karma-family** — Karma / MaterialX / MtlX treated identically for
  routing, thumbnails and capability. One predicate:
  `material.is_karma_renderer()`.
- **USD-builder** — a material that can live in a LOP/Solaris context
  (`rs_usd_material_builder`, `octane_solaris_material_builder`, and all
  Karma-family). MAT-only otherwise.

---

## 8. Reference fixtures & conventions

- **KARMA_REF** — the hand-built reference material, the canonical
  structure every generated Karma material must match: a MaterialX
  Material Builder (`render_context = mtlx`) with `surface_output` /
  `displacement_output` subnetconnectors. The **Material Engine** builds
  to this shape. (A second, textured KARMA_REF is planned.)
- **`__activate__` toggle** — the per-input on/off switch that
  `editmaterial` adds and that drops a deactivated input from the USD
  export (the black-material bug). `activate_shader_inputs()` turns them
  all on; the **MaterialX Translator** avoids them entirely.
- **The 2× rule** — this Retina display renders widget geometry at ~2×
  the code pixel value; QSS `border-width` renders 1:1. Pixel values are
  specified as *end* (rendered) pixels; code halves them.

---

## 9. Where things live (quick map)

```
panel/panel.py            The Panel (shell, widgets, shared handlers)
panel/sections.py         The Sections (node-types) + registry
panel/dragdrop_widgets.py Drag-and-drop into the network editor
core/thumbnails.py        THUMBNAIL ENGINE
render/nodes.py           MATERIAL ENGINE (build_karma_material) + save/import
core/debug.py             DEBUG ENGINE
core/library.py           Library Model (Materials) + base for Cop/Code
core/material.py          Material record + is_karma_renderer()
core/category.py          Categories Model + Sidebar Proxy
core/multifilterproxy_model.py   Filter Proxy
core/matx_translate.py    Adapter: online .mtlx -> clean VOP
render/material_converter.py     Adapter: Redshift -> Karma
core/matx_sources.py      Online source adapters (GPUOpen/PolyHaven/...)
core/matx_import.py       Online import orchestration + Values Adapter
core/matx_library.py      Online Browser model
core/{texture,geo,gradient,cop,code}_library.py   the other sections' models
render/thumbs.py          Thumbnail SCENE building (shaderball, flipbook)
prefs/prefs.py            Prefs (settings.json)
helpers/                  theme, ui widgets, vex syntax, generic helpers
dialogs/                  save / preferences / about / code / gradient dialogs
```

---

## 10. How to use this doc

- To point at something: use the **Bold Caps** term. "The **Textures
  Section** filter is wrong", "add an event to the **Debug Engine**",
  "the **Redshift Converter** is producing X".
- To rename something: change the term here. The new name is canonical
  from then on (renaming code identifiers is a separate, explicit
  step).
- To add a concept: add a row/section here so it has a name before it
  is built.
- To reword the app's copy: edit [`ui-text.md`](ui-text.md) — every
  user-facing string, grouped by where it appears.
