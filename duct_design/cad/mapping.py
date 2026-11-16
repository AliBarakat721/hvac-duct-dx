"""Convert confirmed CAD centerline mappings into validated engineering networks."""
from __future__ import annotations

import math
from collections import defaultdict

from ..network.models import DuctNetwork, DuctNetworkSegment, DuctNode, DuctTerminal, NetworkType, NodeType
from ..network.validation import validate_network
from .io import polyline_length
from .models import CadDiagnostic, CadEntityKind, CadImport, CadNetworkMapping, EngineeringCategory, LayerMapping

_TARGET_UNITS_TO_FEET = {
    "feet": 1.0,
    "inches": 1.0 / 12.0,
    "millimeters": 1.0 / 304.8,
    "centimeters": 1.0 / 30.48,
    "meters": 3.280839895013123,
}


def _cluster_endpoints(points: list[tuple[float, float]], tolerance: float) -> tuple[dict[int, int], dict[int, tuple[float, float]], tuple[CadDiagnostic, ...]]:
    """Cluster endpoints by exact distance, not quantization bins."""
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            if math.dist(points[left], points[right]) <= tolerance:
                union(left, right)
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(points)):
        groups[find(index)].append(index)
    ordered = sorted(groups.values(), key=lambda group: min(points[index] for index in group))
    cluster_by_point: dict[int, int] = {}
    representative: dict[int, tuple[float, float]] = {}
    for cluster_id, group in enumerate(ordered):
        representative[cluster_id] = min((points[index] for index in group))
        for index in group:
            cluster_by_point[index] = cluster_id
    return cluster_by_point, representative, ()


def _find_cluster(point: tuple[float, float], representatives: dict[int, tuple[float, float]], tolerance: float) -> tuple[int | None, CadDiagnostic | None]:
    matches = [(cluster, coordinate) for cluster, coordinate in representatives.items() if math.dist(point, coordinate) <= tolerance]
    if not matches:
        return None, CadDiagnostic("ERROR", "unconnected_coordinate", f"Coordinate {point!r} is not within the configured connection tolerance.")
    if len(matches) > 1:
        return None, CadDiagnostic("ERROR", "ambiguous_connection", f"Coordinate {point!r} is within tolerance of multiple endpoint clusters.")
    return matches[0][0], None


