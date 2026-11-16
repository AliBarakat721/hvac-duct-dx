"""Validated fan total-pressure and external-static pressure budgeting."""
from __future__ import annotations

import math

from ..engine import PA_PER_IN_WG
from ..network.pressure_analysis import CriticalPathAnalysisResult, PressureAnalysisStatus
from .models import (
    BoundaryClassification,
    FanCalculationStatus,
    FanPressureDiagnostic,
    FanPressureInput,
    FanPressurePath,
    FanPressureResult,
    FanSystemType,
    PressureBudgetLine,
    PressureComponentType,
    PressureContribution,
    PressureDataSource,
    PressureUnit,
)

CFM_TO_M3_S = 0.0004719474432
FPM_TO_M_S = 0.00508


class FanPressureCalculationError(ValueError):
    """Invalid fan pressure input or unsupported calculation request."""


def pressure_to_inwg(value: float, unit: PressureUnit) -> float:
    """Convert a validated pressure value to inches water gauge."""
    try:
        unit = PressureUnit(unit)
    except (TypeError, ValueError) as exc:
        raise FanPressureCalculationError(f"Unsupported pressure unit {unit!r}.") from exc
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise FanPressureCalculationError("Pressure must be finite.")
    if unit is PressureUnit.IN_WG:
        return float(value)
    if unit is PressureUnit.PA:
        return float(value) / PA_PER_IN_WG
    return float(value) * 1000.0 / PA_PER_IN_WG


def velocity_pressure_inwg(air_density_kg_m3: float, velocity_fpm: float) -> float:
    """Return rho V²/2 converted from Pa to in.w.g."""
    if not _finite_positive(air_density_kg_m3) or not _finite_nonnegative(velocity_fpm):
        raise FanPressureCalculationError("Air density must be positive and velocity must be finite and non-negative.")
    dynamic_pa = 0.5 * air_density_kg_m3 * (velocity_fpm * FPM_TO_M_S) ** 2
    return dynamic_pa / PA_PER_IN_WG


def pressure_path_from_critical_path(
    analysis: CriticalPathAnalysisResult,
    *,
    airflow_cfm: float,
    boundary: BoundaryClassification = BoundaryClassification.EXTERNAL,
    path_id: str | None = None,
) -> FanPressurePath:
    """Adapt the verified Phase 3 critical path without duplicating losses."""
    if analysis.status is not PressureAnalysisStatus.COMPLETE or not analysis.critical_terminal_id:
        raise FanPressureCalculationError(
            "A complete critical SUPPLY path is required; no pressure path was fabricated."
        )
    if not _finite_positive(airflow_cfm):
        raise FanPressureCalculationError("Path airflow must be finite and positive.")
    contributions: list[PressureContribution] = []
    for segment in analysis.critical_path_segments:
        contributions.extend((
            PressureContribution(
                component_id=f"{segment.segment_id}:straight",
                component_type=PressureComponentType.DUCT_FRICTION,
                pressure_loss=segment.straight_loss_inwg,
                pressure_unit=PressureUnit.IN_WG,
                airflow_cfm=airflow_cfm,
                data_source=PressureDataSource.CALCULATED,
                boundary=boundary,
                network_id=analysis.network_id,
                segment_id=segment.segment_id,
            ),
            PressureContribution(
                component_id=f"{segment.segment_id}:fitting",
                component_type=PressureComponentType.FITTING,
                pressure_loss=segment.fitting_loss_inwg,
                pressure_unit=PressureUnit.IN_WG,
                airflow_cfm=airflow_cfm,
                data_source=PressureDataSource.CALCULATED,
                boundary=boundary,
                network_id=analysis.network_id,
                segment_id=segment.segment_id,
            ),
        ))
    return FanPressurePath(
        path_id=path_id or f"{analysis.network_id}:{analysis.critical_terminal_id}",
        system_type=FanSystemType.SUPPLY,
        network_id=analysis.network_id,
        design_airflow_cfm=airflow_cfm,
        contributions=tuple(contributions),
    )


def _finite_positive(value: object) -> bool:
    return _finite_number(value) and value > 0


