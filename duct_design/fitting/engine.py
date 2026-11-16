"""Coefficient-driven HVAC fitting and dynamic pressure-loss calculations."""
from __future__ import annotations

import math
from collections.abc import Iterable

from ..engine import CFM_TO_M3_S, IN_TO_M, PA_PER_IN_WG
from .models import (
    CoefficientSource,
    CoefficientValidity,
    FittingCalculationStatus,
    FittingCoefficient,
    FittingGeometry,
    FittingInput,
    FittingIntegrationResult,
    FittingLossResult,
    FittingType,
    ReferenceVelocity,
)

FPM_TO_M_S = 0.00508


class FittingCalculationError(ValueError):
    """Invalid fitting geometry, coefficient, flow, or integration input."""


def _number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _positive(value: object) -> bool:
    return _number(value) and value > 0


def _nonnegative(value: object) -> bool:
    return _number(value) and value >= 0


def _area_m2(width_in: float | None, height_in: float | None) -> float | None:
    if width_in is None or height_in is None:
        return None
    if not _positive(width_in) or not _positive(height_in):
        raise FittingCalculationError("Rectangular fitting dimensions must be finite and positive.")
    return width_in * IN_TO_M * height_in * IN_TO_M


def _round_area_m2(diameter_in: float | None) -> float | None:
    if diameter_in is None:
        return None
    if not _positive(diameter_in):
        raise FittingCalculationError("Round fitting diameter must be finite and positive.")
    diameter_m = diameter_in * IN_TO_M
    return math.pi * diameter_m**2 / 4.0


def _reference_area(geometry: FittingGeometry, reference: ReferenceVelocity) -> float:
    if reference in (ReferenceVelocity.INLET, ReferenceVelocity.STRAIGHT_THROUGH):
        area = _area_m2(geometry.inlet_width_in, geometry.inlet_height_in) or _round_area_m2(geometry.inlet_diameter_in)
    elif reference is ReferenceVelocity.OUTLET:
        area = _area_m2(geometry.outlet_width_in, geometry.outlet_height_in) or _round_area_m2(geometry.outlet_diameter_in)
    elif reference is ReferenceVelocity.BRANCH:
        area = _area_m2(geometry.branch_width_in, geometry.branch_height_in) or _round_area_m2(geometry.branch_diameter_in)
    else:
        area = None
    if area is None:
        raise FittingCalculationError(
            f"Geometry is missing for reference velocity {reference.value}; provide an explicit reference_velocity_fpm for USER_DEFINED."
        )
    return area


def _validate_geometry(geometry: FittingGeometry) -> None:
    for label, value in (
        ("angle", geometry.angle_deg),
        ("radius", geometry.radius_in),
        ("transition length", geometry.transition_length_in),
    ):
        if value is not None and (not _number(value) or value < 0):
            raise FittingCalculationError(f"Fitting {label} must be finite and non-negative.")
    if geometry.angle_deg is not None and geometry.angle_deg > 180:
        raise FittingCalculationError("Fitting angle cannot exceed 180 degrees.")
    if geometry.turning_vane_count is not None and (
        not isinstance(geometry.turning_vane_count, int) or geometry.turning_vane_count < 0
    ):
        raise FittingCalculationError("Turning vane count must be a non-negative integer.")
    # Validate all supplied dimensions, including dimensions not selected as Vref.
    for value in (
        geometry.inlet_width_in, geometry.inlet_height_in, geometry.outlet_width_in,
        geometry.outlet_height_in, geometry.inlet_diameter_in, geometry.outlet_diameter_in,
        geometry.branch_width_in, geometry.branch_height_in, geometry.branch_diameter_in,
    ):
        if value is not None and not _positive(value):
            raise FittingCalculationError("All supplied fitting dimensions must be finite and positive.")


