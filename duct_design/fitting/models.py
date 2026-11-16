"""Typed models for fitting coefficients and dynamic-loss calculations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FittingType(StrEnum):
    ELBOW_90_RECTANGULAR = "ELBOW_90_RECTANGULAR"
    ELBOW_45_RECTANGULAR = "ELBOW_45_RECTANGULAR"
    ELBOW_RECTANGULAR_TURNING_VANES = "ELBOW_RECTANGULAR_TURNING_VANES"
    ELBOW_RECTANGULAR_NO_TURNING_VANES = "ELBOW_RECTANGULAR_NO_TURNING_VANES"
    ELBOW_ROUND = "ELBOW_ROUND"
    TEE_STRAIGHT_THROUGH = "TEE_STRAIGHT_THROUGH"
    TEE_BRANCH = "TEE_BRANCH"
    WYE = "WYE"
    LATERAL = "LATERAL"
    TAKEOFF = "TAKEOFF"
    TRANSITION_RECTANGULAR = "TRANSITION_RECTANGULAR"
    TRANSITION_ROUND = "TRANSITION_ROUND"
    TRANSITION_RECTANGULAR_TO_ROUND = "TRANSITION_RECTANGULAR_TO_ROUND"
    TRANSITION_CONVERGING = "TRANSITION_CONVERGING"
    TRANSITION_DIVERGING = "TRANSITION_DIVERGING"
    SUDDEN_EXPANSION = "SUDDEN_EXPANSION"
    SUDDEN_CONTRACTION = "SUDDEN_CONTRACTION"
    ENTRANCE = "ENTRANCE"
    EXIT = "EXIT"
    DAMPER = "DAMPER"
    LOUVER = "LOUVER"
    SCREEN = "SCREEN"
    FLEXIBLE_CONNECTION = "FLEXIBLE_CONNECTION"
    OTHER = "OTHER"


class ReferenceVelocity(StrEnum):
    INLET = "INLET"
    OUTLET = "OUTLET"
    BRANCH = "BRANCH"
    STRAIGHT_THROUGH = "STRAIGHT_THROUGH"
    USER_DEFINED = "USER_DEFINED"


class CoefficientSource(StrEnum):
    USER_SUPPLIED = "USER_SUPPLIED"
    MANUFACTURER = "MANUFACTURER"
    VERIFIED_REFERENCE = "VERIFIED_REFERENCE"
    ESTIMATE = "ESTIMATE"


class CoefficientValidity(StrEnum):
    VERIFIED = "VERIFIED"
    PROVISIONAL = "PROVISIONAL"
    UNSUPPORTED = "UNSUPPORTED"


class FittingCalculationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    ZERO_FLOW = "ZERO_FLOW"


@dataclass(frozen=True)
class FittingGeometry:
    """Optional geometry used to derive the selected reference velocity."""

    inlet_width_in: float | None = None
    inlet_height_in: float | None = None
    outlet_width_in: float | None = None
    outlet_height_in: float | None = None
    inlet_diameter_in: float | None = None
    outlet_diameter_in: float | None = None
    branch_width_in: float | None = None
    branch_height_in: float | None = None
    branch_diameter_in: float | None = None
    angle_deg: float | None = None
    radius_in: float | None = None
    transition_length_in: float | None = None
    turning_vane_count: int | None = None


@dataclass(frozen=True)
class FittingCoefficient:
    """A coefficient with explicit provenance and applicability metadata."""

    coefficient_id: str
    fitting_type: FittingType
    k: float
    reference_velocity: ReferenceVelocity
    source: CoefficientSource
    source_reference: str
    validity: CoefficientValidity = CoefficientValidity.PROVISIONAL
    reynolds_min: float | None = None
    reynolds_max: float | None = None
    aspect_ratio_min: float | None = None
    aspect_ratio_max: float | None = None
    restrictions: str | None = None


@dataclass(frozen=True)
class FittingInput:
    fitting_id: str
    segment_id: str
    fitting_type: FittingType
    airflow_cfm: float
    air_density_kg_m3: float
    coefficient: FittingCoefficient
    geometry: FittingGeometry = FittingGeometry()
    reference_velocity_fpm: float | None = None
    airflow_unit: str = "CFM"
    air_density_unit: str = "kg/m3"
    geometry_unit: str = "in"
    reference_velocity_unit: str = "FPM"


@dataclass(frozen=True)
class FittingLossResult:
    fitting_id: str
    segment_id: str
    fitting_type: FittingType
    coefficient_id: str
    k: float
    reference_velocity: ReferenceVelocity
    reference_velocity_fpm: float
    dynamic_pressure_pa: float
    dynamic_pressure_inwg: float
    pressure_loss_pa: float
    pressure_loss_inwg: float
    coefficient_source: CoefficientSource
    source_reference: str
    status: FittingCalculationStatus


@dataclass(frozen=True)
class FittingIntegrationResult:
    segment_id: str
    fitting_results: tuple[FittingLossResult, ...]
    aggregate_fitting_loss_inwg: float
    existing_segment_fitting_loss_inwg: float | None
    already_included_in_segment_total: bool
    double_counting_prevented: bool
