import math

import pytest

from duct_design import (
    AirflowSource,
    CalculationStatus,
    DiagnosticSeverity,
    DuctDesignOptions,
    DuctNetwork,
    DuctNetworkSegment,
    DuctNetworkValidationError,
    DuctNode,
    DuctTerminal,
    NetworkType,
    NetworkValidationPolicy,
    NodeType,
    ValidationStatus,
    calculate_duct_network,
    evaluate_rectangular_duct,
    solve_network_airflow,
    validate_network,
)
from duct_design.engine import DuctSegmentInput


def _options():
    return DuctDesignOptions(
        max_velocity_fpm=100_000,
        max_friction_inwg_100ft=10,
        min_width_in=8,
        max_width_in=8,
        min_height_in=8,
        max_height_in=8,
        size_step_in=1,
    )


def _simple_network(*, airflow=1000.0, source_airflow=None, segment_airflow=None):
    return DuctNetwork(
        network_id="N1",
        network_type=NetworkType.SUPPLY,
        nodes=(
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("T1_NODE", NodeType.TERMINAL),
        ),
        segments=(
            DuctNetworkSegment(
                "S1", "SRC", "T1_NODE", 30, airflow_cfm=segment_airflow, fitting_k=0.5
            ),
        ),
        terminals=(DuctTerminal("T1", "T1_NODE", airflow),),
        source_node_id="SRC",
        source_airflow_cfm=source_airflow,
    )


def _branch_network():
    return DuctNetwork(
        network_id="BRANCH",
        network_type=NetworkType.SUPPLY,
        nodes=(
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
            DuctNode("T1_NODE", NodeType.TERMINAL),
            DuctNode("T2_NODE", NodeType.TERMINAL),
        ),
        segments=(
            DuctNetworkSegment("S1", "SRC", "J1", 30),
            DuctNetworkSegment("S2", "J1", "T1_NODE", 20),
            DuctNetworkSegment("S3", "J1", "T2_NODE", 25),
        ),
        terminals=(
            DuctTerminal("T1", "T1_NODE", 1000),
            DuctTerminal("T2", "T2_NODE", 1500),
        ),
        source_node_id="SRC",
    )


def _nested_network():
    return DuctNetwork(
        network_id="NESTED",
        network_type=NetworkType.RETURN,
        nodes=(
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
            DuctNode("J2", NodeType.JUNCTION),
            DuctNode("T1_NODE", NodeType.TERMINAL),
            DuctNode("T2_NODE", NodeType.TERMINAL),
            DuctNode("T3_NODE", NodeType.TERMINAL),
        ),
        segments=(
            DuctNetworkSegment("S1", "SRC", "J1", 30),
            DuctNetworkSegment("S2", "J1", "T1_NODE", 20),
            DuctNetworkSegment("S3", "J1", "J2", 25),
            DuctNetworkSegment("S4", "J2", "T2_NODE", 20),
            DuctNetworkSegment("S5", "J2", "T3_NODE", 20),
        ),
        terminals=(
            DuctTerminal("T1", "T1_NODE", 500),
            DuctTerminal("T2", "T2_NODE", 700),
            DuctTerminal("T3", "T3_NODE", 800),
        ),
        source_node_id="SRC",
    )


def _three_level_network():
    return DuctNetwork(
        network_id="THREE_LEVEL",
        network_type=NetworkType.SUPPLY,
        nodes=(
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
            DuctNode("J2", NodeType.JUNCTION),
            DuctNode("J3", NodeType.JUNCTION),
            DuctNode("T1_NODE", NodeType.TERMINAL),
            DuctNode("T2_NODE", NodeType.TERMINAL),
            DuctNode("T3_NODE", NodeType.TERMINAL),
            DuctNode("T4_NODE", NodeType.TERMINAL),
        ),
        segments=(
            DuctNetworkSegment("S1", "SRC", "J1", 40),
            DuctNetworkSegment("S2", "J1", "T1_NODE", 20),
            DuctNetworkSegment("S3", "J1", "J2", 30),
            DuctNetworkSegment("S4", "J2", "T2_NODE", 20),
            DuctNetworkSegment("S5", "J2", "J3", 25),
            DuctNetworkSegment("S6", "J3", "T3_NODE", 20),
            DuctNetworkSegment("S7", "J3", "T4_NODE", 20),
        ),
        terminals=(
            DuctTerminal("T1", "T1_NODE", 100),
            DuctTerminal("T2", "T2_NODE", 200),
            DuctTerminal("T3", "T3_NODE", 300),
            DuctTerminal("T4", "T4_NODE", 400),
        ),
        source_node_id="SRC",
    )


