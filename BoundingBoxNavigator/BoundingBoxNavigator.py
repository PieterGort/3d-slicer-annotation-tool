"""
Bounding Box Navigator - 3D Slicer Scripted Loadable Module

Quickly navigate through a folder of medical imaging scans (CT volumes)
and annotate 3D bounding boxes (Markups ROI) saved directly as per-box .mrk.json
files inside per-case output directories.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_MODULE_DIR = str(Path(__file__).resolve().parent)
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
)

from cases import case_is_completed, choose_output_dir, scan_input_dir
from filenames import (
    AXIAL_3MM_KEY,
    COMPLETION_MARKER_FILENAME,
    default_axial_thickness,
    natural_sort_key,
    primary_axial_key,
    volume_key,
)
from markups import (
    MarkupOps,
    DEFAULT_FINDING_LETTER,
    FINDING_LABEL_BY_LETTER,
    ROI_ATTR_CONVENTION,
    ROI_ATTR_FINDING_TEXT,
    ROI_ATTR_LETTER,
    ROI_ATTR_SOURCE_FILE,
    plan_markup_files,
)
from session import filenames_to_delete
from geometry_check import GeometryOps
from volumes import VolumeOps

MODULE_NAME = "BoundingBoxNavigator"
MODULE_TITLE = "Bounding Box Navigator"

class BoundingBoxNavigator(ScriptedLoadableModule):
    """Slicer module registration."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = MODULE_TITLE
        self.parent.categories = ["Annotation"]
        self.parent.dependencies = ["Markups"]
        self.parent.contributors = ["Pieter Gort"]
        self.parent.helpText = (
            "Navigate a folder of CT scans and annotate 3D bounding boxes (Markups ROI). "
            "Files named {case_id}_{scan_number}_{AX|COR|SAG}_{phase}_{thickness}.nii.gz are grouped per case. "
            "A file without a scan number ({case_id}_{AX|COR|SAG}_{phase}_{thickness}.nii.gz) is scan 1. "
            "Scan 1 uses the case folder {case_id}; scan 2 and higher use {case_id}_{scan_number}. "
            "Native AX/COR/SAG volumes are shown when present; missing planes or a thickness "
            "without a native pair (e.g. TS) use reconstructions of the current axial volume. "
            "Boxes are saved as <letter>_<n>.mrk.json (R, A, O, S, L)."
        )
        self.parent.acknowledgementText = (
            "Developed for fast medical imaging annotation in 3D Slicer."
        )



