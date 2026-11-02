"""Geometry check for the views of the current case. Mixed into the logic class.

Compares every loaded volume of the case (so 3 mm vs TS is checked when both are
in memory), names reconstructed views, and checks that each saved box lies inside
the volume its view draws. Pure rules live in geometry.py.
"""

from __future__ import annotations

from typing import Any, Dict, List

import vtk

import geometry


def read_geometry(volume_node) -> Dict[str, Any]:
    """IJK-to-RAS matrix and voxel counts of a loaded Slicer volume."""
    matrix = vtk.vtkMatrix4x4()
    volume_node.GetIJKToRASMatrix(matrix)
    rows = [[matrix.GetElement(r, c) for c in range(4)] for r in range(4)]
    dims = list(volume_node.GetImageData().GetDimensions())
    return {"matrix": rows, "dims": dims}


class GeometryOps:
    """check_geometry() returns the messages for the volumes and views that are showing now."""

    def check_geometry(self) -> List[str]:
        sources: Dict[str, Dict[str, Any]] = getattr(self, "view_sources", {}) or {}
        messages: List[str] = []
        messages += geometry.reconstruction_warnings(
            [
                {
                    "view": name,
                    "label": source["label"],
                    "plane": source["plane"],
                    "reconstructed": source["reconstructed"],
                }
                for name, source in sources.items()
            ]
        )

        red = sources.get("Red")
        volumes = getattr(self, "current_volume_nodes", {}) or {}
        if red is None and not volumes:
            return messages

        red_item = self._geometry_item(red) if red is not None else None
        red_id = red["node"].GetID() if red is not None else None
        seen: set = set()
        for vol_key, node in volumes.items():
            if node is None:
                continue
            node_id = node.GetID()
            if node_id in seen:
                continue
            seen.add(node_id)
            item = self._item_from_loaded(vol_key, node)
            messages += geometry.check_volume(item)
            if red_item is not None and node_id != red_id:
                messages += geometry.compare_volumes(red_item, item)

        boxes = [
            (roi.GetName() or "box", roi.GetCenter(), roi.GetSize())
            for roi in self.current_roi_nodes
            if self.is_roi_valid(roi)
        ]
        if boxes and sources:
            messages += self._box_visibility_messages(sources, boxes)
        return messages

    def _geometry_item(self, source: Dict[str, Any]) -> Dict[str, Any]:
        geo = read_geometry(source["node"])
        return {
            "label": source["label"],
            "plane": source["plane"],
            "thickness": source["thickness"],
            "matrix": geo["matrix"],
            "dims": geo["dims"],
        }

    def _item_from_loaded(self, vol_key: str, node) -> Dict[str, Any]:
        geo = read_geometry(node)
        return geometry.item_from_volume_key(vol_key, geo["matrix"], geo["dims"])

    def _box_visibility_messages(
        self, sources: Dict[str, Dict[str, Any]], boxes: List[tuple]
    ) -> List[str]:
        """Group views by the volume they draw, so a box is reported once per volume."""
        groups: Dict[str, Dict[str, Any]] = {}
        for view_name, source in sources.items():
            node_id = source["node"].GetID()
            if node_id not in groups:
                geo = read_geometry(source["node"])
                groups[node_id] = {
                    "label": source["label"],
                    "bounds": geometry.ras_bounds(geo["matrix"], geo["dims"]),
                    "views": [],
                }
            groups[node_id]["views"].append(view_name)

        messages: List[str] = []
        for group in groups.values():
            view_text = " and ".join(group["views"])
            messages += geometry.box_warnings(boxes, group["label"], group["bounds"], view_text)
        return messages
