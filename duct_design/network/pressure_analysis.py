"""Critical source-to-terminal pressure-path analysis for supply networks."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from .models import (
    DiagnosticSeverity,
    DuctNetwork,
    DuctNetworkResult,
    NetworkDiagnostic,
    NetworkType,
)
from .topology import build_network_topology
from .validation import DuctNetworkCalculationError


class PressureAnalysisStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PressureSegmentResult:
    """Pressure-loss components copied from one existing sizing result."""

    segment_id: str
    straight_loss_inwg: float
    fitting_loss_inwg: float
    total_loss_inwg: float


@dataclass(frozen=True)
class PressureTerminalPath:
    """Accumulated pressure loss along one source-to-terminal path."""

    terminal_id: str
    node_id: str
    segment_ids: tuple[str, ...]
    segment_losses: tuple[PressureSegmentResult, ...]
    straight_loss_inwg: float | None
    fitting_loss_inwg: float | None
    total_loss_inwg: float | None
    difference_from_critical_inwg: float | None = None


@dataclass(frozen=True)
class CriticalPathAnalysisResult:
    """Critical modeled duct pressure path and all terminal path results."""

    network_id: str
    network_type: NetworkType
    pressure_unit: str
    critical_terminal_id: str | None
    critical_path_segment_ids: tuple[str, ...]
    critical_path_pressure_loss_inwg: float | None
    critical_path_segments: tuple[PressureSegmentResult, ...]
    terminal_paths: tuple[PressureTerminalPath, ...]
    diagnostics: tuple[NetworkDiagnostic, ...]
    status: PressureAnalysisStatus


_SUPPORTED_PRESSURE_UNIT = "in.w.g."


def _diagnostic(code: str, message: str, element_id: str | None = None) -> NetworkDiagnostic:
    return NetworkDiagnostic(DiagnosticSeverity.ERROR, code, message, element_id)


def _finite_nonnegative(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _sum_losses(values: tuple[float, ...]) -> float | None:
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError):
        return None
    return total if math.isfinite(total) else None


def analyze_critical_path(
    network: DuctNetwork,
    network_result: DuctNetworkResult,
    *,
    pressure_unit: str = _SUPPORTED_PRESSURE_UNIT,
    component_tolerance: float = 1e-9,
) -> CriticalPathAnalysisResult:
    """Analyze modeled pressure loss from the source to every terminal.

    The function consumes the existing network sizing result and never recalculates
    duct friction or fitting loss. Only the verified directed SUPPLY semantics are
    supported. Equal total losses select the lexicographically smallest terminal
    ID, making the result independent of input ordering.
    """
    if pressure_unit != _SUPPORTED_PRESSURE_UNIT:
        raise DuctNetworkCalculationError(
            f"Unsupported pressure unit {pressure_unit!r}; expected {_SUPPORTED_PRESSURE_UNIT!r}."
        )
    if not _finite_nonnegative(component_tolerance):
        raise DuctNetworkCalculationError("component_tolerance must be finite and non-negative.")

    topology = build_network_topology(network)
    diagnostics: list[NetworkDiagnostic] = []
    if network_result.network_id != network.network_id:
        diagnostics.append(_diagnostic(
            "network_result_mismatch",
            f"Sizing result belongs to network {network_result.network_id}, not {network.network_id}.",
            network.network_id,
        ))
    if NetworkType(network_result.network_type) is not NetworkType(network.network_type):
        diagnostics.append(_diagnostic(
            "network_type_mismatch",
            "Sizing result network type does not match the analyzed network.",
            network.network_id,
        ))
    if not network_result.validation.is_valid:
        diagnostics.append(_diagnostic(
            "network_result_invalid",
            "The connected network sizing result contains validation errors.",
            network.network_id,
        ))

    if NetworkType(network.network_type) is NetworkType.RETURN:
        diagnostics.append(_diagnostic(
            "return_network_unsupported",
            "Critical path analysis supports directed SOURCE-to-TERMINAL SUPPLY networks only; "
            "RETURN networks require terminal-to-fan reverse-flow semantics.",
            network.network_id,
        ))
        return CriticalPathAnalysisResult(
            network_id=network.network_id,
            network_type=NetworkType(network.network_type),
            pressure_unit=pressure_unit,
            critical_terminal_id=None,
            critical_path_segment_ids=(),
            critical_path_pressure_loss_inwg=None,
            critical_path_segments=(),
            terminal_paths=(),
            diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.code, item.element_id or ""))),
            status=PressureAnalysisStatus.UNSUPPORTED,
        )

    result_by_id: dict[str, object] = {}
    for segment in network_result.segments:
        if segment.segment_id in result_by_id:
            diagnostics.append(_diagnostic(
                "duplicate_segment_result",
                f"Duplicate sizing result for segment {segment.segment_id}.",
                segment.segment_id,
            ))
        else:
            result_by_id[segment.segment_id] = segment

    loss_by_id: dict[str, PressureSegmentResult] = {}
    for segment_id in sorted(topology.segments_by_id):
        segment_result = result_by_id.get(segment_id)
        sizing = getattr(segment_result, "sizing_result", None)
        if sizing is None:
            diagnostics.append(_diagnostic(
                "missing_segment_sizing_result",
                f"No sizing result is available for segment {segment_id}; its pressure loss cannot be analyzed.",
                segment_id,
            ))
            continue
        values = {
            "straight": getattr(sizing, "straight_friction_loss_inwg", None),
            "fitting": getattr(sizing, "fitting_loss_inwg", None),
            "total": getattr(sizing, "total_modeled_loss_inwg", None),
        }
        invalid = [name for name, value in values.items() if not _finite_nonnegative(value)]
        if invalid:
            diagnostics.append(_diagnostic(
                "invalid_segment_pressure_loss",
                f"Segment {segment_id} has missing, negative, or non-finite {', '.join(invalid)} pressure loss.",
                segment_id,
            ))
            continue
        expected_total = values["straight"] + values["fitting"]
        if not math.isclose(
            values["total"], expected_total, rel_tol=component_tolerance, abs_tol=component_tolerance
        ):
            diagnostics.append(_diagnostic(
                "inconsistent_pressure_loss_total",
                f"Segment {segment_id} total pressure loss {values['total']:g} does not equal "
                f"straight plus fitting loss {expected_total:g} within tolerance {component_tolerance:g}.",
                segment_id,
            ))
            continue
        loss_by_id[segment_id] = PressureSegmentResult(
            segment_id=segment_id,
            straight_loss_inwg=float(values["straight"]),
            fitting_loss_inwg=float(values["fitting"]),
            total_loss_inwg=float(values["total"]),
        )

    terminal_paths: list[PressureTerminalPath] = []
    for terminal_id, terminal in sorted(topology.terminals_by_id.items()):
        segment_ids = topology.terminal_paths[terminal_id]
        segment_losses = tuple(loss_by_id[segment_id] for segment_id in segment_ids if segment_id in loss_by_id)
        complete = len(segment_losses) == len(segment_ids)
        if not complete:
            terminal_paths.append(PressureTerminalPath(
                terminal_id=terminal_id,
                node_id=terminal.node_id,
                segment_ids=segment_ids,
                segment_losses=segment_losses,
                straight_loss_inwg=None,
                fitting_loss_inwg=None,
                total_loss_inwg=None,
            ))
            continue
        straight = _sum_losses(tuple(item.straight_loss_inwg for item in segment_losses))
        fitting = _sum_losses(tuple(item.fitting_loss_inwg for item in segment_losses))
        total = _sum_losses(tuple(item.total_loss_inwg for item in segment_losses))
        if straight is None or fitting is None or total is None:
            diagnostics.append(_diagnostic(
                "path_pressure_overflow",
                f"Accumulated pressure loss for terminal {terminal_id} is not finite.",
                terminal_id,
            ))
            straight = fitting = total = None
        terminal_paths.append(PressureTerminalPath(
            terminal_id=terminal_id,
            node_id=terminal.node_id,
            segment_ids=segment_ids,
            segment_losses=segment_losses,
            straight_loss_inwg=straight,
            fitting_loss_inwg=fitting,
            total_loss_inwg=total,
        ))

    complete_paths = [item for item in terminal_paths if item.total_loss_inwg is not None]
    critical = min(complete_paths, key=lambda item: (-item.total_loss_inwg, item.terminal_id)) if complete_paths else None
    if critical is not None:
        updated_paths = tuple(
            replace(
                item,
                difference_from_critical_inwg=(
                    critical.total_loss_inwg - item.total_loss_inwg
                    if item.total_loss_inwg is not None else None
                ),
            )
            for item in terminal_paths
        )
        critical_segments = critical.segment_losses
    else:
        updated_paths = tuple(terminal_paths)
        critical_segments = ()

    ordered_diagnostics = tuple(sorted(diagnostics, key=lambda item: (item.code, item.element_id or "", item.message)))
    status = PressureAnalysisStatus.INVALID if ordered_diagnostics else PressureAnalysisStatus.COMPLETE
    return CriticalPathAnalysisResult(
        network_id=network.network_id,
        network_type=NetworkType(network.network_type),
        pressure_unit=pressure_unit,
        critical_terminal_id=critical.terminal_id if critical else None,
        critical_path_segment_ids=critical.segment_ids if critical else (),
        critical_path_pressure_loss_inwg=critical.total_loss_inwg if critical else None,
        critical_path_segments=critical_segments,
        terminal_paths=updated_paths,
        diagnostics=ordered_diagnostics,
        status=status,
    )


__all__ = [
    "CriticalPathAnalysisResult",
    "PressureAnalysisStatus",
    "PressureSegmentResult",
    "PressureTerminalPath",
    "analyze_critical_path",
]