def _codes(network):
    return {item.code for item in validate_network(network).diagnostics}


def test_single_path_calculates_source_and_segment_demand():
    result = calculate_duct_network(_simple_network(), _options())

    assert result.calculated_airflow_cfm == pytest.approx(1000)
    assert result.segments[0].calculated_airflow_cfm == pytest.approx(1000)
    assert result.segments[0].sizing_result.airflow_cfm == pytest.approx(1000)
    assert result.validation.is_valid


def test_two_branches_conserve_flow_and_size_each_segment_independently():
    result = calculate_duct_network(_branch_network(), _options())
    flows = {segment.segment_id: segment.calculated_airflow_cfm for segment in result.segments}

    assert flows == {"S1": 2500, "S2": 1000, "S3": 1500}
    assert result.calculated_airflow_cfm == pytest.approx(2500)
    assert flows["S1"] == pytest.approx(flows["S2"] + flows["S3"])
    assert result.network_total_pressure_inwg is None
    assert result.network_pressure_status.value == "NOT_CALCULATED"


def test_three_outlets_and_nested_branches_are_supported():
    network = _nested_network()
    result = calculate_duct_network(network, _options())
    flows = {segment.segment_id: segment.calculated_airflow_cfm for segment in result.segments}

    assert flows == {"S1": 2000, "S2": 500, "S3": 1500, "S4": 700, "S5": 800}
    assert result.calculated_airflow_cfm == pytest.approx(2000)
    assert result.segments[0].segment_id == "S1"
    assert {item.terminal_id: item.upstream_path_segment_ids for item in result.terminals} == {
        "T1": ("S1", "S2"),
        "T2": ("S1", "S3", "S4"),
        "T3": ("S1", "S3", "S5"),
    }


def test_three_levels_of_branching_aggregate_multiple_terminal_demands():
    result = calculate_duct_network(_three_level_network(), _options())
    flows = {segment.segment_id: segment.calculated_airflow_cfm for segment in result.segments}

    assert flows == {
        "S1": 1000,
        "S2": 100,
        "S3": 900,
        "S4": 200,
        "S5": 700,
        "S6": 300,
        "S7": 400,
    }
    assert result.calculated_airflow_cfm == pytest.approx(1000)
    assert flows["S1"] == pytest.approx(flows["S2"] + flows["S3"])
    assert flows["S3"] == pytest.approx(flows["S4"] + flows["S5"])
    assert flows["S5"] == pytest.approx(flows["S6"] + flows["S7"])
    assert {terminal.terminal_id: terminal.upstream_path_segment_ids for terminal in result.terminals} == {
        "T1": ("S1", "S2"),
        "T2": ("S1", "S3", "S4"),
        "T3": ("S1", "S3", "S5", "S6"),
        "T4": ("S1", "S3", "S5", "S7"),
    }


def test_airflow_and_results_are_independent_of_input_order():
    original = _branch_network()
    reversed_input = DuctNetwork(
        network_id=original.network_id,
        network_type=original.network_type,
        nodes=tuple(reversed(original.nodes)),
        segments=tuple(reversed(original.segments)),
        terminals=tuple(reversed(original.terminals)),
        source_node_id=original.source_node_id,
    )

    first = calculate_duct_network(original, _options())
    second = calculate_duct_network(reversed_input, _options())
    assert first.segments == second.segments
    assert first.terminals == second.terminals
    assert first.validation == second.validation


