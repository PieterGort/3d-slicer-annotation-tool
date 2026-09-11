"""
Bounding Box Navigator - 3D Slicer Scripted Loadable Module

Quickly navigate through a folder of medical imaging scans (CT volumes)
and annotate 3D bounding boxes (Markups ROI) saved directly as per-box .mrk.json
files inside per-case output directories.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ctk
import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleWidget,
)

MODULE_NAME = "BoundingBoxNavigator"
MODULE_TITLE = "Bounding Box Navigator"

CT_ABDOMEN_WINDOW = 350.0
CT_ABDOMEN_LEVEL = 40.0

SETTINGS_KEY_INPUT_DIR = "BoundingBoxNavigator/InputDir"
SETTINGS_KEY_OUTPUT_DIR = "BoundingBoxNavigator/OutputDir"
SETTINGS_KEY_SKIP_COMPLETED = "BoundingBoxNavigator/SkipCompleted"
SETTINGS_KEY_SEG_PATH = "BoundingBoxNavigator/SegmentationPath"

COMPLETION_MARKER_FILENAME = "_annotation_done.json"
ROI_ATTR_LETTER = "BBN_finding_letter"
PM_CONFLUENT_THRESHOLD_MM = 30.0

# Letter is the filename class; software class for R depends on size.
FINDING_TYPES: Tuple[Tuple[str, str, str], ...] = (
    ("R", "PM nodule", "pm_nodule"),
    ("A", "Ascites", "ascites"),
    ("O", "Omental cake", "omental_cake"),
    ("S", "Fat stranding", "stranding"),
    ("L", "Lymph node", "lymph_node"),
)
FINDING_LABEL_BY_LETTER = {letter: label for letter, label, _ in FINDING_TYPES}
FINDING_CLASS_BY_LETTER = {letter: class_id for letter, _, class_id in FINDING_TYPES}
DEFAULT_FINDING_LETTER = "R"

MARKUP_NAME_RE = re.compile(r"^([RAOSL])[-_](\d+)$", re.IGNORECASE)
SEGMENTATION_FILE_SUFFIXES = (".seg.nrrd", ".nii.gz", ".nii")

SUPPORTED_EXTENSIONS = (
    ".nii.gz",
    ".nii",
    ".nrrd",
    ".nhdr",
    ".mha",
    ".mhd",
)

# {case_id}_{AX|COR|SAG}_{phase}_{3mm|TS}
SCAN_FILENAME_RE = re.compile(
    r"^(?P<case_id>[^_]+)_(?P<plane>AX|COR|SAG)_(?P<phase>[^_]+)_(?P<thickness>[^_]+)$",
    re.IGNORECASE,
)

PLANE_TO_VIEW = {"AX": "Red", "COR": "Green", "SAG": "Yellow"}
AXIAL_3MM_KEY = "AX_3mm"
AXIAL_TS_KEY = "AX_TS"
CORONAL_3MM_KEY = "COR_3mm"
SAGITTAL_3MM_KEY = "SAG_3mm"


def get_clean_stem(path: Path) -> str:
    """Extract file stem handling compound extensions like .nii.gz."""
    name = path.name
    for ext in (".nii.gz", ".nhdr.gz", ".tar.gz"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return path.stem


def extract_case_id(filename_or_path: Path | str) -> str:
    """
    Extract 4-5 digit case ID from file stem using regex (?<!\\d)\\d{4,5}(?!\\d).
    Falls back to the full clean stem if no 4-5 digit run is found.
    """
    path = Path(filename_or_path)
    stem = get_clean_stem(path)
    match = re.search(r"(?<!\d)\d{4,5}(?!\d)", stem)
    if match:
        return match.group(0)
    return stem


def normalize_thickness(thickness: str) -> str:
    """Normalize slice-thickness token: TS stays TS; everything else is lowercased (e.g. 3mm)."""
    if thickness.upper() == "TS":
        return "TS"
    return thickness.lower()


def volume_key(plane: str, thickness: str) -> str:
    """Build a dict key such as AX_3mm or AX_TS."""
    return f"{plane.upper()}_{normalize_thickness(thickness)}"


def parse_scan_filename(filename_or_path: Path | str) -> Optional[Dict[str, str]]:
    """
    Parse {case_id}_{AX|COR|SAG}_{phase}_{thickness} from a volume filename.
    Returns dict with case_id, plane, phase, thickness, volume_key, or None if unmatched.
    """
    path = Path(filename_or_path)
    stem = get_clean_stem(path)
    match = SCAN_FILENAME_RE.match(stem)
    if not match:
        return None
    plane = match.group("plane").upper()
    thickness = normalize_thickness(match.group("thickness"))
    return {
        "case_id": match.group("case_id"),
        "plane": plane,
        "phase": match.group("phase"),
        "thickness": thickness,
        "volume_key": volume_key(plane, thickness),
    }


def thickness_sort_key(thickness: str) -> Tuple[int, float]:
    """Sort millimeter thicknesses ascending; TS last."""
    if thickness.upper() == "TS":
        return (1, 9999.0)
    match = re.match(r"(\d+(?:\.\d+)?)", thickness)
    if match:
        return (0, float(match.group(1)))
    return (0, 9998.0)


def thickness_display_label(thickness: str) -> str:
    if thickness.upper() == "TS":
        return "Thin slices (TS)"
    return thickness.replace("mm", " mm")


def available_axial_thicknesses(volumes: Dict[str, Path]) -> List[str]:
    found = [key[3:] for key in volumes if key.startswith("AX_")]
    found.sort(key=thickness_sort_key)
    return found


def default_axial_thickness(volumes: Dict[str, Path]) -> Optional[str]:
    """Prefer 3mm, then any other millimeter axial, then TS."""
    thicknesses = available_axial_thicknesses(volumes)
    if "3mm" in thicknesses:
        return "3mm"
    non_ts = [t for t in thicknesses if t.upper() != "TS"]
    if non_ts:
        return non_ts[0]
    if thicknesses:
        return thicknesses[0]
    return None


def primary_axial_key(volumes: Dict[str, Path]) -> Optional[str]:
    """Prefer AX_3mm, then another AX_* mm volume, then AX_TS."""
    thickness = default_axial_thickness(volumes)
    return f"AX_{thickness}" if thickness else None


def plane_keys_for(volumes: Dict[str, Path], plane: str) -> List[str]:
    prefix = f"{plane.upper()}_"
    keys = [key for key in volumes if key.startswith(prefix)]
    keys.sort(key=lambda k: thickness_sort_key(k[len(prefix) :]))
    return keys


def natural_sort_key(value: Any) -> list:
    """Natural sorting key for human-friendly ordering (e.g. 2 before 10)."""
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", str(value))
    ]


def apply_ct_abdomen_window(volume_node: slicer.vtkMRMLScalarVolumeNode) -> None:
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
            "Files named {case_id}_{AX|COR|SAG}_{phase}_{thickness}.nii.gz are grouped per case. "
            "Native AX/COR/SAG volumes are shown when present; missing planes or a thickness "
            "without a native pair (e.g. TS) use reconstructions of the current axial volume. "
            "Boxes are saved as <letter>_<n>.mrk.json (R, A, O, S, L)."
        )
        self.parent.acknowledgementText = (
            "Developed for fast medical imaging annotation in 3D Slicer."
        )


class BoundingBoxNavigatorLogic(ScriptedLoadableModuleLogic):
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

    def _warn(self, message: str) -> None:
        self.scan_warnings.append(message)
        print(message)

    def scan_folder(
        self, input_dir: Path, output_dir: Optional[Path] = None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Scan input directory recursively for supported medical volume files.
        Groups {case_id}_{AX|COR|SAG}_{phase}_{thickness} files into one case.
        Unmatched filenames fall back to legacy single-volume cases.
        """
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

        if not self.input_dir.exists() or not self.input_dir.is_dir():
            raise ValueError(f"Input directory does not exist: {self.input_dir}")

        found_files: List[Path] = []
        for root, _, files in os.walk(str(self.input_dir)):
            for file_name in files:
                if file_name.startswith("."):
                    continue
                lower_name = file_name.lower()
                for ext in SUPPORTED_EXTENSIONS:
                    if lower_name.endswith(ext):
                        found_files.append(Path(root) / file_name)
                        break

        found_files.sort(key=lambda p: natural_sort_key(p.name))

        grouped: Dict[str, Dict[str, Any]] = {}
        unmatched: List[Path] = []

        for file_path in found_files:
            parsed = parse_scan_filename(file_path)
            if parsed is None:
                unmatched.append(file_path)
                continue

            case_id = parsed["case_id"]
            vol_key = parsed["volume_key"]
            if case_id not in grouped:
                grouped[case_id] = {
                    "case_id": case_id,
                    "volumes": {},
                    "phase": parsed["phase"],
                }
            volumes: Dict[str, Path] = grouped[case_id]["volumes"]
            if vol_key in volumes:
                self._warn(
                    f"Duplicate volume '{vol_key}' for case '{case_id}': "
                    f"keeping '{volumes[vol_key].name}', ignoring '{file_path.name}'."
                )
                continue
            volumes[vol_key] = file_path

        used_ids: Dict[str, int] = {case_id: 1 for case_id in grouped}
        for file_path in unmatched:
            base_id = extract_case_id(file_path)
            if base_id not in used_ids:
                used_ids[base_id] = 1
                case_id = base_id
            else:
                used_ids[base_id] += 1
                case_id = f"{base_id}_{used_ids[base_id]}"
                self._warn(
                    f"Unmatched filename '{file_path.name}' shares case ID '{base_id}'. "
                    f"Loaded as legacy single-volume case '{case_id}'."
                )
            grouped[case_id] = {
                "case_id": case_id,
                "volumes": {AXIAL_3MM_KEY: file_path},
                "phase": None,
                "legacy": True,
            }

        case_records: List[Dict[str, Any]] = []
        for case_id in grouped:
            rec = grouped[case_id]
            volumes = rec["volumes"]
            ax_key = primary_axial_key(volumes)
            if ax_key is None:
                self._warn(
                    f"Skipping case '{case_id}': no axial volume "
                    f"(expected {AXIAL_3MM_KEY} or {AXIAL_TS_KEY})."
                )
                continue

            missing_planes: List[str] = []
            if not plane_keys_for(volumes, "COR"):
                missing_planes.append("COR")
                self._warn(
                    f"Case '{case_id}' has no coronal volume; "
                    "coronal view will show a reconstruction from the axial scan."
                )
            if not plane_keys_for(volumes, "SAG"):
                missing_planes.append("SAG")
                self._warn(
                    f"Case '{case_id}' has no sagittal volume; "
                    "sagittal view will show a reconstruction from the axial scan."
                )

            primary_path = volumes[ax_key]
            axial_thicknesses = available_axial_thicknesses(volumes)
            default_thickness = default_axial_thickness(volumes)
            case_records.append(
                {
                    "case_id": case_id,
                    "base_id": case_id,
                    "volume_path": primary_path,
                    "filename": primary_path.name,
                    "volumes": volumes,
                    "axial_thicknesses": axial_thicknesses,
                    "default_axial_thickness": default_thickness,
                    "has_axial_ts": AXIAL_TS_KEY in volumes,
                    "missing_planes": missing_planes,
                    "legacy": bool(rec.get("legacy")),
                    "phase": rec.get("phase"),
                }
            )

        case_records.sort(key=lambda c: natural_sort_key(c["case_id"]))
        self.cases = case_records
        return self.cases, self.scan_warnings

    def get_case_output_dir(self, case_id: str) -> Optional[Path]:
        """Return the per-case output directory path."""
        if self.output_dir is None:
            return None
        return self.output_dir / case_id

    def is_case_completed(self, case_id: str) -> bool:
        """Check if case has a completed review marker or saved bounding boxes."""
        case_dir = self.get_case_output_dir(case_id)
        if case_dir is None or not case_dir.is_dir():
            return False

        marker_file = case_dir / COMPLETION_MARKER_FILENAME
        if marker_file.is_file() and marker_file.stat().st_size > 0:
            try:
                payload = json.loads(marker_file.read_text(encoding="utf-8"))
                if payload.get("case_id") == case_id:
                    return True
            except Exception:
                pass

        # Fallback check: any non-empty .mrk.json files
        mrk_files = list(case_dir.glob("*.mrk.json"))
        return len(mrk_files) > 0

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
        thickness = normalize_thickness(thickness)
        ax_key = volume_key("AX", thickness)
        ax_path = volumes.get(ax_key)
        if ax_path is None:
            raise RuntimeError(f"Case '{case_id}' has no axial volume for thickness '{thickness}'.")

        axial_node = self._get_or_load_volume(ax_key, ax_path, case_id)
        cor_node = self._native_plane_node(volumes, "COR", thickness, case_id)
        sag_node = self._native_plane_node(volumes, "SAG", thickness, case_id)

        self.load_warnings = []
        if cor_node is None:
            self.load_warnings.append(
                f"No native COR {thickness_display_label(thickness)} volume — "
                "coronal view is a reconstruction of the current axial scan."
            )
            cor_node = axial_node
            cor_reconstruct = True
        else:
            cor_reconstruct = False
        if sag_node is None:
            self.load_warnings.append(
                f"No native SAG {thickness_display_label(thickness)} volume — "
                "sagittal view is a reconstruction of the current axial scan."
            )
            sag_node = axial_node
            sag_reconstruct = True
        else:
            sag_reconstruct = False

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

    def load_case(self, index: int) -> Dict[str, Any]:
        """Load native AX/COR/SAG volumes for a case and reload any existing saved boxes."""
        if index < 0 or index >= len(self.cases):
            raise IndexError(f"Case index {index} out of range [0, {len(self.cases)}).")

        self.clear_current_nodes()
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
        case_dir = self.get_case_output_dir(case_id)
        if case_dir and case_dir.is_dir():
            marker_file = case_dir / COMPLETION_MARKER_FILENAME
            if marker_file.is_file():
                try:
                    payload = json.loads(marker_file.read_text(encoding="utf-8"))
                    notes = str(payload.get("notes", "")).strip()
                except Exception:
                    pass

            mrk_files = sorted(case_dir.glob("*.mrk.json"), key=lambda p: natural_sort_key(p.name))
            for mrk_file in mrk_files:
                try:
                    roi_node = slicer.util.loadMarkups(str(mrk_file))
                    if roi_node and isinstance(roi_node, slicer.vtkMRMLMarkupsROINode):
                        self._configure_roi_display(roi_node)
                        self._apply_finding_from_filename(roi_node, mrk_file)
                        self.current_roi_nodes.append(roi_node)
                except Exception as exc:
                    print(f"Warning: could not load markup file {mrk_file}: {exc}")

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

    def _configure_roi_display(self, roi_node: slicer.vtkMRMLMarkupsROINode) -> None:
        """Configure interactive handles and visibility for 3D bounding box ROI."""
        roi_node.CreateDefaultDisplayNodes()
        display_node = roi_node.GetDisplayNode()
        if display_node is None:
            return
        display_node.SetHandlesInteractive(True)
        display_node.SetTranslationHandleVisibility(True)
        display_node.SetScaleHandleVisibility(True)
        display_node.SetRotationHandleVisibility(False)
        display_node.SetVisibility(True)
        display_node.SetVisibility2D(True)
        display_node.SetColor(1.0, 0.8, 0.0)  # Distinct gold / yellow
        display_node.SetSelectedColor(1.0, 0.25, 0.25)  # Bright coral/red when selected
        display_node.SetPropertiesLabelVisibility(True)

    def _apply_finding_from_filename(self, roi_node, mrk_file: Path) -> None:
        stem = get_clean_stem(mrk_file)
        if stem.endswith(".mrk"):
            stem = stem[: -len(".mrk")]
        match = MARKUP_NAME_RE.match(stem)
        letter = match.group(1).upper() if match else DEFAULT_FINDING_LETTER
        roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
        roi_node.SetName(self._preview_box_name(letter, roi_node))

    def _roi_letter(self, roi_node) -> str:
        letter = ""
        try:
            letter = str(roi_node.GetAttribute(ROI_ATTR_LETTER) or "")
        except Exception:
            letter = ""
        if letter.upper() in FINDING_LABEL_BY_LETTER:
            return letter.upper()
        match = MARKUP_NAME_RE.match(roi_node.GetName() or "")
        if match:
            return match.group(1).upper()
        return DEFAULT_FINDING_LETTER

    def _preview_box_name(self, letter: str, roi_node=None) -> str:
        count = 1
        for roi in self.current_roi_nodes:
            if roi is roi_node:
                continue
            if self._roi_letter(roi) == letter:
                count += 1
        if roi_node is not None and self._roi_letter(roi_node) == letter:
            # Keep a stable preview index among current same-letter boxes
            same = [roi for roi in self.current_roi_nodes if self._roi_letter(roi) == letter]
            if roi_node in same:
                count = same.index(roi_node) + 1
        return f"{letter}_{count}"

    def finding_label(self, roi_node) -> str:
        letter = self._roi_letter(roi_node)
        label = FINDING_LABEL_BY_LETTER.get(letter, "PM nodule")
        if letter == "R" and self.is_roi_valid(roi_node):
            max_dim = max(roi_node.GetSize())
            if max_dim >= PM_CONFLUENT_THRESHOLD_MM:
                return "PM confluent (size >= 30mm)"
        return label

    def software_class_for_roi(self, roi_node) -> str:
        letter = self._roi_letter(roi_node)
        if letter == "R" and self.is_roi_valid(roi_node):
            if max(roi_node.GetSize()) >= PM_CONFLUENT_THRESHOLD_MM:
                return "pm_confluent"
        return FINDING_CLASS_BY_LETTER.get(letter, "pm_nodule")

    def add_bounding_box(self, letter: str = DEFAULT_FINDING_LETTER) -> slicer.vtkMRMLMarkupsROINode:
        """Create a new vtkMRMLMarkupsROINode and activate interactive Place mode."""
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")

        letter = (letter or DEFAULT_FINDING_LETTER).upper()
        if letter not in FINDING_LABEL_BY_LETTER:
            letter = DEFAULT_FINDING_LETTER
        node_name = self._preview_box_name(letter)

        roi_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsROINode", node_name)
        roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
        self._configure_roi_display(roi_node)
        self.current_roi_nodes.append(roi_node)
        roi_node.SetName(self._preview_box_name(letter, roi_node))

        # Activate interactive placement mode so user can immediately click & drag
        selection_node = slicer.mrmlScene.GetNodeByID("vtkMRMLSelectionNodeSingleton")
        if selection_node:
            selection_node.SetReferenceActivePlaceNodeClassName("vtkMRMLMarkupsROINode")
            selection_node.SetActivePlaceNodeID(roi_node.GetID())

        interaction_node = slicer.mrmlScene.GetNodeByID("vtkMRMLInteractionNodeSingleton")
        if interaction_node:
            interaction_node.SetCurrentInteractionMode(slicer.vtkMRMLInteractionNode.Place)

        return roi_node

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
        Save valid boxes as <letter>_<n>.mrk.json (R/A/O/S/L) in <output>/<case_id>/.
        R boxes with max diameter ≥ 30 mm are classed pm_confluent automatically.
        Cleans up stale boxes and writes _annotation_done.json completion marker.
        """
        if self.current_case is None:
            raise RuntimeError("No case is currently loaded.")
        if self.output_dir is None:
            raise RuntimeError("Output directory is not specified.")

        case_id = self.current_case["case_id"]
        case_dir = self.output_dir / case_id
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

        # Remove unplaced or zero-sized ROIs from scene
        for roi in list(self.current_roi_nodes):
            if roi not in valid_rois:
                if slicer.mrmlScene.GetNodeByID(roi.GetID()):
                    slicer.mrmlScene.RemoveNode(roi)
        self.current_roi_nodes = list(valid_rois)

        # Clean stale .mrk.json files in case directory
        for old_file in case_dir.glob("*.mrk.json"):
            try:
                old_file.unlink()
            except Exception as exc:
                print(f"Warning: could not delete old file {old_file}: {exc}")

        # Number 1..n per finding letter and save
        counters: Dict[str, int] = {}
        saved_filenames: List[str] = []
        box_classes: Dict[str, str] = {}
        for roi in valid_rois:
            letter = self._roi_letter(roi)
            counters[letter] = counters.get(letter, 0) + 1
            box_name = f"{letter}_{counters[letter]}"
            roi.SetAttribute(ROI_ATTR_LETTER, letter)
            roi.SetName(box_name)
            target_file = case_dir / f"{box_name}.mrk.json"
            saved = slicer.util.saveNode(roi, str(target_file))
            if not saved:
                raise RuntimeError(f"Failed to save bounding box to: {target_file}")
            saved_filenames.append(target_file.name)
            box_classes[target_file.name] = self.software_class_for_roi(roi)

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
            "notes": str(notes).strip(),
            "format_version": 1,
        }

        marker_file = case_dir / COMPLETION_MARKER_FILENAME
        tmp_marker = case_dir / f"{COMPLETION_MARKER_FILENAME}.tmp"
        tmp_marker.write_text(json.dumps(marker_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_marker.replace(marker_file)

        return True

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
            matched = [p for p in files if case_id in p.name]
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


class BoundingBoxNavigatorWidget(ScriptedLoadableModuleWidget):
    """GUI widget for Bounding Box Navigator in Slicer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = BoundingBoxNavigatorLogic()
        self.shortcut_b: Optional[qt.QShortcut] = None
        self.shortcut_shift_b: Optional[qt.QShortcut] = None
        self._updating_ui: bool = False
        self._is_saving: bool = False
        self._is_loading_case: bool = False
        self._thickness_buttons: Dict[str, Any] = {}

    def setup(self):
        super().setup()

        # -------------------------------------------------------------
        # Section 1: Directories & Setup
        # -------------------------------------------------------------
        setup_collapsible = ctk.ctkCollapsibleButton()
        setup_collapsible.text = "1. Setup & Folders"
        self.layout.addWidget(setup_collapsible)
        setup_layout = qt.QFormLayout(setup_collapsible)

        # Input Directory Picker
        self.input_dir_picker = ctk.ctkPathLineEdit()
        self.input_dir_picker.filters = ctk.ctkPathLineEdit.Dirs
        self.input_dir_picker.toolTip = "Directory containing patient CT scan volumes (.nii.gz, .nrrd, etc.)"
        saved_input = slicer.util.settingsValue(SETTINGS_KEY_INPUT_DIR, "")
        if saved_input:
            self.input_dir_picker.setCurrentPath(saved_input)
        setup_layout.addRow("Input Scans Folder:", self.input_dir_picker)

        # Output Directory Picker
        self.output_dir_picker = ctk.ctkPathLineEdit()
        self.output_dir_picker.filters = ctk.ctkPathLineEdit.Dirs
        self.output_dir_picker.toolTip = "Directory where per-case folders and .mrk.json boxes will be saved."
        saved_output = slicer.util.settingsValue(SETTINGS_KEY_OUTPUT_DIR, "")
        if saved_output:
            self.output_dir_picker.setCurrentPath(saved_output)
        setup_layout.addRow("Output Annotations Folder:", self.output_dir_picker)

        # Scan Button and Options row
        scan_row = qt.QHBoxLayout()
        self.scan_button = qt.QPushButton("Scan Folder")
        self.scan_button.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        scan_row.addWidget(self.scan_button)

        self.skip_completed_checkbox = qt.QCheckBox("Skip completed cases during navigation")
        saved_skip = slicer.util.settingsValue(
            SETTINGS_KEY_SKIP_COMPLETED, True, converter=slicer.util.toBool
        )
        self.skip_completed_checkbox.setChecked(saved_skip)
        scan_row.addWidget(self.skip_completed_checkbox)
        scan_row.addStretch(1)
        setup_layout.addRow(scan_row)

        # Progress / Status label
        self.progress_label = qt.QLabel("No folder scanned yet.")
        self.progress_label.setStyleSheet("font-weight: bold; color: #1565C0;")
        setup_layout.addRow("Progress:", self.progress_label)

        self.status_label = qt.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        setup_layout.addRow("Status:", self.status_label)

        # -------------------------------------------------------------
        # Section 2: Case Navigation
        # -------------------------------------------------------------
        nav_collapsible = ctk.ctkCollapsibleButton()
        nav_collapsible.text = "2. Case Navigation"
        self.layout.addWidget(nav_collapsible)
        nav_layout = qt.QVBoxLayout(nav_collapsible)

        # Case selection combobox row
        case_select_row = qt.QHBoxLayout()
        case_select_row.addWidget(qt.QLabel("Active Case:"))
        self.case_combobox = qt.QComboBox()
        self.case_combobox.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        case_select_row.addWidget(self.case_combobox, 1)
        nav_layout.addLayout(case_select_row)

        # Case Details label
        self.case_details_label = qt.QLabel("No case loaded.")
        self.case_details_label.setWordWrap(True)
        self.case_details_label.setStyleSheet(
            "padding: 8px; font-size: 13px; background-color: rgba(128,128,128,0.1); border-radius: 4px;"
        )
        nav_layout.addWidget(self.case_details_label)

        self.plane_warning_label = qt.QLabel("")
        self.plane_warning_label.setWordWrap(True)
        self.plane_warning_label.setStyleSheet("color: #E65100; font-weight: bold; padding: 2px 4px;")
        self.plane_warning_label.setVisible(False)
        nav_layout.addWidget(self.plane_warning_label)

        thickness_header = qt.QLabel("Slice thickness:")
        thickness_header.setStyleSheet("font-weight: bold; margin-top: 6px;")
        nav_layout.addWidget(thickness_header)

        self.thickness_buttons_widget = qt.QWidget()
        self.thickness_buttons_layout = qt.QHBoxLayout(self.thickness_buttons_widget)
        self.thickness_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.thickness_button_group = qt.QButtonGroup(self.thickness_buttons_widget)
        self.thickness_button_group.setExclusive(True)
        nav_layout.addWidget(self.thickness_buttons_widget)
        self._thickness_buttons: Dict[str, Any] = {}

        self.axial_file_label = qt.QLabel("Showing: —")
        self.axial_file_label.setWordWrap(True)
        self.axial_file_label.setStyleSheet("color: #555; font-size: 11px; padding: 2px 0;")
        nav_layout.addWidget(self.axial_file_label)

        # Primary Navigation buttons
        button_row_1 = qt.QHBoxLayout()
        self.prev_button = qt.QPushButton("◀ Previous")
        self.next_no_save_button = qt.QPushButton("Next (No Save) ▶")
        self.reload_button = qt.QPushButton("⟳ Reload")

        button_row_1.addWidget(self.prev_button)
        button_row_1.addWidget(self.next_no_save_button)
        button_row_1.addWidget(self.reload_button)
        nav_layout.addLayout(button_row_1)

        button_row_2 = qt.QHBoxLayout()
        self.save_button = qt.QPushButton("💾 Save")
        self.save_button.setStyleSheet("font-weight: bold; padding: 6px;")

        self.save_next_button = qt.QPushButton("💾 Save + Next ▶")
        self.save_next_button.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 8px; "
            "background-color: #2E7D32; color: white; border-radius: 4px;"
        )
        button_row_2.addWidget(self.save_button)
        button_row_2.addWidget(self.save_next_button, 1)
        nav_layout.addLayout(button_row_2)

        # -------------------------------------------------------------
        # Section 3: Bounding Boxes Annotation & Review
        # -------------------------------------------------------------
        box_collapsible = ctk.ctkCollapsibleButton()
        box_collapsible.text = "3. Bounding Boxes (Markups ROI)"
        self.layout.addWidget(box_collapsible)
        box_layout = qt.QVBoxLayout(box_collapsible)

        finding_row = qt.QHBoxLayout()
        finding_row.addWidget(qt.QLabel("Finding type:"))
        self.finding_combo = qt.QComboBox()
        for letter, label, _class_id in FINDING_TYPES:
            self.finding_combo.addItem(f"{label}  ({letter})", letter)
        self.finding_combo.setCurrentIndex(0)
        self.finding_combo.setToolTip(
            "Saved as <letter>_<n>.mrk.json. PM nodule (R) is the default. "
            "R boxes ≥ 30 mm are labelled pm_confluent automatically on save."
        )
        finding_row.addWidget(self.finding_combo, 1)
        box_layout.addLayout(finding_row)

        # Box Action Buttons
        box_actions_row = qt.QHBoxLayout()
        self.add_box_button = qt.QPushButton("➕ Add Bounding Box (Shortcut: B)")
        self.add_box_button.setStyleSheet(
            "font-weight: bold; padding: 6px; background-color: #1976D2; color: white; border-radius: 4px;"
        )
        self.delete_box_button = qt.QPushButton("✖ Delete Selected")
        self.delete_all_button = qt.QPushButton("🗑 Delete All")

        box_actions_row.addWidget(self.add_box_button, 2)
        box_actions_row.addWidget(self.delete_box_button, 1)
        box_actions_row.addWidget(self.delete_all_button, 1)
        box_layout.addLayout(box_actions_row)

        # Box Table Widget
        self.box_table = qt.QTableWidget(0, 3)
        self.box_table.setHorizontalHeaderLabels(["Name", "Finding", "Size (W × L × H mm)"])
        self.box_table.horizontalHeader().setStretchLastSection(True)
        self.box_table.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.box_table.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.box_table.setFixedHeight(140)
        box_layout.addWidget(self.box_table)

        # Notes / PCI Score row
        notes_row = qt.QHBoxLayout()
        notes_row.addWidget(qt.QLabel("Notes / PCI:"))
        self.notes_edit = qt.QLineEdit()
        self.notes_edit.setPlaceholderText("Optional notes or PCI score for this case")
        notes_row.addWidget(self.notes_edit, 1)
        box_layout.addLayout(notes_row)

        # Workflow hint
        hint_label = qt.QLabel(
            "Workflow: Choose a finding type (PM nodule is default), press 'B', then click and drag "
            "in any slice. Files are saved as R_1.mrk.json, A_1.mrk.json, … . Use the large "
            "thickness buttons to switch series; if a thickness has no native COR/SAG, those views "
            "show reconstructions of the current axial volume."
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666; font-size: 11px; padding: 2px;")
        box_layout.addWidget(hint_label)

        # -------------------------------------------------------------
        # Section 4: Segmentation overlay
        # -------------------------------------------------------------
        seg_collapsible = ctk.ctkCollapsibleButton()
        seg_collapsible.text = "4. Segmentation"
        self.layout.addWidget(seg_collapsible)
        seg_layout = qt.QVBoxLayout(seg_collapsible)

        self.seg_path_picker = ctk.ctkPathLineEdit()
        self.seg_path_picker.filters = ctk.ctkPathLineEdit.Files | ctk.ctkPathLineEdit.Dirs
        self.seg_path_picker.nameFilters = ["Segmentations (*.seg.nrrd *.nii.gz *.nii)", "All files (*)"]
        self.seg_path_picker.toolTip = (
            "A single .seg.nrrd / .nii.gz file, or a folder of segmentations for this scan. "
            ".nii.gz files are loaded as labelmap segmentations (not as CT volumes)."
        )
        saved_seg = slicer.util.settingsValue(SETTINGS_KEY_SEG_PATH, "")
        if saved_seg:
            self.seg_path_picker.setCurrentPath(saved_seg)
        seg_layout.addWidget(qt.QLabel("Segmentation file or folder:"))
        seg_layout.addWidget(self.seg_path_picker)

        seg_btn_row = qt.QHBoxLayout()
        self.load_seg_button = qt.QPushButton("Load segmentation")
        self.load_seg_button.setStyleSheet(
            "font-weight: bold; padding: 8px; background-color: #6A1B9A; color: white; border-radius: 4px;"
        )
        self.clear_seg_button = qt.QPushButton("Clear overlay")
        seg_btn_row.addWidget(self.load_seg_button, 2)
        seg_btn_row.addWidget(self.clear_seg_button, 1)
        seg_layout.addLayout(seg_btn_row)

        self.seg_status_label = qt.QLabel("No segmentation loaded.")
        self.seg_status_label.setWordWrap(True)
        self.seg_status_label.setStyleSheet("color: #555; font-size: 11px;")
        seg_layout.addWidget(self.seg_status_label)

        # Add vertical stretch
        self.layout.addStretch(1)

        # Connect signals
        self._connect_signals()

        # Keyboard shortcuts for Add Bounding Box: 'b' and 'Shift+B'
        main_win = slicer.util.mainWindow()
        if main_win:
            self.shortcut_b = qt.QShortcut(qt.QKeySequence("b"), main_win)
            self.shortcut_b.activated.connect(self.on_add_box_clicked)
            self.shortcut_shift_b = qt.QShortcut(qt.QKeySequence("Shift+b"), main_win)
            self.shortcut_shift_b.activated.connect(self.on_add_box_clicked)

        # Auto-scan if paths already set
        if saved_input and os.path.isdir(saved_input):
            self.on_scan_clicked()

    def _connect_signals(self):
        self.input_dir_picker.currentPathChanged.connect(self.on_input_dir_changed)
        self.output_dir_picker.currentPathChanged.connect(self.on_output_dir_changed)
        self.skip_completed_checkbox.toggled.connect(self.on_skip_completed_toggled)
        self.scan_button.clicked.connect(self.on_scan_clicked)

        self.case_combobox.currentIndexChanged.connect(self.on_case_combobox_changed)
        self.prev_button.clicked.connect(self.on_prev_clicked)
        self.next_no_save_button.clicked.connect(self.on_next_no_save_clicked)
        self.reload_button.clicked.connect(self.on_reload_clicked)
        self.save_button.clicked.connect(self.on_save_clicked)
        self.save_next_button.clicked.connect(self.on_save_next_clicked)
        self.thickness_button_group.buttonClicked.connect(self.on_thickness_button_clicked)

        self.add_box_button.clicked.connect(self.on_add_box_clicked)
        self.seg_path_picker.currentPathChanged.connect(self.on_seg_path_changed)
        self.load_seg_button.clicked.connect(self.on_load_segmentation_clicked)
        self.clear_seg_button.clicked.connect(self.on_clear_segmentation_clicked)
        self.delete_box_button.clicked.connect(self.on_delete_box_clicked)
        self.delete_all_button.clicked.connect(self.on_delete_all_clicked)
        self.box_table.itemSelectionChanged.connect(self.on_box_table_selection_changed)

    def cleanup(self):
        if self.shortcut_b:
            self.shortcut_b.disconnect()
            self.shortcut_b = None
        if self.shortcut_shift_b:
            self.shortcut_shift_b.disconnect()
            self.shortcut_shift_b = None

    def on_input_dir_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_INPUT_DIR, new_path)

    def on_output_dir_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_OUTPUT_DIR, new_path)

    def on_seg_path_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_SEG_PATH, new_path)

    def on_skip_completed_toggled(self, checked: bool):
        slicer.app.settings().setValue(SETTINGS_KEY_SKIP_COMPLETED, checked)

    def set_status(self, text: str, is_error: bool = False):
        self.status_label.setText(text)
        if is_error:
            self.status_label.setStyleSheet("color: #b00020; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("color: #2E7D32;")
        print(text)

    def on_scan_clicked(self):
        """Scan input directory, build case list, and initialize navigation."""
        input_path_str = self.input_dir_picker.currentPath.strip()
        output_path_str = self.output_dir_picker.currentPath.strip()

        if not input_path_str or not os.path.isdir(input_path_str):
            self.set_status("Please select a valid input scans folder.", is_error=True)
            return

        input_dir = Path(input_path_str)
        output_dir = Path(output_path_str) if output_path_str else (input_dir.parent / "annotations")
        if not output_path_str:
            self.output_dir_picker.setCurrentPath(str(output_dir))

        try:
            cases, warnings = self.logic.scan_folder(input_dir, output_dir)
        except Exception as exc:
            self.set_status(f"Scan failed: {exc}", is_error=True)
            slicer.util.errorDisplay(f"Scan failed:\n{exc}")
            return

        if not cases:
            self.set_status(f"No supported medical scans found in {input_dir}.", is_error=True)
            self._update_progress_summary()
            self._populate_case_combobox()
            return

        self._populate_case_combobox()
        self._update_progress_summary()

        warning_info = f" ({len(warnings)} warning(s))" if warnings else ""
        self.set_status(f"Scanned {len(cases)} case(s) successfully{warning_info}.")

        # Auto-load initial case
        skip_completed = self.skip_completed_checkbox.isChecked()
        initial_idx = self.logic.find_next_case_index(-1, step=1, skip_completed=skip_completed)
        if initial_idx is None:
            initial_idx = 0
            if skip_completed:
                self.set_status("All cases appear completed! You can review them with 'Skip completed' unchecked.")

        self.load_case_index(initial_idx)

    def _update_progress_summary(self):
        summary = self.logic.get_status_summary()
        self.progress_label.setText(
            f"Total: {summary['total']}  |  Completed: {summary['completed']}  |  Remaining: {summary['pending']}"
        )

    def _populate_case_combobox(self):
        self._updating_ui = True
        self.case_combobox.clear()
        total = len(self.logic.cases)
        for idx, c in enumerate(self.logic.cases):
            case_id = c["case_id"]
            completed = self.logic.is_case_completed(case_id)
            status_mark = "✓ " if completed else "   "
            item_text = f"[{idx + 1:02d}/{total:02d}] {status_mark}{case_id}"
            self.case_combobox.addItem(item_text, idx)
        self._updating_ui = False

    def load_case_index(self, index: int):
        """Load case and update all UI views."""
        if self._is_loading_case:
            return
        if index < 0 or index >= len(self.logic.cases):
            return

        self._is_loading_case = True
        try:
            result = self.logic.load_case(index)
        except Exception as exc:
            self.set_status(f"Failed to load case {index + 1}: {exc}", is_error=True)
            traceback.print_exc()
            slicer.util.errorDisplay(f"Failed to load case {index + 1}:\n{exc}")
            return
        finally:
            self._is_loading_case = False

        case_info = result["case_info"]
        case_id = case_info["case_id"]

        self._updating_ui = True
        self.case_combobox.setCurrentIndex(index)
        self._rebuild_thickness_buttons(case_info)
        self._updating_ui = False

        self.case_details_label.setText(
            f"Case id: {case_id}\n"
            f"Scan number: {index + 1} out of {len(self.logic.cases)}"
        )
        self._update_plane_warning(result.get("load_warnings") or [])
        self._update_axial_file_label()

        self.notes_edit.setText(result.get("notes", ""))
        self.refresh_box_table()
        self._update_progress_summary()
        self._try_autoload_segmentations()

        warning_note = ""
        if result.get("load_warnings"):
            warning_note = " " + result["load_warnings"][0]
        self.set_status(f"Loaded case '{case_id}'. Press 'B' to add bounding boxes.{warning_note}")

    def _rebuild_thickness_buttons(self, case_info: Dict[str, Any]) -> None:
        while self.thickness_buttons_layout.count():
            item = self.thickness_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget:
                self.thickness_button_group.removeButton(widget)
                widget.deleteLater()
        self._thickness_buttons = {}

        volumes = case_info.get("volumes") or {}
        thicknesses = case_info.get("axial_thicknesses") or available_axial_thicknesses(volumes)
        active = self.logic.current_axial_thickness or case_info.get("default_axial_thickness")
        if not thicknesses:
            placeholder = qt.QLabel("No axial thicknesses found for this case.")
            self.thickness_buttons_layout.addWidget(placeholder)
            return

        for thickness in thicknesses:
            button = qt.QPushButton(thickness_display_label(thickness))
            button.setCheckable(True)
            button.setMinimumHeight(32)
            button.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
            button.setStyleSheet(
                "QPushButton { font-weight: bold; font-size: 12px; padding: 6px 10px; "
                "border-radius: 4px; border: 2px solid #90A4AE; }"
                "QPushButton:checked { background-color: #1565C0; color: white; border-color: #0D47A1; }"
            )
            button.setProperty("thickness", thickness)
            if thickness.upper() == "TS":
                button.setToolTip(
                    "Show the original axial thin-slice scan. Coronal and sagittal become "
                    "reconstructions of this axial volume."
                )
            else:
                button.setToolTip(
                    f"Show the {thickness_display_label(thickness)} series. "
                    "Native COR/SAG are used when that thickness exists; otherwise those "
                    "views are reconstructed from this axial volume."
                )
            self.thickness_button_group.addButton(button)
            self.thickness_buttons_layout.addWidget(button)
            self._thickness_buttons[thickness] = button
            if thickness == active:
                button.setChecked(True)

        if active and active in self._thickness_buttons:
            self._thickness_buttons[active].setChecked(True)
        elif self._thickness_buttons:
            next(iter(self._thickness_buttons.values())).setChecked(True)

    def _update_plane_warning(self, warnings: List[str]) -> None:
        if warnings:
            self.plane_warning_label.setText("Warning: " + " ".join(warnings))
            self.plane_warning_label.setVisible(True)
        else:
            self.plane_warning_label.setText("")
            self.plane_warning_label.setVisible(False)

    def _update_axial_file_label(self) -> None:
        filename = self.logic.active_axial_filename()
        thickness = self.logic.current_axial_thickness
        if not filename:
            self.axial_file_label.setText("Showing: —")
            return
        extra = ""
        if self.logic.cor_sag_are_reconstructions():
            extra = " — coronal and sagittal are reconstructions of this axial scan"
        label = thickness_display_label(thickness) if thickness else "axial"
        self.axial_file_label.setText(f"Showing {label}: {filename}{extra}")

    def on_thickness_button_clicked(self, button) -> None:
        if self._updating_ui:
            return
        if self.logic.current_case is None:
            return
        if isinstance(button, int):
            button = self.thickness_button_group.button(button)
        if button is None:
            return
        thickness = button.property("thickness")
        if not thickness:
            return
        try:
            self.logic.set_slice_thickness(str(thickness))
            self._update_axial_file_label()
            self._update_plane_warning(self.logic.load_warnings)
            if self.logic.cor_sag_are_reconstructions():
                self.set_status(
                    f"Switched to {thickness_display_label(str(thickness))}. "
                    "Coronal and sagittal show reconstructions of the current axial scan."
                )
            else:
                self.set_status(
                    f"Switched to native {thickness_display_label(str(thickness))} AX/COR/SAG."
                )
        except Exception as exc:
            self.set_status(f"Could not switch slice thickness: {exc}", is_error=True)

    def refresh_box_table(self):
        """Populate the table widget with current ROI nodes and dimensions."""
        self.box_table.setRowCount(0)
        for idx, roi in enumerate(self.logic.current_roi_nodes):
            self.box_table.insertRow(idx)

            name = roi.GetName() if roi else f"R_{idx + 1}"
            name_item = qt.QTableWidgetItem(name)
            name_item.setData(qt.Qt.UserRole, roi)

            finding_text = self.logic.finding_label(roi) if roi else "PM nodule"
            size_text = "(not placed)"
            if roi and self.logic.is_roi_valid(roi):
                size = roi.GetSize()
                size_text = f"{size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f}"

            finding_item = qt.QTableWidgetItem(finding_text)
            size_item = qt.QTableWidgetItem(size_text)

            self.box_table.setItem(idx, 0, name_item)
            self.box_table.setItem(idx, 1, finding_item)
            self.box_table.setItem(idx, 2, size_item)

    def on_box_table_selection_changed(self):
        """When an ROI row is selected in the table, jump slice viewers to its center."""
        selected_rows = self.box_table.selectedItems()
        if not selected_rows:
            return
        item = self.box_table.item(selected_rows[0].row(), 0)
        if item:
            roi_node = item.data(qt.Qt.UserRole)
            if roi_node and self.logic.is_roi_valid(roi_node):
                self.logic.jump_to_roi(roi_node)

    def on_case_combobox_changed(self, combo_idx: int):
        if self._updating_ui:
            return
        if combo_idx >= 0:
            case_index = self.case_combobox.itemData(combo_idx)
            if case_index is not None and case_index != self.logic.current_index:
                self.load_case_index(case_index)

    def on_add_box_clicked(self):
        """Add new bounding box ROI and enter interactive placement."""
        if self.logic.current_case is None:
            self.set_status("Scan and load a case before adding bounding boxes.", is_error=True)
            return
        try:
            letter = self.finding_combo.itemData(self.finding_combo.currentIndex) or DEFAULT_FINDING_LETTER
            roi_node = self.logic.add_bounding_box(letter=str(letter))
            self.refresh_box_table()
            self.set_status(
                f"Placing '{roi_node.GetName()}' ({self.logic.finding_label(roi_node)}). "
                "Click and drag in any slice viewer to draw box."
            )
        except Exception as exc:
            self.set_status(f"Error adding bounding box: {exc}", is_error=True)

    def on_delete_box_clicked(self):
        """Delete currently selected bounding box."""
        selected_items = self.box_table.selectedItems()
        if not selected_items:
            self.set_status("Select a bounding box in the table to delete.")
            return
        row = selected_items[0].row()
        item = self.box_table.item(row, 0)
        if item:
            roi_node = item.data(qt.Qt.UserRole)
            if roi_node:
                self.logic.delete_roi_node(roi_node)
                self.refresh_box_table()
                self.set_status("Deleted selected bounding box.")

    def on_delete_all_clicked(self):
        """Delete all bounding boxes for current case."""
        if not self.logic.current_roi_nodes:
            return
        confirmed = slicer.util.confirmYesNoDisplay(
            "Are you sure you want to delete ALL bounding boxes for this case?"
        )
        if confirmed:
            self.logic.delete_all_roi_nodes()
            self.refresh_box_table()
            self.set_status("Deleted all bounding boxes.")

    def on_save_clicked(self) -> bool:
        """Save current case annotations."""
        if self._is_saving:
            return False
        if self.logic.current_case is None:
            self.set_status("No case loaded to save.", is_error=True)
            return False

        self._is_saving = True
        try:
            notes = self.notes_edit.text.strip()
            saved = self.logic.save_current_case(
                notes=notes,
                confirm_zero_boxes_fn=slicer.util.confirmYesNoDisplay,
            )
            if not saved:
                self.set_status("Save canceled by user.")
                return False

            self.refresh_box_table()
            self._populate_case_combobox()
            self._update_progress_summary()
            case_id = self.logic.current_case["case_id"]
            num_boxes = len(self.logic.current_roi_nodes)
            self.set_status(f"Saved case '{case_id}' successfully ({num_boxes} boxes).")
            return True
        except Exception as exc:
            self.set_status(f"Save failed: {exc}", is_error=True)
            traceback.print_exc()
            slicer.util.errorDisplay(f"Save failed for case '{self.logic.current_case['case_id']}':\n{exc}")
            return False
        finally:
            self._is_saving = False

    def on_save_next_clicked(self):
        """Save current case and advance to next."""
        if self._is_saving:
            return
        if self.on_save_clicked():
            self._go_to_next_case()

    def on_next_no_save_clicked(self):
        """Advance to next case without saving."""
        self._go_to_next_case()

    def _go_to_next_case(self):
        skip_completed = self.skip_completed_checkbox.isChecked()
        next_idx = self.logic.find_next_case_index(
            self.logic.current_index, step=1, skip_completed=skip_completed
        )
        if next_idx is None:
            if skip_completed:
                msg = "No more uncompleted cases! All scanned scans are done."
            else:
                msg = "Reached the end of the case list."
            self.set_status(msg)
            slicer.util.infoDisplay(msg)
            return
        self.load_case_index(next_idx)

    def on_prev_clicked(self):
        """Go to previous case."""
        skip_completed = self.skip_completed_checkbox.isChecked()
        prev_idx = self.logic.find_next_case_index(
            self.logic.current_index, step=-1, skip_completed=skip_completed
        )
        if prev_idx is None:
            self.set_status("Already at the first case.")
            return
        self.load_case_index(prev_idx)

    def on_reload_clicked(self):
        """Reload current case from disk."""
        if self.logic.current_index >= 0:
            self.load_case_index(self.logic.current_index)

    def _try_autoload_segmentations(self) -> None:
        path_str = self.seg_path_picker.currentPath.strip()
        if not path_str or not os.path.exists(path_str):
            self.seg_status_label.setText("No segmentation loaded.")
            return
        path = Path(path_str)
        if not path.is_dir():
            self.seg_status_label.setText("No segmentation loaded. Click Load to overlay a file.")
            return
        case = self.logic.current_case
        if case is None:
            return
        try:
            names = self.logic.load_segmentations_from_path(
                path, case_id=case["case_id"], require_case_match=True
            )
            self.seg_status_label.setText(
                f"Loaded {len(names)} segmentation(s) for case {case['case_id']}: " + ", ".join(names)
            )
        except Exception as exc:
            self.seg_status_label.setText(f"No matching segmentation in folder ({exc}).")

    def on_load_segmentation_clicked(self):
        if self.logic.current_case is None:
            self.set_status("Load a case before adding a segmentation overlay.", is_error=True)
            return
        path_str = self.seg_path_picker.currentPath.strip()
        if not path_str or not os.path.exists(path_str):
            self.set_status("Select a segmentation file or folder first.", is_error=True)
            return
        try:
            names = self.logic.load_segmentations_from_path(
                Path(path_str), case_id=self.logic.current_case["case_id"]
            )
            self.seg_status_label.setText(f"Loaded {len(names)} segmentation(s): " + ", ".join(names))
            self.set_status(f"Loaded segmentation overlay ({len(names)}). .nii.gz files were imported as labelmaps.")
        except Exception as exc:
            self.set_status(f"Failed to load segmentation: {exc}", is_error=True)
            slicer.util.errorDisplay(f"Failed to load segmentation:\n{exc}")

    def on_clear_segmentation_clicked(self):
        self.logic.clear_segmentations()
        self.seg_status_label.setText("No segmentation loaded.")
        self.set_status("Cleared segmentation overlay.")
