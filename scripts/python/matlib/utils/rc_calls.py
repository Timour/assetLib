import hou

from matlib import branding

#: Pane-tab labels the panel may carry: the current app name, plus the
#: historical labels so an older saved desktop/panel still resolves.
_PANEL_LABELS = (branding.APP_NAME, "AssetLib", "MatLib")


def _find_panel():
    """The app panel's root widget, or None (with the user told to open it
    first). The pane tab is found BY LABEL - branding.APP_NAME, with the
    old labels still accepted for older panel definitions."""
    panel = None
    for pane_tab in hou.ui.paneTabs():  # type: ignore
        if pane_tab.type() == hou.paneTabType.PythonPanel:
            if pane_tab.label() in _PANEL_LABELS:
                panel = pane_tab.activeInterfaceRootWidget()
    if not panel:
        hou.ui.displayMessage(  # type: ignore
            "Please open the %s panel first." % branding.APP_NAME
        )
    return panel


def save_material() -> None:
    """Call Save Script from RC-Menus in Houdini Network Pane"""
    panel = _find_panel()
    if panel:
        panel.save_asset()


def save_cop(node=None) -> None:
    """Called from the node right-click "Save to AssetLib" on a COP
    network container - the OPmenu script passes the clicked node
    through so the save doesn't depend on the selection state."""
    panel = _find_panel()
    if panel:
        panel.save_cop_from_node(node)


def save_gradient(node=None) -> None:
    """Called from the node right-click "Save Gradient to AssetLib" -
    the OPmenu script passes the clicked node through so the save
    doesn't depend on the selection state."""
    panel = _find_panel()
    if panel:
        panel.save_gradient_from_node(node)


def save_code(node=None) -> None:
    """Called from the node right-click "Save Code to AssetLib" - grabs
    the clicked node's code/snippet parm."""
    panel = _find_panel()
    if panel:
        panel.save_code_from_node(node)
