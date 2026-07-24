# AssetLib UI text — every word shown in the app

**The single source of truth for user-facing copy.** Every label, menu
entry, button, tab and title the user sees is listed here, **in the order
it appears in the UI**, with the dividers the UI has. Edit the text, the
order, or the dividers here — then tell me *"sync the UI text"* (or name
the item) and I change the matching code so the app and this doc stay in
sync.

**Conventions**
- Each `- text` line is one exact string shown in the UI.
- **Order matches the UI top-to-bottom** — reorder lines here to reorder
  the menu.
- `- ---- divider ----` marks a separator line in the UI. Move / add /
  delete these to change the dividers.
- `(hidden)` = exists in code but not shown (see *Hidden & legacy* at the
  end). `(conditional)` = shown only in some states. `(⚑ verify)` = I
  wasn't fully sure — check and prune.
- **Bold Caps** names refer to [`overview.md`](overview.md).

---

## Section Tab Strip

Tabs, left → right:

- Materials
- Textures
- Colors
- Cop
- Geometry
- Code

---

## Toolbar

- **Filter Box** label: `Filter`
- Menu buttons (icons, no text): **Library**, **View**, **Renderer**

### Library menu

- Set Library
- Reload Library
- ---- divider ----
- Preferences
- Cleanup Library
- ---- divider ----
- Open Library Directory
- ---- divider ----
- About
- ---- divider ----
- Render All Thumbnails

### View menu

*(All items render with a radio-style circle indicator, including the free-toggle "Show Categories" — an ExclusiveOptional single-action group gives it a circle instead of a checkmark.)*

