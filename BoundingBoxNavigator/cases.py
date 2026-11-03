"""Group scan files into cases and choose output folders. No 3D Slicer import."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from filenames import (
    AXIAL_3MM_KEY,
    AXIAL_TS_KEY,
    COMPLETION_MARKER_FILENAME,
    SUPPORTED_EXTENSIONS,
    available_axial_thicknesses,
    default_axial_thickness,
    enclosing_case_folder,
    extract_case_id,
    name_contains_case_key,
    natural_sort_key,
    normalize_thickness,
    parse_scan_filename,
    plane_keys_for,
    primary_axial_key,
    DEFAULT_SCAN_NUMBER,
    thickness_display_label,
    volume_key,
)


def list_volume_files(input_dir: Path) -> List[Path]:
    """Recursive list of supported volume files, skipping dotfiles."""
    found_files: List[Path] = []
    for root, _, files in os.walk(str(input_dir)):
        for file_name in files:
            if file_name.startswith("."):
                continue
            lower_name = file_name.lower()
            for ext in SUPPORTED_EXTENSIONS:
                if lower_name.endswith(ext):
                    found_files.append(Path(root) / file_name)
                    break
    found_files.sort(key=lambda p: natural_sort_key(p.name))
    return found_files


def build_cases(
    found_files: List[Path], root: Optional[Path] = None
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Group volume files into cases.

    Native AX/COR/SAG files that share a case id and scan number are one case.
    A second file for the same plane and thickness is ignored. The warning names that file.
    A file whose case id does not match the case folder it sits in is still loaded,
    and the warning names that folder and file.
    A case with no axial volume is skipped.
    """
    warnings: List[str] = []

    def warn(message: str) -> None:
        warnings.append(message)
        print(message)

    grouped: Dict[str, Dict[str, Any]] = {}
    unmatched: List[Path] = []

    for file_path in found_files:
        parsed = parse_scan_filename(file_path)
        if parsed is None:
            unmatched.append(file_path)
            continue

        case_id = parsed["case_id"]
        vol_key = parsed["volume_key"]
        if root is not None:
            folder = enclosing_case_folder(file_path, root)
            if folder is not None and folder[1] != parsed["base_id"]:
                warn(
                    f"Check folder '{folder[0]}': {file_path.name} is case "
                    f"{parsed['base_id']}, not {folder[1]}."
                )

        if case_id not in grouped:
            grouped[case_id] = {
                "case_id": case_id,
                "base_id": parsed["base_id"],
                "scan_number": parsed["scan_number"],
                "volumes": {},
                "phase": parsed["phase"],
                "phases": set(),
            }
        grouped[case_id]["phases"].add(parsed["phase"])
        volumes: Dict[str, Path] = grouped[case_id]["volumes"]
        if vol_key in volumes:
            warn(
                f"Duplicate volume '{vol_key}' for case '{case_id}': "
                f"keeping '{volumes[vol_key].name}', ignoring '{file_path.name}'."
            )
            continue
        volumes[vol_key] = file_path

    for file_path in unmatched:
        base_id = extract_case_id(file_path)
        case_id = base_id
        if base_id in grouped:
            # Never overwrite a real case that already uses this id.
            suffix = 2
            while f"{base_id}_legacy{suffix}" in grouped:
                suffix += 1
            case_id = f"{base_id}_legacy{suffix}"
            warn(
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
            warn(
                f"Skipping case '{case_id}': no axial volume "
                f"(expected {AXIAL_3MM_KEY} or {AXIAL_TS_KEY})."
            )
            continue

        phases = sorted(rec.get("phases") or set())
        if len(phases) > 1:
            warn(
                f"Case '{case_id}' has more than one contrast phase ({', '.join(phases)}). "
                "Check that all its files belong to this scan."
            )

        missing_planes: List[str] = []
        if not plane_keys_for(volumes, "COR"):
            missing_planes.append("COR")
            warn(
                f"Case '{case_id}' has no coronal volume; "
                "coronal view will show a reconstruction from the axial scan."
            )
        if not plane_keys_for(volumes, "SAG"):
            missing_planes.append("SAG")
            warn(
                f"Case '{case_id}' has no sagittal volume; "
                "sagittal view will show a reconstruction from the axial scan."
            )

        primary_path = volumes[ax_key]
        axial_thicknesses = available_axial_thicknesses(volumes)
        default_thickness = default_axial_thickness(volumes)
        case_records.append(
            {
                "case_id": case_id,
                "base_id": rec.get("base_id", case_id),
                "scan_number": rec.get("scan_number") or DEFAULT_SCAN_NUMBER,
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
    return case_records, warnings


def scan_input_dir(input_dir: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Scan a directory tree and group the volume files into cases."""
    input_dir = Path(input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    return build_cases(list_volume_files(input_dir), root=input_dir)


def choose_output_dir(output_dir: Optional[Path], case_id: str) -> Optional[Path]:
    """
    Prefer <output>/<case_id>, then a folder whose name contains that full case key.
    Case 1001 does not match a folder named 1001_2.
    """
    if output_dir is None:
        return None
    output_dir = Path(output_dir)
    exact = output_dir / case_id
    if exact.is_dir():
        return exact
    if output_dir.is_dir():
        matches: List[Path] = []
        for child in output_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name.lower() == case_id.lower():
                return child
            if name_contains_case_key(child.name, case_id):
                matches.append(child)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            matches.sort(key=lambda p: natural_sort_key(p.name))
            return matches[0]
    return exact


def case_is_completed(case_dir: Optional[Path], case_id: str) -> bool:
    """True when the case folder has a matching completion marker or any markup file."""
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
    return len(list(case_dir.glob("*.mrk.json"))) > 0


def view_plan(volumes: Dict[str, Path], thickness: str, case_id: str) -> Dict[str, Any]:
    """
    Decide which file each slice view shows.

    Red is the axial file of this thickness. Green and Yellow use the native
    coronal and sagittal files of the same thickness when they exist. A missing
    plane is a reconstruction of that axial file.
    """
    thickness = normalize_thickness(thickness)
    ax_key = volume_key("AX", thickness)
    ax_path = volumes.get(ax_key)
    if ax_path is None:
        raise RuntimeError(f"Case '{case_id}' has no axial volume for thickness '{thickness}'.")

    cor_path = volumes.get(volume_key("COR", thickness))
    sag_path = volumes.get(volume_key("SAG", thickness))
    warnings: List[str] = []
    if cor_path is None:
        warnings.append(
            f"No native COR {thickness_display_label(thickness)} volume. "
            "Coronal view is a reconstruction of the current axial scan."
        )
    if sag_path is None:
        warnings.append(
            f"No native SAG {thickness_display_label(thickness)} volume. "
            "Sagittal view is a reconstruction of the current axial scan."
        )
    return {
        "thickness": thickness,
        "axial_path": ax_path,
        "cor_path": cor_path,
        "sag_path": sag_path,
        "warnings": warnings,
        "views": {
            "Red": {"path": ax_path, "reconstruct": False},
            "Green": {"path": cor_path or ax_path, "reconstruct": cor_path is None},
            "Yellow": {"path": sag_path or ax_path, "reconstruct": sag_path is None},
        },
    }
