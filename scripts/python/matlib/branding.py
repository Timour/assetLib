"""
Single source of truth for the app's DISPLAY name and tagline.

Rename the app by editing APP_NAME here - it flows to every place the USER
sees the name through Python: the About dialog, the panel title + subtitle,
the save-dialog titles, the node right-click "Save to <name>" menu labels
(OPmenu.xml imports this), and the panel lookup in utils/rc_calls.py.

Two things a rename does NOT (and must not) touch, because they are
FUNCTIONAL IDENTIFIERS - changing them breaks existing installs, saved
scenes and saved desktops:

  * the ``$ASSETLIB`` environment variable and the install folder path
  * the ``matlib`` python package name (every import)
  * ``/obj/MatLib`` COP companion networks in saved scenes
  * the ``.pypanel`` interface ``name="MatLib"`` (saved desktops key off it)
  * the ``assetlib_id`` node userdata key
  * cache/marker directory and file names

One spot Python cannot reach at load time, so it stays a manual edit on a
rename (kept to a single line, and called out here):

  * ``python_panels/AssetLib.pypanel`` -> the ``label="..."`` attribute
    (Houdini reads the pane-tab label from the XML before any Python runs)

The console debug prefix ("Amaze: ...") is a plain literal by choice
(developer-facing, ~100 call sites); a future rename find/replaces it.
"""

#: The app's display name. Change this to rename the app.
APP_NAME = "Amaze"

#: One-line subtitle / tagline, shown under the name in the panel + docs.
APP_TAGLINE = "Browse it, save it, drag it."

#: Console log prefix (kept as a literal at the call sites - see module doc).
LOG_PREFIX = APP_NAME + ":"
