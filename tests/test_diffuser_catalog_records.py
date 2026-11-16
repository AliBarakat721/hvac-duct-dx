from dataclasses import replace

from diffuser_catalog_records import (
    ceiling_selection_limits,
    cfm_balance_issue,
    circular_selection_guide,
    operating_points,
    validate_operating_points,
)
from diffuser_calculator import get_catalog, performance


def test_supported_diffuser_data_normalizes_with_provenance_and_geometry():
    records = operating_points()
    assert records
    assert {record.family for record in records} == {
        "CCD-supply", "CCD-return", "4SCD", "3SCD"
    }
    assert all(record.printed_page > 0 for record in records)
    assert all(record.verification_status for record in records)
    assert all(record.face_shape and record.neck_shape for record in records)
    assert all(record.airflow_cfm > 0 and record.neck_velocity_fpm > 0 for record in records)


def test_current_vendor_tables_have_ordered_operating_points_and_explicit_audit_warnings():
    issues = validate_operating_points()
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    warnings = [issue for issue in issues if issue.severity == "WARNING"]
    assert errors == []
    # Several legacy SQUARE flows were calculated from face area × velocity;
    # the new records preserve the printed total values and make differences visible.
    assert any(issue.field == "calculator_airflow_cfm" for issue in warnings)
    assert any(issue.field == "directional_flow_cfm_each" for issue in warnings)


def test_supplier_total_airflows_are_not_silently_replaced_by_legacy_calculations():
    square_9_at_300 = next(
        row for row in operating_points()
        if row.model_id == "4SCD-9"
        and row.throw_direction == "each-of-4-sides"
        and row.neck_velocity_fpm == 300
    )
    assert square_9_at_300.airflow_cfm == 168
    assert square_9_at_300.calculator_airflow_cfm == 169
    assert square_9_at_300.directional_flow_cfm_each == 42
    assert square_9_at_300.printed_page == 11


def test_directional_flow_balance_checks_total_with_declared_rounding_tolerance():
    assert cfm_balance_issue(168, (42, 42, 42, 42), tolerance_cfm=5) is None
    issue = cfm_balance_issue(160, (42, 42, 42, 42), tolerance_cfm=5)
    assert issue == "directional flow sum differs from total by 8 CFM"


def test_bad_catalog_row_is_reported_instead_of_accepted():
    row = next(
        item for item in operating_points()
        if item.model_id == "CCD-6" and item.neck_velocity_fpm == 300
    )
    issues = validate_operating_points((replace(row, total_pressure_inwg=-0.1),))
    assert any(issue.field == "total_pressure_inwg" for issue in issues)


def test_ccd_metric_selection_guide_is_separate_and_source_traced():
    guide = circular_selection_guide()
    assert len(guide) == 8
    assert guide[0].model_id == "CCD-6-SI-guide"
    assert guide[0].printed_page == 8
    assert guide[0].min_flow_m3h < guide[0].max_flow_m3h
    assert guide[0].effective_area_m2 > 0
    assert "LwA 40 dB(A)" in guide[0].notes


def test_ceiling_limits_are_source_traced_and_match_the_calculator_table():
    rows = ceiling_selection_limits()
    assert [row.ceiling_height_ft for row in rows] == [8, 10, 12, 14, 16]
    assert all(row.printed_page == 5 for row in rows)
    assert rows[0].max_airflow_cfm_per_diffuser == 800
    assert rows[-1].max_supply_delta_t_f == 30


def test_18_in_square_ak_printed_mismatch_is_exposed_as_a_warning():
    row = next(
        item for item in operating_points()
        if item.model_id == "4SCD-18" and item.throw_direction == "each-of-4-sides"
    )
    assert row.catalog_ak_raw_ft2 == 0.13
    assert row.calculator_ak_ft2 == 1.13
    assert any(
        issue.field == "AK" and issue.severity == "WARNING"
        for issue in validate_operating_points((row,))
    )


def test_performance_exposes_clamping_warning_beyond_catalog_range():
    diffuser = next(item for item in get_catalog("ccd") if item.neck_dia_inch == 10)
    result = performance(diffuser, diffuser.cfm[-1] * 1.1)
    assert not result.in_range
    assert result.warning is not None
    assert "outside catalog table" in result.warning
    assert "not extrapolated" in result.warning
