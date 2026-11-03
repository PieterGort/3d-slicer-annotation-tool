"""Slice views, volume loading, and segmentation overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import slicer

from cases import view_plan
from filenames import (
    CT_ABDOMEN_LEVEL,
    CT_ABDOMEN_WINDOW,
    SEGMENTATION_FILE_SUFFIXES,
    get_clean_stem,
    name_contains_case_key,
    natural_sort_key,
    parse_scan_filename,
    volume_key,
)


def apply_ct_abdomen_window(volume_node) -> None:
    """Apply standard CT-Abdomen window and level to volume display node."""
    if volume_node is None:
        return
    display_node = volume_node.GetDisplayNode()
    if display_node is None:
        volume_node.CreateDefaultDisplayNodes()
        display_node = volume_node.GetDisplayNode()
    if display_node is None:
        return
    display_node.AutoWindowLevelOff()
    display_node.SetWindow(CT_ABDOMEN_WINDOW)
    display_node.SetLevel(CT_ABDOMEN_LEVEL)


class VolumeOps:
    """Volume and segmentation methods mixed into BoundingBoxNavigatorLogic."""

    def _remove_volume_node(self, volume_node) -> None:
        """Remove a volume node and its display/storage nodes from the scene."""
        if volume_node is None:
            return
        node_id = volume_node.GetID()
        if not node_id or slicer.mrmlScene.GetNodeByID(node_id) is None:
            return
        vol_display = volume_node.GetDisplayNode()
        if vol_display and slicer.mrmlScene.GetNodeByID(vol_display.GetID()):
            slicer.mrmlScene.RemoveNode(vol_display)
        vol_storage = volume_node.GetStorageNode()
        if vol_storage and slicer.mrmlScene.GetNodeByID(vol_storage.GetID()):
            slicer.mrmlScene.RemoveNode(vol_storage)
        if slicer.mrmlScene.GetNodeByID(volume_node.GetID()):
            slicer.mrmlScene.RemoveNode(volume_node)

    def clear_current_nodes(self) -> None:
        """Remove previously loaded volume and ROI nodes from MRML scene."""
        for roi in list(self.current_roi_nodes):
            if roi:
                display_node = roi.GetDisplayNode()
                if display_node and slicer.mrmlScene.GetNodeByID(display_node.GetID()):
                    slicer.mrmlScene.RemoveNode(display_node)
                if slicer.mrmlScene.GetNodeByID(roi.GetID()):
                    slicer.mrmlScene.RemoveNode(roi)
        self.current_roi_nodes = []

        seen_ids = set()
        for volume_node in list(self.current_volume_nodes.values()):
            if volume_node is None:
                continue
            node_id = volume_node.GetID()
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            self._remove_volume_node(volume_node)
        self.current_volume_nodes = {}
        self.current_axial_thickness = None
        self.axial_using_ts = False
        self.load_warnings = []
        self.view_sources: Dict[str, Dict[str, Any]] = {}

        for seg_node in list(self.current_segmentation_nodes):
            self._remove_segmentation_node(seg_node)
        self.current_segmentation_nodes = []

    def _remove_segmentation_node(self, seg_node) -> None:
        if seg_node is None:
            return
        try:
            display_node = seg_node.GetDisplayNode()
            if display_node and slicer.mrmlScene.GetNodeByID(display_node.GetID()):
                slicer.mrmlScene.RemoveNode(display_node)
        except Exception:
            pass
        try:
            if slicer.mrmlScene.GetNodeByID(seg_node.GetID()):
                slicer.mrmlScene.RemoveNode(seg_node)
        except Exception:
            pass

    def _ensure_four_up_layout(self) -> None:
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return
        try:
            layout_manager.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        except Exception as exc:
            print(f"Warning: could not switch to Four-Up layout: {exc}")

    def _assign_volume_to_view(
        self, view_name: str, volume_node, fit: bool = True, reconstruct: bool = False
    ) -> None:
        """
        Show volume_node in a named slice view (Red/Green/Yellow).
        Native acquisitions use RotateToVolumePlane. Reconstructions keep the
        standard axial/coronal/sagittal orientation of that viewer.
        """
        if volume_node is None:
            return
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return
        slice_widget = layout_manager.sliceWidget(view_name)
        if slice_widget is None:
            print(f"Warning: slice view '{view_name}' is not available.")
            return
        slice_logic = slice_widget.sliceLogic()
        composite = slice_logic.GetSliceCompositeNode()
        slice_node = slice_logic.GetSliceNode()
        composite.SetLinkedControl(False)
        if hasattr(composite, "SetHotLinkedControl"):
            composite.SetHotLinkedControl(False)
        composite.SetBackgroundVolumeID(volume_node.GetID())
        try:
            if reconstruct:
                if view_name == "Red":
                    slice_node.SetOrientationToAxial()
                elif view_name == "Green":
                    slice_node.SetOrientationToCoronal()
                elif view_name == "Yellow":
                    slice_node.SetOrientationToSagittal()
            else:
                slice_node.RotateToVolumePlane(volume_node)
        except Exception as exc:
            print(f"Warning: could not orient slice view {view_name}: {exc}")
        if fit:
            slice_logic.FitSliceToAll()

    def _slice_ras_center(self, view_name: str) -> Optional[List[float]]:
        """RAS point at the center of a slice view. This is an anatomical position, not a slice index."""
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return None
        slice_widget = layout_manager.sliceWidget(view_name)
        if slice_widget is None:
            return None
        matrix = slice_widget.sliceLogic().GetSliceNode().GetSliceToRAS()
        return [matrix.GetElement(i, 3) for i in range(3)]

    def _jump_slice_through_ras(self, view_name: str, ras: Optional[List[float]]) -> None:
        """Put the slice plane through the same patient point after the volume changes."""
        if ras is None:
            return
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return
        slice_widget = layout_manager.sliceWidget(view_name)
        if slice_widget is None:
            return
        slice_node = slice_widget.sliceLogic().GetSliceNode()
        jump = getattr(slice_node, "JumpSlice", None)
        if jump is None:
            return
        jump(float(ras[0]), float(ras[1]), float(ras[2]))

    def _load_volume_file(self, path: Path, node_name: str):
        """Load a volume without showing it in every slice view."""
        loaded = slicer.util.loadVolume(str(path), properties={"show": False})
        volume_node = loaded[1] if isinstance(loaded, tuple) else loaded
        if volume_node is None:
            raise RuntimeError(f"Failed to load volume file: {path}")
        volume_node.SetName(node_name)
        apply_ct_abdomen_window(volume_node)
        return volume_node

    def _get_or_load_volume(self, vol_key: str, path: Path, case_id: str):
        if vol_key in self.current_volume_nodes:
            return self.current_volume_nodes[vol_key]
        node = self._load_volume_file(path, f"{case_id}_{vol_key}")
        self.current_volume_nodes[vol_key] = node
        return node

    def _native_plane_node(self, volumes: Dict[str, Path], plane: str, thickness: str, case_id: str):
        """Return a loaded native volume for plane+thickness, or None if that file is missing."""
        key = volume_key(plane, thickness)
        path = volumes.get(key)
        if path is None:
            return None
        return self._get_or_load_volume(key, path, case_id)

    def apply_thickness_to_views(self, thickness: str, fit: bool = True, preserve_offset: bool = False) -> str:
        """
        Show the chosen axial thickness in Red.
        Use native COR/SAG of the same thickness when they exist; otherwise
        reconstruct those views from the current axial volume.
        preserve_offset keeps each view on the same patient point. A slice-offset
        number is not reused, because 3 mm and TS slices are not the same index.
        """
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")
        volumes: Dict[str, Path] = self.current_case.get("volumes") or {}
        case_id = self.current_case["case_id"]
        plan = view_plan(volumes, thickness, case_id)
        thickness = plan["thickness"]
        ax_key = volume_key("AX", thickness)

        axial_node = self._get_or_load_volume(ax_key, plan["axial_path"], case_id)
        cor_node = (
            self._get_or_load_volume(volume_key("COR", thickness), plan["cor_path"], case_id)
            if plan["cor_path"] is not None
            else None
        )
        sag_node = (
            self._get_or_load_volume(volume_key("SAG", thickness), plan["sag_path"], case_id)
            if plan["sag_path"] is not None
            else None
        )

        self.load_warnings = list(plan["warnings"])
        cor_reconstruct = cor_node is None
        sag_reconstruct = sag_node is None
        if cor_node is None:
            cor_node = axial_node
        if sag_node is None:
            sag_node = axial_node

        centers = {}
        if preserve_offset:
            for view_name in ("Red", "Green", "Yellow"):
                centers[view_name] = self._slice_ras_center(view_name)

        self._ensure_four_up_layout()
        self._assign_volume_to_view("Red", axial_node, fit=fit, reconstruct=False)
        self._assign_volume_to_view("Green", cor_node, fit=fit, reconstruct=cor_reconstruct)
        self._assign_volume_to_view("Yellow", sag_node, fit=fit, reconstruct=sag_reconstruct)
        self.view_sources = {
            "Red": self._view_source("AX", thickness, axial_node, reconstructed=False),
            "Green": self._view_source("COR", thickness, cor_node, cor_reconstruct),
            "Yellow": self._view_source("SAG", thickness, sag_node, sag_reconstruct),
        }

        if preserve_offset:
            for view_name, ras in centers.items():
                self._jump_slice_through_ras(view_name, ras)

        self.current_axial_thickness = thickness
        self.axial_using_ts = thickness.upper() == "TS"
        return axial_node.GetName()

    def _view_source(self, plane: str, thickness: str, node, reconstructed: bool) -> Dict[str, Any]:
        """What a slice view draws. A reconstructed view draws the axial volume."""
        if reconstructed:
            return {"label": f"AX {thickness}", "plane": "AX", "thickness": thickness,
                    "node": node, "reconstructed": True}
        return {"label": f"{plane} {thickness}", "plane": plane, "thickness": thickness,
                "node": node, "reconstructed": False}

    def find_segmentation_files(
        self,
        path: Path,
        case_id: Optional[str] = None,
        require_case_match: bool = False,
    ) -> List[Path]:
        """Collect .seg.nrrd / .nii.gz segmentation files, skipping CT volume names."""
        path = Path(path)
        files: List[Path] = []
        if path.is_file():
            files = [path]
        elif path.is_dir():
            for candidate in sorted(path.rglob("*"), key=lambda p: natural_sort_key(p.name)):
                if not candidate.is_file() or candidate.name.startswith("."):
                    continue
                lower = candidate.name.lower()
                if not any(lower.endswith(suffix) for suffix in SEGMENTATION_FILE_SUFFIXES):
                    continue
                if parse_scan_filename(candidate) is not None:
                    continue
                files.append(candidate)
        else:
            raise ValueError(f"Segmentation path does not exist: {path}")

        if case_id:
            matched = [p for p in files if name_contains_case_key(p.name, case_id)]
            if matched:
                return matched
            if require_case_match:
                return []
        return files

    def _configure_segmentation_display(self, seg_node) -> None:
        seg_node.CreateDefaultDisplayNodes()
        display_node = seg_node.GetDisplayNode()
        if display_node is None:
            return
        display_node.SetVisibility(True)
        if hasattr(display_node, "SetVisibility2DFill"):
            display_node.SetVisibility2DFill(True)
        if hasattr(display_node, "SetVisibility2DOutline"):
            display_node.SetVisibility2DOutline(True)

    def load_segmentation_file(self, path: Path):
        """Load a .seg.nrrd or force a .nii.gz/.nii to load as a labelmap segmentation."""
        path = Path(path)
        lower = path.name.lower()
        if lower.endswith(".seg.nrrd"):
            loaded = slicer.util.loadSegmentation(str(path))
            seg_node = loaded[1] if isinstance(loaded, tuple) else loaded
            if seg_node is None:
                raise RuntimeError(f"Failed to load segmentation: {path}")
        else:
            loaded = slicer.util.loadVolume(
                str(path), properties={"labelmap": True, "show": False}
            )
            labelmap = loaded[1] if isinstance(loaded, tuple) else loaded
            if labelmap is None:
                raise RuntimeError(f"Failed to load labelmap: {path}")
            if not isinstance(labelmap, slicer.vtkMRMLLabelMapVolumeNode):
                labelmap_node = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLabelMapVolumeNode", f"{get_clean_stem(path)}_label"
                )
                slicer.modules.volumes.logic().CreateLabelVolumeFromVolume(
                    slicer.mrmlScene, labelmap_node, labelmap
                )
                if slicer.mrmlScene.GetNodeByID(labelmap.GetID()):
                    slicer.mrmlScene.RemoveNode(labelmap)
                labelmap = labelmap_node
            seg_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", get_clean_stem(path)
            )
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                labelmap, seg_node
            )
            if slicer.mrmlScene.GetNodeByID(labelmap.GetID()):
                slicer.mrmlScene.RemoveNode(labelmap)

        self._configure_segmentation_display(seg_node)
        reference = None
        if self.current_axial_thickness:
            reference = self.current_volume_nodes.get(
                volume_key("AX", self.current_axial_thickness)
            )
        if reference is None and self.current_volume_nodes:
            reference = next(iter(self.current_volume_nodes.values()))
        if reference is not None:
            try:
                seg_node.SetReferenceImageGeometryParameterFromVolumeNode(reference)
            except Exception:
                pass
        self.current_segmentation_nodes.append(seg_node)
        return seg_node

    def load_segmentations_from_path(
        self,
        path: Path,
        case_id: Optional[str] = None,
        require_case_match: bool = False,
    ) -> List[str]:
        """Replace current segmentations with files from a file or folder."""
        for seg_node in list(self.current_segmentation_nodes):
            self._remove_segmentation_node(seg_node)
        self.current_segmentation_nodes = []

        files = self.find_segmentation_files(
            path, case_id=case_id, require_case_match=require_case_match
        )
        loaded_names: List[str] = []
        errors: List[str] = []
        for file_path in files:
            try:
                node = self.load_segmentation_file(file_path)
                loaded_names.append(node.GetName())
            except Exception as exc:
                errors.append(f"{file_path.name}: {exc}")
                print(f"Warning: could not load segmentation {file_path}: {exc}")
        if not files:
            raise RuntimeError("No .seg.nrrd or segmentation .nii.gz files found at that path.")
        if not loaded_names and errors:
            raise RuntimeError("Failed to load segmentations:\n" + "\n".join(errors))
        return loaded_names

    def clear_segmentations(self) -> None:
        for seg_node in list(self.current_segmentation_nodes):
            self._remove_segmentation_node(seg_node)
        self.current_segmentation_nodes = []

