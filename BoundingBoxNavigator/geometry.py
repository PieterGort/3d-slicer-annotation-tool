"""Geometry checks for volumes and bounding boxes. No 3D Slicer import.

Slicer stores a volume as a rectilinear grid in patient RAS millimetres. Image Origin
is the RAS position of IJK voxel (0, 0, 0). The columns of IJKToRAS include the
spacing. A Markups ROI center and size are also in RAS millimetres.

A volume is a dict with:
  label      text for messages, e.g. "AX 3mm"
  plane      "AX", "COR", or "SAG"
  thickness  "3mm", "2.5mm", or "TS"
  matrix     4x4 IJK-to-RAS matrix, row-major. Columns 0-2 include the voxel spacing.
  dims       [i, j, k] voxel counts

A view is a dict with:
  view           "Red", "Green", or "Yellow"
  label          volume shown in that view, e.g. "AX 3mm"
  plane          named plane of the view ("AX", "COR", "SAG")
  reconstructed  True when the view reslices another volume (usually AX)

A box is a tuple (name, center, size) in RAS millimetres.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

AXIS_NAMES = ("left-right", "anterior-posterior", "superior-inferior")
# Index into RAS where the slice normal of each plane should point.
AXIS_FOR_PLANE = {"SAG": 0, "COR": 1, "AX": 2}

DIRECTION_TOL = 0.01          # max difference of unit direction vectors (about 0.6 degrees)
IN_PLANE_SPACING_TOL_MM = 0.01
ORIGIN_TOL_MM = 0.5
THICKNESS_TOL_MM = 0.2
TS_MAX_SPACING_MM = 2.0
TILT_WARN_DEG = 5.0
MIN_OVERLAP = 0.5             # share of the smaller volume that must overlap the reference
MIN_BOX_COVERAGE = 0.999

Bounds = Tuple[List[float], List[float]]


def _column(matrix: Sequence[Sequence[float]], j: int) -> List[float]:
    return [float(matrix[r][j]) for r in range(3)]


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def _origin(matrix: Sequence[Sequence[float]]) -> List[float]:
    return [float(matrix[r][3]) for r in range(3)]


def _origin_offsets(
    reference: Sequence[Sequence[float]], other: Sequence[Sequence[float]]
) -> Tuple[float, float]:
    """In-plane and through-plane distance between two volume origins, in mm.

    The through-plane axis is the slice direction of the reference volume.
    """
    delta = [b - a for a, b in zip(_origin(reference), _origin(other))]
    normal = axes(reference)[2]
    through = sum(delta[i] * normal[i] for i in range(3))
    in_plane = [delta[i] - through * normal[i] for i in range(3)]
    return _norm(in_plane), abs(through)


def spacing(matrix: Sequence[Sequence[float]]) -> List[float]:
    """Voxel size along i, j, k in mm (length of each matrix column)."""
    return [_norm(_column(matrix, j)) for j in range(3)]


def axes(matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    """Unit direction vectors of i, j, k in RAS."""
    result: List[List[float]] = []
    for j in range(3):
        column = _column(matrix, j)
        length = _norm(column)
        result.append([x / length for x in column] if length > 0 else column)
    return result


def ras_bounds(matrix: Sequence[Sequence[float]], dims: Sequence[int]) -> Bounds:
    """Axis-aligned RAS box that covers all voxel centers of the volume."""
    origin = _origin(matrix)
    cols = [_column(matrix, j) for j in range(3)]
    corners = []
    for index in itertools.product(*[(0, max(int(d) - 1, 0)) for d in dims]):
        corners.append(
            [origin[r] + sum(cols[j][r] * index[j] for j in range(3)) for r in range(3)]
        )
    mins = [min(point[r] for point in corners) for r in range(3)]
    maxs = [max(point[r] for point in corners) for r in range(3)]
    return mins, maxs


def _volume_of(bounds: Bounds) -> float:
    mins, maxs = bounds
    result = 1.0
    for lo, hi in zip(mins, maxs):
        result *= max(0.0, hi - lo)
    return result


def _intersection(a: Bounds, b: Bounds) -> Bounds:
    mins = [max(a[0][r], b[0][r]) for r in range(3)]
    maxs = [min(a[1][r], b[1][r]) for r in range(3)]
    return mins, maxs


def overlap_fraction(a: Bounds, b: Bounds) -> float:
    """Share of the smaller box that lies inside the other box. A flat volume returns 1.0."""
    smaller = min(_volume_of(a), _volume_of(b))
    if smaller <= 0:
        return 1.0
    return _volume_of(_intersection(a, b)) / smaller


def box_bounds(center: Sequence[float], size: Sequence[float]) -> Bounds:
    half = [float(s) / 2.0 for s in size]
    return (
        [float(c) - h for c, h in zip(center, half)],
        [float(c) + h for c, h in zip(center, half)],
    )


def box_coverage(box: Bounds, volume: Bounds) -> float:
    """Share of the box that lies inside the volume (0.0 to 1.0)."""
    box_volume = _volume_of(box)
    if box_volume <= 0:
        return 1.0
    return _volume_of(_intersection(box, volume)) / box_volume


def item_from_volume_key(vol_key: str, matrix, dims) -> Dict[str, Any]:
    """Turn a key such as AX_3mm or AX_TS into a geometry item."""
    plane, thickness = str(vol_key).split("_", 1)
    return {
        "label": f"{plane} {thickness}",
        "plane": plane.upper(),
        "thickness": thickness,
        "matrix": matrix,
        "dims": list(dims),
    }


def expected_thickness_mm(thickness: str) -> Optional[float]:
    """3mm -> 3.0. TS and unknown labels return None (no size check)."""
    match = re.match(r"^(\d+(?:\.\d+)?)mm$", str(thickness).strip(), re.IGNORECASE)
    return float(match.group(1)) if match else None


def check_volume(item: Dict[str, Any]) -> List[str]:
    """Orientation and slice spacing of one volume, compared with its file name."""
    label = item["label"]
    plane = item["plane"]
    thickness = str(item["thickness"])
    matrix = item["matrix"]
    messages: List[str] = []

    normal = axes(matrix)[2]
    axis = max(range(3), key=lambda i: abs(normal[i]))
    if axis != AXIS_FOR_PLANE[plane]:
        messages.append(
            f"{label} is named {plane} but its slices run along the "
            f"{AXIS_NAMES[axis]} direction. Check the file."
        )
    else:
        tilt = math.degrees(math.acos(min(1.0, abs(normal[axis]))))
        if tilt > TILT_WARN_DEG:
            messages.append(
                f"{label} is tilted {tilt:.1f} degrees. Its {plane} view is slightly "
                "off the standard plane."
            )

    slice_mm = spacing(matrix)[2]
    if thickness.upper() == "TS":
        if slice_mm > TS_MAX_SPACING_MM:
            messages.append(f"{label} is named TS but its slices are {slice_mm:.2f} mm apart.")
    else:
        expected = expected_thickness_mm(thickness)
        if expected is not None and abs(slice_mm - expected) > THICKNESS_TOL_MM:
            messages.append(
                f"{label} is named {thickness} but its slices are {slice_mm:.2f} mm apart."
            )
    return messages


def compare_volumes(reference: Dict[str, Any], other: Dict[str, Any]) -> List[str]:
    """
    Compare a volume with the reference volume of the same case.

    Same plane (for example AX 3mm and AX TS): orientation, in-plane spacing, and origin
    must match. Different planes are not compared for orientation, because they are
    supposed to differ. Both: the two volumes must cover the same part of the scan.
    """
    ref_label = reference["label"]
    label = other["label"]
    messages: List[str] = []

    if reference["plane"] == other["plane"]:
        ref_axes = axes(reference["matrix"])
        other_axes = axes(other["matrix"])
        direction_diff = max(
            abs(a - b) for ra, oa in zip(ref_axes, other_axes) for a, b in zip(ra, oa)
        )
        if direction_diff > DIRECTION_TOL:
            messages.append(
                f"{label} has a different orientation from {ref_label}. "
                "Boxes drawn on one may not line up on the other."
            )

        ref_sp = spacing(reference["matrix"])
        other_sp = spacing(other["matrix"])
        if max(abs(ref_sp[i] - other_sp[i]) for i in (0, 1)) > IN_PLANE_SPACING_TOL_MM:
            messages.append(
                f"{label} has in-plane pixel spacing {other_sp[0]:.2f} x {other_sp[1]:.2f} mm, "
                f"but {ref_label} has {ref_sp[0]:.2f} x {ref_sp[1]:.2f} mm."
            )

        in_plane, _through = _origin_offsets(reference["matrix"], other["matrix"])
        if in_plane > ORIGIN_TOL_MM:
            messages.append(
                f"{label} starts {in_plane:.1f} mm away from {ref_label} in the slice plane."
            )

    ref_bounds = ras_bounds(reference["matrix"], reference["dims"])
    other_bounds = ras_bounds(other["matrix"], other["dims"])
    if overlap_fraction(ref_bounds, other_bounds) < MIN_OVERLAP:
        messages.append(
            f"{label} covers a different part of the scan than {ref_label}. "
            "Check that both files come from the same scan."
        )
    return messages


def reconstruction_warnings(views: Sequence[Dict[str, Any]]) -> List[str]:
    """
    Name reconstructed views and warn that boxes there are drawn on the source volume.

    Native views use the volume plane (RotateToVolumePlane). Reconstructed views use
    the standard axial, coronal, or sagittal orientation of the source volume.
    """
    reconstructed = [item for item in views if item.get("reconstructed")]
    if not reconstructed:
        return []

    names = [str(item.get("view") or "") for item in reconstructed]
    source_label = reconstructed[0].get("label") or "the axial volume"
    name_text = " and ".join(name for name in names if name)
    if len(names) == 1:
        messages = [
            f"{name_text} is a reconstruction of {source_label}, not a native scan. "
            f"Boxes in that view are drawn on {source_label}."
        ]
    else:
        messages = [
            f"{name_text} are reconstructions of {source_label}, not native scans. "
            f"Boxes in those views are drawn on {source_label}."
        ]

    native_other = [
        item
        for item in views
        if not item.get("reconstructed") and str(item.get("plane") or "").upper() != "AX"
    ]
    if native_other:
        native_text = " and ".join(
            f"{item.get('view')} ({item.get('label')})" for item in native_other
        )
        show = "shows" if len(native_other) == 1 else "show"
        messages.append(
            f"{native_text} {show} a native volume. A box may appear there and not in "
            "the reconstructed view if those volumes do not cover the same region."
        )
    return messages


def box_warnings(
    boxes: List[Tuple[str, Sequence[float], Sequence[float]]],
    source_label: str,
    source_bounds: Bounds,
    view_text: str,
) -> List[str]:
    """Boxes that are outside or partly outside the volume a view shows."""
    messages: List[str] = []
    for name, center, size in boxes:
        coverage = box_coverage(box_bounds(center, size), source_bounds)
        if coverage <= 1e-6:
            messages.append(
                f"Box {name} is outside {source_label}, so it does not show in {view_text}."
            )
        elif coverage < MIN_BOX_COVERAGE:
            messages.append(
                f"Box {name} is partly outside {source_label}. "
                f"The part outside does not show in {view_text}."
            )
    return messages
