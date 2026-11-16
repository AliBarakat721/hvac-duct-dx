"""Structural and engineering-input validation for duct networks."""
from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterable

from ..engine import DuctDesignCalculationError
from .models import (
    DiagnosticSeverity,
    DuctNetwork,
    NetworkDiagnostic,
    NetworkType,
    NetworkValidationResult,
    NodeType,
)


class DuctNetworkValidationError(DuctDesignCalculationError):
    """Raised when a network cannot be analyzed as a rooted directed tree."""

    def __init__(self, diagnostics: Iterable[NetworkDiagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        details = "\n".join(f"{item.severity}: {item.message}" for item in self.diagnostics)
        super().__init__(details or "Duct network validation failed.")


class DuctNetworkCalculationError(DuctDesignCalculationError):
    """Invalid network calculation policy or unsupported calculation request."""


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _finite_nonnegative(value: object) -> bool:
    return _finite_number(value) and value >= 0


def _enum_value(enum_type: type[NodeType] | type[NetworkType], value: object) -> bool:
    try:
        enum_type(value)
    except (TypeError, ValueError):
        return False
    return True


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_supply_network(network: DuctNetwork) -> NetworkValidationResult:
    """Validate a source-rooted directed tree."""
    diagnostics: list[NetworkDiagnostic] = []

    def error(code: str, message: str, element_id: str | None = None) -> None:
        diagnostics.append(NetworkDiagnostic(DiagnosticSeverity.ERROR, code, message, element_id))

    if not _valid_id(network.network_id):
        error("invalid_network_id", "Network ID must be a non-empty string.")
    if not _enum_value(NetworkType, network.network_type):
        error("invalid_network_type", f"Network type {network.network_type!r} is not supported.")
    if not _valid_id(network.source_node_id):
        error("invalid_source_id", "Source node ID must be a non-empty string.")
    if network.source_airflow_cfm is not None and not _finite_nonnegative(network.source_airflow_cfm):
        error("invalid_source_airflow", "Specified source airflow must be finite and non-negative.")

    node_counts: dict[str, int] = defaultdict(int)
    segment_counts: dict[str, int] = defaultdict(int)
    terminal_counts: dict[str, int] = defaultdict(int)
    nodes: dict[str, object] = {}
    segments: dict[str, object] = {}
    terminals: dict[str, object] = {}
    for node in network.nodes:
        if not _valid_id(node.node_id):
            error("invalid_node_id", "Node ID must be a non-empty string.")
            continue
        node_counts[node.node_id] += 1
        nodes.setdefault(node.node_id, node)
        if not _enum_value(NodeType, node.node_type):
            error("invalid_node_type", f"Node {node.node_id} has unsupported type {node.node_type!r}.", node.node_id)
    for segment in network.segments:
        if not _valid_id(segment.segment_id):
            error("invalid_segment_id", "Segment ID must be a non-empty string.")
            continue
        segment_counts[segment.segment_id] += 1
        segments.setdefault(segment.segment_id, segment)
        if not _valid_id(segment.start_node_id) or not _valid_id(segment.end_node_id):
            error("invalid_segment_endpoint", f"Segment {segment.segment_id} must have valid endpoint IDs.", segment.segment_id)
        if segment.start_node_id == segment.end_node_id:
            error("self_loop", f"Segment {segment.segment_id} connects a node to itself.", segment.segment_id)
        if not _finite_number(segment.length_ft) or segment.length_ft <= 0:
            error("invalid_segment_length", f"Segment {segment.segment_id} length must be finite and positive.", segment.segment_id)
        if segment.airflow_cfm is not None and not _finite_nonnegative(segment.airflow_cfm):
            error("invalid_segment_airflow", f"Segment {segment.segment_id} airflow must be finite and non-negative.", segment.segment_id)
        if not _finite_nonnegative(segment.fitting_k):
            error("invalid_fitting_k", f"Segment {segment.segment_id} fitting K must be finite and non-negative.", segment.segment_id)
        if (segment.width_in is None) != (segment.height_in is None):
            error("incomplete_dimensions", f"Segment {segment.segment_id} must specify both width and height, or neither.", segment.segment_id)
        for field_name, value in (("width", segment.width_in), ("height", segment.height_in)):
            if value is not None and (not _finite_number(value) or value <= 0):
                error("invalid_segment_dimensions", f"Segment {segment.segment_id} {field_name} must be finite and positive.", segment.segment_id)
        if _valid_id(segment.start_node_id) and segment.start_node_id not in nodes:
            error("unknown_node_reference", f"Segment {segment.segment_id} references unknown start node {segment.start_node_id}.", segment.segment_id)
        if _valid_id(segment.end_node_id) and segment.end_node_id not in nodes:
            error("unknown_node_reference", f"Segment {segment.segment_id} references unknown end node {segment.end_node_id}.", segment.segment_id)
    for terminal in network.terminals:
        if not _valid_id(terminal.terminal_id):
            error("invalid_terminal_id", "Terminal ID must be a non-empty string.")
            continue
        terminal_counts[terminal.terminal_id] += 1
        terminals.setdefault(terminal.terminal_id, terminal)
        if not _valid_id(terminal.node_id):
            error("invalid_terminal_node", f"Terminal {terminal.terminal_id} must reference a valid node ID.", terminal.terminal_id)
        if not _finite_nonnegative(terminal.required_airflow_cfm):
            error("invalid_terminal_airflow", f"Terminal {terminal.terminal_id} airflow demand must be finite and non-negative.", terminal.terminal_id)
        if _valid_id(terminal.node_id) and terminal.node_id not in nodes:
            error("unknown_node_reference", f"Terminal {terminal.terminal_id} references unknown node {terminal.node_id}.", terminal.terminal_id)

    for label, counts, code in (
        ("node", node_counts, "duplicate_node_id"),
        ("segment", segment_counts, "duplicate_segment_id"),
        ("terminal", terminal_counts, "duplicate_terminal_id"),
    ):
        for element_id in sorted(key for key, count in counts.items() if count > 1):
            error(code, f"Duplicate {label} ID {element_id}.", element_id)

    source_nodes = sorted(
        node_id for node_id, node in nodes.items()
        if _enum_value(NodeType, node.node_type) and NodeType(node.node_type) is NodeType.SOURCE
    )
    if len(source_nodes) != 1:
        error("source_count", f"Network must contain exactly one SOURCE node; found {len(source_nodes)}.")
    if not _valid_id(network.source_node_id) or network.source_node_id not in nodes:
        error("unknown_source", f"Source node {network.source_node_id} does not exist.", network.source_node_id)
    elif (
        _enum_value(NodeType, nodes[network.source_node_id].node_type)
        and NodeType(nodes[network.source_node_id].node_type) is not NodeType.SOURCE
    ):
        error("invalid_source_type", f"Configured source node {network.source_node_id} is not a SOURCE.", network.source_node_id)
    for source_id in source_nodes:
        if source_id != network.source_node_id:
            error("unexpected_source", f"Unexpected additional SOURCE node {source_id}.", source_id)

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for segment_id, segment in segments.items():
        if (
            _valid_id(segment.start_node_id)
            and _valid_id(segment.end_node_id)
            and segment.start_node_id in nodes
            and segment.end_node_id in nodes
        ):
            outgoing[segment.start_node_id].append(segment_id)
            incoming[segment.end_node_id].append(segment_id)
    for node_id in nodes:
        incoming[node_id].sort()
        outgoing[node_id].sort()

    terminal_ids_by_node: dict[str, list[str]] = defaultdict(list)
    for terminal_id, terminal in terminals.items():
        if _valid_id(terminal.node_id) and terminal.node_id in nodes:
            terminal_ids_by_node[terminal.node_id].append(terminal_id)
            node_type = nodes[terminal.node_id].node_type
            if _enum_value(NodeType, node_type) and NodeType(node_type) is not NodeType.TERMINAL:
                error("terminal_node_type", f"Terminal {terminal_id} must attach to a TERMINAL node, not {node_type}.", terminal_id)
    for node_id, node in nodes.items():
        node_type = NodeType(node.node_type) if _enum_value(NodeType, node.node_type) else None
        terminal_count = len(terminal_ids_by_node[node_id])
        if node_type is NodeType.TERMINAL and terminal_count != 1:
            error("terminal_demand_count", f"TERMINAL node {node_id} must have exactly one terminal demand; found {terminal_count}.", node_id)
        if node_type is NodeType.TERMINAL and outgoing[node_id]:
            error("terminal_has_outgoing", f"TERMINAL node {node_id} cannot have outgoing duct segments.", node_id)
        if node_type is NodeType.END and (outgoing[node_id] or terminal_count):
            error("invalid_end_node", f"END node {node_id} cannot have outgoing segments or terminal demands.", node_id)
        if node_type is NodeType.SOURCE and not outgoing[node_id]:
            error("source_without_segment", f"SOURCE node {node_id} must connect to a duct segment.", node_id)
        if node_type is NodeType.JUNCTION and not outgoing[node_id]:
            error("junction_without_outlet", f"JUNCTION node {node_id} has no downstream segment.", node_id)
        if not incoming[node_id] and not outgoing[node_id]:
            error("isolated_node", f"Node {node_id} is isolated from all duct segments.", node_id)
        if node_id == network.source_node_id:
            if incoming[node_id]:
                error("source_has_incoming", f"SOURCE node {node_id} cannot have incoming segments.", node_id)
        elif len(incoming[node_id]) != 1:
            error("invalid_in_degree", f"Node {node_id} must have exactly one upstream segment; found {len(incoming[node_id])}.", node_id)
        if node_type in (NodeType.TERMINAL, NodeType.END) and incoming[node_id] != [] and outgoing[node_id]:
            error("invalid_leaf", f"{node_type} node {node_id} must be a leaf.", node_id)
        if not outgoing[node_id] and node_type not in (NodeType.TERMINAL, NodeType.END):
            error("unterminated_branch", f"Branch at node {node_id} ends without a TERMINAL or END node.", node_id)

    if not network.terminals:
        error("no_terminals", "Network must contain at least one terminal demand.")

    # Directed DFS reports actual cycle participants, independent of input order.
    state: dict[str, int] = {}
    stack: list[str] = []
    reported_cycles: set[tuple[str, ...]] = set()

    for node_id in sorted(nodes):
        if state.get(node_id, 0) != 0:
            continue
        state[node_id] = 1
        stack = [node_id]
        frames = [(node_id, iter(outgoing[node_id]))]
        while frames:
            current, edges = frames[-1]
            try:
                segment_id = next(edges)
            except StopIteration:
                frames.pop()
                stack.pop()
                state[current] = 2
                continue
            child = segments[segment_id].end_node_id
            if not _valid_id(child) or child not in nodes:
                continue
            if state.get(child, 0) == 0:
                state[child] = 1
                stack.append(child)
                frames.append((child, iter(outgoing[child])))
            elif state.get(child) == 1:
                cycle = tuple(stack[stack.index(child):] + [child])
                if cycle not in reported_cycles:
                    reported_cycles.add(cycle)
                    error(
                        "cycle_detected",
                        f"Directed cycle detected through nodes {' -> '.join(cycle)}.",
                        cycle[0],
                    )

    reachable: set[str] = set()
    if _valid_id(network.source_node_id) and network.source_node_id in nodes:
        queue = deque([network.source_node_id])
        while queue:
            node_id = queue.popleft()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for segment_id in outgoing[node_id]:
                child = segments[segment_id].end_node_id
                if child in nodes and child not in reachable:
                    queue.append(child)
    for node_id in sorted(set(nodes) - reachable):
        error("unreachable_node", f"Node {node_id} is not reachable from source {network.source_node_id}.", node_id)
    for segment_id, segment in sorted(segments.items()):
        if (
            not _valid_id(segment.start_node_id)
            or not _valid_id(segment.end_node_id)
            or segment.start_node_id not in reachable
            or segment.end_node_id not in reachable
        ):
            error("disconnected_segment", f"Segment {segment_id} is disconnected from source {network.source_node_id}.", segment_id)
    for terminal_id, terminal in sorted(terminals.items()):
        if not _valid_id(terminal.node_id) or terminal.node_id not in reachable:
            error("unreachable_terminal", f"Terminal {terminal_id} is not connected to source {network.source_node_id}.", terminal_id)

    ordered = tuple(sorted(diagnostics, key=lambda item: (item.code, item.element_id or "", item.message)))
    return NetworkValidationResult(
        is_valid=not any(item.severity is DiagnosticSeverity.ERROR for item in ordered),
        diagnostics=ordered,
    )


def validate_network(network: DuctNetwork) -> NetworkValidationResult:
    """Validate a supply network or an explicitly directed return network.

    A return network is physically directed from terminal grilles to the
    equipment sink when ``sink_node_id`` is supplied (or when the historical
    source node is typed as ``END``).  Older source-rooted RETURN objects are
    retained for compatibility and validated as their declared graph.
    """
    from .return_flow import is_explicit_return, return_as_supply_network

    if is_explicit_return(network):
        normalized, sink_id = return_as_supply_network(network)
        result = _validate_supply_network(normalized)
        diagnostics = list(result.diagnostics)
        if any(node.node_id == sink_id and node.node_type is NodeType.TERMINAL for node in network.nodes):
            diagnostics.append(NetworkDiagnostic(
                DiagnosticSeverity.ERROR,
                "return_sink_terminal",
                f"Return sink {sink_id} cannot also be a return terminal.",
                sink_id,
            ))
        ordered = tuple(sorted(diagnostics, key=lambda item: (item.code, item.element_id or "", item.message)))
        return NetworkValidationResult(
            is_valid=not any(item.severity is DiagnosticSeverity.ERROR for item in ordered),
            diagnostics=ordered,
        )
    return _validate_supply_network(network)