@pytest.mark.parametrize(
    ("node_order", "segment_order", "terminal_order"),
    [
        (("T2_NODE", "J1", "SRC", "T1_NODE"), ("S3", "S1", "S2"), ("T2", "T1")),
        (("SRC", "T1_NODE", "T2_NODE", "J1"), ("S2", "S3", "S1"), ("T1", "T2")),
        (("J1", "T2_NODE", "T1_NODE", "SRC"), ("S1", "S3", "S2"), ("T2", "T1")),
    ],
)
def test_multiple_input_permutations_preserve_flow_and_paths(
    node_order, segment_order, terminal_order
):
    source = _branch_network()
    node_by_id = {node.node_id: node for node in source.nodes}
    segment_by_id = {segment.segment_id: segment for segment in source.segments}
    terminal_by_id = {terminal.terminal_id: terminal for terminal in source.terminals}
    reordered = DuctNetwork(
        source.network_id,
        source.network_type,
        tuple(node_by_id[node_id] for node_id in node_order),
        tuple(segment_by_id[segment_id] for segment_id in segment_order),
        tuple(terminal_by_id[terminal_id] for terminal_id in terminal_order),
        source.source_node_id,
    )

    result = calculate_duct_network(reordered, _options())
    assert {item.segment_id: item.calculated_airflow_cfm for item in result.segments} == {
        "S1": 2500,
        "S2": 1000,
        "S3": 1500,
    }
    assert {item.terminal_id: item.upstream_path_segment_ids for item in result.terminals} == {
        "T1": ("S1", "S2"),
        "T2": ("S1", "S3"),
    }


def test_terminal_demands_resolve_to_segment_inputs_and_existing_loss_calculations():
    result = calculate_duct_network(_simple_network(), _options())
    segment = result.segments[0]
    sized = segment.sizing_result

    assert segment.segment_id == sized.tag == "S1"
    assert sized.length_ft == pytest.approx(30)
    assert sized.fitting_loss_inwg == pytest.approx(
        0.5 * sized.velocity_pressure_inwg
    )
    assert sized.total_modeled_loss_inwg == pytest.approx(
        sized.straight_friction_loss_inwg + sized.fitting_loss_inwg
    )


def test_network_sizing_preserves_every_existing_engine_output():
    network = _simple_network()
    network_segment = DuctNetworkSegment(
        "S1", "SRC", "T1_NODE", 30, width_in=16, height_in=12, fitting_k=0.5
    )
    fixed_network = DuctNetwork(
        network.network_id,
        network.network_type,
        network.nodes,
        (network_segment,),
        network.terminals,
        network.source_node_id,
    )
    options = DuctDesignOptions(
        max_velocity_fpm=100_000,
        max_friction_inwg_100ft=10,
        min_width_in=8,
        max_width_in=16,
        min_height_in=8,
        max_height_in=12,
    )
    actual = calculate_duct_network(fixed_network, options).segments[0].sizing_result
    expected = evaluate_rectangular_duct(
        DuctSegmentInput("S1", 1000, 30, 0.5), 16, 12, options
    )

    assert actual == expected


def test_sizing_engine_receives_calculated_flow_and_original_segment_identity(monkeypatch):
    import duct_design.network.network_engine as network_engine

    original_run_design = network_engine.run_design
    observed_inputs = []

    def observe_run_design(segments, options):
        rows = tuple(segments)
        observed_inputs.extend(rows)
        return original_run_design(rows, options)

    monkeypatch.setattr(network_engine, "run_design", observe_run_design)
    result = calculate_duct_network(_simple_network(), _options())

    assert len(observed_inputs) == 1
    assert observed_inputs[0].tag == "S1"
    assert observed_inputs[0].airflow_cfm == pytest.approx(1000)
    assert result.segments[0].sizing_result.tag == "S1"


def test_sizing_failure_is_reported_with_segment_identity():
    restrictive = DuctDesignOptions(
        max_velocity_fpm=1,
        max_friction_inwg_100ft=0.001,
        min_width_in=8,
        max_width_in=8,
        min_height_in=8,
        max_height_in=8,
        size_step_in=1,
    )
    result = calculate_duct_network(_simple_network(), restrictive)

    assert result.segments[0].sizing_result is None
    assert result.segments[0].validation_status is ValidationStatus.ERROR
    assert any(item.code == "segment_sizing_failed" for item in result.validation.diagnostics)


