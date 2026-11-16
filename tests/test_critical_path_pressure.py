import math
from dataclasses import replace

import pytest

from duct_design import (
    DuctDesignOptions,
    DuctNetworkCalculationError,
    DuctNetworkValidationError,
    DuctNetwork,
    DuctNetworkSegment,
    DuctNode,
    DuctTerminal,
    NetworkType,
    NodeType,
    PressureAnalysisStatus,
    analyze_critical_path,
    calculate_duct_network,
)


def options():
    return DuctDesignOptions(
        max_velocity_fpm=100_000,
        max_friction_inwg_100ft=10,
        min_width_in=8,
        max_width_in=16,
        min_height_in=8,
        max_height_in=16,
    )


def branch_network(*, reverse=False, network_type=NetworkType.SUPPLY):
    nodes = (
        DuctNode("SRC", NodeType.SOURCE),
        DuctNode("J", NodeType.JUNCTION),
        DuctNode("T1N", NodeType.TERMINAL),
        DuctNode("T2N", NodeType.TERMINAL),
    )
    segments = (
        DuctNetworkSegment("MAIN", "SRC", "J", 20, width_in=12, height_in=12, fitting_k=0.2),
        DuctNetworkSegment("LOW", "J", "T1N", 40, width_in=12, height_in=12, fitting_k=0.1),
        DuctNetworkSegment("HIGH", "J", "T2N", 10, width_in=8, height_in=8, fitting_k=3.0),
    )
    terminals = (DuctTerminal("T1", "T1N", 400), DuctTerminal("T2", "T2N", 400))
    if reverse:
        nodes, segments, terminals = tuple(reversed(nodes)), tuple(reversed(segments)), tuple(reversed(terminals))
    return DuctNetwork("PRESSURE", network_type, nodes, segments, terminals, "SRC")


def calculate(network=None):
    network = network or branch_network()
    return network, calculate_duct_network(network, options())


def test_single_path_sums_straight_fitting_and_total_without_double_counting():
    network = DuctNetwork(
        "ONE", NetworkType.SUPPLY,
        (DuctNode("SRC", NodeType.SOURCE), DuctNode("T", NodeType.TERMINAL)),
        (DuctNetworkSegment("S1", "SRC", "T", 30, width_in=12, height_in=12, fitting_k=1.0),),
        (DuctTerminal("TERM", "T", 500),), "SRC",
    )
    network, sized = calculate(network)
    result = analyze_critical_path(network, sized)
    segment = sized.segments[0].sizing_result
    assert result.critical_terminal_id == "TERM"
    assert result.critical_path_segment_ids == ("S1",)
    assert result.critical_path_pressure_loss_inwg == pytest.approx(segment.total_modeled_loss_inwg)
    path = result.terminal_paths[0]
    assert path.straight_loss_inwg == pytest.approx(segment.straight_friction_loss_inwg)
    assert path.fitting_loss_inwg == pytest.approx(segment.fitting_loss_inwg)
    assert path.total_loss_inwg == pytest.approx(path.straight_loss_inwg + path.fitting_loss_inwg)


def test_branch_paths_share_upstream_once_and_select_higher_loss_not_longer_path():
    network, sized = calculate()
    result = analyze_critical_path(network, sized)
    paths = {path.terminal_id: path for path in result.terminal_paths}
    assert paths["T1"].segment_ids == ("MAIN", "LOW")
    assert paths["T2"].segment_ids == ("MAIN", "HIGH")
    sized_by_id = {item.segment_id: item for item in sized.segments}
    main_loss = sized_by_id["MAIN"].sizing_result.total_modeled_loss_inwg
    assert paths["T1"].total_loss_inwg == pytest.approx(main_loss + paths["T1"].segment_losses[1].total_loss_inwg)
    assert result.critical_terminal_id == "T2"
    assert result.critical_path_pressure_loss_inwg == pytest.approx(paths["T2"].total_loss_inwg)
    assert paths["T1"].difference_from_critical_inwg == pytest.approx(
        paths["T2"].total_loss_inwg - paths["T1"].total_loss_inwg
    )


def test_deterministic_for_input_order_and_equal_loss_tie_uses_terminal_id():
    network, sized = calculate()
    reversed_network, reversed_sized = calculate(branch_network(reverse=True))
    assert analyze_critical_path(network, sized) == analyze_critical_path(reversed_network, reversed_sized)

    equal = branch_network()
    equal = replace(equal, segments=(
        equal.segments[0],
        replace(equal.segments[1], length_ft=10, fitting_k=0.1),
        replace(equal.segments[2], width_in=12, height_in=12, fitting_k=0.1),
    ))
    _, equal_sized = calculate(equal)
    equal_result = analyze_critical_path(equal, equal_sized)
    assert equal_result.critical_terminal_id == "T1"


def test_segment_details_are_reported_in_critical_path_order():
    network, sized = calculate()
    result = analyze_critical_path(network, sized)
    assert tuple(item.segment_id for item in result.critical_path_segments) == result.critical_path_segment_ids
    assert result.pressure_unit == "in.w.g."
    assert result.status is PressureAnalysisStatus.COMPLETE


