"""Connected duct-network data models, validation, and calculation API."""

from .flow_solver import AirflowSolution, solve_network_airflow
from .models import (
    AirflowSource,
    CalculationStatus,
    DiagnosticSeverity,
    DuctNetwork,
    DuctNetworkResult,
    DuctNetworkSegment,
    DuctNetworkSegmentResult,
    DuctNode,
    DuctTerminal,
    DuctTerminalResult,
    NetworkDiagnostic,
    NetworkPressureStatus,
    NetworkType,
    NetworkValidationPolicy,
    NetworkValidationResult,
    NodeType,
    ValidationStatus,
)
from .network_engine import calculate_duct_network
from .topology import NetworkTopology, build_network_topology
from .validation import (
    DuctNetworkCalculationError,
    DuctNetworkValidationError,
    validate_network,
)

__all__ = [
    "AirflowSolution",
    "AirflowSource",
    "CalculationStatus",
    "DiagnosticSeverity",
    "DuctNetwork",
    "DuctNetworkCalculationError",
    "DuctNetworkResult",
    "DuctNetworkSegment",
    "DuctNetworkSegmentResult",
    "DuctNetworkValidationError",
    "DuctNode",
    "DuctTerminal",
    "DuctTerminalResult",
    "NetworkDiagnostic",
    "NetworkPressureStatus",
    "NetworkTopology",
    "NetworkType",
    "NetworkValidationPolicy",
    "NetworkValidationResult",
    "NodeType",
    "ValidationStatus",
    "build_network_topology",
    "calculate_duct_network",
    "solve_network_airflow",
    "validate_network",
]
