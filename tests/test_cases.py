"""Case grouping, native views, and output folders. Synthetic ids only."""

from pathlib import Path

from cases import build_cases, choose_output_dir, scan_input_dir, view_plan


def touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"")
    return path


def test_native_planes_are_one_case_and_open_in_red_green_yellow(tmp_path):
    files = [
        touch(tmp_path, "1001_SAG_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_COR_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_AX_pvp_TS.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    assert [case["case_id"] for case in cases] == ["1001"]
    case = cases[0]
    assert case["default_axial_thickness"] == "3mm"
    assert case["has_axial_ts"] is True
    assert case["missing_planes"] == []
    assert warnings == []

    plan = view_plan(case["volumes"], "3mm", "1001")
    assert plan["views"]["Red"] == {"path": case["volumes"]["AX_3mm"], "reconstruct": False}
    assert plan["views"]["Green"] == {"path": case["volumes"]["COR_3mm"], "reconstruct": False}
    assert plan["views"]["Yellow"] == {"path": case["volumes"]["SAG_3mm"], "reconstruct": False}

    thin = view_plan(case["volumes"], "TS", "1001")
    assert thin["views"]["Red"]["path"] == case["volumes"]["AX_TS"]
    assert thin["views"]["Green"]["reconstruct"] is True
    assert thin["views"]["Yellow"]["reconstruct"] is True
    assert thin["views"]["Green"]["path"] == case["volumes"]["AX_TS"]


def test_missing_plane_is_a_reconstruction_and_a_case_without_axial_is_skipped(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_COR_pvp_3mm.nii.gz"),
        touch(tmp_path, "1002_COR_pvp_3mm.nii.gz"),
        touch(tmp_path, "1002_SAG_pvp_3mm.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    assert [case["case_id"] for case in cases] == ["1001"]
    plan = view_plan(cases[0]["volumes"], "3mm", "1001")
    assert plan["views"]["Yellow"]["reconstruct"] is True
    assert plan["views"]["Yellow"]["path"].name == "1001_AX_pvp_3mm.nii.gz"
    assert any("no sagittal" in message for message in warnings)
    assert any("Skipping case '1002'" in message for message in warnings)


def test_scan_number_splits_cases_in_the_same_folder(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_2_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_2_COR_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_2_SAG_pvp_3mm.nii.gz"),
    ]
    cases, _warnings = build_cases(files)
    by_id = {case["case_id"]: case for case in cases}
    assert set(by_id) == {"1001", "1001_2"}
    assert by_id["1001_2"]["scan_number"] == "2"
    assert by_id["1001"]["scan_number"] == "1"
    assert set(by_id["1001_2"]["volumes"]) == {"AX_3mm", "COR_3mm", "SAG_3mm"}


def test_file_without_scan_number_is_scan_one_and_shares_the_case_with_scan_one(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_1_COR_pvp_3mm.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    assert [case["case_id"] for case in cases] == ["1001"]
    assert cases[0]["scan_number"] == "1"
    assert set(cases[0]["volumes"]) == {"AX_3mm", "COR_3mm"}
    assert not any("Duplicate" in message or "contrast phase" in message for message in warnings)


def test_scan_number_with_leading_zero_is_normalized(tmp_path):
    cases, _warnings = build_cases([touch(tmp_path, "1001_02_AX_pvp_3mm.nii.gz")])
    assert cases[0]["case_id"] == "1001_2"
    assert cases[0]["scan_number"] == "2"


def test_same_slot_from_scan_one_with_and_without_number_keeps_one_file(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_1_AX_pvp_3mm.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    assert [case["case_id"] for case in cases] == ["1001"]
    assert any("Duplicate volume 'AX_3mm' for case '1001'" in message for message in warnings)


def test_mixed_contrast_phases_in_one_case_raise_a_warning(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_art_3mm.nii.gz"),
        touch(tmp_path, "1001_COR_pvp_3mm.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    assert [case["case_id"] for case in cases] == ["1001"]
    assert any(
        "Case '1001' has more than one contrast phase (art, pvp)" in message
        for message in warnings
    )


def test_one_contrast_phase_gives_no_phase_warning(tmp_path):
    files = [
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_COR_pvp_3mm.nii.gz"),
    ]
    _cases, warnings = build_cases(files)
    assert not any("contrast phase" in message for message in warnings)


def test_unmatched_file_never_overwrites_a_numbered_case(tmp_path):
    files = [
        touch(tmp_path, "1001_2_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_AX_pvp_3mm.nii.gz"),
        touch(tmp_path, "1001_extra.nii.gz"),
    ]
    cases, warnings = build_cases(files)
    by_id = {case["case_id"]: case for case in cases}
    assert set(by_id) == {"1001", "1001_2", "1001_legacy2"}
    assert by_id["1001_2"]["volumes"]["AX_3mm"].name == "1001_2_AX_pvp_3mm.nii.gz"
    assert by_id["1001_legacy2"]["legacy"] is True
    assert any("shares case ID '1001'" in message for message in warnings)


def test_duplicate_plane_and_thickness_keeps_the_first_file(tmp_path):
    touch(tmp_path, "1001_AX_pvp_3mm.nii.gz")
    touch(tmp_path, "1001_AX_art_3mm.nii.gz")
    cases, warnings = scan_input_dir(tmp_path)
    # Natural sort keeps art and ignores pvp. Phase is not part of the slot.
    assert cases[0]["volumes"]["AX_3mm"].name == "1001_AX_art_3mm.nii.gz"
    assert any("ignoring '1001_AX_pvp_3mm.nii.gz'" in message for message in warnings)


def test_output_folder_prefers_the_exact_case_key(tmp_path):
    (tmp_path / "1001").mkdir()
    (tmp_path / "1001_2").mkdir()
    (tmp_path / "Scan 1001").mkdir()
    assert choose_output_dir(tmp_path, "1001") == tmp_path / "1001"
    assert choose_output_dir(tmp_path, "1001_2") == tmp_path / "1001_2"

    exact = tmp_path / "other"
    exact.mkdir()
    (exact / "Scan 1004").mkdir()
    assert choose_output_dir(exact, "1004") == exact / "Scan 1004"
    # 1004 must not fall into a numbered sibling when that is the only folder.
    numbered_only = tmp_path / "numbered"
    numbered_only.mkdir()
    (numbered_only / "1004_2").mkdir()
    assert choose_output_dir(numbered_only, "1004") == numbered_only / "1004"
