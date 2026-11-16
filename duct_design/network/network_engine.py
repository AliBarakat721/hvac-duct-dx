"""Orchestration of network validation, airflow, and existing section sizing."""
from __future__ import annotations

import math

from ..engine import (
    DuctDesignCalculationError,
    DuctDesignOptions,
    DuctSegmentInput,
    DuctSizingResult,
    evaluate_rectangular_duct,
    run_design,
    validate_design_inputs,
)
from .flow_solver import _solve_topology
from .models import (
    AirflowSource,
    CalculationStatus,
    DiagnosticSeverity,
    DuctNetwork,
    DuctNetworkResult,
    DuctNetworkSegmentResult,
    DuctTerminalResult,
    NetworkDiagnostic,
    NetworkType,
    NetworkValidationPolicy,
    NetworkValidationResult,
    ValidationStatus,
)
from .topology import build_network_topology
from .validation import DuctNetworkCalculationError


def _validate_policy(policy: NetworkValidationPolicy) -> None:
    for label, value in (
        ("absolute airflow tolerance", policy.absolute_tolerance_cfm),
        ("relative airflow tolerance", policy.relative_tolerance),
    ):
        try:
            is_finite = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            is_finite = False
        if not is_finite or value < 0:
            raise DuctNetworkCalculationError(f"{label} must be finite and non-negative.")
    if policy.mismatch_severity not in (DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR):
        raise DuctNetworkCalculationError("Airflow mismatch severity must be WARNING or ERROR.")


def _flow_matches(
    specified_cfm: float,
    calculated_cfm: float,
    policy: NetworkValidationPolicy,
) -> bool:
    tolerance = max(
        policy.absolute_tolerance_cfm,
        policy.relative_tolerance * calculated_cfm,
    )
    return abs(specified_cfm - calculated_cfm) <= tolerance


def _status(diagnostics: tuple[NetworkDiagnostic, ...]) -> ValidationStatus:
    if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
        return ValidationStatus.ERROR
    if any(item.severity is DiagnosticSeverity.WARNING for item in diagnostics):
        return ValidationStatus.WARNING
    return ValidationStatus.PASS