def validate_coefficient(coefficient: FittingCoefficient) -> None:
    if not isinstance(coefficient.coefficient_id, str) or not coefficient.coefficient_id.strip():
        raise FittingCalculationError("Fitting coefficient ID must be non-empty.")
    if not _nonnegative(coefficient.k):
        raise FittingCalculationError(f"Coefficient {coefficient.coefficient_id} K must be finite and non-negative.")
    if not isinstance(coefficient.source_reference, str) or not coefficient.source_reference.strip():
        raise FittingCalculationError(f"Coefficient {coefficient.coefficient_id} requires a source reference.")
    try:
        FittingType(coefficient.fitting_type)
        ReferenceVelocity(coefficient.reference_velocity)
        CoefficientSource(coefficient.source)
        CoefficientValidity(coefficient.validity)
    except (TypeError, ValueError) as exc:
        raise FittingCalculationError("Fitting coefficient metadata is invalid.") from exc
    for label, value in (
        ("minimum Reynolds number", coefficient.reynolds_min),
        ("maximum Reynolds number", coefficient.reynolds_max),
        ("minimum aspect ratio", coefficient.aspect_ratio_min),
        ("maximum aspect ratio", coefficient.aspect_ratio_max),
    ):
        if value is not None and not _positive(value):
            raise FittingCalculationError(f"{label} must be finite and positive when supplied.")
    if coefficient.reynolds_min is not None and coefficient.reynolds_max is not None and coefficient.reynolds_max < coefficient.reynolds_min:
        raise FittingCalculationError("Coefficient Reynolds range is reversed.")
    if coefficient.aspect_ratio_min is not None and coefficient.aspect_ratio_max is not None and coefficient.aspect_ratio_max < coefficient.aspect_ratio_min:
        raise FittingCalculationError("Coefficient aspect-ratio range is reversed.")
    if CoefficientValidity(coefficient.validity) is CoefficientValidity.UNSUPPORTED:
        raise FittingCalculationError(f"Coefficient {coefficient.coefficient_id} is marked UNSUPPORTED.")


def calculate_fitting_loss(fitting: FittingInput) -> FittingLossResult:
    """Calculate `ΔP = K rho Vref²/2` using an explicit coefficient and Vref."""
    if not isinstance(fitting.fitting_id, str) or not fitting.fitting_id.strip():
        raise FittingCalculationError("Fitting ID must be non-empty.")
    if not isinstance(fitting.segment_id, str) or not fitting.segment_id.strip():
        raise FittingCalculationError("Fitting segment ID must be non-empty.")
    if not _nonnegative(fitting.airflow_cfm):
        raise FittingCalculationError("Fitting airflow must be finite and non-negative.")
    if not _positive(fitting.air_density_kg_m3):
        raise FittingCalculationError("Air density must be finite and positive.")
    expected_units = {
        "airflow_unit": "CFM",
        "air_density_unit": "kg/m3",
        "geometry_unit": "in",
        "reference_velocity_unit": "FPM",
    }
    for field_name, expected in expected_units.items():
        if getattr(fitting, field_name) != expected:
            raise FittingCalculationError(
                f"Invalid {field_name}: expected {expected}, got {getattr(fitting, field_name)!r}."
            )
    try:
        fitting_type = FittingType(fitting.fitting_type)
    except (TypeError, ValueError) as exc:
        raise FittingCalculationError("Fitting type is invalid.") from exc
    validate_coefficient(fitting.coefficient)
    if FittingType(fitting.coefficient.fitting_type) is not fitting_type:
        raise FittingCalculationError("Coefficient fitting type does not match fitting input type.")
    _validate_geometry(fitting.geometry)
    reference = ReferenceVelocity(fitting.coefficient.reference_velocity)
    if fitting.airflow_cfm == 0:
        velocity_fpm = 0.0
    elif fitting.reference_velocity_fpm is not None:
        if not _nonnegative(fitting.reference_velocity_fpm):
            raise FittingCalculationError("Explicit reference velocity must be finite and non-negative.")
        velocity_fpm = float(fitting.reference_velocity_fpm)
    elif reference is ReferenceVelocity.USER_DEFINED:
        raise FittingCalculationError("USER_DEFINED reference velocity requires reference_velocity_fpm.")
    else:
        area_m2 = _reference_area(fitting.geometry, reference)
        velocity_fpm = fitting.airflow_cfm * CFM_TO_M3_S / area_m2 / FPM_TO_M_S
    velocity_m_s = velocity_fpm * FPM_TO_M_S
    dynamic_pa = 0.5 * fitting.air_density_kg_m3 * velocity_m_s**2
    pressure_pa = fitting.coefficient.k * dynamic_pa
    for value, label in ((velocity_fpm, "reference velocity"), (dynamic_pa, "dynamic pressure"), (pressure_pa, "pressure loss")):
        if not _number(value):
            raise FittingCalculationError(f"Calculated {label} is not finite.")
    status = FittingCalculationStatus.ZERO_FLOW if fitting.airflow_cfm == 0 else FittingCalculationStatus.COMPLETE
    return FittingLossResult(
        fitting_id=fitting.fitting_id,
        segment_id=fitting.segment_id,
        fitting_type=fitting_type,
        coefficient_id=fitting.coefficient.coefficient_id,
        k=float(fitting.coefficient.k),
        reference_velocity=reference,
        reference_velocity_fpm=velocity_fpm,
        dynamic_pressure_pa=dynamic_pa,
        dynamic_pressure_inwg=dynamic_pa / PA_PER_IN_WG,
        pressure_loss_pa=pressure_pa,
        pressure_loss_inwg=pressure_pa / PA_PER_IN_WG,
        coefficient_source=fitting.coefficient.source,
        source_reference=fitting.coefficient.source_reference,
        status=status,
    )


