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

    def _slice_offset(self, view_name: str) -> Optional[float]:
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return None
        slice_widget = layout_manager.sliceWidget(view_name)
        if slice_widget is None:
            return None
        return slice_widget.sliceLogic().GetSliceNode().GetSliceOffset()

    def _set_slice_offset(self, view_name: str, offset: Optional[float]) -> None:
        if offset is None:
            return
        layout_manager = slicer.app.layoutManager()
        if layout_manager is None:
            return
        slice_widget = layout_manager.sliceWidget(view_name)
        if slice_widget is None:
            return
        slice_widget.sliceLogic().GetSliceNode().SetSliceOffset(offset)

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

        offsets = {}
        if preserve_offset:
            for view_name in ("Red", "Green", "Yellow"):
                offsets[view_name] = self._slice_offset(view_name)

        self._ensure_four_up_layout()
        self._assign_volume_to_view("Red", axial_node, fit=fit, reconstruct=False)
        self._assign_volume_to_view("Green", cor_node, fit=fit, reconstruct=cor_reconstruct)
        self._assign_volume_to_view("Yellow", sag_node, fit=fit, reconstruct=sag_reconstruct)

        if preserve_offset:
            for view_name, offset in offsets.items():
                self._set_slice_offset(view_name, offset)

        self.current_axial_thickness = thickness
        self.axial_using_ts = thickness.upper() == "TS"
        return axial_node.GetName()

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

