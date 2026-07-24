# AssetLib Manual

A walkthrough of every function, section by section. See the [README](README.md) for installation.

---

## The panel at a glance

```
┌─────────────────────────────────────────────────────────────┐
│ ⚙ 👁 ⧉   ▦  ★   ──●──   Filter [__________🔍]               │  toolbar
├─────────────────────────────────────────────────────────────┤
│ Materials │ Textures │ Colors │ Cop │ Geometry │ Code        │  section tabs
├──────────┬──────────────────────────────────────────────────┤
│ sidebar  │  thumbnail grid / list                           │
└──────────┴──────────────────────────────────────────────────┘
```

**Toolbar** (left to right):
- **⚙ Library menu** — Set Library, Reload Library, Preferences, Cleanup Library, Open Library Directory, About, Render All Thumbnails.
- **👁 View menu** — **Material Library** (your local library) and **Online Materials ▸** (a submenu of online sources — see [Online Materials](#online-materials)); **Show Categories** (sidebar on/off); **Grid / List** mode.
- **⧉ Renderer menu** — filter Materials by renderer (All / Karma / Mantra / Redshift / Octane / MtlX). Persists across sessions. Only renderers enabled in Preferences appear.
- **Grid/List toggle** — same as the View menu options.
- **★ Favorites filter** — show only favorited items, in every section.
- **Size slider** — thumbnail size, per view mode (grid and list remember separate sizes). Snaps near 128/256/384; range 16–512.
- **Filter box** — live text search on names. In Materials, Cop and Code, type `:tag` to search tags instead.

**Section tabs** — Materials / Textures / Colors / Cop / Geometry / Code. Each section remembers its sidebar selection and scroll position while you switch around. Choose which tabs are shown in Preferences ▸ Sections. (When you're browsing an online source, the Materials tab reads **Online** so it's clear you've left your own library.)

**Sidebar** — categories (Materials, Colors, Cop, Code) or registered folders (Textures, Geometry), with entry counts. "All" always shows everything. Right-click for section-specific actions. **Drag any asset onto a category** to file it there — the category glows in the accent color as you hover over it.

**Material Info** (Materials) — right-click a material ▸ **Edit Info** opens a floating dialog: name, type, category, tags, favorite, and (for downloaded materials) a **License** and an **About** credit note. **Update Info** saves your edits.

---

## Materials

### Saving
- Right-click a material builder in any network editor → **Save to AssetLib**. Works on: Karma Material Builder and MaterialX subnets, Redshift Material Builder and `rs_usd_material_builder`, Octane builders and `octane_solaris_material_builder`, Principled Shader, Mantra Material Builder.
- You can also **drag a node from the network editor onto the panel** to save it.
- If the node matches an existing library entry (it was imported from, or previously saved to, the library), you get **Overwrite / Save as New / Cancel** — normal file-save semantics. Overwrite keeps the entry's name, categories, tags and favorite, replaces the content and re-renders the thumbnail.
- The save dialog pre-selects the category you're currently browsing.
- Materials that reference Copernicus networks via `op:/` paths save their COP setup alongside and rebuild it on import (**COP companions**).

### Importing
- **Double-click** — context-aware: imports into whatever network editor you're looking at (MAT-style networks, LOP material libraries, SOP contexts get a matnet).
- **Right-click → Import to MAT / Import to LOP** — explicit destinations. A VOP-only material (classic Redshift/Octane) refuses LOP with a clear message instead of silently redirecting.
- **Drag a tile:**
  - onto an **object in the OBJ viewport** → imports to `/mat` and assigns it to that object;
  - onto an **object in the Solaris viewport** → triggers Houdini's native material assigner (materiallibrary + assignmaterial in the stage);
  - onto a **`materiallibrary` LOP node** → imports into that library;
  - onto **empty LOP network space** → new materiallibrary;
  - onto an **OBJ-side network editor** → lands in `/mat`;
  - released over nothing → nothing happens (a fresh copy is only kept when a drop succeeds).

### Organizing
- Each material has **one category**. To change it, either **drag the tile(s) onto a category** in the sidebar, or right-click ▸ **Move to** ▸ pick a category. Both work on multi-selections.
- Right-click tiles: **Edit Info**, **Import to MAT / LOP**, **Toggle Favorite**, **Rerender Thumbnail**, **Move to**, **Delete Entry**.
- **Edit Info** (right-click a material) opens the floating Material Info dialog: edit name, category (a dropdown — new names typed there create categories), tags, favorite, and, for downloaded materials, the License and About credit note. Press **Update Info**.
- The sidebar hides categories that are empty *under the current renderer filter* (so clicking never shows an empty grid); every assignment surface still lists all categories. Toggle this in Preferences ▸ Hide Empty Categories.
- Tile subtitles show renderer and shader type ("Redshift:Standard", "USD Redshift:PBR", "Karma"). List mode adds Type and Category columns.

### Convert to Karma (test)
Right-click a Redshift material → **Convert to Karma (test)**. Best-effort translation into a real Karma Material Builder: StandardMaterial and OpenPBR parameters → `mtlxstandard_surface`, textures with preserved UV scale, bump/normal → `mtlxnormalmap`, displacement (including Change Range) → `mtlxdisplacement`/`mtlxremap`. Everything it can't translate (OSL, Toon, most procedural utilities) is listed honestly in a summary dialog. The result lands as a new Karma entry in the same category; the original is untouched. *(Converting reads the Redshift source, so it needs the Redshift plugin loaded.)*

### Online Materials

Browse and import thousands of free materials without leaving Houdini. **View ▸ Online Materials ▸** pick a source:

- **PolyHaven** (CC0) and **AMD GPUOpen** (MIT) — full MaterialX materials with textures.
- **PhysicallyBased** — measured, physically accurate base values (real gold, real water) that come in as ready-to-build `mtlxstandard_surface` presets, no textures to download.

While a source is showing, the Materials tab reads **Online** and the sidebar lists that source's categories. Search, favorites and the size slider all work as usual. **Double-click** (or right-click ▸ Import) downloads the material into your local library as a Karma material — a progress bar shows the download and first thumbnail load. Imported materials are tagged with the **MtlX** renderer and carry a **License** and **About** note crediting the source and creator (see Edit Info). Pick **View ▸ Material Library** to return to your own library.

Download resolution and how many downloads run in parallel are set in Preferences ▸ Online Materials.

---

## Textures

- Right-click the sidebar → **Add Folder** / **Remove Folder** to register real folders on disk. **Include Subfolders** toggles recursive scanning. "All" browses every registered folder at once.
- Thumbnails generate in background threads and cache to disk (`~/Library/Caches/AssetLib`) — revisits are instant. EXR/HDR decode via the OS-native decoder when available (much faster), `iconvert` as fallback.
- **Double-click** an image with a node selected → sets that node's file/image parameter (works on any node with a file parm: Karma, Redshift, Octane, Copernicus...).
- **Drag** an image onto a parameter field in the Parameter Editor — a native file-path drag, exactly like dragging from Finder.
- Right-click tiles: **Load to Node**, **Toggle Favorite**, **Rerender Thumbnail**.
- Tile subtitles show the format (HDR, EXR, PNG...); the list-mode Category column shows the containing folder.

---

## Colors

Curated color-theory sets plus your own gradients. Sidebar groups: your categories, then **Wada** (Sanzo Wada's *A Dictionary of Color Combinations*, 348 combinations), **Klee**, **Albers**, **Itten** — grouped by palette size.

- **Double-click** a palette → applies it as a **stepped ramp** to the selected node's first color ramp parameter.
- **Drag** a palette onto a node in the network editor, or onto the **Parameter Editor pane** (applies to the node whose parameters are showing — handy when you're already looking at the ramp).
- Right-click: **Apply as Stepped Ramp**, **Apply as Linear Ramp** (smooth blend), **Apply Color to Selected Node ▸** (a swatch submenu — sets a single color parameter), **Toggle Favorite**.
- **Save your own gradients:** right-click any node with a color ramp → **Save Gradient to AssetLib**. Saved with full fidelity (bases and keys re-apply exactly). Right-click a saved gradient for **Delete Gradient**.
- Sidebar right-click: **Add Category** / **Remove Category** (removing keeps the gradients, uncategorized).
- Tooltips show the color names — and for Klee/Albers/Itten, the color-theory principle behind each combination.

---

## Cop

Save and reuse Copernicus networks.

- **Save a whole network:** right-click a `copnet` node → **Save to AssetLib**.
- **Save a selection:** select nodes *inside* a Copernicus network, right-click one → **Save Selection to AssetLib**. Wires and dots between them come along.
- The save dialog lets you set the **name**, category and tags. The thumbnail renders whatever the network's display node shows (recorded at save time).
- **Import** (double-click, right-click, or drag): context-aware — inside a Copernicus network the saved nodes load **directly into it**; anywhere else a new copnet container is created. Dropping a drag onto a copnet node imports into that container.
- Right-click tiles: **Import**, **Toggle Favorite**, **Rerender Thumbnail**, **Delete Entry**. Categories work like Materials.

---

## Geometry

- Right-click the sidebar → **Add Folder** / **Remove Folder**; **Include Subfolders** for recursive scans. Formats: `.bgeo/.bgeo.sc`, `.geo`, `.obj`, `.fbx`, `.abc`, `.usd/.usda/.usdc`, `.ply`, `.stl`.
- Thumbnails render through Houdini's Flipbook ROP (fast, viewport-quality) on first visit — a progress bar shows, ESC interrupts, finished files are cached and the pass resumes next visit. **Shading mode** (wire-over-shaded by default) and **background** (white by default) are configurable in Preferences; each look keeps its own cache.
- **Double-click** a model → imports it to `/obj` with the right loader (File/Alembic/USD Import SOP).
- **Drag** a model onto a file parameter field — native file-path drag.
- Right-click tiles: **Import**, **Toggle Favorite**, **Rerender Thumbnail**.

---

## Code

A reusable library of wrangle/kernel/script snippets (VEX, OpenCL, Python). Each tile shows a syntax-highlighted preview of the code; hover for the description. A curated **Starter Toolbox** category is seeded on first use.

- **Save a snippet:** right-click any node with a code parameter (a wrangle, OpenCL, Python SOP...) → **Save Code to AssetLib**. Or right-click in the grid → **New Snippet** to type one in.
- **Apply** (double-click, right-click ▸ Apply to Node, or drag onto a node) — sets the snippet onto the target node's code parameter.
- Right-click tiles: **View / Copy Code**, **Apply to Node**, **Edit Snippet**, **Toggle Favorite**, **Delete Entry**. Categories and drag-to-category work like Materials.
- The editor uses Houdini's wrangle-style dark theme with line numbers and matching syntax colors.

---

## Preferences (Library ▸ Preferences)

**Library Settings** — library directory (where all saved assets live) and file extension settings.

**Render Settings**
- **RenderSize** — resolution of material/cop thumbnails on disk, and of texture thumbnails in the cache.
- **RenderSamples (Redshift)** / **(Karma)** — per-renderer thumbnail sampling.
- **RAM Cache (MB)** — how much memory thumbnails may use before the oldest are dropped (they reload from disk when scrolled back into view). Raise it for very large libraries, lower it to keep the footprint small.
- **Render Thumbs on Import** — re-render a material's thumbnail whenever it's imported.
- **Geometry Shading / Geometry Background** — the look of geometry thumbnails.

**Enabled Renderers** — which renderers appear in the Renderer filter menu and are offered on save.

**Sections** — tick which section tabs are shown (Materials / Textures / Colors / Cop / Geometry / Code). Hide the ones you don't use.

**Texture Cache / Generation** — cache path display, **Clear Thumbnail Caches** (textures + geometry), **Parallel Conversions** (how many EXR/HDR conversions run at once), **Force iconvert** (skip the OS-native decoder if its color handling ever looks off).

**Online Materials** — **Download Resolution** (target texture resolution for online imports; falls back to the next-highest available) and **Parallel Downloads** (how many files download at once).

**Appearance**
- **Accent Color** — only shown on Houdini 21: on Houdini 22 the panel follows your Houdini theme (base, accent, highlight) automatically.
- **Favorite Star** — the badge on favorited tiles: Background (a "stamped hole"), Yellow, or Custom color.
- **Show Counts on Categories** — the "(N)" on sidebar entries ("All" always shows its total).
- **Hide Empty Categories** — sidebar hiding of categories with nothing to show (see Materials above).
- **Scroll Speed** — grid/list scroll feel, applies immediately.

**Debug** — **Debug Mode** (off by default) writes a detailed log to `~/Library/Logs/AssetLib/` for diagnosing problems, with **Show Log in Finder** and **Clear Log**. Leave it off for normal use.

---

## Housekeeping

- **Library ▸ Cleanup Library** — one combined integrity pass: removes entries whose files are gone (materials and COPs), rescues uncategorized assets, normalizes legacy category data, deletes orphaned asset files, drops registered texture/geometry folders that no longer exist, and prunes favorites pointing at missing files.
- **Library ▸ Render All Thumbnails** — re-renders every *currently visible* (filtered) asset in the active section. Long and blocking; it asks first.
- **Missing thumbnails** — tiles whose thumbnail file is missing or failed show a "Missing Thumbnail" placeholder once loading finishes. Right-click ▸ Rerender Thumbnail fixes them.
- **Storage format** — each asset is a Houdini node archive: `<id>.mat` (the nodes), `<id>.interface` (creation code — also the source of truth for the node type), plus `library.json` / `cops.json` / `gradients.json` / `code.json` indexes in the library folder. Downloaded online materials keep their textures in a `matX/` subfolder. Everything is recoverable with vanilla Houdini.
