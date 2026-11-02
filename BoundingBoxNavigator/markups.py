"""Bounding-box names, findings, and the save plan. No 3D Slicer import."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from filenames import markup_stem, safe_markup_filename

ROI_ATTR_LETTER = "BBN_finding_letter"
ROI_ATTR_SOURCE_FILE = "BBN_source_filename"
ROI_ATTR_CONVENTION = "BBN_uses_convention"
ROI_ATTR_FINDING_TEXT = "BBN_finding_text"
MRK_ANNOTATION_KEY = "BoundingBoxNavigator"
PM_CONFLUENT_THRESHOLD_MM = 30.0

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

MARKUP_NAME_RE = re.compile(r"^([RAOSL])(?:_(\d+))?$", re.IGNORECASE)
CONVENTION_INDEX_RE = re.compile(r"^([RAOSL])_(\d+)", re.IGNORECASE)

FINDING_ALIASES = {
    "r": "R",
    "pm nodule": "R",
    "pm nodules": "R",
    "pm confluent": "R",
    "peritoneal deposit": "R",
    "a": "A",
    "ascites": "A",
    "o": "O",
    "omental cake": "O",
    "s": "S",
    "stranding": "S",
    "fat stranding": "S",
    "l": "L",
    "lymph node": "L",
    "lymph nodes": "L",
    "lymphadenopathy": "L",
}


def used_convention_numbers(names: Iterable[str], letter: str) -> set:
    """Indexes already taken by convention names such as R_1. A bare letter counts as 1."""
    used = set()
    letter = (letter or "").upper()
    for name in names:
        text = name or ""
        match = CONVENTION_INDEX_RE.match(text)
        if match and match.group(1).upper() == letter:
            used.add(int(match.group(2)))
        elif text.upper() == letter:
            used.add(1)
    return used


def next_convention_name(
    letter: str, names: Iterable[str], reserved_filenames: Optional[Iterable[str]] = None
) -> str:
    """Next free <class>_<n> name. Numbering starts at 1."""
    letter = (letter or DEFAULT_FINDING_LETTER).upper()
    if letter not in FINDING_LABEL_BY_LETTER:
        letter = DEFAULT_FINDING_LETTER
    extra = list(names)
    for filename in reserved_filenames or ():
        extra.append(markup_stem(filename))
    used = used_convention_numbers(extra, letter)
    number = 1
    while number in used:
        number += 1
    return f"{letter}_{number}"


def finding_text_for_save(letter: str, finding_text: str) -> str:
    """Typed finding text wins. Otherwise the label for the class letter."""
    custom = (finding_text or "").strip()
    if custom:
        return custom
    letter = (letter or "").upper()
    if letter:
        return FINDING_LABEL_BY_LETTER.get(letter, "")
    return ""


def software_class_for_box(letter: str, finding_text: str, max_size_mm: Optional[float]) -> str:
    """R boxes at least 30 mm are pm_confluent. A custom label with no letter is kept."""
    letter = (letter or "").upper()
    if not letter:
        custom = (finding_text or "").strip()
        return custom or "existing"
    if letter == "R" and max_size_mm is not None and max_size_mm >= PM_CONFLUENT_THRESHOLD_MM:
        return "pm_confluent"
    return FINDING_CLASS_BY_LETTER.get(letter, "pm_nodule")


def annotation_dict(letter: str, finding_text: str, max_size_mm: Optional[float]) -> Dict[str, str]:
    """Metadata written back into the markup file."""
    return {
        "finding": finding_text_for_save(letter, finding_text),
        "finding_letter": (letter or "").upper(),
        "software_class": software_class_for_box(letter, finding_text, max_size_mm),
    }


def plan_markup_files(entries: List[dict], protected: Iterable[str] = ()) -> List[dict]:
    """
    Filenames for one case.

    An empty name becomes the next convention name, or the stem of a preserved
    source file. A name the physician set is kept. Visibility does not drop a box.
    Protected filenames (files in the folder that could not be loaded) are never reused.
    Each result includes filename, the stored name, and the finding text.
    """
    reserved: set = set(protected)
    assigned: List[str] = []
    planned: List[dict] = []

    def current_name(entry: dict) -> str:
        return (entry.get("name") or "").strip()

    for index, entry in enumerate(entries):
        letter = (entry.get("letter") or "").upper()
        name = current_name(entry)
        if not name:
            source = (entry.get("source_filename") or "").strip()
            if source.lower().endswith(".mrk.json"):
                name = markup_stem(source)
            else:
                others = [
                    current_name(other)
                    for other_index, other in enumerate(entries)
                    if other_index != index and current_name(other)
                ]
                name = next_convention_name(letter or DEFAULT_FINDING_LETTER, others + assigned)
        filename = safe_markup_filename(name)
        if filename in reserved:
            stem = markup_stem(filename)
            suffix = 2
            while f"{stem}_{suffix}.mrk.json" in reserved:
                suffix += 1
            filename = f"{stem}_{suffix}.mrk.json"
            name = markup_stem(filename)
        reserved.add(filename)
        assigned.append(name)
        finding = finding_text_for_save(letter, entry.get("finding_text") or "")
        planned.append(
            {
                **entry,
                "name": name,
                "filename": filename,
                "letter": letter,
                "finding": finding,
                "visible": bool(entry.get("visible", True)),
            }
        )
    return planned


def read_annotation_from_mrk(mrk_file: Path) -> Dict[str, str]:
    """Read finding metadata from a markup file. Missing data returns an empty dict."""
    try:
        data = json.loads(Path(mrk_file).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get(MRK_ANNOTATION_KEY)
    if not isinstance(block, dict):
        markups = data.get("markups")
        if isinstance(markups, list) and markups and isinstance(markups[0], dict):
            block = markups[0].get(MRK_ANNOTATION_KEY)
            if not isinstance(block, dict):
                control_points = markups[0].get("controlPoints")
                if (
                    isinstance(control_points, list)
                    and control_points
                    and isinstance(control_points[0], dict)
                ):
                    description = str(control_points[0].get("description") or "").strip()
                    return {"finding": description} if description else {}
                return {}
        else:
            return {}
    finding = str(block.get("finding") or "").strip()
    letter = str(block.get("finding_letter") or "").strip()
    software_class = str(block.get("software_class") or "").strip()
    out: Dict[str, str] = {}
    if finding:
        out["finding"] = finding
    if letter:
        out["finding_letter"] = letter
    if software_class:
        out["software_class"] = software_class
    return out


def embed_annotation_in_mrk_json(mrk_file: Path, annotation: Dict[str, str]) -> None:
    """Write finding metadata into the markup file (schema allows extra keys)."""
    mrk_file = Path(mrk_file)
    try:
        data = json.loads(mrk_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: could not read {mrk_file} to embed finding: {exc}")
        return
    if not isinstance(data, dict):
        return
    data[MRK_ANNOTATION_KEY] = annotation
    markups = data.get("markups")
    if isinstance(markups, list) and markups and isinstance(markups[0], dict):
        markups[0][MRK_ANNOTATION_KEY] = annotation
        control_points = markups[0].get("controlPoints")
        if (
            isinstance(control_points, list)
            and control_points
            and isinstance(control_points[0], dict)
        ):
            control_points[0]["description"] = annotation.get("finding") or ""
    try:
        mrk_file.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Warning: could not write finding into {mrk_file}: {exc}")


def letter_for_finding_text(text: str) -> str:
    """Map a typed finding to R/A/O/S/L. Unknown text returns an empty string."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if raw in ("", "—", "-"):
        return ""
    key = raw.lower().replace("-", " ").replace("_", " ")
    letter = FINDING_ALIASES.get(key, "")
    if letter:
        return letter
    for known_letter, label, class_id in FINDING_TYPES:
        if key == label.lower() or key == class_id.replace("_", " "):
            return known_letter
    return ""


