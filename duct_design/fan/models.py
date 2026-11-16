"""Typed inputs and outputs for fan pressure-budget calculations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FanSystemType(StrEnum):
    SUPPLY = "SUPPLY"
    RETURN = "RETURN"
    COMPLETE = "COMPLETE"


class PressureUnit(StrEnum):
    IN_WG = "in.w.g."
    PA = "Pa"
    KPA = "kPa"


class PressureComponentType(StrEnum):
    DUCT_FRICTION = "DUCT_FRICTION"
    FITTING = "FITTING"
    FILTER = "FILTER"
    COOLING_COIL = "COOLING_COIL"
    HEATING_COIL = "HEATING_COIL"
    DAMPER = "DAMPER"
    EQUIPMENT = "EQUIPMENT"
    LOUVER = "LOUVER"
    ATTENUATOR = "ATTENUATOR"
    TERMINAL = "TERMINAL"
    OTHER = "OTHER"


class PressureDataSource(StrEnum):
    CALCULATED = "CALCULATED"
    MANUFACTURER = "MANUFACTURER"
    MEASURED = "MEASURED"


class BoundaryClassification(StrEnum):
    EXTERNAL = "EXTERNAL"
    INTERNAL = "INTERNAL"


class FanCalculationStatus(StrEnum):
    COMPLETE_MODELLED = "COMPLETE_MODELLED"
    PRELIMINARY = "PRELIMINARY"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PressureContribution:
    """One traceable pressure-loss contribution at the declared airflow."""

    component_id: str
    component_type: PressureComponentType
    pressure_loss: float
    pressure_unit: PressureUnit
    airflow_cfm: float
    data_source: PressureDataSource
    boundary: BoundaryClassification
    network_id: str | None = None
    segment_id: str | None = None
    reference_velocity_fpm: float | None = None


@dataclass(frozen=True)
class FanPressurePath:
    """A single source-to-outlet or inlet-to-fan pressure path."""

    path_id: str
    system_type: FanSystemType
    network_id: str | None
    design_airflow_cfm: float
    contributions: tuple[PressureContribution, ...]


@dataclass(frozen=True)
class FanPressureInput:
    """Fan boundary and pressure paths selected for one calculation."""

    fan_id: str
    system_type: FanSystemType
    design_airflow_cfm: float
    paths: tuple[FanPressurePath, ...]
    fan_inlet_reference: str | None
    fan_outlet_reference: str | None
    air_density_kg_m3: float = 1.204
    inlet_velocity_fpm: float = 0.0
    outlet_velocity_fpm: float = 0.0
    boundaries_defined: bool = False
    known_components_complete: bool = False


@dataclass(frozen=True)
class PressureBudgetLine:
    """Normalized contribution retained in the calculation trace."""

    component_id: str
    component_type: PressureComponentType
    system_type: FanSystemType
    pressure_loss_inwg: float
    airflow_cfm: float
    data_source: PressureDataSource
    boundary: BoundaryClassification
    network_id: str | None
    segment_id: str | None
    reference_velocity_fpm: float | None


@dataclass(frozen=True)
class FanPressureDiagnostic:
    code: str
    message: str
    component_id: str | None = None
    path_id: str | None = None


@dataclass(frozen=True)
class FanPressureResult:
    """Traceable fan pressure budget; FESP is never claimed without boundaries."""

    fan_id: str
    system_type: FanSystemType
    pressure_unit: PressureUnit
    design_airflow_cfm: float
    supply_pressure_loss_inwg: float | None
    return_pressure_loss_inwg: float | None
    component_pressure_loss_inwg: float | None
    inlet_velocity_pressure_inwg: float | None
    outlet_velocity_pressure_inwg: float | None
    fan_total_pressure_inwg: float | None
    fan_external_static_pressure_inwg: float | None
    fesp_is_authoritative: bool
    status: FanCalculationStatus
    lines: tuple[PressureBudgetLine, ...]
    diagnostics: tuple[FanPressureDiagnostic, ...]
    assumptions: tuple[str, ...]