def test_manual_airflow_is_retained_and_mismatch_is_not_a_pass():
    network = _simple_network(airflow=1000, segment_airflow=900)
    result = calculate_duct_network(network, _options())
    segment = result.segments[0]

    assert segment.calculated_airflow_cfm == pytest.approx(1000)
    assert segment.specified_airflow_cfm == pytest.approx(900)
    assert segment.sizing_result.airflow_cfm == pytest.approx(1000)
    assert segment.airflow_source is AirflowSource.EXPLICIT_MISMATCH
    assert segment.validation_status is ValidationStatus.ERROR
    assert result.calculation_status is CalculationStatus.VALIDATION_FAILED
    assert any("900 CFM" in item.message and "1000 CFM" in item.message for item in result.validation.diagnostics)


def test_airflow_mismatch_policy_can_report_warning_without_a_false_pass():
    policy = NetworkValidationPolicy(mismatch_severity=DiagnosticSeverity.WARNING)
    result = calculate_duct_network(
        _simple_network(source_airflow=1200), _options(), policy
    )

    assert result.calculated_airflow_cfm == pytest.approx(1000)
    assert result.validation.is_valid
    assert result.calculation_status is CalculationStatus.COMPLETE_WITH_WARNINGS
    assert result.terminals[0].validation_status is ValidationStatus.WARNING


def test_source_flow_mismatch_reports_delta_and_relative_error():
    result = calculate_duct_network(_simple_network(source_airflow=1200), _options())
    diagnostic = next(
        item for item in result.validation.diagnostics
        if item.code == "source_airflow_mismatch"
    )

    assert "source minus calculated = 200 CFM" in diagnostic.message
    assert "relative error 20%" in diagnostic.message
    assert not result.validation.is_valid


@pytest.mark.parametrize(
    ("network", "expected_code"),
    [
        (
            DuctNetwork(
                "DUP_NODE", NetworkType.SUPPLY,
                (DuctNode("SRC", NodeType.SOURCE), DuctNode("SRC", NodeType.SOURCE)),
                (), (), "SRC",
            ),
            "duplicate_node_id",
        ),
        (
            DuctNetwork(
                "DUP_SEG", NetworkType.SUPPLY,
                (DuctNode("SRC", NodeType.SOURCE), DuctNode("END", NodeType.END)),
                (
                    DuctNetworkSegment("S", "SRC", "END", 10),
                    DuctNetworkSegment("S", "SRC", "END", 10),
                ),
                (DuctTerminal("T", "END", 100),), "SRC",
            ),
            "duplicate_segment_id",
        ),
        (
            DuctNetwork(
                "DUP_TERM", NetworkType.SUPPLY,
                (DuctNode("SRC", NodeType.SOURCE), DuctNode("T_NODE", NodeType.TERMINAL)),
                (DuctNetworkSegment("S", "SRC", "T_NODE", 10),),
                (DuctTerminal("T", "T_NODE", 100), DuctTerminal("T", "T_NODE", 100)),
                "SRC",
            ),
            "duplicate_terminal_id",
        ),
    ],
)
def test_duplicate_ids_are_rejected(network, expected_code):
    assert expected_code in _codes(network)
    with pytest.raises(DuctNetworkValidationError):
        solve_network_airflow(network)


def test_unknown_node_references_and_disconnected_elements_are_rejected():
    missing = DuctNetwork(
        "MISSING", NetworkType.SUPPLY,
        (DuctNode("SRC", NodeType.SOURCE), DuctNode("T", NodeType.TERMINAL)),
        (DuctNetworkSegment("S", "SRC", "UNKNOWN", 10),),
        (DuctTerminal("T1", "T", 100),), "SRC",
    )
    assert "unknown_node_reference" in _codes(missing)

    disconnected = DuctNetwork(
        "DISCONNECTED", NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("T", NodeType.TERMINAL),
            DuctNode("ORPHAN_A", NodeType.JUNCTION),
            DuctNode("ORPHAN_B", NodeType.END),
        ),
        (
            DuctNetworkSegment("S1", "SRC", "T", 10),
            DuctNetworkSegment("S2", "ORPHAN_A", "ORPHAN_B", 10),
        ),
        (DuctTerminal("T1", "T", 100),), "SRC",
    )
    assert {"disconnected_segment", "unreachable_node"} <= _codes(disconnected)


