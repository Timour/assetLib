# Drag and drop into Houdini from a Python panel

Researched across 2026-07-19/20 (the long "texture drag" and "Solaris
material drag" sagas). Expensive to derive — read this before touching
drag code.

## The one rule that explains everything

**"Native" describes the drop MECHANISM, not the drag PICTURE.** They're
independent:

- *Mechanism*: a real `QDrag` means Houdini/the OS receives and handles
  the drop. A self-managed gesture means **we** decide what happens.
- *Picture*: whatever you pass to `setPixmap()` (native) or draw in a
  floating widget (self-managed). Two native drags can look completely
  different; a native and a self-managed drag can look identical.

## The hard constraint

The network editor canvas **is not a Qt widget**, so it can never be a Qt
drop target. And `QDrag.exec()` traps the gesture in macOS's native
drag-tracking run loop, where our own code does not run at all —
**proven**: a polling `QTimer` fired **zero** times inside `exec()`.

Consequence: if the drop target must be resolved *by us* (a specific
node/network under the cursor), the gesture **cannot** be a real `QDrag`.
It must be self-managed. Reading the cursor after `exec()` returns is
unreliable — on fast flicks it reports the drag's origin.

## Mime formats

Full list found in `$HFS/houdini/python3.13libs/houpythonportion/qt/__init__.py`
(`hou.qt.mimeType`):

| `hou.qt.mimeType.*` | String |
|---|---|
| `nodePath` | `application/sidefx-houdini-node.path` |
| `itemPath` | `application/sidefx-houdini-item.path` |
| `usdPrimitivePath` | `application/sidefx-houdini-usd.primitive.path` |
| `nodePathAndUsdPrimitivePath` | `application/sidefx-houdini-node.and.usd.primitive.path` |
| `parmPath` | `application/sidefx-houdini-parm.path` |
| `primitivePath` | `application/sidefx-houdini-primitive.path` |
| `galleryEntry`, `asset`, `shelfToolName`, … | see file |

- `usdPrimitivePath` value = **tab-joined** USD prim path strings
  (`husdui/models/primtreemodel.py` builds it that way; its own reader
  does `.split("\t")`).
- `nodePathAndUsdPrimitivePath` = node path, `?`, then a USD prim path.

## Houdini's own Solaris drop handler

`$HFS/houdini/scripts/scene/lop_dragdrop.py` — read this file, it is the
authority. Key behaviour:

- `dropGetChoices()` reads **`nodePath`**; if the dropped node is a **VOP**
  it treats it as a material and offers the *"Set as Material on &lt;prim&gt;"*
  menu. So dragging a `/mat` material node works and produces the
  **Drop Actions** menu.
- For `usdPrimitivePath` it does `stage.GetPrimAtPath(p).IsValid()` and
  **bails with no menu at all** if the prim isn't already in the *cursor
  node's* stage. (This is why handing it a prim from a freshly-made,
  unwired materiallibrary silently did nothing.)
- `getMaterialLibraryLop()` **already reuses an existing editable
  materiallibrary in the cooked input chain, and only creates one if none
  exists.** Don't reimplement this.
- `defineMatOnLibraryNode()` sets the library's `matnode` to a **relative
  path back at the `/mat` VOP** — so the material *node* legitimately
  stays in `/mat` and is referenced. That is Houdini's design, not a bug.
- The materiallibrary and the `assignmaterial` are made in one drop; if
  the library hasn't cooked, the assignment can dangle with
  *"Unable to find primitive: /materials/&lt;name&gt;"*.

**The network editor accepts no native node drops** — a release there
returns `IgnoreAction`.

## Consequences for this project

Two systems, split by task:

- **Native drags** — Houdini handles the drop:
  - Textures: file mime (`QUrl.fromLocalFile` + text) → parm fields accept
    it exactly like a Finder drag.
  - Materials: `nodePath` → Houdini's viewport handler → Drop Actions menu.
    **Materials must stay native**, or that menu is lost.
- **Self-managed (the "black" system)** — Cop, Color, Code: no file to
  hand off and the target must be resolved by us, so the gesture stays in
  our hands.

The *look* is unified regardless (one shared black name tag), because look
and mechanism are independent — see the rule at the top.

**Speed:** don't poll `networkItemsInBox` per mouse-move; that HOM call is
the lag. Resolve the target **once at release** (a self-managed release
fires in our own event loop with an accurate cursor).

## Coordinate convention (hard-won)

`hou.NetworkEditor.networkItemsInBox()` wants pane-local coordinates with
a **LOWER-LEFT origin**, in **plain logical pixels — no Retina 2x** (a
rare exception to this project's usual 2x rule), and returns hits as
**tuples**, not bare items. Confirmed by live console probing.

## Sources
- `$HFS/houdini/scripts/scene/lop_dragdrop.py`
- `$HFS/houdini/python3.13libs/houpythonportion/qt/__init__.py`
- `$HFS/houdini/python3.13libs/husdui/models/primtreemodel.py`