def build_network_from_cad(
    cad: CadImport,
    mapping: CadNetworkMapping,
    layer_mapping: LayerMapping,
    *,
    tolerance: float | None = None,
) -> tuple[DuctNetwork | None, tuple[CadDiagnostic, ...]]:
    """Build a network only from confirmed, direction-authored CAD mappings."""
    diagnostics: list[CadDiagnostic] = []
    tol = mapping.tolerance if tolerance is None else tolerance
    if not isinstance(tol, (int, float)) or isinstance(tol, bool) or not math.isfinite(tol) or tol <= 0:
        return None, (CadDiagnostic("ERROR", "invalid_tolerance", "Connection tolerance must be finite and positive."),)
    try:
        network_type = NetworkType(mapping.network_type)
    except (TypeError, ValueError):
        return None, (CadDiagnostic("ERROR", "invalid_network_type", "CAD network type must be SUPPLY or RETURN."),)
    if not isinstance(mapping.network_id, str) or not mapping.network_id.strip():
        diagnostics.append(CadDiagnostic("ERROR", "invalid_network_id", "CAD network ID must be non-empty."))
    if cad.target_units not in _TARGET_UNITS_TO_FEET:
        diagnostics.append(CadDiagnostic("ERROR", "ambiguous_cad_units", "CAD geometry must have an explicit physical target unit before network conversion."))
    if len(set(mapping.segment_handles)) != len(mapping.segment_handles):
        diagnostics.append(CadDiagnostic("ERROR", "duplicate_segment_handle", "Mapped segment handles must be unique."))
    terminal_ids = [item.terminal_id for item in mapping.terminal_mappings]
    if len(set(terminal_ids)) != len(terminal_ids):
        diagnostics.append(CadDiagnostic("ERROR", "duplicate_terminal_id", "Mapped terminal IDs must be unique."))
    by_handle = {entity.handle: entity for entity in cad.entities}
    if len(by_handle) != len(cad.entities):
        diagnostics.append(CadDiagnostic("ERROR", "duplicate_entity_handle", "Imported CAD entity handles must be unique."))

    selected = []
    for handle in mapping.segment_handles:
        entity = by_handle.get(handle)
        if entity is None:
            diagnostics.append(CadDiagnostic("ERROR", "unknown_entity", f"Mapped segment handle {handle} is not present.", handle))
            continue
        category = layer_mapping.category_for(entity.layer)
        expected = EngineeringCategory.SUPPLY_DUCT if network_type is NetworkType.SUPPLY else EngineeringCategory.RETURN_DUCT
        if category not in {expected, EngineeringCategory.DUCT_CENTERLINE}:
            diagnostics.append(CadDiagnostic("ERROR", "incompatible_duct_layer", f"Entity {handle} is mapped as {category!s}, incompatible with {network_type.value} network.", handle))
            continue
        if entity.kind not in {CadEntityKind.LINE, CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE} or len(entity.geometry) < 2:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_duct_geometry", f"Entity {handle} is not a usable centerline.", handle))
            continue
        if entity.closed:
            diagnostics.append(CadDiagnostic("ERROR", "closed_centerline_unsupported", f"Closed entity {handle} cannot be unambiguously converted into a directed tree segment.", handle))
            continue
        selected.append((entity, category))

    for item in mapping.terminal_mappings:
        entity = by_handle.get(item.handle)
        if entity is None:
            diagnostics.append(CadDiagnostic("ERROR", "unknown_terminal", f"Terminal entity {item.handle} is not present.", item.handle))
            continue
        actual_category = layer_mapping.category_for(entity.layer)
        allowed = {EngineeringCategory.GRILLE} if network_type is NetworkType.RETURN else {EngineeringCategory.GRILLE, EngineeringCategory.DIFFUSER}
        if item.category not in allowed or actual_category is not item.category:
            diagnostics.append(CadDiagnostic("ERROR", "terminal_category_mismatch", f"Terminal {item.terminal_id} category does not match its mapped CAD layer or network type.", item.handle))
        if entity.kind not in {CadEntityKind.INSERT, CadEntityKind.CIRCLE, CadEntityKind.TEXT, CadEntityKind.MTEXT}:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_terminal_entity", f"Entity {item.handle} is not a supported terminal symbol entity.", item.handle))
        if not math.isfinite(item.airflow_cfm) or item.airflow_cfm < 0:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_terminal_airflow", f"Terminal {item.terminal_id} airflow must be finite and non-negative.", item.handle))
    if diagnostics:
        return None, tuple(diagnostics)
    if not selected:
        return None, (CadDiagnostic("ERROR", "no_duct_segments", "At least one confirmed duct centerline is required."),)

    endpoint_points = [(entity.geometry[0][0], entity.geometry[0][1]) for entity, _ in selected]
    endpoint_points += [(entity.geometry[-1][0], entity.geometry[-1][1]) for entity, _ in selected]
    point_cluster, representatives, cluster_diagnostics = _cluster_endpoints(endpoint_points, tol)
    diagnostics.extend(cluster_diagnostics)
    segment_nodes: dict[str, tuple[int, int, float]] = {}
    adjacency: dict[int, list[str]] = defaultdict(list)
    for index, (entity, _) in enumerate(selected):
        start_index, end_index = index, index + len(selected)
        start_cluster, end_cluster = point_cluster[start_index], point_cluster[end_index]
        length = polyline_length(entity) * _TARGET_UNITS_TO_FEET.get(cad.target_units, 1.0)
        if not math.isfinite(length) or length <= 0:
            diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", f"Entity {entity.handle} has no positive engineering length.", entity.handle))
            continue
        segment_nodes[entity.handle] = (start_cluster, end_cluster, length)
        adjacency[start_cluster].append(entity.handle)
        adjacency[end_cluster].append(entity.handle)
    root, error = _find_cluster(tuple(mapping.source_or_sink_coordinate), representatives, tol)
    if error:
        error = CadDiagnostic(error.severity, "missing_root", "Configured source/sink coordinate is not on the mapped duct geometry.")
        diagnostics.append(error)
    if diagnostics:
        return None, tuple(diagnostics)

    node_ids = {cluster: f"CAD-N-{index + 1}" for index, cluster in enumerate(sorted(representatives, key=lambda item: representatives[item]))}
    nodes = []
    for cluster, node_id in node_ids.items():
        role = (NodeType.SOURCE if network_type is NetworkType.SUPPLY else NodeType.END) if cluster == root else (NodeType.TERMINAL if len(adjacency[cluster]) == 1 else NodeType.JUNCTION)
        nodes.append(DuctNode(node_id, role, name=f"CAD {node_id}"))
    segments = tuple(DuctNetworkSegment(handle, node_ids[start], node_ids[end], length) for handle, (start, end, length) in sorted(segment_nodes.items()))
    mapped_terminals = []
    for item in sorted(mapping.terminal_mappings, key=lambda value: value.terminal_id):
        cluster, error = _find_cluster(tuple(item.node_coordinate), representatives, tol)
        if error or cluster is None:
            diagnostics.append(CadDiagnostic("ERROR", "terminal_not_connected", f"Terminal {item.terminal_id} is not on mapped duct geometry.", item.handle))
            continue
        mapped_terminals.append(DuctTerminal(item.terminal_id, node_ids[cluster], item.airflow_cfm))
    if diagnostics:
        return None, tuple(diagnostics)
    network = DuctNetwork(
        network_id=mapping.network_id,
        network_type=network_type,
        nodes=tuple(nodes),
        segments=segments,
        terminals=tuple(mapped_terminals),
        source_node_id=node_ids[root],
        sink_node_id=node_ids[root] if network_type is NetworkType.RETURN else None,
    )
    validation = validate_network(network)
    if not validation.is_valid:
        return None, tuple(CadDiagnostic(item.severity.value, f"network_{item.code}", item.message, item.element_id) for item in validation.diagnostics)
    return network, ()