- Material Library
- Online Materials →  *(submenu, one checkable entry per online source; picking one enters that source's browser, picking the active one again returns to the local library)*
  - PolyHaven
  - GPUOpen
  - PhysicallyBased
- ---- divider ----
- Show Categories
- Grid View
- List View


### Renderer menu

- All
- Karma
- Mantra
- Redshift
- Octane
- MtlX  (conditional — only when the MtlX renderer is enabled)

---

## Grid right-click menus (per section)

### Materials

- Edit Info
- Import to MAT
- Import to LOP
- Toggle Favorite
- Rerender Thumbnail
- Convert to Karma (test)  (conditional — a Redshift material is selected)
- ---- divider ----
- Move to →  *(submenu of category names; also draggable — drop assets onto a sidebar category)*
- ---- divider ----
- Delete Entry

### Textures

- Load to Node
- Toggle Favorite
- Rerender Thumbnail

### Colors

- Apply as Stepped Ramp *(curated)* / Apply Ramp *(user gradient)*
- Apply as Linear Ramp  (conditional — curated gradients only)
- Apply Color to Selected Node →  *(submenu of swatches)*
- Toggle Favorite
- ---- divider ----
- Delete Gradient  (conditional — user gradients only)

### Cop

- Import
- Toggle Favorite
- Rerender Thumbnail
- ---- divider ----
- Delete Entry

### Geometry

- Import
- Toggle Favorite
- Rerender Thumbnail

### Code

- New Snippet
- ---- divider ----
- View / Copy Code
- Apply to Selected Node
- Edit Snippet
- Toggle Favorite
- ---- divider ----
- Delete Entry

### Online Browser (Materials → Online Materials)

- Refresh
- Import  (⚑ verify — per-record import on the online menu)

---

## Sidebar right-click menus (per section)

### Materials / Cop / Code (categories)

- Add Category
- Rename Category
- Remove Category

### Textures / Geometry (folders)

- Add Folder
- Remove Folder
- ---- divider ----
- Include Subfolders  *(checkable)*

### Colors (gradient categories)

- Add Category
- Remove Category "…"  (conditional — only on a user category)

---

## Dialogs

### Preferences

Section headers (bold) and their rows, top to bottom:

- **Library Settings**
  - Working Directory
- **Render Settings**
  - RenderSize
  - RenderSamples (Redshift)
  - RenderSamples (Karma)
  - RAM Cache (MB)
  - Geometry Shading
  - Geometry Background
- **Enabled Renderers**  *(checkboxes)*
  - Karma
  - Mantra
  - Redshift
  - Octane
- **Sections**  *(checkboxes — which tabs show)*
  - Materials
  - Textures
  - Colors
  - Cop
  - Geometry
  - Code
- **Texture Cache**
  - Cached Thumbnails  *(path label)*
  - Clear Thumbnail Caches (Textures + Geometry)
- **Texture Generation**
  - Parallel Conversions
  - Force iconvert only
- **Online Materials**
  - Download Resolution
  - Parallel Downloads
- **Appearance**
  - Accent Color
  - Match Houdini Accent Color
  - Custom Star Color
  - Favorite Star
  - Hide Empty Categories
  - Show Counts on Categories
  - Scroll Speed (%)
- **Debug**
  - Debug Mode
  - Log File  *(path label)*
  - Show Log in Finder
  - Clear Log

### Save Dialog (Materials / Cop — "Save to AssetLib")

- Title: `Save to AssetLib`
- Name  *(Cop only; a Material is named after its node)*
- Category
- Tags

### Edit Info Dialog (Materials)

- Title: `Material Info`
- Name
- Type  *(read-only)*
- Category
- Tags
- Favorite
- Date  *(read-only)*
- ID  *(read-only)*
- License  *(the license the material is released under; auto-filled for online imports)*
- About  *(multi-line credit/homage text — source, author, link; auto-filled for online imports, editable)*

### Code Dialog

- Title: `Save to AssetLib`  (⚑ verify)
- Name
- Language
- Category
- Tags
- Description
- *(read-only view)*: Copy to Clipboard · Close

### Gradient Dialog ("Save Gradient to AssetLib")

- Title: `Save Gradient to AssetLib`
- Name
- Category

### Category Dialog (add/rename a category)

- Title: `Add Category` / `Add Gradient Category` / `Rename Category`
- Name

### About

- Title: `About AssetLib`

---

## Node right-click (Houdini network editor — OPmenu)

On a node, right-clicked:

- Save to AssetLib  *(materials)*
- Save Code to AssetLib  *(nodes with a code parm)*
- Save Gradient to AssetLib  *(nodes with a colour ramp)*
- Save Selection to AssetLib  *(inside a Copernicus network)*

---

## Tile subtitle labels (the greyed line under a name)

The **Renderer** shown on each **Tile**:

- Redshift · Redshift:Standard · Redshift:PBR · Redshift:Toon ·
  Redshift:OSL  *(and other shader-type suffixes)*
- USD Redshift · USD Redshift:PBR
- Karma · USD Karma
- Octane · USD Octane
- MtlX
- COP
- Gradient  *(Colors section)*
- File-format extension  *(Textures/Geometry — e.g. `EXR`, `OBJ`)*

---

## Common messages & confirmations

*(Not listed yet — the `hou.ui.displayMessage` / `displayConfirmation`
strings. Say "add the messages" and I'll list every one with its
trigger.)*

---

## Cleanup history

**2026-07-21 Designer clean-up.** Removed 9 dead actions from
`ui/matlib.ui` (verified: the file still loads, every element the code
uses is present):

- **7 orphans** never wired into any menu or referenced in code (upstream
  egMatLib leftovers): Import from Files, Import from Folder, Import From
  Files (Mantra), Check Integrity, Force Update Views, Update All
  Materials, `_deleteMaterial`.
- **2 hidden legacy** that had been shown-then-hidden: **Import from
  MatLib V1** (the v1-library importer, removed with v1 support) and
  **Show Detail View** (toggled the old docked Details Panel, now the
  **Edit Info Dialog**). Their menu refs and the code that hid them were
  removed too.

The `.ui` is the Qt Designer source, maintained externally (never edited
from code); anything removed from it is removed *deliberately*, both the
definition and every reference, and load-tested before shipping.

## Status of this doc

Order + dividers extracted from the code 2026-07-21. `(⚑ verify)` = not
fully certain it's live. Add anything missing and I'll find it.
