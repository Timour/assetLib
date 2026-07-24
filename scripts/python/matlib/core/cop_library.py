"""
Models for the Cop section - standalone Copernicus/COP network assets.

A second, fully independent material-style library over its own
cops.json database in the same library directory: CopLibrary and
CopCategories subclass the material machinery (MaterialLibrary /
Categories with a different DB_FILENAME), so categories, favorites,
tags, thumbnail loading/delivery, deletion and the proxy filtering all
come from the proven material code paths. Only what genuinely differs
is overridden here: the save chain (no renderer detection - the
renderer field is the fixed string "COP", which also becomes the tile
subtitle via renderer_label), the import (recreates the copnet instead
of routing to MAT/LOP) and the thumbnail (the network's own output
image, rendered by thumbs.create_thumb_cop - no shaderball).

Asset files share the material library's asset/img directories
(<id>.mat / <id>.interface / <id>.png with globally unique ids);
cleanup_db unions ids across both databases so neither library's
cleanup treats the other's files as orphans.
"""

import hou

from matlib.core import library, category, material
from matlib.render import nodes, thumbs


class CopCategories(category.Categories):
    """The Cop section's category sidebar - same model, own database."""

    DB_FILENAME = "cops.json"


class CopLibrary(library.MaterialLibrary):
    """The Cop section's asset model - material machinery over cops.json."""

    DB_FILENAME = "cops.json"

    def add_asset(
        self,
        node: hou.Node,
        cats: str,
        tags: str,
        fav: bool,
        items: list | None = None,
        name: str = "",
    ) -> str:
        """Register a COP network as a library asset - the whole
        container (items None), or a SELECTION of items inside one.
        The asset takes `name` (the save dialog's editable Name field),
        falling back to the node's own name when empty. Returns "COP"
        on success (the renderer-string contract the material add_asset
        has), "" on failure."""
        handler = nodes.NodeHandler(self.preferences)
        new_mat = material.Material()
        tags = self.sanitize_tags(tags)
        new_mat.set_data(name.strip() or node.name(), cats, tags, fav, "COP")

        if not handler.save_node_cop(node, new_mat.mat_id, items=items):
            return ""
        # For COP assets the cop_net field carries the recorded
        # thumbnail source node name ({"thumb_node": ...}) - read from
        # the LIVE network's display flag at save time, since flags
        # don't reliably survive the items-file round-trip.
        new_mat.cop_net = handler.cop_info
        self._assets.append(new_mat)
        self._add_thumb_paths(self.index(self.rowCount() - 1, 0))
        self.save()
        try:
            node.setUserData("assetlib_id", str(new_mat.mat_id))
        except hou.OperationFailed:
            pass
        return "COP"

    def import_asset_to_scene(
        self, index, target: str = "auto", context_node=None
    ):
        """Recreate the saved network in the scene. The target argument
        is accepted for signature compatibility but ignored - a COP
        network has exactly one kind of home. context_node optionally
        pins the destination (drag release point)."""
        handler = nodes.NodeHandler(self.preferences)
        return handler.import_cop_asset(
            self._assets[index.row()], context_node=context_node
        )

    def render_thumbnail(self, index) -> None:
        """Rerender one COP asset's thumbnail from its saved files -
        replaces the material version's shaderball pipeline with the
        network's own output image."""
        try:
            asset = self._assets[index.row()]
            info = getattr(asset, "cop_net", {}) or {}
            thumber = thumbs.ThumbNailRenderer(self.preferences)
            with hou.InterruptableOperation(
                "Rendering", "Performing Tasks", open_interrupt_dialog=True
            ):
                thumber.create_thumb_cop(
                    str(asset.mat_id), str(info.get("thumb_node", ""))
                )
        except Exception as exc:
            print("Amaze: COP thumbnail rerender failed: " + str(exc))
        finally:
            self._add_thumb_paths(index)
