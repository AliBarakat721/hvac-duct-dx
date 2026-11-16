import math

import pytest

from duct_design import (
    CoefficientSource,
    CoefficientValidity,
    FittingCalculationError,
    FittingCalculationStatus,
    FittingCoefficient,
    FittingGeometry,
    FittingInput,
    FittingType,
    ReferenceVelocity,
    calculate_fitting_loss,
    calculate_fitting_losses,
    integrate_fitting_losses,
)


def coefficient(
    fitting_type=FittingType.ELBOW_90_RECTANGULAR,
    *,
    k=0.5,
    reference=ReferenceVelocity.USER_DEFINED,
    validity=CoefficientValidity.PROVISIONAL,
):
    return FittingCoefficient(
        coefficient_id="fixture-k-050",
        fitting_type=fitting_type,
        k=k,
        reference_velocity=reference,
        source=CoefficientSource.USER_SUPPLIED,
        source_reference="synthetic test fixture; not design data",
        validity=validity,
    )


def fitting(**kwargs):
    values = dict(
        fitting_id="F1",
        segment_id="S1",
        fitting_type=FittingType.ELBOW_90_RECTANGULAR,
        airflow_cfm=500,
        air_density_kg_m3=1.2,
        coefficient=coefficient(),
        geometry=FittingGeometry(inlet_width_in=12, inlet_height_in=12),
        reference_velocity_fpm=1000,
    )
    values.update(kwargs)
    return FittingInput(**values)


def test_explicit_reference_velocity_uses_dynamic_pressure_formula_and_units():
    result = calculate_fitting_loss(fitting())
    assert result.reference_velocity is ReferenceVelocity.USER_DEFINED
    assert result.reference_velocity_fpm == pytest.approx(1000)
    assert result.dynamic_pressure_pa == pytest.approx(15.48384)
    assert result.pressure_loss_pa == pytest.approx(7.74192)
    assert result.pressure_loss_inwg == pytest.approx(7.74192 / 249.08891)
    assert result.coefficient_source is CoefficientSource.USER_SUPPLIED


def test_inlet_outlet_and_branch_reference_areas_are_explicit():
    base = dict(
        airflow_cfm=600,
        reference_velocity_fpm=None,
        geometry=FittingGeometry(
            inlet_width_in=12, inlet_height_in=12,
            outlet_width_in=8, outlet_height_in=12,
            branch_width_in=6, branch_height_in=10,
        ),
    )
    inlet = calculate_fitting_loss(fitting(**base, coefficient=coefficient(reference=ReferenceVelocity.INLET)))
    outlet = calculate_fitting_loss(fitting(**base, coefficient=coefficient(reference=ReferenceVelocity.OUTLET)))
    branch = calculate_fitting_loss(fitting(**base, coefficient=coefficient(reference=ReferenceVelocity.BRANCH)))
    assert inlet.reference_velocity_fpm < outlet.reference_velocity_fpm < branch.reference_velocity_fpm


def test_missing_user_defined_velocity_and_missing_geometry_are_rejected():
    with pytest.raises(FittingCalculationError, match="USER_DEFINED"):
        calculate_fitting_loss(fitting(reference_velocity_fpm=None, geometry=FittingGeometry()))
    with pytest.raises(FittingCalculationError, match="Geometry is missing"):
        calculate_fitting_loss(fitting(
            reference_velocity_fpm=None,
            coefficient=coefficient(reference=ReferenceVelocity.INLET),
            geometry=FittingGeometry(),
        ))


@pytest.mark.parametrize("field", ["airflow_unit", "air_density_unit", "geometry_unit", "reference_velocity_unit"])
def test_invalid_units_are_rejected(field):
    with pytest.raises(FittingCalculationError, match="Invalid"):
        calculate_fitting_loss(fitting(**{field: "SI"}))


@pytest.mark.parametrize("airflow", [0, -1, math.nan, math.inf, -math.inf])
def test_invalid_or_zero_airflow_is_explicitly_handled(airflow):
    if airflow == 0:
        result = calculate_fitting_loss(fitting(airflow_cfm=0))
        assert result.status is FittingCalculationStatus.ZERO_FLOW
        assert result.pressure_loss_inwg == 0
    else:
        with pytest.raises(FittingCalculationError, match="airflow"):
            calculate_fitting_loss(fitting(airflow_cfm=airflow))


@pytest.mark.parametrize("k", [-0.1, math.nan, math.inf])
def test_invalid_k_is_rejected(k):
    with pytest.raises(FittingCalculationError, match="K"):
        calculate_fitting_loss(fitting(coefficient=coefficient(k=k)))


def test_unsupported_coefficient_and_mismatched_type_are_not_silently_used():
    with pytest.raises(FittingCalculationError, match="UNSUPPORTED"):
        calculate_fitting_loss(fitting(coefficient=coefficient(validity=CoefficientValidity.UNSUPPORTED)))
    with pytest.raises(FittingCalculationError, match="does not match"):
        calculate_fitting_loss(fitting(coefficient=coefficient(FittingType.TEE_BRANCH)))


def test_geometry_validation_rejects_bad_dimensions_aspect_and_angle():
    with pytest.raises(FittingCalculationError, match="dimensions"):
        calculate_fitting_loss(fitting(geometry=FittingGeometry(inlet_width_in=0, inlet_height_in=12)))
    with pytest.raises(FittingCalculationError, match="angle"):
        calculate_fitting_loss(fitting(geometry=FittingGeometry(angle_deg=181), reference_velocity_fpm=1000))
    with pytest.raises(FittingCalculationError, match="aspect-ratio"):
        bad = coefficient()
        bad = FittingCoefficient(**{**bad.__dict__, "aspect_ratio_min": 10, "aspect_ratio_max": 2})
        calculate_fitting_loss(fitting(coefficient=bad))


def test_multiple_fittings_are_sorted_and_duplicate_ids_are_rejected():
    second = fitting(fitting_id="F2", coefficient=coefficient(k=1.0))
    results = calculate_fitting_losses([second, fitting()])
    assert [result.fitting_id for result in results] == ["F1", "F2"]
    assert sum(result.pressure_loss_inwg for result in results) == pytest.approx(
        results[0].pressure_loss_inwg + results[1].pressure_loss_inwg
    )
    with pytest.raises(FittingCalculationError, match="Duplicate"):
        calculate_fitting_losses([fitting(), fitting()])


def test_integration_requires_one_segment_and_prevents_double_counting():
    result = calculate_fitting_loss(fitting())
    integrated = integrate_fitting_losses(
        [fitting()], segment_id="S1", existing_segment_fitting_loss_inwg=result.pressure_loss_inwg
    )
    assert integrated.aggregate_fitting_loss_inwg == pytest.approx(result.pressure_loss_inwg)
    assert integrated.already_included_in_segment_total
    assert integrated.double_counting_prevented
    with pytest.raises(FittingCalculationError, match="does not match"):
        integrate_fitting_losses([fitting()], segment_id="S1", existing_segment_fitting_loss_inwg=0.0)
    with pytest.raises(FittingCalculationError, match="Every fitting"):
        integrate_fitting_losses([fitting(segment_id="OTHER")], segment_id="S1")


def test_typed_categories_are_metadata_only_without_fabricated_coefficients():
    tee = fitting(
        fitting_type=FittingType.TEE_BRANCH,
        coefficient=coefficient(FittingType.TEE_BRANCH),
    )
    result = calculate_fitting_loss(tee)
    assert result.fitting_type is FittingType.TEE_BRANCH
    assert result.coefficient_source is CoefficientSource.USER_SUPPLIED
