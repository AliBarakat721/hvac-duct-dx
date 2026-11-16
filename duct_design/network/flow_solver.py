"""Post-order airflow-demand solver for directed rooted duct trees."""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .models import DiagnosticSeverity, NetworkDiagnostic, DuctNetwork
from .topology import NetworkTopology, build_network_topology
from .validation import DuctNetworkCalculationError


@dataclass(frozen=True)
class AirflowSolution:
    """Calculated segment demands and verified flow-conservation diagnostics."""

    segment_airflow_cfm: Mapping[str, float]
    source_airflow_cfm: float
    diagnostics: tuple[NetworkDiagnostic, ...]


def _solve_topology(topology: NetworkTopology) -> AirflowSolution:
    network = topology.network
    demand_at_node = {node_id: 0.0 for node_id in topology.nodes_by_id}
    for terminal in topology.terminals_by_id.values():
        demand_at_node[terminal.node_id] += terminal.required_airflow_cfm

    for node_id in reversed(topology.node_order):
        demand = demand_at_node[node_id] + sum(
            demand_at_node[segment.end_node_id]
            for segment in topology.outgoing_segments[node_id]
        )
        if not math.isfinite(demand):
            raise DuctNetworkCalculationError(
                f"Aggregated airflow at node {node_id} is not finite."
            )
        demand_at_node[node_id] = demand

    segment_flows = {
        segment.segment_id: demand_at_node[segment.end_node_id]
        for segment in topology.segments_by_id.values()
    }
    diagnostics: list[NetworkDiagnostic] = []
    for node_id in topology.node_order:
        incoming_flow = (
            demand_at_node[node_id]
            if node_id == network.source_node_id
            else segment_flows[topology.incoming_segment[node_id].segment_id]
        )
        terminal = topology.terminal_by_node.get(node_id)
        downstream_flow = sum(
            segment_flows[segment.segment_id]
            for segment in topology.outgoing_segments[node_id]
        )
        terminal_flow = terminal.required_airflow_cfm if terminal else 0.0
        outgoing_flow = downstream_flow + terminal_flow
        if not math.isclose(incoming_flow, outgoing_flow, rel_tol=1e-12, abs_tol=1e-9):
            diagnostics.append(NetworkDiagnostic(
                DiagnosticSeverity.ERROR,
                "flow_conservation",
                f"Flow conservation failed at node {node_id}: incoming "
                f"{incoming_flow:g} CFM, outgoing plus terminal demand "
                f"{outgoing_flow:g} CFM.",
                node_id,
            ))

    return AirflowSolution(
        segment_airflow_cfm=MappingProxyType(segment_flows),
        source_airflow_cfm=demand_at_node[network.source_node_id],
        diagnostics=tuple(diagnostics),
    )


def solve_network_airflow(network: DuctNetwork) -> AirflowSolution:
    """Calculate every segment's required CFM from downstream terminal demands.

    Structural validation is performed first; invalid or disconnected networks
    raise :class:`DuctNetworkValidationError` rather than yielding partial flows.
    """
    return _solve_topology(build_network_topology(network))
