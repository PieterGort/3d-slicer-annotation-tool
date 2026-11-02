"""Naming convention and save plan. Synthetic ids only."""

from pathlib import Path

from markups import (
    finding_text_for_save,
    next_convention_name,
    plan_markup_files,
)
from session import filenames_to_delete


def test_convention_names_start_at_one():
    assert next_convention_name("R", []) == "R_1"
    assert next_convention_name("R", ["R_1"]) == "R_2"
    assert next_convention_name("A", ["R_1", "A_1"]) == "A_2"


def test_manual_name_is_kept_and_a_hidden_box_is_still_saved():
    planned = plan_markup_files(
        [
            {"name": "", "letter": "R", "finding_text": "", "visible": True},
            {"name": "upper nodule", "letter": "", "finding_text": "note", "visible": True},
            {"name": "", "letter": "R", "finding_text": "PM nodule", "visible": False},
        ]
    )
    assert [item["filename"] for item in planned] == [
        "R_1.mrk.json",
        "upper nodule.mrk.json",
        "R_2.mrk.json",
    ]
    assert planned[2]["visible"] is False
    assert " " not in planned[0]["filename"]
    assert planned[1]["name"] == "upper nodule"


def test_finding_stored_on_the_box_is_written_into_the_plan():
    assert finding_text_for_save("A", "") == "Ascites"
    planned = plan_markup_files(
        [{"name": "A_1", "letter": "A", "finding_text": "Ascites", "visible": True}]
    )
    assert planned[0]["finding"] == "Ascites"
    assert planned[0]["filename"] == "A_1.mrk.json"


def test_replaced_markup_files_are_the_ones_removed_from_the_folder():
    planned = plan_markup_files(
        [{"name": "R_1", "letter": "R", "finding_text": "", "visible": True}]
    )
    removed = filenames_to_delete(["R_1.mrk.json", "old box.mrk.json"], planned)
    assert removed == ["old box.mrk.json"]


def test_protected_file_is_never_overwritten_by_a_new_box():
    planned = plan_markup_files(
        [{"name": "R_1", "letter": "R", "finding_text": "", "visible": True}],
        protected=["R_1.mrk.json"],
    )
    assert planned[0]["filename"] == "R_1_2.mrk.json"


def test_saved_names_show_up_in_the_case_folder(tmp_path: Path):
    planned = plan_markup_files(
        [
            {"name": "", "letter": "R", "finding_text": "PM nodule", "visible": True},
            {"name": "manual note", "letter": "", "finding_text": "manual note", "visible": True},
        ]
    )
    case_dir = tmp_path / "1001"
    case_dir.mkdir()
    for item in planned:
        (case_dir / item["filename"]).write_text(item["finding"], encoding="utf-8")

    assert (case_dir / "R_1.mrk.json").read_text(encoding="utf-8") == "PM nodule"
    assert (case_dir / "manual note.mrk.json").read_text(encoding="utf-8") == "manual note"
    names = sorted(path.name for path in case_dir.glob("*.mrk.json"))
    assert names == ["R_1.mrk.json", "manual note.mrk.json"]