def _is_on_size_grid(dimension_in: float, size_step_in: float) -> bool:
    grid_index = dimension_in / size_step_in
    return math.isfinite(grid_index) and math.isclose(
        grid_index,
        round(grid_index),
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def _size_segment(
    segment_id: str,
    airflow_cfm: float,
    length_ft: float,
    fitting_k: float,
    width_in: float | None,
    height_in: float | None,
    options: DuctDesignOptions,
) -> DuctSizingResult:
    existing_input = DuctSegmentInput(
        tag=segment_id,
        airflow_cfm=airflow_cfm,
        length_ft=length_ft,
        fitting_k=fitting_k,
    )
    if width_in is not None and height_in is not None:
        validated_input = validate_design_inputs((existing_input,), options)[0]
        result = evaluate_rectangular_duct(validated_input, width_in, height_in, options)
    else:
        result = run_design((existing_input,), options)[0]
    for field_name in result.__dataclass_fields__:
        value = getattr(result, field_name)
        if isinstance(value, (int, float)):
            try:
                is_finite = math.isfinite(value)
            except OverflowError:
                is_finite = False
            if not is_finite:
                raise DuctNetworkCalculationError(
                    f"Sizing engine returned a non-finite {field_name} for segment {segment_id}."
                )
    return result


def calculate_duct_network(
    network: DuctNetwork,
    options: DuctDesignOptions,
    validation_policy: NetworkValidationPolicy = NetworkValidationPolicy(),
) -> DuctNetworkResult:
    """Validate a rooted network, calculate demands, and size its duct segments.

    Calculated downstream demand is used for sizing. Explicit segment airflow is
    retained and compared against that demand; it is never silently substituted.
    Segment pressure results remain individual section results. Network critical
    path pressure and fan external static pressure are not calculated.
    """
    _validate_policy(validation_policy)
    topology = build_network_topology(network)
    airflow = _solve_topology(topology)
    diagnostics = list(airflow.diagnostics)
    segment_results: list[DuctNetworkSegmentResult] = []
    segment_diagnostics: dict[str, tuple[NetworkDiagnostic, ...]] = {}

    for segment_id, segment in sorted(topology.segments_by_id.items()):
        calculated_flow = airflow.segment_airflow_cfm[segment_id]
        local_diagnostics: list[NetworkDiagnostic] = []
        explicit_matches = True
        if segment.airflow_cfm is not None:
            explicit_matches = _flow_matches(
                segment.airflow_cfm, calculated_flow, validation_policy
            )
            if not explicit_matches:
                delta = abs(segment.airflow_cfm - calculated_flow)
                relative = (
                    f"{delta / calculated_flow * 100:.3g}%"
                    if calculated_flow > 0
                    else "undefined because calculated demand is zero"
                )
                local_diagnostics.append(NetworkDiagnostic(
                    validation_policy.mismatch_severity,
                    "segment_airflow_mismatch",
                    f"Segment {segment_id} specifies {segment.airflow_cfm:g} CFM, "
                    f"but downstream terminal demand is {calculated_flow:g} CFM "
                    f"(difference {delta:g} CFM; relative difference {relative}).",
                    segment_id,
                ))

        sizing_result: DuctSizingResult | None = None
        dimensions_valid = True
        if segment.width_in is not None and segment.height_in is not None:
            aspect_ratio = max(segment.width_in, segment.height_in) / min(
                segment.width_in, segment.height_in
            )
            dimension_issues = []
            if not options.min_width_in <= segment.width_in <= options.max_width_in:
                dimension_issues.append(
                    f"width {segment.width_in:g} in is outside "
                    f"{options.min_width_in:g}–{options.max_width_in:g} in"
                )
            if not options.min_height_in <= segment.height_in <= options.max_height_in:
                dimension_issues.append(
                    f"height {segment.height_in:g} in is outside "
                    f"{options.min_height_in:g}–{options.max_height_in:g} in"
                )
            if aspect_ratio > options.max_aspect_ratio:
                dimension_issues.append(
                    f"aspect ratio {aspect_ratio:g}:1 exceeds "
                    f"{options.max_aspect_ratio:g}:1"
                )
            if dimension_issues:
                dimensions_valid = False
                local_diagnostics.append(NetworkDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "specified_size_exceeds_criteria",
                    f"Specified dimensions for segment {segment_id} do not meet "
                    f"the sizing criteria: {'; '.join(dimension_issues)}.",
                    segment_id,
                ))
        if calculated_flow > 0 and dimensions_valid:
            try:
                sizing_result = _size_segment(
                    segment_id,
                    calculated_flow,
                    segment.length_ft,
                    segment.fitting_k,
                    segment.width_in,
                    segment.height_in,
                    options,
                )
            except (DuctDesignCalculationError, ArithmeticError) as exc:
                local_diagnostics.append(NetworkDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "segment_sizing_failed",
                    f"Could not size segment {segment_id}: {exc}",
                    segment_id,
                ))
        elif calculated_flow == 0:
            local_diagnostics.append(NetworkDiagnostic(
                DiagnosticSeverity.INFO,
                "zero_flow_not_sized",
                f"Segment {segment_id} has zero downstream demand; velocity and pressure "
                "loss are not calculated.",
                segment_id,
            ))

        if sizing_result:
            if segment.width_in is not None and segment.height_in is not None:
                off_grid = tuple(
                    dimension
                    for dimension in (segment.width_in, segment.height_in)
                    if not _is_on_size_grid(dimension, options.size_step_in)
                )
                if off_grid:
                    local_diagnostics.append(NetworkDiagnostic(
                        DiagnosticSeverity.ERROR,
                        "specified_size_off_grid",
                        f"Specified dimensions for segment {segment_id} must align "
                        f"with the {options.size_step_in:g} in size increment.",
                        segment_id,
                    ))
                if sizing_result.velocity_fpm > options.max_velocity_fpm:
                    local_diagnostics.append(NetworkDiagnostic(
                        DiagnosticSeverity.ERROR,
                        "specified_size_exceeds_criteria",
                        f"Specified dimensions for segment {segment_id} do not meet "
                        f"the sizing criteria: velocity {sizing_result.velocity_fpm:g} "
                        f"fpm exceeds {options.max_velocity_fpm:g} fpm.",
                        segment_id,
                    ))
                if sizing_result.friction_rate_inwg_100ft > options.max_friction_inwg_100ft:
                    local_diagnostics.append(NetworkDiagnostic(
                        DiagnosticSeverity.ERROR,
                        "specified_size_exceeds_criteria",
                        f"Specified dimensions for segment {segment_id} do not meet "
                        f"the sizing criteria: friction rate "
                        f"{sizing_result.friction_rate_inwg_100ft:g} in.w.g./100 ft "
                        f"exceeds {options.max_friction_inwg_100ft:g} in.w.g./100 ft.",
                        segment_id,
                    ))
            for warning in sizing_result.warnings:
                local_diagnostics.append(NetworkDiagnostic(
                    DiagnosticSeverity.WARNING,
                    "sizing_warning",
                    f"Segment {segment_id}: {warning}",
                    segment_id,
                ))

        local = tuple(local_diagnostics)
        segment_diagnostics[segment_id] = local
        diagnostics.extend(local)
        if segment.airflow_cfm is not None and not explicit_matches:
            airflow_source = AirflowSource.EXPLICIT_MISMATCH
        elif calculated_flow == 0:
            airflow_source = AirflowSource.ZERO_DEMAND
        elif segment.airflow_cfm is None:
            airflow_source = AirflowSource.CALCULATED
        else:
            airflow_source = AirflowSource.EXPLICIT_VALIDATED
        segment_results.append(DuctNetworkSegmentResult(
            segment_id=segment_id,
            start_node_id=segment.start_node_id,
            end_node_id=segment.end_node_id,
            calculated_airflow_cfm=calculated_flow,
            specified_airflow_cfm=segment.airflow_cfm,
            sizing_result=sizing_result,
            airflow_source=airflow_source,
            validation_status=_status(local),
            diagnostics=local,
        ))

    if network.source_airflow_cfm is not None and not _flow_matches(
        network.source_airflow_cfm, airflow.source_airflow_cfm, validation_policy
    ):
        delta = abs(network.source_airflow_cfm - airflow.source_airflow_cfm)
        relative = (
            f"{delta / airflow.source_airflow_cfm * 100:.3g}%"
            if airflow.source_airflow_cfm > 0
            else "undefined because calculated source airflow is zero"
        )
        diagnostics.append(NetworkDiagnostic(
            validation_policy.mismatch_severity,
            "source_airflow_mismatch",
            f"Specified source airflow is {network.source_airflow_cfm:g} CFM, "
            f"but calculated terminal demand is {airflow.source_airflow_cfm:g} CFM "
            f"(source minus calculated = "
            f"{network.source_airflow_cfm - airflow.source_airflow_cfm:g} CFM; "
            f"absolute error {delta:g} CFM; relative error {relative}).",
            network.source_node_id,
        ))

    all_diagnostics = tuple(sorted(
        diagnostics,
        key=lambda item: (item.code, item.element_id or "", item.message),
    ))
    validation = NetworkValidationResult(
        is_valid=not any(item.severity is DiagnosticSeverity.ERROR for item in all_diagnostics),
        diagnostics=all_diagnostics,
    )
    source_errors = tuple(
        item for item in all_diagnostics
        if item.element_id == network.source_node_id
    )
    terminal_results: list[DuctTerminalResult] = []
    for terminal_id, terminal in sorted(topology.terminals_by_id.items()):
        path_diagnostics = [
            item
            for segment_id in topology.terminal_paths[terminal_id]
            for item in segment_diagnostics[segment_id]
        ]
        path_diagnostics.extend(source_errors)
        terminal_results.append(DuctTerminalResult(
            terminal_id=terminal_id,
            node_id=terminal.node_id,
            required_airflow_cfm=terminal.required_airflow_cfm,
            connected_segment_id=topology.terminal_segment_by_id[terminal_id],
            upstream_path_segment_ids=topology.terminal_paths[terminal_id],
            validation_status=_status(tuple(path_diagnostics)),
        ))

    if not validation.is_valid:
        calculation_status = CalculationStatus.VALIDATION_FAILED
    elif any(item.severity is DiagnosticSeverity.WARNING for item in all_diagnostics):
        calculation_status = CalculationStatus.COMPLETE_WITH_WARNINGS
    else:
        calculation_status = CalculationStatus.COMPLETE
    return DuctNetworkResult(
        network_id=network.network_id,
        network_type=NetworkType(network.network_type),
        source_node_id=network.source_node_id,
        specified_source_airflow_cfm=network.source_airflow_cfm,
        calculated_airflow_cfm=airflow.source_airflow_cfm,
        segments=tuple(segment_results),
        terminals=tuple(terminal_results),
        validation=validation,
        warnings=tuple(item.message for item in all_diagnostics if item.severity is DiagnosticSeverity.WARNING),
        calculation_status=calculation_status,
    )