class BoundingBoxNavigatorLogic(MarkupOps, VolumeOps, GeometryOps, ScriptedLoadableModuleLogic):
    """Core logic for scanning directories, loading volumes, managing ROIs, and saving files."""

    def __init__(self):
        super().__init__()
        self.input_dir: Optional[Path] = None
        self.output_dir: Optional[Path] = None
        self.cases: List[Dict[str, Any]] = []
        self.scan_warnings: List[str] = []
        self.current_index: int = -1
        self.current_case: Optional[Dict[str, Any]] = None
        self.current_volume_nodes: Dict[str, slicer.vtkMRMLScalarVolumeNode] = {}
        self.current_roi_nodes: List[slicer.vtkMRMLMarkupsROINode] = []
        self.current_segmentation_nodes: List[Any] = []
        self.current_axial_thickness: Optional[str] = None
        self.axial_using_ts: bool = False
        self.load_warnings: List[str] = []
        self.load_problems: List[str] = []
        self.protected_markup_files: List[str] = []
        self.last_dropped_boxes: List[str] = []
        self.view_sources: Dict[str, Dict[str, Any]] = {}

    def _warn(self, message: str) -> None:
        self.scan_warnings.append(message)
        print(message)

    def _protect_markup_file(self, mrk_file: Path, reason: str) -> None:
        """Keep a markup file in the folder untouched; report it to the physician."""
        self.protected_markup_files.append(mrk_file.name)
        self.load_problems.append(f"{mrk_file.name}: {reason}")
        print(f"Warning: {mrk_file.name} {reason}")

    def scan_folder(
        self, input_dir: Path, output_dir: Optional[Path] = None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Scan the input folder and group volumes into cases."""
        self.input_dir = Path(input_dir)
        if output_dir:
            self.output_dir = Path(output_dir)
        self.cases = []
        self.scan_warnings = []
        self.current_index = -1
        self.current_case = None
        self.current_volume_nodes = {}
        self.current_axial_thickness = None
        self.axial_using_ts = False
        self.load_warnings = []
        self.load_problems = []
        self.protected_markup_files = []
        self.last_dropped_boxes = []
        self.view_sources = {}
        self.cases, self.scan_warnings = scan_input_dir(self.input_dir)
        return self.cases, self.scan_warnings

    def get_case_output_dir(self, case_id: str) -> Optional[Path]:
        """Prefer <output>/<case_id>, then a folder whose name contains that case key."""
        return choose_output_dir(self.output_dir, case_id)

    def is_case_completed(self, case_id: str) -> bool:
        """Check if case has a completed review marker or saved bounding boxes."""
        return case_is_completed(self.get_case_output_dir(case_id), case_id)

    def get_status_summary(self) -> Dict[str, int]:
        """Return total, completed, and pending counts."""
        total = len(self.cases)
        completed = sum(1 for c in self.cases if self.is_case_completed(c["case_id"]))
        pending = total - completed
        return {"total": total, "completed": completed, "pending": pending}

    def find_next_case_index(
        self, start_index: int, step: int = 1, skip_completed: bool = True
    ) -> Optional[int]:
        """Find next or previous case index, optionally skipping completed cases."""
        if step not in (-1, 1):
            raise ValueError("step must be -1 or 1")
        idx = start_index + step
        while 0 <= idx < len(self.cases):
            if not skip_completed or not self.is_case_completed(self.cases[idx]["case_id"]):
                return idx
            idx += step
        return None

    def load_case(self, index: int) -> Dict[str, Any]:
        """Load native AX/COR/SAG volumes for a case and reload any existing saved boxes."""
        if index < 0 or index >= len(self.cases):
            raise IndexError(f"Case index {index} out of range [0, {len(self.cases)}).")

        self.clear_current_nodes()
        self.load_problems = []
        self.protected_markup_files = []
        self.last_dropped_boxes = []
        self.current_index = index
        self.current_case = self.cases[index]
        case_id = self.current_case["case_id"]
        volumes: Dict[str, Path] = self.current_case.get("volumes") or {
            AXIAL_3MM_KEY: self.current_case["volume_path"]
        }

        if primary_axial_key(volumes) is None:
            raise RuntimeError(
                f"Case '{case_id}' has no axial volume to load. "
                "Cannot reconstruct coronal/sagittal without an axial scan."
            )

        thickness = self.current_case.get("default_axial_thickness") or default_axial_thickness(volumes)
        axial_name = self.apply_thickness_to_views(thickness, fit=True, preserve_offset=False)
        axial_node = self.current_volume_nodes.get(volume_key("AX", thickness))

        notes = ""
        box_classes: Dict[str, str] = {}
        box_findings: Dict[str, str] = {}
        case_dir = self.get_case_output_dir(case_id)
        if case_dir and case_dir.is_dir():
            marker_file = case_dir / COMPLETION_MARKER_FILENAME
            if marker_file.is_file():
                try:
                    payload = json.loads(marker_file.read_text(encoding="utf-8"))
                    notes = str(payload.get("notes", "")).strip()
                    raw_classes = payload.get("box_classes") or {}
                    raw_findings = payload.get("box_findings") or {}
                    if isinstance(raw_classes, dict):
                        box_classes = {str(k): str(v) for k, v in raw_classes.items()}
                    if isinstance(raw_findings, dict):
                        box_findings = {str(k): str(v) for k, v in raw_findings.items()}
                except Exception:
                    pass

            mrk_files = sorted(case_dir.glob("*.mrk.json"), key=lambda p: natural_sort_key(p.name))
            for mrk_file in mrk_files:
                roi_node = None
                try:
                    roi_node = slicer.util.loadMarkups(str(mrk_file))
                    if roi_node is None:
                        self._protect_markup_file(mrk_file, "could not be read")
                        continue
                    if not isinstance(roi_node, slicer.vtkMRMLMarkupsROINode):
                        slicer.mrmlScene.RemoveNode(roi_node)
                        self._protect_markup_file(mrk_file, "is not a bounding box; kept unchanged")
                        continue
                    self._configure_roi_display(roi_node)
                    self._apply_finding_from_filename(roi_node, mrk_file)
                    self._restore_finding_from_saved(
                        roi_node,
                        mrk_file,
                        box_findings.get(mrk_file.name),
                        box_classes.get(mrk_file.name),
                    )
                    self.current_roi_nodes.append(roi_node)
                except Exception as exc:
                    if roi_node is not None and roi_node not in self.current_roi_nodes:
                        slicer.mrmlScene.RemoveNode(roi_node)
                    self._protect_markup_file(mrk_file, f"could not be loaded ({exc})")

        return {
            "case_info": self.current_case,
            "volume_node": axial_node,
            "volume_nodes": self.current_volume_nodes,
            "roi_nodes": self.current_roi_nodes,
            "notes": notes,
            "axial_using_ts": self.axial_using_ts,
            "axial_thickness": self.current_axial_thickness,
            "axial_name": axial_name,
            "load_warnings": list(self.load_warnings),
            "load_problems": list(self.load_problems),
        }

    def set_slice_thickness(self, thickness: str) -> str:
        """Switch all views to the given axial thickness (lazy-load that volume)."""
        return self.apply_thickness_to_views(thickness, fit=False, preserve_offset=True)

    def set_axial_thickness(self, use_thin_slices: bool) -> str:
        """Backward-compatible wrapper: True selects TS, False selects the default mm series."""
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")
        volumes = self.current_case.get("volumes") or {}
        if use_thin_slices:
            return self.set_slice_thickness("TS")
        default = self.current_case.get("default_axial_thickness") or default_axial_thickness(volumes) or "3mm"
        if default.upper() == "TS":
            default = "3mm" if AXIAL_3MM_KEY in volumes else default
        return self.set_slice_thickness(default)

    def active_axial_filename(self) -> str:
        """Filename currently shown in the axial (Red) view."""
        if self.current_case is None:
            return ""
        volumes: Dict[str, Path] = self.current_case.get("volumes") or {}
        thickness = self.current_axial_thickness
        if thickness:
            path = volumes.get(volume_key("AX", thickness))
            if path:
                return path.name
        ax_key = primary_axial_key(volumes)
        if ax_key and ax_key in volumes:
            return volumes[ax_key].name
        return str(self.current_case.get("filename", ""))

    def cor_sag_are_reconstructions(self) -> bool:
        if self.current_case is None or not self.current_axial_thickness:
            return False
        volumes = self.current_case.get("volumes") or {}
        thickness = self.current_axial_thickness
        return (
            volume_key("COR", thickness) not in volumes
            or volume_key("SAG", thickness) not in volumes
        )

    def add_bounding_box(self, letter: str = DEFAULT_FINDING_LETTER) -> slicer.vtkMRMLMarkupsROINode:
        """Create a new vtkMRMLMarkupsROINode and activate interactive Place mode."""
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")

        letter = (letter or DEFAULT_FINDING_LETTER).upper()
        if letter not in FINDING_LABEL_BY_LETTER:
            letter = DEFAULT_FINDING_LETTER
        node_name = self._next_convention_name(letter)

        roi_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode", node_name)
        roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
        roi_node.SetAttribute(ROI_ATTR_CONVENTION, "1")
        roi_node.SetAttribute(ROI_ATTR_FINDING_TEXT, FINDING_LABEL_BY_LETTER.get(letter, ""))
        self._configure_roi_display(roi_node)
        self.current_roi_nodes.append(roi_node)
        roi_node.SetName(node_name)

        # Activate interactive placement mode so user can immediately click & drag
        selection_node = slicer.mrmlScene.GetNodeByID("vtkMRMLSelectionNodeSingleton")
        if selection_node:
            selection_node.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsROINode")
            selection_node.SetActivePlaceNodeID(roi_node.GetID())

        interaction_node = slicer.mrmlScene.GetNodeByID("vtkMRMLInteractionNodeSingleton")
        if interaction_node:
            interaction_node.SetCurrentInteractionMode(slicer.vtkMRMLInteractionNode.Place)

        return roi_node

    def is_roi_visible(self, roi_node: slicer.vtkMRMLMarkupsROINode) -> bool:
        """Whether the box is currently shown in the slice/3D views."""
        if roi_node is None:
            return False
        display_node = roi_node.GetDisplayNode()
        if display_node is None:
            return True
        try:
            return bool(display_node.GetVisibility())
        except Exception:
            return True

    def set_roi_visibility(self, roi_node: slicer.vtkMRMLMarkupsROINode, visible: bool) -> None:
        """
        Show or hide a single box in all views. Hidden boxes stay in the case and
        are still saved; visibility is only a viewing aid and is reset on reload.
        """
        if roi_node is None:
            return
        roi_node.CreateDefaultDisplayNodes()
        display_node = roi_node.GetDisplayNode()
        if display_node is None:
            return
        display_node.SetVisibility(bool(visible))

    def delete_roi_node(self, roi_node: slicer.vtkMRMLMarkupsROINode) -> None:
        """Remove a bounding box node from scene and tracking list."""
        if roi_node in self.current_roi_nodes:
            self.current_roi_nodes.remove(roi_node)
        if roi_node:
            display_node = roi_node.GetDisplayNode()
            if display_node and slicer.mrmlScene.GetNodeByID(display_node.GetID()):
                slicer.mrmlScene.RemoveNode(display_node)
            if slicer.mrmlScene.GetNodeByID(roi_node.GetID()):
                slicer.mrmlScene.RemoveNode(roi_node)

    def delete_all_roi_nodes(self) -> None:
        """Remove all current bounding box nodes."""
        for roi_node in list(self.current_roi_nodes):
            self.delete_roi_node(roi_node)

    def jump_to_roi(self, roi_node: slicer.vtkMRMLMarkupsROINode) -> None:
        """Center slice views on the center of the given ROI."""
        if roi_node is None:
            return
        if slicer.app.layoutManager():
            try:
                center = roi_node.GetCenter()
                markups_logic = getattr(slicer.modules, "markups", None)
                if markups_logic and hasattr(markups_logic, "logic"):
                    markups_logic.logic().JumpSlicesToLocation(center[0], center[1], center[2], True)
            except Exception as exc:
                print(f"Warning: could not jump slices to ROI: {exc}")

    def is_roi_valid(self, roi_node: slicer.vtkMRMLMarkupsROINode) -> bool:
        """Check if an ROI is placed and has positive dimensions."""
        if roi_node is None or slicer.mrmlScene.GetNodeByID(roi_node.GetID()) is None:
            return False
        try:
            size = roi_node.GetSize()
            return all(dim > 1e-3 for dim in size)
        except Exception:
            return False

    def save_current_case(
        self, notes: str = "", confirm_zero_boxes_fn=None
    ) -> bool:
        """
        Save boxes in <output>/<case folder>/.
        Existing free-form names (e.g. 'nodule 1') are kept. Newly created boxes
        use <letter>_<n>.mrk.json. R boxes ≥ 30 mm are classed pm_confluent.
        """
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")
        if self.output_dir is None:
            raise RuntimeError("Output directory is not specified.")

        case_id = self.current_case["case_id"]
        case_dir = self.get_case_output_dir(case_id)
        if case_dir is None:
            raise RuntimeError("Output directory is not specified.")
        case_dir.mkdir(parents=True, exist_ok=True)

        # Include any ROIs in scene that match
        valid_rois = [roi for roi in self.current_roi_nodes if self.is_roi_valid(roi)]

        # Zero-box confirmation
        if len(valid_rois) == 0:
            if confirm_zero_boxes_fn:
                confirmed = confirm_zero_boxes_fn(
                    f"Case '{case_id}' has 0 bounding boxes.\n\n"
                    "Do you want to save this case as completed with 0 boxes (e.g. PCI score 0)?"
                )
                if not confirmed:
                    return False

        # Remove unplaced or zero-sized ROIs from scene; record their names for the physician
        self.last_dropped_boxes = [
            roi.GetName() or "(unnamed box)"
            for roi in self.current_roi_nodes
            if roi not in valid_rois
        ]
        for roi in list(self.current_roi_nodes):
            if roi not in valid_rois:
                if slicer.mrmlScene.GetNodeByID(roi.GetID()):
                    slicer.mrmlScene.RemoveNode(roi)
        self.current_roi_nodes = list(valid_rois)

        entries = []
        for roi in valid_rois:
            try:
                custom = str(roi.GetAttribute(ROI_ATTR_FINDING_TEXT) or "").strip()
            except Exception:
                custom = ""
            entries.append(
                {
                    "name": roi.GetName() or "",
                    "letter": self._roi_letter(roi),
                    "source_filename": self._preserved_source_filename(roi) or "",
                    "finding_text": custom,
                    "visible": self.is_roi_visible(roi),
                }
            )
        planned = plan_markup_files(entries, protected=self.protected_markup_files)
        save_plan: List[Tuple[Any, str]] = []
        for roi, item in zip(valid_rois, planned):
            roi.SetName(item["name"])
            roi.SetAttribute(ROI_ATTR_SOURCE_FILE, item["filename"])
            save_plan.append((roi, item["filename"]))

        for filename in filenames_to_delete(
            [old_file.name for old_file in case_dir.glob("*.mrk.json")],
            planned,
            protected=self.protected_markup_files,
        ):
            try:
                (case_dir / filename).unlink()
            except Exception as exc:
                print(f"Warning: could not delete old file {case_dir / filename}: {exc}")

        saved_filenames: List[str] = []
        box_classes: Dict[str, str] = {}
        box_findings: Dict[str, str] = {}
        for roi, filename in save_plan:
            target_file = case_dir / filename
            self._write_finding_onto_roi(roi)
            saved = slicer.util.saveNode(roi, str(target_file))
            if not saved:
                raise RuntimeError(f"Failed to save bounding box to: {target_file}")
            annotation = self.roi_annotation_dict(roi)
            self._embed_annotation_in_mrk_json(target_file, annotation)
            saved_filenames.append(target_file.name)
            box_classes[target_file.name] = annotation["software_class"]
            box_findings[target_file.name] = annotation["finding"]

        # Write completion marker JSON
        now_utc = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        marker_data = {
            "case_id": case_id,
            "source_volume": str(self.current_case["volume_path"]),
            "source_volumes": {
                key: str(path) for key, path in sorted(self.current_case.get("volumes", {}).items())
            },
            "completed_at_utc": now_utc,
            "num_boxes": len(valid_rois),
            "box_files": saved_filenames,
            "box_classes": box_classes,
            "box_findings": box_findings,
            "notes": str(notes).strip(),
            "format_version": 1,
        }

        marker_file = case_dir / COMPLETION_MARKER_FILENAME
        tmp_marker = case_dir / f"{COMPLETION_MARKER_FILENAME}.tmp"
        tmp_marker.write_text(json.dumps(marker_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_marker.replace(marker_file)

        return True


from widget import BoundingBoxNavigatorWidget  # Slicer looks this class up on the module.
