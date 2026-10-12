import math

import pytest

from diffuser_calculator import (
    RoomLayout,
    _t50_l_ratios,
    calculate_supply_delta_t,
    evaluate_layout,
    performance,
    rules_load_w_m2,
    rules_tl_band,
)
from placement_engine import validate_polygon, verify_placement
from room_geometry import RoomGeometry


def test_sensible_load_delta_t_uses_documented_factor_and_rejects_invalid_inputs():
    assert calculate_supply_delta_t(10_800, 1_000) == pytest.approx(10.0)
    assert calculate_supply_delta_t(0, 1_000) is None
    assert calculate_supply_delta_t(10_800, 0) is None
    assert calculate_supply_delta_t(math.nan, 1_000) is None
    assert calculate_supply_delta_t(10_800, math.inf) is None


def test_total_load_is_not_accepted_as_sensible_load():
    room = RoomLayout(20, 20, room_h_ft=10)
    with pytest.raises(ValueError, match="total_load_btuh"):
        evaluate_layout(
            1_000,
            positions_ft=[(5, 5)],
            room=room,
            total_load_btuh=12_000,
        )


@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (None, (0.7, 1.2)),
        (60, (0.7, 1.3)),
        (125, (0.5, 1.5)),
        (190, (0.7, 1.2)),
        (250, (0.7, 1.3)),
        (59.99, (0.7, 1.2)),
        (250.01, (0.7, 1.2)),
    ],
)
def test_t50_l_bands_use_only_documented_rows_and_fallback(load, expected):
    assert rules_tl_band(load) == expected


def test_load_normalization_rejects_missing_or_nonpositive_area():
    assert rules_load_w_m2(10_000, 100) == pytest.approx(
        10_000 / 100 * 0.29307107 * 10.7639104
    )
    assert rules_load_w_m2(None, 100) is None
    assert rules_load_w_m2(10_000, 0) is None
    assert rules_load_w_m2(-1, 100) is None


def test_catalog_performance_interpolates_and_marks_out_of_range():
    from diffuser_calculator import get_catalog

    diffuser = next(d for d in get_catalog("ccd") if d.neck_dia_inch == 10)
    midpoint = (diffuser.v[0] + diffuser.v[1]) / 2
    p = performance(diffuser, midpoint * diffuser.neck_area_ft2)
    assert p.in_range
    assert p.pt == pytest.approx((diffuser.pt[0] + diffuser.pt[1]) / 2)

    outside = performance(diffuser, diffuser.v[-1] * diffuser.neck_area_ft2 * 1.1)
    assert not outside.in_range


def test_actual_layout_uses_positions_not_default_grid_for_characteristic_length():
    polygon = [(0, 0), (20, 0), (20, 10), (0, 10)]
    positions = [(2, 5), (18, 5)]
    r_hi, r_lo, ok, characteristic = _t50_l_ratios(
        10, 10, (0.7, 1.2), positions=positions, polygon=polygon
    )
    assert characteristic == pytest.approx(2.0)
    assert r_hi == pytest.approx(5.0)
    assert r_lo == pytest.approx(1.25)
    assert not ok


def test_polygon_and_placement_reject_invalid_outside_clearance_and_spacing_cases():
    valid = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert validate_polygon(valid)[0]
    assert not validate_polygon([(0, 0), (2, 2), (0, 2), (2, 0)])[0]

    outside = verify_placement(valid, [(11, 5)], wall_clearance_m=0.3)
    assert not outside.valid
    assert outside.diagnostics["reason"] == "point_outside_polygon"

    too_close_to_wall = verify_placement(valid, [(0.1, 5)], wall_clearance_m=0.3)
    assert not too_close_to_wall.valid
    assert too_close_to_wall.diagnostics["reason"] == "wall_clearance_violation"

    too_close = verify_placement(
        valid, [(5, 5), (5.2, 5)], wall_clearance_m=0.3, min_spacing_m=0.6
    )
    assert not too_close.valid
    assert too_close.diagnostics["reason"] == "spacing_violation"


def test_geometry_path_rejects_positions_outside_room_polygon():
    geometry = RoomGeometry.from_vertices([(0, 0), (5, 0), (5, 5), (0, 5)], 3)
    room = RoomLayout.from_geometry(geometry)
    with pytest.raises(ValueError, match="outside room polygon"):
        evaluate_layout(500, positions_ft=[(100, 100)], room=room)


def test_selection_keeps_catalog_total_pressure_separate_and_checks_limit():
    from diffuser_calculator import get_catalog

    room = RoomLayout(20, 20, room_h_ft=10)
    model = next(d for d in get_catalog("ccd") if d.neck_dia_inch == 10)
    selected, _ = evaluate_layout(
        500,
        positions_ft=[(10, 10)],
        room=room,
        catalog=[model],
        max_dp_inwg=0.0,
    )
    assert not selected.pressure_ok
    assert any(warning.startswith("ΔP ") for warning in selected.warnings)
    assert selected.rationale["pressure_basis"].startswith(
        "catalog total-pressure loss"
    )


def test_selection_checks_catalog_nc_limit_without_claiming_room_acoustic_compliance():
    from diffuser_calculator import get_catalog

    room = RoomLayout(20, 20, room_h_ft=10)
    model = next(d for d in get_catalog("ccd") if d.neck_dia_inch == 10)
    selected, _ = evaluate_layout(
        165,
        positions_ft=[(10, 10)],
        room=room,
        catalog=[model],
        max_nc=19,
    )
    assert any(warning.startswith("Noise NC ") for warning in selected.warnings)
    assert selected.rationale["noise_compliant"] is False
    assert selected.rationale["acoustic_verification_status"].startswith(
        "Acoustic screening"
    )


def test_missing_sensible_load_does_not_invent_supply_delta_t():
    room = RoomLayout(20, 20, room_h_ft=10)
    selected, _ = evaluate_layout(500, positions_ft=[(10, 10)], room=room)
    assert selected.delta_t_f is None


def test_selection_exposes_screening_and_unresolved_rule_statuses():
    geometry = RoomGeometry.from_vertices([(0, 0), (6, 0), (6, 5), (0, 5)], 3)
    room = RoomLayout.from_geometry(geometry)
    selected, _ = evaluate_layout(
        1_000,
        positions_ft=[(3, 2.5)],
        room=room,
        sensible_load_btuh=8_000,
    )
    rationale = selected.rationale
    assert rationale["verification_status"] == (
        "ASHRAE-aligned screening; ADPI not verified"
    )
    assert rationale["adpi_verified"] is False
    assert rationale["pressure_basis"] == (
        "catalog total-pressure loss; system losses not included"
    )
    assert rationale["acoustic_verification_status"] == (
        "Acoustic screening; catalog NC basis unresolved"
    )
    assert "R-JET-COLLISION" in rationale["unresolved_or_unimplemented_rules"]
    assert "R-HEATING-OVERHEAD" in rationale["unresolved_or_unimplemented_rules"]
    assert "R-VAV" in rationale["unresolved_or_unimplemented_rules"]
    assert rationale["terminal_velocity_status"] == "unresolved"