def test_terminal_connected_to_wrong_node_role_is_rejected():
    network = DuctNetwork(
        "WRONG_TERMINAL_NODE",
        NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
        ),
        (DuctNetworkSegment("S1", "SRC", "J1", 10),),
        (DuctTerminal("T1", "J1", 100),),
        "SRC",
    )

    assert "terminal_node_type" in _codes(network)
    with pytest.raises(DuctNetworkValidationError, match="must attach"):
        solve_network_airflow(network)


def test_cycle_and_unconnected_terminal_are_rejected_with_diagnostics():
    cycle = DuctNetwork(
        "CYCLE", NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
            DuctNode("J2", NodeType.JUNCTION),
            DuctNode("T", NodeType.TERMINAL),
        ),
        (
            DuctNetworkSegment("S1", "SRC", "J1", 10),
            DuctNetworkSegment("S2", "J1", "J2", 10),
            DuctNetworkSegment("S3", "J2", "J1", 10),
            DuctNetworkSegment("S4", "J2", "T", 10),
        ),
        (DuctTerminal("T1", "T", 100),), "SRC",
    )
    assert "cycle_detected" in _codes(cycle)
    with pytest.raises(DuctNetworkValidationError, match="cycle"):
        calculate_duct_network(cycle, _options())

    unconnected = DuctNetwork(
        "NO_LINK", NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("END", NodeType.END),
            DuctNode("T", NodeType.TERMINAL),
        ),
        (DuctNetworkSegment("S1", "SRC", "END", 10),),
        (DuctTerminal("T1", "T", 100),), "SRC",
    )
    assert "unreachable_terminal" in _codes(unconnected)
    with pytest.raises(DuctNetworkValidationError):
        solve_network_airflow(unconnected)


def test_invalid_node_types_self_loops_and_additional_sources_are_rejected():
    base = _simple_network()
    invalid_type = DuctNetwork(
        base.network_id,
        base.network_type,
        (DuctNode("SRC", "UNKNOWN"), base.nodes[1]),
        base.segments,
        base.terminals,
        base.source_node_id,
    )
    assert "invalid_node_type" in _codes(invalid_type)

    self_loop = DuctNetwork(
        base.network_id,
        base.network_type,
        base.nodes,
        (DuctNetworkSegment("S1", "SRC", "SRC", 10),),
        base.terminals,
        base.source_node_id,
    )
    assert "self_loop" in _codes(self_loop)

    additional_source = DuctNetwork(
        "MULTI_SOURCE",
        NetworkType.SUPPLY,
        base.nodes + (DuctNode("SRC2", NodeType.SOURCE),),
        base.segments,
        base.terminals,
        base.source_node_id,
    )
    assert {"source_count", "unexpected_source"} <= _codes(additional_source)


@pytest.mark.parametrize("bad_flow", [-1, math.nan, math.inf, -math.inf])
def test_invalid_terminal_and_source_airflow_are_rejected(bad_flow):
    terminal_network = _simple_network(airflow=bad_flow)
    assert "invalid_terminal_airflow" in _codes(terminal_network)

    source_network = _simple_network(source_airflow=bad_flow)
    assert "invalid_source_airflow" in _codes(source_network)


def test_integer_airflows_and_lengths_outside_float_range_are_diagnostics():
    network = DuctNetwork(
        "UNREPRESENTABLE",
        NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("T", NodeType.TERMINAL),
        ),
        (DuctNetworkSegment("S1", "SRC", "T", 10**1000),),
        (DuctTerminal("T1", "T", 100),),
        "SRC",
    )

    assert "invalid_segment_length" in _codes(network)
    with pytest.raises(DuctNetworkValidationError):
        calculate_duct_network(network, _options())


