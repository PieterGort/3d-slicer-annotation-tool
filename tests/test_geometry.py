"""Geometry checks on synthetic matrices. No real scans."""

import math

from geometry import (
    box_bounds,
    box_warnings,
    check_volume,
    compare_volumes,
    item_from_volume_key,
    ras_bounds,
    reconstruction_warnings,
)


def make_matrix(columns, origin=(0.0, 0.0, 0.0)):
    matrix = [[0.0] * 4 for _ in range(4)]
    for j, column in enumerate(columns):
        for r in range(3):
            matrix[r][j] = float(column[r])
    for r in range(3):
        matrix[r][3] = float(origin[r])
    matrix[3][3] = 1.0
    return matrix


def axial(thickness_mm=3.0, in_plane=0.7, origin=(0.0, 0.0, 0.0), flip=False, dims=(512, 512, 100)):
    i = (-in_plane if flip else in_plane, 0.0, 0.0)
    columns = [i, (0.0, in_plane, 0.0), (0.0, 0.0, thickness_mm)]
    return {
        "label": "AX",
        "plane": "AX",
        "thickness": "3mm",
        "matrix": make_matrix(columns, origin),
        "dims": list(dims),
    }


def coronal(origin=(0.0, 0.0, 0.0), dims=(512, 425, 512)):
    # Slices run along anterior-posterior (y). Columns: x, z, y.
    # Default size covers the same RAS box as axial() (512 x 512 x 100 at 0.7/0.7/3 mm).
    columns = [(0.7, 0.0, 0.0), (0.0, 0.0, 0.7), (0.0, 3.0, 0.0)]
    return {
        "label": "COR 3mm",
        "plane": "COR",
        "thickness": "3mm",
        "matrix": make_matrix(columns, origin),
        "dims": list(dims),
    }


def test_matching_axial_thin_and_thick_pair_has_no_messages():
    thick = axial(3.0, dims=(512, 512, 100))
    thick["label"] = "AX 3mm"
    thin = axial(1.0, dims=(512, 512, 298))
    thin.update({"label": "AX TS", "thickness": "TS"})
    assert check_volume(thin) == []
    assert compare_volumes(thick, thin) == []


def test_slices_running_along_the_wrong_axis_are_reported():
    wrong = coronal()
    wrong["label"] = "COR 3mm"
    wrong["matrix"] = make_matrix([(0.7, 0, 0), (0, 0.7, 0), (0, 0, 3.0)])
    messages = check_volume(wrong)
    assert any("named COR but its slices run along the superior-inferior" in m for m in messages)


def test_slice_spacing_that_does_not_match_the_thickness_label_is_reported():
    mislabeled = axial(1.25)
    mislabeled["label"] = "AX 3mm"
    assert any("named 3mm but its slices are 1.25 mm apart" in m for m in check_volume(mislabeled))


def test_thin_slice_label_with_thick_slices_is_reported():
    thick_as_ts = axial(3.0)
    thick_as_ts.update({"label": "AX TS", "thickness": "TS"})
    assert any("named TS but its slices are 3.00 mm apart" in m for m in check_volume(thick_as_ts))


def test_tilted_slices_are_reported():
    angle = math.radians(10.0)
    tilted = axial(3.0)
    tilted["matrix"] = make_matrix(
        [(0.7, 0, 0), (0, 0.7, 0), (0, 3.0 * math.sin(angle), 3.0 * math.cos(angle))]
    )
    assert any("tilted 10.0 degrees" in m for m in check_volume(tilted))


def test_flipped_direction_on_the_same_plane_is_reported():
    reference = axial()
    flipped = axial(1.0, flip=True, dims=(512, 512, 298))
    flipped.update({"label": "AX TS", "thickness": "TS"})
    assert any("different orientation" in m for m in compare_volumes(reference, flipped))


def test_different_in_plane_spacing_on_the_same_plane_is_reported():
    reference = axial()
    other = axial(1.0, in_plane=0.9, dims=(512, 512, 298))
    other.update({"label": "AX TS", "thickness": "TS"})
    assert any("in-plane pixel spacing" in m for m in compare_volumes(reference, other))


def test_origin_shift_in_plane_is_reported():
    reference = axial()
    shifted = axial(1.0, origin=(2.0, 0.0, 0.0), dims=(512, 512, 298))
    shifted.update({"label": "AX TS", "thickness": "TS"})
    assert any("starts 2.0 mm away" in m and "slice plane" in m for m in compare_volumes(reference, shifted))


def test_origin_shift_along_the_slice_direction_is_not_reported():
    """3 mm and TS of the same scan often differ by about one thick-slice half-width."""
    reference = axial()
    shifted = axial(1.0, origin=(0.0, 0.0, 1.5), dims=(512, 512, 298))
    shifted.update({"label": "AX TS", "thickness": "TS"})
    assert compare_volumes(reference, shifted) == []


