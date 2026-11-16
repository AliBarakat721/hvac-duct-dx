"""Deterministic analysis of valid rooted duct-network topology."""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .models import DuctNetwork, DuctNetworkSegment, DuctNode, DuctTerminal
from .validation import DuctNetworkValidationError, validate_network


@dataclass(frozen=True)
class NetworkTopology:
    """Validated node/edge indexes and a deterministic topological ordering."""

    network: DuctNetwork
    nodes_by_id: Mapping[str, DuctNode]
    segments_by_id: Mapping[str, DuctNetworkSegment]
    terminals_by_id: Mapping[str, DuctTerminal]
    outgoing_segments: Mapping[str, tuple[DuctNetworkSegment, ...]]
    incoming_segment: Mapping[str, DuctNetworkSegment]
    terminal_by_node: Mapping[str, DuctTerminal]
    terminal_segment_by_id: Mapping[str, str]
    terminal_paths: Mapping[str, tuple[str, ...]]
    node_order: tuple[str, ...]


def build_network_topology(network: DuctNetwork) -> NetworkTopology:
    """Validate a network and build indexes independent of input list order."""
    validation = validate_network(network)
    if not validation.is_valid:
        raise DuctNetworkValidationError(validation.diagnostics)

    nodes = {node.node_id: node for node in network.nodes}
    segments = {segment.segment_id: segment for segment in network.segments}
    terminals = {terminal.terminal_id: terminal for terminal in network.terminals}
    outgoing_lists: dict[str, list[DuctNetworkSegment]] = defaultdict(list)
    incoming: dict[str, DuctNetworkSegment] = {}
    for segment in segments.values():
        outgoing_lists[segment.start_node_id].append(segment)
        incoming[segment.end_node_id] = segment
    outgoing = {
        node_id: tuple(sorted(outgoing_lists[node_id], key=lambda item: item.segment_id))
        for node_id in nodes
    }

    indegree = {node_id: int(node_id in incoming) for node_id in nodes}
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered_nodes: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        ordered_nodes.append(node_id)
        for segment in outgoing[node_id]:
            child = segment.end_node_id
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)

    terminal_by_node = {terminal.node_id: terminal for terminal in terminals.values()}
    terminal_segment = {
        terminal_id: incoming[terminal.node_id].segment_id
        for terminal_id, terminal in terminals.items()
    }
    terminal_paths: dict[str, tuple[str, ...]] = {}
    for terminal_id, terminal in terminals.items():
        reverse_path: list[str] = []
        node_id = terminal.node_id
        while node_id != network.source_node_id:
            segment = incoming[node_id]
            reverse_path.append(segment.segment_id)
            node_id = segment.start_node_id
        terminal_paths[terminal_id] = tuple(reversed(reverse_path))

    return NetworkTopology(
        network=network,
        nodes_by_id=MappingProxyType(nodes),
        segments_by_id=MappingProxyType(segments),
        terminals_by_id=MappingProxyType(terminals),
        outgoing_segments=MappingProxyType(outgoing),
        incoming_segment=MappingProxyType(incoming),
        terminal_by_node=MappingProxyType(terminal_by_node),
        terminal_segment_by_id=MappingProxyType(terminal_segment),
        terminal_paths=MappingProxyType(terminal_paths),
        node_order=tuple(ordered_nodes),
    )