@pytest.mark.parametrize(
    ("segment_updates", "expected_code"),
    [
        ({"length_ft": math.nan}, "invalid_segment_length"),
        ({"length_ft": math.inf}, "invalid_segment_length"),
        ({"length_ft": 0}, "invalid_segment_length"),
        ({"airflow_cfm": -1}, "invalid_segment_airflow"),
        ({"airflow_cfm": math.inf}, "invalid_segment_airflow"),
        ({"fitting_k": -0.1}, "invalid_fitting_k"),
        ({"fitting_k": math.nan}, "invalid_fitting_k"),
        ({"width_in": 10}, "incomplete_dimensions"),
    ],
)
def test_invalid_segment_inputs_are_rejected(segment_updates, expected_code):
    segment_values = {"length_ft": 30, **segment_updates}
    segment = DuctNetworkSegment("S1", "SRC", "T1_NODE", **segment_values)
    network = _simple_network()
    invalid = DuctNetwork(
        network.network_id,
        network.network_type,
        network.nodes,
        (segment,),
        network.terminals,
        network.source_node_id,
    )
    assert expected_code in _codes(invalid)


def test_zero_demand_is_valid_but_does_not_run_invalid_zero_flow_sizing():
    result = calculate_duct_network(_simple_network(airflow=0), _options())
    segment = result.segments[0]

    assert result.calculated_airflow_cfm == 0
    assert segment.calculated_airflow_cfm == 0
    assert segment.sizing_result is None
    assert segment.airflow_source is AirflowSource.ZERO_DEMAND
    assert segment.validation_status is ValidationStatus.PASS


def test_zero_flow_branch_is_not_sized_when_other_branch_has_demand():
    network = DuctNetwork(
        "ZERO_BRANCH",
        NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J1", NodeType.JUNCTION),
            DuctNode("T1_NODE", NodeType.TERMINAL),
            DuctNode("ZERO_END", NodeType.END),
        ),
        (
            DuctNetworkSegment("MAIN", "SRC", "J1", 20),
            DuctNetworkSegment("ACTIVE", "J1", "T1_NODE", 10),
            DuctNetworkSegment("ZERO", "J1", "ZERO_END", 5),
        ),
        (DuctTerminal("T1", "T1_NODE", 500),),
        "SRC",
    )

    result = calculate_duct_network(network, _options())
    segments = {segment.segment_id: segment for segment in result.segments}
    assert segments["MAIN"].calculated_airflow_cfm == pytest.approx(500)
    assert segments["ACTIVE"].calculated_airflow_cfm == pytest.approx(500)
    assert segments["ZERO"].calculated_airflow_cfm == 0
    assert segments["ZERO"].sizing_result is None
    assert segments["ZERO"].airflow_source is AirflowSource.ZERO_DEMAND


def test_overflowed_aggregate_terminal_demand_is_rejected():
    network = DuctNetwork(
        "OVERFLOW",
        NetworkType.SUPPLY,
        (
            DuctNode("SRC", NodeType.SOURCE),
            DuctNode("J", NodeType.JUNCTION),
            DuctNode("T1", NodeType.TERMINAL),
            DuctNode("T2", NodeType.TERMINAL),
        ),
        (
            DuctNetworkSegment("S1", "SRC", "J", 10),
            DuctNetworkSegment("S2", "J", "T1", 10),
            DuctNetworkSegment("S3", "J", "T2", 10),
        ),
        (
            DuctTerminal("D1", "T1", 1e308),
            DuctTerminal("D2", "T2", 1e308),
        ),
        "SRC",
    )
    with pytest.raises(ValueError, match="Aggregated airflow"):
        solve_network_airflow(network)