def test_volume_covering_a_different_part_of_the_scan_is_reported():
    reference = axial()
    far = axial(1.0, origin=(0.0, 0.0, 500.0), dims=(512, 512, 298))
    far.update({"label": "AX TS", "thickness": "TS"})
    assert any("covers a different part" in m for m in compare_volumes(reference, far))


def test_different_plane_is_not_compared_for_orientation():
    reference = axial()
    cor = coronal()
    messages = compare_volumes(reference, cor)
    assert not any("orientation" in m for m in messages)
    assert messages == []


def test_coronal_covering_a_different_region_is_reported():
    reference = axial()
    far = coronal(origin=(0.0, 0.0, 500.0), dims=(512, 425, 512))
    messages = compare_volumes(reference, far)
    assert any("covers a different part" in m for m in messages)


def test_ras_bounds_cover_voxel_centers():
    mins, maxs = ras_bounds(axial()["matrix"], [512, 512, 100])
    assert mins == [0.0, 0.0, 0.0]
    assert math.isclose(maxs[0], 511 * 0.7)
    assert math.isclose(maxs[2], 99 * 3.0)


def test_box_inside_the_source_gives_no_message():
    bounds = ras_bounds(axial()["matrix"], [512, 512, 100])
    boxes = [("R_1", (100.0, 100.0, 100.0), (20.0, 20.0, 20.0))]
    assert box_warnings(boxes, "AX 3mm", bounds, "Red") == []


def test_box_outside_the_reconstructed_source_is_reported_for_each_view():
    bounds = ras_bounds(axial()["matrix"], [512, 512, 100])
    boxes = [("R_2", (500.0, 100.0, 100.0), (20.0, 20.0, 20.0))]
    messages = box_warnings(boxes, "AX 3mm", bounds, "Green and Yellow")
    assert messages == [
        "Box R_2 is outside AX 3mm, so it does not show in Green and Yellow."
    ]


def test_box_half_outside_is_reported_as_partly_outside():
    bounds = ras_bounds(axial()["matrix"], [512, 512, 100])
    edge = 511 * 0.7
    boxes = [("R_3", (edge, 100.0, 100.0), (20.0, 20.0, 20.0))]
    messages = box_warnings(boxes, "AX 3mm", bounds, "Green")
    assert len(messages) == 1
    assert messages[0].startswith("Box R_3 is partly outside AX 3mm.")


def test_box_bounds_are_centered_on_the_box_center():
    lo, hi = box_bounds((10.0, 0.0, -5.0), (4.0, 2.0, 6.0))
    assert lo == [8.0, -1.0, -8.0]
    assert hi == [12.0, 1.0, -2.0]


def test_item_from_volume_key_splits_plane_and_thickness():
    item = item_from_volume_key("AX_TS", axial()["matrix"], [512, 512, 298])
    assert item["label"] == "AX TS"
    assert item["plane"] == "AX"
    assert item["thickness"] == "TS"


def test_all_native_views_have_no_reconstruction_warning():
    views = [
        {"view": "Red", "label": "AX 3mm", "plane": "AX", "reconstructed": False},
        {"view": "Green", "label": "COR 3mm", "plane": "COR", "reconstructed": False},
        {"view": "Yellow", "label": "SAG 3mm", "plane": "SAG", "reconstructed": False},
    ]
    assert reconstruction_warnings(views) == []


def test_ax_only_names_green_and_yellow_as_reconstructions_of_axial():
    views = [
        {"view": "Red", "label": "AX 3mm", "plane": "AX", "reconstructed": False},
        {"view": "Green", "label": "AX 3mm", "plane": "AX", "reconstructed": True},
        {"view": "Yellow", "label": "AX 3mm", "plane": "AX", "reconstructed": True},
    ]
    messages = reconstruction_warnings(views)
    assert messages == [
        "Green and Yellow are reconstructions of AX 3mm, not native scans. "
        "Boxes in those views are drawn on AX 3mm."
    ]


def test_ax_and_cor_warns_that_yellow_is_reconstructed_and_green_is_native():
    views = [
        {"view": "Red", "label": "AX 3mm", "plane": "AX", "reconstructed": False},
        {"view": "Green", "label": "COR 3mm", "plane": "COR", "reconstructed": False},
        {"view": "Yellow", "label": "AX 3mm", "plane": "AX", "reconstructed": True},
    ]
    messages = reconstruction_warnings(views)
    assert messages[0].startswith(
        "Yellow is a reconstruction of AX 3mm, not a native scan."
    )
    assert "Boxes in that view are drawn on AX 3mm" in messages[0]
    assert "Green (COR 3mm) shows a native volume" in messages[1]
    assert "may appear there and not in the reconstructed view" in messages[1]
