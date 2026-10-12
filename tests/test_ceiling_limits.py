import pytest

from diffuser_calculator import ceiling_limits, select_layout_for_geometry
from room_geometry import RoomGeometry


@pytest.mark.parametrize(
    ("height_ft", "expected_cfm", "expected_delta_t"),
    [
        (7, 800, 20),
        (8, 800, 20),
        (9.19, 1514, 23.57),
        (9.84, 1904, 25.52),
        (10, 2000, 26),
        (12, 3000, 30),
        (16, 6400, 30),
    ],
)
def test_ceiling_limits_interpolate_table_rows(
    height_ft, expected_cfm, expected_delta_t
):
    _, cfm_cap, delta_t_max, notes = ceiling_limits(height_ft)

    assert cfm_cap == pytest.approx(expected_cfm, abs=2)
    assert delta_t_max == pytest.approx(expected_delta_t, abs=0.02)
    if height_ft == 7:
        assert any("lower than 8 ft" in note for note in notes)


def test_ceiling_limits_clamp_above_table_and_keep_warning():
    row, cfm_cap, delta_t_max, notes = ceiling_limits(18)

    assert (row, cfm_cap, delta_t_max) == (16, 6400, 30)
    assert any("exceeds validated Table 4" in note for note in notes)


def test_layout_at_interpolated_ceiling_limit_is_not_delta_t_rejected():
    geometry = RoomGeometry.from_vertices(
        [(0, 0), (6, 0), (6, 5), (0, 5)], 3.0
    )

    result = select_layout_for_geometry(
        geometry, 1000, delta_t_f=22.2
    )

    assert result.success or result.failure_kind == "no_fully_compliant_option"
    assert not any("sensible ΔT" in error for error in result.errors)
