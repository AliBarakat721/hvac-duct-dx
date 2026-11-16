import pytest

from duct_design import (
    DuctDesignOptions, DuctNetwork, DuctNetworkSegment, DuctNode, DuctTerminal,
    NetworkType, NodeType, calculate_duct_network, solve_network_airflow, validate_network,
)


def options():
    return DuctDesignOptions(max_velocity_fpm=100_000, max_friction_inwg_100ft=10,
                             min_width_in=8, max_width_in=8, min_height_in=8, max_height_in=8)


def return_network():
    return DuctNetwork(
        "RET-1", NetworkType.RETURN,
        (DuctNode("G1", NodeType.TERMINAL), DuctNode("G2", NodeType.TERMINAL),
         DuctNode("J", NodeType.JUNCTION), DuctNode("AHU", NodeType.END)),
        (DuctNetworkSegment("R1", "G1", "J", 10), DuctNetworkSegment("R2", "G2", "J", 12),
         DuctNetworkSegment("R3", "J", "AHU", 15)),
        (DuctTerminal("RG1", "G1", 400), DuctTerminal("RG2", "G2", 600)),
        "AHU", sink_node_id="AHU",
    )


def test_return_solver_collects_grilles_toward_sink():
    network = return_network()
    assert validate_network(network).is_valid
    result = calculate_duct_network(network, options())
    assert result.network_type is NetworkType.RETURN
    assert {item.segment_id: item.calculated_airflow_cfm for item in result.segments} == {
        "R1": 400, "R2": 600, "R3": 1000
    }
    assert {item.terminal_id: item.upstream_path_segment_ids for item in result.terminals} == {
        "RG1": ("R1", "R3"), "RG2": ("R2", "R3")
    }
    assert solve_network_airflow(network).source_airflow_cfm == pytest.approx(1000)


def test_return_rejects_terminal_with_wrong_role():
    network = return_network()
    bad = DuctNetwork(network.network_id, network.network_type,
        tuple(DuctNode("G1", NodeType.JUNCTION) if node.node_id == "G1" else node for node in network.nodes),
        network.segments, network.terminals, network.source_node_id, sink_node_id="AHU")
    result = validate_network(bad)
    assert not result.is_valid
    assert any(item.code == "terminal_node_type" for item in result.diagnostics)
