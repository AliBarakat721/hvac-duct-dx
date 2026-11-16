import math

import pytest

from duct_design.engine import (
    DuctDesignCalculationError,
    DuctDesignOptions,
    DuctSegmentInput,
    air_properties,
    colebrook_darcy_factor,
    evaluate_rectangular_duct,
    rectangular_equivalent_diameter_m,
    run_design,
    size_rect,
)


def test_air_properties_at_standard_conditions_are_reasonable():
    density, viscosity = air_properties(20.0, 101.325)
    assert density == pytest.approx(1.204, rel=0.002)
    assert viscosity == pytest.approx(1.81e-5, rel=0.02)


def test_huebscher_equivalent_diameter_matches_reference_example():
    diameter_m = rectangular_equivalent_diameter_m(0.3, 0.5)
    assert diameter_m * 1000 == pytest.approx(420, abs=1)


def test_colebrook_result_satisfies_implicit_equation():
    reynolds = 100_000.0
    diameter_m = 0.3
    roughness_m = 0.00009
    factor = colebrook_darcy_factor(reynolds, diameter_m, roughness_m)
    lhs = 1 / math.sqrt(factor)
    rhs = -2 * math.log10(
        roughness_m / (3.7 * diameter_m) + 2.51 / (reynolds * math.sqrt(factor))
    )
    assert lhs == pytest.approx(rhs, abs=1e-9)
    assert 0.015 < factor < 0.03


def test_evaluation_separates_straight_friction_from_k_losses():
    options = DuctDesignOptions()
    base = evaluate_rectangular_duct(
        DuctSegmentInput("A", 1000, 50, 0), 16, 12, options
    )
    with_fittings = evaluate_rectangular_duct(
        DuctSegmentInput("A", 1000, 50, 2), 16, 12, options
    )
    assert base.fitting_loss_inwg == pytest.approx(0)
    assert with_fittings.fitting_loss_inwg == pytest.approx(
        2 * with_fittings.velocity_pressure_inwg
    )
    assert with_fittings.straight_friction_loss_inwg == pytest.approx(
        base.straight_friction_loss_inwg
    )
    assert with_fittings.total_modeled_loss_inwg > base.total_modeled_loss_inwg


def test_friction_loss_scales_with_length_for_same_section():
    options = DuctDesignOptions()
    short = evaluate_rectangular_duct(DuctSegmentInput("A", 900, 20), 14, 10, options)
    long = evaluate_rectangular_duct(DuctSegmentInput("A", 900, 40), 14, 10, options)
    assert long.straight_friction_loss_inwg == pytest.approx(
        2 * short.straight_friction_loss_inwg
    )
    assert long.friction_rate_inwg_100ft == pytest.approx(short.friction_rate_inwg_100ft)


def test_size_rect_meets_velocity_friction_and_size_grid_constraints():
    options = DuctDesignOptions(
        max_velocity_fpm=1500,
        max_friction_inwg_100ft=0.08,
        min_width_in=8,
        max_width_in=48,
        min_height_in=8,
        max_height_in=24,
        size_step_in=1,
        max_aspect_ratio=4,
    )
    result = size_rect(DuctSegmentInput("S1", 1000, 50, 0.5), options)
    assert result.velocity_fpm <= options.max_velocity_fpm
    assert result.friction_rate_inwg_100ft <= options.max_friction_inwg_100ft
    assert result.width_in.is_integer()
    assert result.height_in.is_integer()
    assert result.total_modeled_loss_inwg == pytest.approx(
        result.straight_friction_loss_inwg + result.fitting_loss_inwg
    )


def test_no_feasible_size_is_a_user_facing_validation_error():
    options = DuctDesignOptions(
        max_velocity_fpm=100,
        max_friction_inwg_100ft=0.001,
        min_width_in=8,
        max_width_in=8,
        min_height_in=8,
        max_height_in=8,
        size_step_in=1,
    )
    with pytest.raises(DuctDesignCalculationError, match="No size within"):
        size_rect(DuctSegmentInput("S1", 5000, 100), options)


def test_validation_rejects_duplicate_tags_invalid_k_and_nan_inputs():
    options = DuctDesignOptions()
    with pytest.raises(DuctDesignCalculationError, match="unique"):
        run_design((DuctSegmentInput("A", 500, 20), DuctSegmentInput("A", 300, 10)), options)
    with pytest.raises(DuctDesignCalculationError, match="fitting K"):
        run_design((DuctSegmentInput("A", 500, 20, -1),), options)
    with pytest.raises(DuctDesignCalculationError, match="airflow"):
        run_design((DuctSegmentInput("A", math.nan, 20),), options)


def test_run_design_returns_each_segment_without_false_network_total():
    results = run_design(
        (DuctSegmentInput("Main", 1000, 30, 0.4), DuctSegmentInput("Branch", 350, 18, 0.8)),
        DuctDesignOptions(),
    )
    assert [item.tag for item in results] == ["Main", "Branch"]
    assert len(results) == 2