def test_fixed_dimensions_use_existing_duct_evaluation():
    network = _simple_network()
    fixed_segment = DuctNetworkSegment(
        "S1", "SRC", "T1_NODE", 30, width_in=16, height_in=12, fitting_k=0.5
    )
    fixed = DuctNetwork(
        network.network_id, network.network_type, network.nodes,
        (fixed_segment,), network.terminals, network.source_node_id,
    )
    fixed_options = DuctDesignOptions(
        max_velocity_fpm=100_000,
        max_friction_inwg_100ft=10,
        min_width_in=8,
        max_width_in=16,
        min_height_in=8,
        max_height_in=12,
    )
    result = calculate_duct_network(fixed, fixed_options)
    assert result.segments[0].sizing_result.width_in == 16
    assert result.segments[0].sizing_result.height_in == 12


def test_fixed_dimensions_that_exceed_criteria_are_reported_as_errors():
    network = _simple_network()
    segment = DuctNetworkSegment(
        "S1", "SRC", "T1_NODE", 30, width_in=4, height_in=4
    )
    fixed = DuctNetwork(
        network.network_id, network.network_type, network.nodes,
        (segment,), network.terminals, network.source_node_id,
    )
    result = calculate_duct_network(fixed, _options())

    assert result.segments[0].sizing_result is None
    assert result.segments[0].validation_status is ValidationStatus.ERROR
    assert any(
        item.code == "specified_size_exceeds_criteria"
        for item in result.validation.diagnostics
    )


def test_fixed_dimensions_must_match_configured_size_grid():
    network = _simple_network()
    segment = DuctNetwork(
        network.network_id,
        network.network_type,
        network.nodes,
        (
            DuctNetworkSegment(
                "S1", "SRC", "T1_NODE", 30, width_in=9.5, height_in=8
            ),
        ),
        network.terminals,
        network.source_node_id,
    )
    result = calculate_duct_network(
        segment,
        DuctDesignOptions(
            max_velocity_fpm=100_000,
            max_friction_inwg_100ft=10,
            min_width_in=8,
            max_width_in=10,
            min_height_in=8,
            max_height_in=8,
            size_step_in=1,
        ),
    )

    assert result.segments[0].sizing_result is not None
    assert result.segments[0].validation_status is ValidationStatus.ERROR
    assert any(
        item.code == "specified_size_off_grid"
        for item in result.validation.diagnostics
    )


def test_tiny_fixed_dimensions_are_rejected_before_numeric_sizing():
    network = _simple_network()
    segment = DuctNetworkSegment(
        "S1", "SRC", "T1_NODE", 30, width_in=1e-300, height_in=1e-300
    )
    fixed = DuctNetwork(
        network.network_id,
        network.network_type,
        network.nodes,
        (segment,),
        network.terminals,
        network.source_node_id,
    )

    result = calculate_duct_network(fixed, _options())
    assert result.segments[0].sizing_result is None
    assert result.segments[0].validation_status is ValidationStatus.ERROR
    assert any(
        item.code == "specified_size_exceeds_criteria"
        for item in result.validation.diagnostics
    )


def test_unrepresentable_sizing_result_is_reported_without_nonfinite_losses():
    network = _simple_network(airflow=1e200)
    segment = DuctNetworkSegment(
        "S1", "SRC", "T1_NODE", 30, width_in=8, height_in=8
    )
    fixed = DuctNetwork(
        network.network_id,
        network.network_type,
        network.nodes,
        (segment,),
        network.terminals,
        network.source_node_id,
    )

    result = calculate_duct_network(fixed, _options())
    assert result.segments[0].sizing_result is None
    assert result.segments[0].validation_status is ValidationStatus.ERROR
    assert any(
        item.code == "segment_sizing_failed"
        for item in result.validation.diagnostics
    )


def test_separate_supply_and_return_network_calculations_do_not_share_airflow():
    supply = calculate_duct_network(_simple_network(airflow=1200), _options())
    return_network = DuctNetwork(
        "RETURN1",
        NetworkType.RETURN,
        _simple_network(airflow=450).nodes,
        _simple_network(airflow=450).segments,
        _simple_network(airflow=450).terminals,
        "SRC",
    )
    ret = calculate_duct_network(return_network, _options())

    assert supply.calculated_airflow_cfm == pytest.approx(1200)
    assert ret.calculated_airflow_cfm == pytest.approx(450)
    assert supply.network_type is NetworkType.SUPPLY
    assert ret.network_type is NetworkType.RETURN