def calculate_fitting_losses(fittings: Iterable[FittingInput]) -> tuple[FittingLossResult, ...]:
    """Calculate a deterministic collection and reject duplicate fitting IDs."""
    rows = tuple(fittings)
    seen: set[str] = set()
    results: list[FittingLossResult] = []
    for fitting in rows:
        if fitting.fitting_id in seen:
            raise FittingCalculationError(f"Duplicate fitting ID {fitting.fitting_id}.")
        seen.add(fitting.fitting_id)
        results.append(calculate_fitting_loss(fitting))
    return tuple(sorted(results, key=lambda item: item.fitting_id))


def integrate_fitting_losses(
    fittings: Iterable[FittingInput],
    *,
    segment_id: str,
    existing_segment_fitting_loss_inwg: float | None = None,
    tolerance: float = 1e-9,
) -> FittingIntegrationResult:
    """Aggregate fitting losses and explicitly prevent segment double counting."""
    if not isinstance(segment_id, str) or not segment_id.strip():
        raise FittingCalculationError("Integration segment ID must be non-empty.")
    if not _nonnegative(tolerance):
        raise FittingCalculationError("Integration tolerance must be finite and non-negative.")
    results = calculate_fitting_losses(fittings)
    if any(result.segment_id != segment_id for result in results):
        raise FittingCalculationError("Every fitting must reference the integration segment.")
    aggregate = math.fsum(result.pressure_loss_inwg for result in results)
    if not _number(aggregate):
        raise FittingCalculationError("Aggregate fitting pressure loss is not finite.")
    already_included = existing_segment_fitting_loss_inwg is not None
    if already_included:
        if not _nonnegative(existing_segment_fitting_loss_inwg):
            raise FittingCalculationError("Existing segment fitting loss must be finite and non-negative.")
        if not math.isclose(aggregate, existing_segment_fitting_loss_inwg, rel_tol=tolerance, abs_tol=tolerance):
            raise FittingCalculationError(
                f"Fitting aggregate {aggregate:g} in.w.g. does not match existing segment fitting loss "
                f"{existing_segment_fitting_loss_inwg:g} in.w.g.; refusing double counting."
            )
    return FittingIntegrationResult(
        segment_id=segment_id,
        fitting_results=results,
        aggregate_fitting_loss_inwg=aggregate,
        existing_segment_fitting_loss_inwg=existing_segment_fitting_loss_inwg,
        already_included_in_segment_total=already_included,
        double_counting_prevented=already_included,
    )


__all__ = [
    "FittingCalculationError",
    "calculate_fitting_loss",
    "calculate_fitting_losses",
    "integrate_fitting_losses",
    "validate_coefficient",
]