def _finite_nonnegative(value: object) -> bool:
    return _finite_number(value) and value >= 0


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _component_diagnostic(code: str, message: str, component_id: str | None = None, path_id: str | None = None):
    return FanPressureDiagnostic(code, message, component_id, path_id)


def _selected_paths(fan_input: FanPressureInput) -> tuple[FanPressurePath, ...]:
    expected = {
        FanSystemType.SUPPLY: {FanSystemType.SUPPLY},
        FanSystemType.RETURN: {FanSystemType.RETURN},
        FanSystemType.COMPLETE: {FanSystemType.SUPPLY, FanSystemType.RETURN},
    }[FanSystemType(fan_input.system_type)]
    return tuple(path for path in fan_input.paths if FanSystemType(path.system_type) in expected)


def calculate_fan_pressure(fan_input: FanPressureInput) -> FanPressureResult:
    """Calculate a traceable modeled pressure budget for the selected fan circuit.

    Component losses are summed once after unit normalization. Fan total pressure
    includes all selected component losses and the outlet-minus-inlet velocity
    pressure correction. External static pressure includes only contributions
    classified EXTERNAL and is returned only when reference planes and boundaries
    are explicitly defined. It is not an unconditional equipment/FESP claim.
    """
    diagnostics: list[FanPressureDiagnostic] = []
    try:
        system_type = FanSystemType(fan_input.system_type)
    except (TypeError, ValueError):
        raise FanPressureCalculationError(f"Unsupported fan system type {fan_input.system_type!r}.")
    if not isinstance(fan_input.fan_id, str) or not fan_input.fan_id.strip():
        diagnostics.append(_component_diagnostic("invalid_fan_id", "Fan ID must be a non-empty string."))
    if not _finite_positive(fan_input.design_airflow_cfm):
        diagnostics.append(_component_diagnostic("invalid_design_airflow", "Design airflow must be finite and positive."))
    if not _finite_positive(fan_input.air_density_kg_m3):
        diagnostics.append(_component_diagnostic("invalid_air_density", "Air density must be finite and positive."))
    if not _finite_nonnegative(fan_input.inlet_velocity_fpm) or not _finite_nonnegative(fan_input.outlet_velocity_fpm):
        diagnostics.append(_component_diagnostic("invalid_boundary_velocity", "Boundary velocities must be finite and non-negative."))

    try:
        selected_paths = _selected_paths(fan_input)
    except (KeyError, ValueError):
        selected_paths = ()
        diagnostics.append(_component_diagnostic("invalid_system_type", "Fan system type is not supported."))
    expected_types = {
        FanSystemType.SUPPLY: {FanSystemType.SUPPLY},
        FanSystemType.RETURN: {FanSystemType.RETURN},
        FanSystemType.COMPLETE: {FanSystemType.SUPPLY, FanSystemType.RETURN},
    }.get(system_type, set())
    for path_candidate in fan_input.paths:
        try:
            candidate_type = FanSystemType(path_candidate.system_type)
        except (TypeError, ValueError):
            continue
        if candidate_type not in expected_types:
            diagnostics.append(_component_diagnostic(
                "unselected_path",
                f"Path {path_candidate.path_id} is not part of the explicitly selected {system_type} circuit.",
                path_id=path_candidate.path_id,
            ))
    expected_count = 2 if system_type is FanSystemType.COMPLETE else 1
    if len(selected_paths) != expected_count:
        diagnostics.append(_component_diagnostic(
            "incomplete_system_paths",
            f"{system_type} calculation requires {expected_count} selected pressure path(s); found {len(selected_paths)}.",
        ))
    if system_type is FanSystemType.COMPLETE and {path.system_type for path in selected_paths} != {FanSystemType.SUPPLY, FanSystemType.RETURN}:
        diagnostics.append(_component_diagnostic("missing_supply_or_return_path", "COMPLETE requires one SUPPLY and one RETURN path."))

    path_ids: set[str] = set()
    component_ids: set[str] = set()
    lines: list[PressureBudgetLine] = []
    path_totals: dict[FanSystemType, list[float]] = {FanSystemType.SUPPLY: [], FanSystemType.RETURN: []}
    external_total = 0.0
    component_total = 0.0
    for path in selected_paths:
        path_type = FanSystemType(path.system_type)
        if not isinstance(path.path_id, str) or not path.path_id.strip() or path.path_id in path_ids:
            diagnostics.append(_component_diagnostic("duplicate_or_invalid_path_id", f"Invalid or duplicate path ID {path.path_id!r}." , path_id=path.path_id))
        path_ids.add(path.path_id)
        if not path.contributions:
            diagnostics.append(_component_diagnostic(
                "missing_pressure_contribution",
                f"Path {path.path_id} has no pressure contributions; no zero-loss default was applied.",
                path_id=path.path_id,
            ))
        if not _finite_positive(path.design_airflow_cfm):
            diagnostics.append(_component_diagnostic("invalid_path_airflow", f"Path {path.path_id} airflow must be finite and positive.", path_id=path.path_id))
        elif _finite_positive(fan_input.design_airflow_cfm) and not math.isclose(path.design_airflow_cfm, fan_input.design_airflow_cfm, rel_tol=1e-6, abs_tol=1e-6):
            diagnostics.append(_component_diagnostic("path_airflow_mismatch", f"Path {path.path_id} airflow does not match fan design airflow.", path_id=path.path_id))
        path_total = 0.0
        for component in path.contributions:
            if component.component_id in component_ids:
                diagnostics.append(_component_diagnostic("duplicate_component_id", f"Duplicate pressure component ID {component.component_id}.", component.component_id, path.path_id))
            component_ids.add(component.component_id)
            if not isinstance(component.component_id, str) or not component.component_id.strip():
                diagnostics.append(_component_diagnostic("invalid_component_id", "Component ID must be non-empty.", component.component_id, path.path_id))
            if not _finite_nonnegative(component.pressure_loss):
                diagnostics.append(_component_diagnostic("invalid_pressure_loss", f"Component {component.component_id} pressure loss must be finite and non-negative.", component.component_id, path.path_id))
                continue
            if not _finite_positive(component.airflow_cfm):
                diagnostics.append(_component_diagnostic("invalid_component_airflow", f"Component {component.component_id} airflow must be finite and positive.", component.component_id, path.path_id))
                continue
            try:
                loss_inwg = pressure_to_inwg(component.pressure_loss, component.pressure_unit)
                component_type = PressureComponentType(component.component_type)
                source = PressureDataSource(component.data_source)
                boundary = BoundaryClassification(component.boundary)
            except (FanPressureCalculationError, TypeError, ValueError) as exc:
                diagnostics.append(_component_diagnostic("invalid_component_metadata", str(exc), component.component_id, path.path_id))
                continue
            if component.reference_velocity_fpm is not None and not _finite_nonnegative(component.reference_velocity_fpm):
                diagnostics.append(_component_diagnostic("invalid_reference_velocity", f"Component {component.component_id} reference velocity is invalid.", component.component_id, path.path_id))
                continue
            path_total += loss_inwg
            component_total += loss_inwg
            if boundary is BoundaryClassification.EXTERNAL:
                external_total += loss_inwg
            lines.append(PressureBudgetLine(
                component_id=component.component_id,
                component_type=component_type,
                system_type=path_type,
                pressure_loss_inwg=loss_inwg,
                airflow_cfm=component.airflow_cfm,
                data_source=source,
                boundary=boundary,
                network_id=component.network_id or path.network_id,
                segment_id=component.segment_id,
                reference_velocity_fpm=component.reference_velocity_fpm,
            ))
        if math.isfinite(path_total):
            path_totals[path_type].append(path_total)
        else:
            diagnostics.append(_component_diagnostic("path_pressure_overflow", f"Path {path.path_id} pressure total is not finite.", path_id=path.path_id))

    if system_type is FanSystemType.COMPLETE:
        supply_total = math.fsum(path_totals[FanSystemType.SUPPLY]) if path_totals[FanSystemType.SUPPLY] else None
        return_total = math.fsum(path_totals[FanSystemType.RETURN]) if path_totals[FanSystemType.RETURN] else None
    elif system_type is FanSystemType.SUPPLY:
        supply_total = math.fsum(path_totals[FanSystemType.SUPPLY]) if path_totals[FanSystemType.SUPPLY] else None
        return_total = None
    else:
        supply_total = None
        return_total = math.fsum(path_totals[FanSystemType.RETURN]) if path_totals[FanSystemType.RETURN] else None

    blocking_codes = {
        "invalid_fan_id",
        "invalid_design_airflow",
        "invalid_air_density",
        "invalid_boundary_velocity",
        "invalid_system_type",
        "incomplete_system_paths",
        "missing_supply_or_return_path",
        "duplicate_or_invalid_path_id",
        "invalid_path_airflow",
        "path_airflow_mismatch",
        "duplicate_component_id",
        "invalid_component_id",
        "invalid_pressure_loss",
        "invalid_component_airflow",
        "invalid_component_metadata",
        "invalid_reference_velocity",
            "path_pressure_overflow",
            "missing_pressure_contribution",
            "unselected_path",
        }
    has_blocking_diagnostics = any(item.code in blocking_codes for item in diagnostics)
    inlet_vp = outlet_vp = total_pressure = external_static = None
    if _finite_positive(fan_input.air_density_kg_m3) and _finite_nonnegative(fan_input.inlet_velocity_fpm) and _finite_nonnegative(fan_input.outlet_velocity_fpm):
        inlet_vp = velocity_pressure_inwg(fan_input.air_density_kg_m3, fan_input.inlet_velocity_fpm)
        outlet_vp = velocity_pressure_inwg(fan_input.air_density_kg_m3, fan_input.outlet_velocity_fpm)
        if not has_blocking_diagnostics and math.isfinite(component_total):
            total_pressure = component_total + outlet_vp - inlet_vp
    if fan_input.boundaries_defined and fan_input.fan_inlet_reference and fan_input.fan_outlet_reference and not has_blocking_diagnostics:
        external_static = external_total
    elif fan_input.boundaries_defined:
        diagnostics.append(_component_diagnostic("incomplete_fan_reference_planes", "Both fan inlet and outlet reference planes are required for external static pressure."))
    else:
        diagnostics.append(_component_diagnostic("undefined_fan_boundaries", "Fan reference boundaries are not declared; FESP is not reported."))

    if has_blocking_diagnostics:
        status = FanCalculationStatus.INCOMPLETE
    elif (
        not fan_input.known_components_complete
        or not fan_input.boundaries_defined
        or not fan_input.fan_inlet_reference
        or not fan_input.fan_outlet_reference
    ):
        status = FanCalculationStatus.PRELIMINARY
    else:
        status = FanCalculationStatus.COMPLETE_MODELLED
    assumptions = (
        "Pressure losses are summed after conversion to in.w.g.; no missing component is treated as zero.",
        "Fan total pressure includes outlet velocity pressure minus inlet velocity pressure.",
        "External static pressure includes only components classified EXTERNAL and requires declared fan reference planes.",
        "This is a modeled pressure budget, not an unconditional equipment-certified FESP selection.",
    )
    return FanPressureResult(
        fan_id=fan_input.fan_id,
        system_type=system_type,
        pressure_unit=PressureUnit.IN_WG,
        design_airflow_cfm=fan_input.design_airflow_cfm,
        supply_pressure_loss_inwg=supply_total,
        return_pressure_loss_inwg=return_total,
        component_pressure_loss_inwg=component_total if not has_blocking_diagnostics else None,
        inlet_velocity_pressure_inwg=inlet_vp,
        outlet_velocity_pressure_inwg=outlet_vp,
        fan_total_pressure_inwg=total_pressure,
        fan_external_static_pressure_inwg=external_static,
        fesp_is_authoritative=False,
        status=status,
        lines=tuple(sorted(lines, key=lambda item: item.component_id)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.code, item.path_id or "", item.component_id or "", item.message))),
        assumptions=assumptions,
    )


__all__ = ["FanPressureCalculationError", "calculate_fan_pressure", "pressure_path_from_critical_path", "pressure_to_inwg", "velocity_pressure_inwg"]
