"""Delete rule, unsaved-change check, and notice text. Synthetic names only."""

from session import differs_from_saved, filenames_to_delete, format_notice, state_signature


def test_protected_files_stay_in_the_folder_on_save():
    planned = [{"filename": "R_1.mrk.json"}]
    existing = ["R_1.mrk.json", "unreadable.mrk.json", "old box.mrk.json"]
    removed = filenames_to_delete(existing, planned, protected=["unreadable.mrk.json"])
    assert removed == ["old box.mrk.json"]


def test_without_protection_unplanned_files_are_removed():
    planned = [{"filename": "R_1.mrk.json"}]
    assert filenames_to_delete(["R_1.mrk.json", "R_2.mrk.json"], planned) == ["R_2.mrk.json"]


def test_no_baseline_means_no_unsaved_changes():
    assert differs_from_saved(None, state_signature([], "")) is False


def test_same_boxes_and_notes_are_not_a_change():
    boxes = [{"name": "R_1", "finding": "PM nodule", "visible": True, "center": [1.0], "size": [2.0]}]
    baseline = state_signature(boxes, "PCI 3")
    assert differs_from_saved(baseline, state_signature(boxes, "  PCI 3 ")) is False


def test_moved_box_hidden_box_or_new_note_is_a_change():
    boxes = [{"name": "R_1", "finding": "PM nodule", "visible": True, "center": [1.0], "size": [2.0]}]
    baseline = state_signature(boxes, "")
    moved = [{**boxes[0], "center": [1.5]}]
    hidden = [{**boxes[0], "visible": False}]
    assert differs_from_saved(baseline, state_signature(moved, "")) is True
    assert differs_from_saved(baseline, state_signature(hidden, "")) is True
    assert differs_from_saved(baseline, state_signature(boxes, "new note")) is True


def test_notice_is_empty_without_items():
    assert format_notice("Title:", []) == ""


def test_notice_lists_items_and_caps_the_list():
    text = format_notice("Title:", [f"item {i}" for i in range(14)], limit=12)
    lines = text.splitlines()
    assert lines[0] == "Title:"
    assert lines[1] == "- item 0"
    assert lines[12] == "- item 11"
    assert lines[13] == "... and 2 more."