class MarkupOps:
    """ROI naming and finding methods mixed into the logic class."""

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
        """Keep existing markup names. Only convention files get a finding letter."""
        stem = markup_stem(mrk_file)
        roi_node.SetAttribute(ROI_ATTR_SOURCE_FILE, mrk_file.name)
        match = MARKUP_NAME_RE.match(stem)
        if match:
            letter = match.group(1).upper()
            roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
            roi_node.SetAttribute(ROI_ATTR_CONVENTION, "1")
        else:
            roi_node.SetAttribute(ROI_ATTR_LETTER, "")
            roi_node.SetAttribute(ROI_ATTR_CONVENTION, "0")
        roi_node.SetName(stem)

    def _read_annotation_from_mrk(self, mrk_file: Path) -> Dict[str, str]:
        return read_annotation_from_mrk(mrk_file)

    def _restore_finding_from_saved(
        self,
        roi_node,
        mrk_file: Path,
        marker_finding: Optional[str] = None,
        marker_class: Optional[str] = None,
    ) -> None:
        """Reload finding text from the .mrk.json, then the case overview file."""
        annotation = self._read_annotation_from_mrk(mrk_file)
        finding = annotation.get("finding", "").strip()
        letter = annotation.get("finding_letter", "").strip().upper()
        if not finding:
            finding = str(marker_finding or "").strip()
        if not finding:
            fallback = str(marker_class or "").strip()
            if fallback and fallback not in ("existing",):
                finding = fallback
        if letter and letter in FINDING_LABEL_BY_LETTER and not finding:
            self.set_roi_finding(roi_node, letter)
            return
        if finding:
            self.set_roi_finding_text(roi_node, finding)

    def _write_finding_onto_roi(self, roi_node) -> None:
        """Keep the finding on the markup node so Slicer writes it into .mrk.json."""
        finding = self.finding_text_for_save(roi_node)
        try:
            if roi_node.GetNumberOfControlPoints() > 0:
                roi_node.SetNthControlPointDescription(0, finding)
        except Exception:
            pass

    def _embed_annotation_in_mrk_json(self, mrk_file: Path, annotation: Dict[str, str]) -> None:
        embed_annotation_in_mrk_json(mrk_file, annotation)

    def _uses_convention(self, roi_node) -> bool:
        try:
            flag = str(roi_node.GetAttribute(ROI_ATTR_CONVENTION) or "")
            if flag == "1":
                return True
            if flag == "0":
                return False
        except Exception:
            pass
        return bool(MARKUP_NAME_RE.match(roi_node.GetName() or ""))

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
        return ""

    def _used_convention_numbers(self, letter: str, exclude=None) -> set:
        used = set()
        for roi in self.current_roi_nodes:
            if roi is exclude:
                continue
            name = roi.GetName() or ""
            match = CONVENTION_INDEX_RE.match(name)
            if match and match.group(1).upper() == letter:
                used.add(int(match.group(2)))
            elif name.upper() == letter:
                used.add(1)
        return used

    def _next_convention_name(
        self, letter: str, exclude=None, reserved_filenames: Optional[set] = None
    ) -> str:
        names = []
        for roi in self.current_roi_nodes:
            if roi is exclude:
                continue
            names.append(roi.GetName() or "")
        return next_convention_name(letter, names, reserved_filenames)

    def rename_roi(self, roi_node, new_name: str) -> str:
        """Set the ROI display name from the table; empty names fall back to the previous name."""
        text = re.sub(r"\s+", " ", (new_name or "").strip())
        if not text:
            text = roi_node.GetName() or self._next_convention_name(
                self._roi_letter(roi_node) or DEFAULT_FINDING_LETTER, exclude=roi_node
            )
        roi_node.SetName(text)
        return text

    def _preserved_source_filename(self, roi_node) -> Optional[str]:
        try:
            name = str(roi_node.GetAttribute(ROI_ATTR_SOURCE_FILE) or "").strip()
        except Exception:
            name = ""
        if name.lower().endswith(".mrk.json"):
            return name
        return None

    def set_roi_finding(self, roi_node, letter: str) -> str:
        """Change the finding type without renaming the box."""
        letter = (letter or "").upper()
        if letter and letter not in FINDING_LABEL_BY_LETTER:
            letter = ""
        roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
        roi_node.SetAttribute(ROI_ATTR_CONVENTION, "1" if letter else "0")
        roi_node.SetAttribute(ROI_ATTR_FINDING_TEXT, FINDING_LABEL_BY_LETTER.get(letter, ""))
        self._write_finding_onto_roi(roi_node)
        return letter

    def set_roi_finding_text(self, roi_node, text: str) -> str:
        """Set a typed finding label. Known names/letters map to R/A/O/S/L; anything else is kept as-is."""
        raw = re.sub(r"\s+", " ", (text or "").strip())
        if raw in ("", "—", "-"):
            roi_node.SetAttribute(ROI_ATTR_LETTER, "")
            roi_node.SetAttribute(ROI_ATTR_CONVENTION, "0")
            roi_node.SetAttribute(ROI_ATTR_FINDING_TEXT, "")
            self._write_finding_onto_roi(roi_node)
            return ""

        key = raw.lower().replace("-", " ").replace("_", " ")
        letter = ""
        aliases = {
            "r": "R",
            "pm nodule": "R",
            "pm nodules": "R",
            "pm confluent": "R",
            "peritoneal deposit": "R",
            "a": "A",
            "ascites": "A",
            "o": "O",
            "omental cake": "O",
            "s": "S",
            "stranding": "S",
            "fat stranding": "S",
            "l": "L",
            "lymph node": "L",
            "lymph nodes": "L",
            "lymphadenopathy": "L",
        }
        letter = aliases.get(key, "")
        if not letter:
            for known_letter, label, class_id in FINDING_TYPES:
                if key == label.lower() or key == class_id.replace("_", " "):
                    letter = known_letter
                    break

        if letter:
            roi_node.SetAttribute(ROI_ATTR_LETTER, letter)
            roi_node.SetAttribute(ROI_ATTR_CONVENTION, "1")
            roi_node.SetAttribute(ROI_ATTR_FINDING_TEXT, FINDING_LABEL_BY_LETTER[letter])
            self._write_finding_onto_roi(roi_node)
            return self.finding_label(roi_node)

        roi_node.SetAttribute(ROI_ATTR_LETTER, "")
        roi_node.SetAttribute(ROI_ATTR_CONVENTION, "0")
        roi_node.SetAttribute(ROI_ATTR_FINDING_TEXT, raw)
        self._write_finding_onto_roi(roi_node)
        return raw

    def finding_label(self, roi_node) -> str:
        try:
            custom = str(roi_node.GetAttribute(ROI_ATTR_FINDING_TEXT) or "").strip()
        except Exception:
            custom = ""
        letter = self._roi_letter(roi_node)
        if letter:
            label = FINDING_LABEL_BY_LETTER.get(letter, "PM nodule")
            if letter == "R" and self.is_roi_valid(roi_node):
                max_dim = max(roi_node.GetSize())
                if max_dim >= PM_CONFLUENT_THRESHOLD_MM:
                    return "PM confluent (size >= 30mm)"
            return label
        if custom:
            return custom
        return ""

    def finding_text_for_save(self, roi_node) -> str:
        """Finding text stored with the ROI (typed note or canonical label)."""
        try:
            custom = str(roi_node.GetAttribute(ROI_ATTR_FINDING_TEXT) or "").strip()
        except Exception:
            custom = ""
        return finding_text_for_save(self._roi_letter(roi_node), custom)

    def roi_annotation_dict(self, roi_node) -> Dict[str, str]:
        return {
            "finding": self.finding_text_for_save(roi_node),
            "finding_letter": self._roi_letter(roi_node),
            "software_class": self.software_class_for_roi(roi_node),
        }

    def software_class_for_roi(self, roi_node) -> str:
        letter = self._roi_letter(roi_node)
        if not letter:
            try:
                custom = str(roi_node.GetAttribute(ROI_ATTR_FINDING_TEXT) or "").strip()
            except Exception:
                custom = ""
            return custom or "existing"
        if letter == "R" and self.is_roi_valid(roi_node):
            if max(roi_node.GetSize()) >= PM_CONFLUENT_THRESHOLD_MM:
                return "pm_confluent"
        return FINDING_CLASS_BY_LETTER.get(letter, "pm_nodule")

