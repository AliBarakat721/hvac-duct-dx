"""Convert confirmed CAD centerline mappings into engineering network objects."""
from __future__ import annotations

import math
from collections import defaultdict

from ..network.models import DuctNetwork, DuctNetworkSegment, DuctNode, DuctTerminal, NetworkType, NodeType
from .models import CadDiagnostic, CadEntityKind, CadImport, CadNetworkMapping, EngineeringCategory, LayerMapping


def _key(point: tuple[float, ...], tolerance: float) -> tuple[int, int]:
    return round(point[0] / tolerance), round(point[1] / tolerance)


def build_network_from_cad(
    cad: CadImport,
    mapping: CadNetworkMapping,
    layer_mapping: LayerMapping,
    *,
    tolerance: float | None = None,
) -> tuple[DuctNetwork | None, tuple[CadDiagnostic, ...]]:
    """Build a tree only from user-confirmed duct layers and terminal mappings.

    The function intentionally does not infer airflow direction from geometry.
    Segment direction must already be authored in the CAD mapping: supply paths
    point source-to-terminal and return paths point grille-to-sink.
    """
    tol = tolerance or mapping.tolerance
    diagnostics: list[CadDiagnostic] = []
    by_handle = {entity.handle: entity for entity in cad.entities}
    selected = []
    for handle in mapping.segment_handles:
        entity = by_handle.get(handle)
        if entity is None:
            diagnostics.append(CadDiagnostic("ERROR", "unknown_entity", f"Mapped segment handle {handle} is not present." , handle))
            continue
        category = layer_mapping.category_for(entity.layer)
        if category not in {EngineeringCategory.SUPPLY_DUCT, EngineeringCategory.RETURN_DUCT, EngineeringCategory.DUCT_CENTERLINE}:
            diagnostics.append(CadDiagnostic("ERROR", "unconfirmed_duct_layer", f"Entity {handle} is not mapped to a duct centerline category.", handle))
            continue
        if entity.kind not in {CadEntityKind.LINE, CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE} or len(entity.geometry) < 2:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_duct_geometry", f"Entity {handle} is not a usable centerline.", handle))
            continue
        selected.append(entity)
    if mapping.network_type not in {NetworkType.SUPPLY.value, NetworkType.RETURN.value}:
        diagnostics.append(CadDiagnostic("ERROR", "invalid_network_type", "CAD network type must be SUPPLY or RETURN."))
    if diagnostics:
        return None, tuple(diagnostics)

    coordinates: dict[tuple[int, int], tuple[float, float]] = {}
    adjacency: dict[tuple[int, int], list[str]] = defaultdict(list)
    segment_nodes: dict[str, tuple[tuple[int, int], tuple[int, int], float]] = {}
    for entity in selected:
        start, end = entity.geometry[0], entity.geometry[-1]
        if math.dist(start[:2], end[:2]) <= tol:
            diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", f"Entity {entity.handle} has zero length.", entity.handle))
            continue
        a, b = _key(start, tol), _key(end, tol)
        coordinates[a], coordinates[b] = (start[0], start[1]), (end[0], end[1])
        adjacency[a].append(entity.handle)
        adjacency[b].append(entity.handle)
        length = math.dist(start[:2], end[:2])
        segment_nodes[entity.handle] = (a, b, length)
    root = _key(mapping.source_or_sink_coordinate, tol)
    if root not in coordinates:
        diagnostics.append(CadDiagnostic("ERROR", "missing_root", "Configured source/sink coordinate is not on the mapped duct geometry."))
    for item in mapping.terminal_mappings:
        if item.handle not in by_handle:
            diagnostics.append(CadDiagnostic("ERROR", "unknown_terminal", f"Terminal entity {item.handle} is not present.", item.handle))
        if not math.isfinite(item.airflow_cfm) or item.airflow_cfm < 0:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_terminal_airflow", f"Terminal {item.terminal_id} airflow must be finite and non-negative.", item.handle))
    if diagnostics:
        return None, tuple(diagnostics)

    node_ids = {coord: f"CAD-N-{index + 1}" for index, coord in enumerate(sorted(coordinates))}
    root_node = node_ids[root]
    sink_mode = mapping.network_type == NetworkType.RETURN.value
    nodes = []
    for coord, node_id in node_ids.items():
        if coord == root:
            role = NodeType.END if sink_mode else NodeType.SOURCE
        elif len(adjacency[coord]) == 1:
            role = NodeType.TERMINAL
        else:
            role = NodeType.JUNCTION
        nodes.append(DuctNode(node_id, role, name=f"CAD {node_id}"))
    segments = tuple(
        DuctNetworkSegment(handle, node_ids[a], node_ids[b], length)
        for handle, (a, b, length) in sorted(segment_nodes.items())
    )
    mapped_terminals = []
    for index, item in enumerate(sorted(mapping.terminal_mappings, key=lambda value: value.terminal_id)):
        coord = _key(item.node_coordinate, tol)
        if coord not in node_ids:
            diagnostics.append(CadDiagnostic("ERROR", "terminal_not_connected", f"Terminal {item.terminal_id} is not on mapped duct geometry.", item.handle))
            continue
        mapped_terminals.append(DuctTerminal(item.terminal_id, node_ids[coord], item.airflow_cfm))
    if diagnostics:
        return None, tuple(diagnostics)
    network = DuctNetwork(
        network_id=mapping.network_id,
        network_type=NetworkType(mapping.network_type),
        nodes=tuple(nodes),
        segments=segments,
        terminals=tuple(mapped_terminals),
        source_node_id=root_node,
        sink_node_id=root_node if sink_mode else None,
    )
    return network, ()