def test_missing_segment_result_is_explicit_and_does_not_invent_zero():
    network, sized = calculate()
    missing_segments = tuple(
        replace(item, sizing_result=None) if item.segment_id == "MAIN" else item
        for item in sized.segments
    )
    missing = replace(sized, segments=missing_segments)
    result = analyze_critical_path(network, missing)
    assert result.status is PressureAnalysisStatus.INVALID
    assert any(item.code == "missing_segment_sizing_result" for item in result.diagnostics)
    assert all(path.total_loss_inwg is None for path in result.terminal_paths)


@pytest.mark.parametrize("field", ["straight_friction_loss_inwg", "fitting_loss_inwg", "total_modeled_loss_inwg"])
def test_negative_nan_and_infinite_pressure_values_are_rejected(field):
    network, sized = calculate()
    replacement = replace(sized.segments[0].sizing_result, **{field: -1.0})
    bad = replace(sized, segments=(replace(sized.segments[0], sizing_result=replacement), *sized.segments[1:]))
    assert any(item.code == "invalid_segment_pressure_loss" for item in analyze_critical_path(network, bad).diagnostics)
    for value in (math.nan, math.inf, -math.inf):
        replacement = replace(sized.segments[0].sizing_result, **{field: value})
        bad = replace(sized, segments=(replace(sized.segments[0], sizing_result=replacement), *sized.segments[1:]))
        assert any(item.code == "invalid_segment_pressure_loss" for item in analyze_critical_path(network, bad).diagnostics)


def test_extremely_large_finite_component_sum_reports_overflow():
    network, sized = calculate()
    huge = replace(
        sized.segments[0].sizing_result,
        straight_friction_loss_inwg=1e308,
        fitting_loss_inwg=1e308,
        total_modeled_loss_inwg=2e308,
    )
    bad = replace(sized, segments=(replace(sized.segments[0], sizing_result=huge), *sized.segments[1:]))
    result = analyze_critical_path(network, bad)
    assert any(item.code == "invalid_segment_pressure_loss" for item in result.diagnostics)


def test_inconsistent_total_is_rejected_with_component_tolerance():
    network, sized = calculate()
    original = sized.segments[0].sizing_result
    bad_total = replace(original, total_modeled_loss_inwg=original.total_modeled_loss_inwg + 0.1)
    bad = replace(sized, segments=(replace(sized.segments[0], sizing_result=bad_total), *sized.segments[1:]))
    result = analyze_critical_path(network, bad)
    assert any(item.code == "inconsistent_pressure_loss_total" for item in result.diagnostics)


def test_invalid_unit_and_return_network_are_explicit():
    network, sized = calculate()
    with pytest.raises(DuctNetworkCalculationError, match="Unsupported pressure unit"):
        analyze_critical_path(network, sized, pressure_unit="Pa")
    return_network, return_sized = calculate(branch_network(network_type=NetworkType.RETURN))
    result = analyze_critical_path(return_network, return_sized)
    assert result.status is PressureAnalysisStatus.UNSUPPORTED
    assert any(item.code == "return_network_unsupported" for item in result.diagnostics)
    assert result.critical_terminal_id is None


def test_duplicate_result_and_mismatched_network_are_reported():
    network, sized = calculate()
    duplicate = replace(sized, segments=sized.segments + (sized.segments[0],))
    other = replace(duplicate, network_id="OTHER")
    result = analyze_critical_path(network, other)
    assert {item.code for item in result.diagnostics} >= {"duplicate_segment_result", "network_result_mismatch"}


def test_zero_flow_terminal_has_no_sizing_and_is_not_silently_zero_loss():
    network = DuctNetwork(
        "ZERO", NetworkType.SUPPLY,
        (DuctNode("SRC", NodeType.SOURCE), DuctNode("T", NodeType.TERMINAL)),
        (DuctNetworkSegment("S1", "SRC", "T", 10, width_in=8, height_in=8),),
        (DuctTerminal("T1", "T", 0),), "SRC",
    )
    network, sized = calculate(network)
    result = analyze_critical_path(network, sized)
    assert result.status is PressureAnalysisStatus.INVALID
    assert any(item.code == "missing_segment_sizing_result" for item in result.diagnostics)


def test_topology_errors_remain_domain_errors_and_no_pressure_is_fabricated():
    network = DuctNetwork(
        "BAD", NetworkType.SUPPLY,
        (DuctNode("SRC", NodeType.SOURCE), DuctNode("T", NodeType.TERMINAL)),
        (), (DuctTerminal("T1", "T", 100),), "SRC",
    )
    with pytest.raises(DuctNetworkValidationError):
        calculate_duct_network(network, options())


def test_public_api_exports_analysis_function():
    import duct_design

    assert duct_design.analyze_critical_path is analyze_critical_path
    assert "analyze_critical_path" in duct_design.__all__
