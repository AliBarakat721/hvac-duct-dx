from pathlib import Path

import pytest

from duct_design.duct_design_page_fixed import (
    DuctDesignInputError,
    DuctDesignPageFixed,
    append_note_once,
    edge_label_position,
    edge_length,
    safe_export,
    validate_height_limits,
)


def test_note_append_is_spaced_and_idempotent():
    note = "widened for velocity"
    result = append_note_once("  width adjustment ", note)
    result = append_note_once(result, note)
    assert result == "width adjustment | widened for velocity"


def test_export_boundary_reports_missing_ezdxf_and_write_errors():
    def no_ezdxf():
        raise ModuleNotFoundError("No module named 'ezdxf'", name="ezdxf")

    missing = safe_export(no_ezdxf)
    assert not missing.success
    assert "pip install ezdxf" in missing.message
    assert missing.error_type == "ModuleNotFoundError"

    def denied():
        raise PermissionError("read-only destination")

    denied_result = safe_export(denied)
    assert not denied_result.success
    assert "permission denied" in denied_result.message

    success = safe_export(lambda: Path("design.dxf"))
    assert success.success
    assert success.path == "design.dxf"


def test_label_location_is_offset_perpendicular_for_horizontal_and_vertical_edges():
    horizontal = edge_label_position((0, 0), (2, 0), 0.1)
    vertical = edge_label_position((0, 0), (0, 2), 0.1)
    assert horizontal == pytest.approx((1.0, 0.1))
    assert vertical == pytest.approx((-0.1, 1.0))


def test_euclidean_edge_length_and_diagonal_label_position():
    assert edge_length((0, 0), (3, 4)) == 5
    x, y = edge_label_position((0, 0), (1, 1), 1)
    # The offset has unit length and is perpendicular to the segment.
    assert (x - 0.5) ** 2 + (y - 0.5) ** 2 == pytest.approx(1)
    assert (x - 0.5) + (y - 0.5) == pytest.approx(0)


def test_height_range_rejects_inverted_or_physically_impossible_limits():
    with pytest.raises(DuctDesignInputError, match="below the requested minimum"):
        validate_height_limits(500, 150, 1.0)
    with pytest.raises(DuctDesignInputError, match="below the requested minimum"):
        validate_height_limits(500, 600, 0.5, clearance_mm=100)
    assert validate_height_limits(150, 600, 0.5, clearance_mm=100) == (150.0, 400.0)


def test_calculate_catches_engine_errors_and_ensure_returns_false():
    reported = []
    page = DuctDesignPageFixed(
        lambda: 1 / 0,
        on_error=lambda title, message: reported.append((title, message)),
    )
    assert page.calculate() is False
    assert page.res is None
    assert page.last_error == "ZeroDivisionError: division by zero"
    assert reported == [("Calculation error", page.last_error)]
    assert page._ensure() is False


def test_ensure_calculates_once_then_returns_true_for_existing_result():
    calls = []
    page = DuctDesignPageFixed(lambda value: calls.append(value) or {"ok": value})
    assert page._ensure(7) is True
    assert page.res == {"ok": 7}
    assert page._ensure(99) is True
    assert page.res == {"ok": 7}
    assert calls == [7]
