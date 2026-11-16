"""Typed data models for connected duct networks."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..engine import DuctSizingResult


class NodeType(StrEnum):
    """Supported node roles in a directed duct network."""

    SOURCE = "SOURCE"
    JUNCTION = "JUNCTION"
    TERMINAL = "TERMINAL"
    END = "END"


class NetworkType(StrEnum):
    """Air distribution network classification."""

    SUPPLY = "SUPPLY"
    RETURN = "RETURN"


class DiagnosticSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AirflowSource(StrEnum):
    CALCULATED = "CALCULATED"
    EXPLICIT_VALIDATED = "EXPLICIT_VALIDATED"
    EXPLICIT_MISMATCH = "EXPLICIT_MISMATCH"
    ZERO_DEMAND = "ZERO_DEMAND"


class CalculationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class NetworkPressureStatus(StrEnum):
    NOT_CALCULATED = "NOT_CALCULATED"


@dataclass(frozen=True)
class DuctNode:
    """A network connection point. A terminal node is paired with one DuctTerminal."""

    node_id: str
    node_type: NodeType
    name: str | None = None


@dataclass(frozen=True)
class DuctNetworkSegment:
    """A directed duct section; length is ft, flow CFM, dimensions inches, and K is unitless."""

    segment_id: str
    start_node_id: str
    end_node_id: str
    length_ft: float
    airflow_cfm: float | None = None
    width_in: float | None = None
    height_in: float | None = None
    fitting_k: float = 0.0


@dataclass(frozen=True)
class DuctTerminal:
    """An air outlet/inlet demand attached to a terminal node; demand is CFM."""

    terminal_id: str
    node_id: str
    required_airflow_cfm: float


@dataclass(frozen=True)
class DuctNetwork:
    """One independent, directed supply or return network."""

    network_id: str
    network_type: NetworkType
    nodes: tuple[DuctNode, ...]
    segments: tuple[DuctNetworkSegment, ...]
    terminals: tuple[DuctTerminal, ...]
    source_node_id: str
    source_airflow_cfm: float | None = None


@dataclass(frozen=True)
class NetworkDiagnostic:
    """Actionable validation or calculation diagnostic."""

    severity: DiagnosticSeverity
    code: str
    message: str
    element_id: str | None = None


@dataclass(frozen=True)
class NetworkValidationResult:
    """Validation state and diagnostics for a calculated network."""

    is_valid: bool
    diagnostics: tuple[NetworkDiagnostic, ...] = ()


@dataclass(frozen=True)
class DuctNetworkSegmentResult:
    """Calculated airflow and existing per-section sizing result for one segment."""

    segment_id: str
    start_node_id: str
    end_node_id: str
    calculated_airflow_cfm: float
    specified_airflow_cfm: float | None
    sizing_result: DuctSizingResult | None
    airflow_source: AirflowSource
    validation_status: ValidationStatus
    diagnostics: tuple[NetworkDiagnostic, ...] = ()


@dataclass(frozen=True)
class DuctTerminalResult:
    """Terminal demand, its directly connected segment, and upstream trace."""

    terminal_id: str
    node_id: str
    required_airflow_cfm: float
    connected_segment_id: str
    upstream_path_segment_ids: tuple[str, ...]
    validation_status: ValidationStatus


@dataclass(frozen=True)
class DuctNetworkResult:
    """Network calculation results; no network pressure total is calculated."""

    network_id: str
    network_type: NetworkType
    source_node_id: str
    specified_source_airflow_cfm: float | None
    calculated_airflow_cfm: float
    segments: tuple[DuctNetworkSegmentResult, ...]
    terminals: tuple[DuctTerminalResult, ...]
    validation: NetworkValidationResult
    warnings: tuple[str, ...]
    calculation_status: CalculationStatus
    network_pressure_status: NetworkPressureStatus = NetworkPressureStatus.NOT_CALCULATED
    network_total_pressure_inwg: None = None


@dataclass(frozen=True)
class NetworkValidationPolicy:
    """Tolerance and severity for differences from explicitly entered airflow."""

    absolute_tolerance_cfm: float = 1.0
    relative_tolerance: float = 0.01
    mismatch_severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
