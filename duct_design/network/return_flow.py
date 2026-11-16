"""Explicit reverse-direction semantics for return-air networks."""
from __future__ import annotations

from dataclasses import replace

from .models import DuctNetwork, NetworkType, NodeType


def return_as_supply_network(network: DuctNetwork) -> tuple[DuctNetwork, str]:
    """Normalize a return network into an equivalent source-rooted graph.

    The input graph is required to be physically directed from return grilles
    toward the equipment sink.  Reversing the edges for the existing rooted-tree
    solver is an implementation detail; result mapping restores the original
    physical direction.  ``source_node_id`` is retained as the public sink
    alias for legacy callers, while ``sink_node_id`` is preferred.
    """
    sink_id = network.sink_node_id or network.source_node_id
    nodes = tuple(
        replace(
            node,
            node_type=NodeType.SOURCE if node.node_id == sink_id else node.node_type,
        )
        for node in network.nodes
    )
    segments = tuple(
        replace(
            segment,
            start_node_id=segment.end_node_id,
            end_node_id=segment.start_node_id,
        )
        for segment in network.segments
    )
    return (
        DuctNetwork(
            network_id=network.network_id,
            network_type=NetworkType.SUPPLY,
            nodes=nodes,
            segments=segments,
            terminals=network.terminals,
            source_node_id=sink_id,
            source_airflow_cfm=network.source_airflow_cfm,
        ),
        sink_id,
    )


def is_explicit_return(network: DuctNetwork) -> bool:
    """Return whether the model requests physical return-flow semantics."""
    try:
        network_type = NetworkType(network.network_type)
    except (TypeError, ValueError):
        return False
    if network_type is not NetworkType.RETURN:
        return False
    if network.sink_node_id:
        return True
    for node in network.nodes:
        try:
            node_type = NodeType(node.node_type)
        except (TypeError, ValueError):
            continue
        if node.node_id == network.source_node_id and node_type is NodeType.END:
            return True
    return False
