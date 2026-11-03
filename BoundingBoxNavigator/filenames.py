"""Filename rules for scans and markup files. No 3D Slicer import."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CT_ABDOMEN_WINDOW = 350.0
CT_ABDOMEN_LEVEL = 40.0

SETTINGS_KEY_INPUT_DIR = "BoundingBoxNavigator/InputDir"
SETTINGS_KEY_OUTPUT_DIR = "BoundingBoxNavigator/OutputDir"
SETTINGS_KEY_SKIP_COMPLETED = "BoundingBoxNavigator/SkipCompleted"
SETTINGS_KEY_SEG_PATH = "BoundingBoxNavigator/SegmentationPath"

COMPLETION_MARKER_FILENAME = "_annotation_done.json"
SEGMENTATION_FILE_SUFFIXES = (".seg.nrrd", ".nii.gz", ".nii")

SUPPORTED_EXTENSIONS = (
    ".nii.gz",
    ".nii",
    ".nrrd",
    ".nhdr",
    ".mha",
    ".mhd",
)

# {case_id}_{AX|COR|SAG}_{phase}_{thickness}
# or {case_id}_{scan_number}_{AX|COR|SAG}_{phase}_{thickness}
DEFAULT_SCAN_NUMBER = "1"

SCAN_FILENAME_RE = re.compile(
    r"^(?P<base_id>[^_]+)(?:_(?P<scan_number>\d+))?_(?P<plane>AX|COR|SAG)_(?P<phase>[^_]+)_(?P<thickness>[^_]+)$",
    re.IGNORECASE,
)

PLANE_TO_VIEW = {"AX": "Red", "COR": "Green", "SAG": "Yellow"}
AXIAL_3MM_KEY = "AX_3mm"
AXIAL_TS_KEY = "AX_TS"
CORONAL_3MM_KEY = "COR_3mm"
SAGITTAL_3MM_KEY = "SAG_3mm"


def safe_markup_filename(name: str) -> str:
    """Turn an ROI display name into a .mrk.json filename, keeping spaces."""
    text = re.sub(r"[\s]+", " ", (name or "").strip())
    text = text.replace("/", "-").replace("\\", "-").replace("\0", "")
    if not text:
        text = "box"
    return f"{text}.mrk.json"


def markup_stem(path: Path | str) -> str:
    """Stem of a .mrk.json markup file (handles 'nodule 1.mrk.json' and 'R_1.mrk.json')."""
    name = Path(path).name
    lower = name.lower()
    if lower.endswith(".mrk.json"):
        return name[: -len(".mrk.json")]
    if lower.endswith(".json"):
        return name[: -len(".json")]
    return get_clean_stem(Path(path))


def get_clean_stem(path: Path) -> str:
    """Extract file stem handling compound extensions like .nii.gz."""
    name = path.name
    for ext in (".nii.gz", ".nhdr.gz", ".tar.gz"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return path.stem


def case_id_in_text(name: str) -> Optional[str]:
    """A 4-5 digit case id in a folder name, or None when the name has none."""
    match = re.search(r"(?<!\d)\d{4,5}(?!\d)", name or "")
    return match.group(0) if match else None


def enclosing_case_folder(file_path: Path, root: Path) -> Optional[Tuple[str, str]]:
    """
    Nearest folder at or below root whose name contains a case id.

    Returns (folder name, case id). The walk includes root and does not go above it.
    """
    root = Path(root).resolve()
    current = Path(file_path).resolve().parent
    if current != root and root not in current.parents:
        return None
    while True:
        found = case_id_in_text(current.name)
        if found:
            return current.name, found
        if current == root or current.parent == current:
            return None
        current = current.parent


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


def normalize_scan_number(value: str) -> str:
    """
    Plain scan number. A missing number means scan 1, so '02' becomes '2' and '' becomes '1'.
    """
    text = str(value or "").strip()
    if not text:
        return DEFAULT_SCAN_NUMBER
    if text.isdigit():
        return str(int(text))
    return text


def case_key(base_id: str, scan_number: str = DEFAULT_SCAN_NUMBER) -> str:
    """
    Folder and case name. Scan 1 is the bare case id (1001), so files without a
    scan number and files named _1 are one case. Scan 2 and higher add the number (1001_2).
    """
    number = normalize_scan_number(scan_number)
    if number == DEFAULT_SCAN_NUMBER:
        return base_id
    return f"{base_id}_{number}"


def name_contains_case_key(name: str, case_id: str) -> bool:
    """
    True when name contains case_id as its own token.
    Case 1001 matches 'Scan 1001' and '1001_AX_...'. It does not match '1001_2' or '10012'.
    Case 1001_2 matches '1001_2' and 'Scan 1001_2'.
    """
    if not case_id:
        return False
    return (
        re.search(
            rf"(?<!\d){re.escape(case_id)}(?!_\d)(?!\d)",
            name,
        )
        is not None
    )


def parse_scan_filename(filename_or_path: Path | str) -> Optional[Dict[str, str]]:
    """
    Parse a volume filename.

    Accepted forms (the scan number is optional; both may occur in one folder):
      {case_id}_{AX|COR|SAG}_{phase}_{thickness}          (scan number 1)
      {case_id}_{scan_number}_{AX|COR|SAG}_{phase}_{thickness}

    case_id in the result is the bounding-box folder name: '1001' or '1001_2'.
    scan_number is always set: '1' when the filename has no scan number.
    Returns None if the name does not match.
    """
    path = Path(filename_or_path)
    stem = get_clean_stem(path)
    match = SCAN_FILENAME_RE.match(stem)
    if not match:
        return None
    plane = match.group("plane").upper()
    thickness = normalize_thickness(match.group("thickness"))
    base_id = match.group("base_id")
    scan_number = normalize_scan_number(match.group("scan_number") or "")
    return {
        "case_id": case_key(base_id, scan_number),
        "base_id": base_id,
        "scan_number": scan_number,
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
