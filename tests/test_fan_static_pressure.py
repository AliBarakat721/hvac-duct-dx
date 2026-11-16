import math
from dataclasses import replace

import pytest

from duct_design import (
    BoundaryClassification,
    DuctDesignOptions,
    DuctNetwork,
    DuctNetworkSegment,
    DuctNode,
    DuctTerminal,
    FanCalculationStatus,
    FanPressureCalculationError,
    FanPressureInput,
    FanPressurePath,
    FanSystemType,
    NetworkType,
    NodeType,
    PressureComponentType,
    PressureContribution,
    PressureDataSource,
    PressureUnit,
    analyze_critical_path,
    calculate_duct_network,
    calculate_fan_pressure,
    pressure_path_from_critical_path,
    pressure_to_inwg,
    velocity_pressure_inwg,
)


def component(component_id, loss, unit=PressureUnit.IN_WG, *, kind=PressureComponentType.EQUIPMENT, boundary=BoundaryClassification.EXTERNAL, source=PressureDataSource.MANUFACTURER, airflow=1000):
    return PressureContribution(component_id, kind, loss, unit, airflow, source, boundary)


def path(path_id, system_type, *components, airflow=1000):
    return FanPressurePath(path_id, system_type, "SYS", airflow, tuple(components))


def fan_input(system_type, paths, **kwargs):
    return FanPressureInput(
        fan_id="FAN-1",
        system_type=system_type,
        design_airflow_cfm=1000,
        paths=tuple(paths),
        fan_inlet_reference=kwargs.pop("fan_inlet_reference", "fan inlet plane"),
        fan_outlet_reference=kwargs.pop("fan_outlet_reference", "fan outlet plane"),
        boundaries_defined=kwargs.pop("boundaries_defined", True),
        known_components_complete=kwargs.pop("known_components_complete", True),
        **kwargs,
    )


def test_pressure_unit_conversions_and_velocity_pressure():
    assert pressure_to_inwg(249.08891, PressureUnit.PA) == pytest.approx(1.0)
    assert pressure_to_inwg(1, PressureUnit.KPA) == pytest.approx(1000 / 249.08891)
    assert velocity_pressure_inwg(1.204, 1000) == pytest.approx(0.06237, rel=0.01)


def test_simple_supply_budget_separates_external_and_internal_losses():
    supply = path(
        "SUPPLY-CRITICAL", FanSystemType.SUPPLY,
        component("FILTER", 0.20, kind=PressureComponentType.FILTER, boundary=BoundaryClassification.INTERNAL),
        component("DUCT", 100, PressureUnit.PA, kind=PressureComponentType.DUCT_FRICTION),
        component("COIL", 0.30, kind=PressureComponentType.COOLING_COIL, boundary=BoundaryClassification.INTERNAL),
    )
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [supply], inlet_velocity_fpm=500, outlet_velocity_fpm=800))
    expected_components = 0.20 + 100 / 249.08891 + 0.30
    assert result.component_pressure_loss_inwg == pytest.approx(expected_components)
    assert result.supply_pressure_loss_inwg == pytest.approx(expected_components)
    assert result.fan_external_static_pressure_inwg == pytest.approx(100 / 249.08891)
    assert result.fan_total_pressure_inwg == pytest.approx(expected_components + result.outlet_velocity_pressure_inwg - result.inlet_velocity_pressure_inwg)
    assert result.fesp_is_authoritative is False
    assert result.status is FanCalculationStatus.COMPLETE_MODELLED


def test_missing_boundaries_produces_preliminary_budget_not_false_fesp():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.SUPPLY,
        [path("S", FanSystemType.SUPPLY, component("D", 0.5))],
        boundaries_defined=False,
    ))
    assert result.component_pressure_loss_inwg == pytest.approx(0.5)
    assert result.fan_total_pressure_inwg == pytest.approx(0.5)
    assert result.fan_external_static_pressure_inwg is None
    assert result.status is FanCalculationStatus.PRELIMINARY
    assert any(item.code == "undefined_fan_boundaries" for item in result.diagnostics)


def test_complete_supply_return_circuit_sums_paths_but_not_unrelated_paths():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.COMPLETE,
        [
            path("SUP", FanSystemType.SUPPLY, component("S-DUCT", 0.7)),
            path("RET", FanSystemType.RETURN, component("R-DUCT", 0.4)),
        ],
    ))
    assert result.supply_pressure_loss_inwg == pytest.approx(0.7)
    assert result.return_pressure_loss_inwg == pytest.approx(0.4)
    assert result.component_pressure_loss_inwg == pytest.approx(1.1)
    assert result.fan_total_pressure_inwg == pytest.approx(1.1)


def test_return_budget_requires_explicit_return_path():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.RETURN,
        [path("RET", FanSystemType.RETURN, component("R", 0.6))],
    ))
    assert result.return_pressure_loss_inwg == pytest.approx(0.6)
    assert result.supply_pressure_loss_inwg is None


def test_complete_requires_both_supply_and_return_paths():
    result = calculate_fan_pressure(fan_input(FanSystemType.COMPLETE, [path("SUP", FanSystemType.SUPPLY, component("S", 0.5))]))
    assert result.status is FanCalculationStatus.INCOMPLETE
    assert any(item.code == "incomplete_system_paths" for item in result.diagnostics)
    assert result.fan_total_pressure_inwg is None


def test_unrelated_path_is_not_silently_combined():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.SUPPLY,
        [
            path("SUP", FanSystemType.SUPPLY, component("S", 0.5)),
            path("RET", FanSystemType.RETURN, component("R", 0.8)),
        ],
    ))
    assert result.status is FanCalculationStatus.INCOMPLETE
    assert any(item.code == "unselected_path" for item in result.diagnostics)


def test_missing_reference_plane_keeps_modelled_budget_but_not_complete_fesp():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.SUPPLY,
        [path("S", FanSystemType.SUPPLY, component("D", 0.5))],
        fan_inlet_reference=None,
    ))
    assert result.component_pressure_loss_inwg == pytest.approx(0.5)
    assert result.fan_external_static_pressure_inwg is None
    assert result.status is FanCalculationStatus.PRELIMINARY
    assert any(item.code == "incomplete_fan_reference_planes" for item in result.diagnostics)


def test_internal_losses_are_not_added_to_external_static_budget():
    result = calculate_fan_pressure(fan_input(
        FanSystemType.SUPPLY,
        [path("S", FanSystemType.SUPPLY,
              component("EXT", 0.4, boundary=BoundaryClassification.EXTERNAL),
              component("INT", 0.9, boundary=BoundaryClassification.INTERNAL))],
    ))
    assert result.fan_external_static_pressure_inwg == pytest.approx(0.4)
    assert result.component_pressure_loss_inwg == pytest.approx(1.3)


def test_critical_path_adapter_reuses_straight_and_fitting_losses_once():
    network = DuctNetwork(
        "N", NetworkType.SUPPLY,
        (DuctNode("SRC", NodeType.SOURCE), DuctNode("TERM", NodeType.TERMINAL)),
        (DuctNetworkSegment("S1", "SRC", "TERM", 30, width_in=12, height_in=12, fitting_k=0.5),),
        (DuctTerminal("T1", "TERM", 1000),), "SRC",
    )
    options = DuctDesignOptions(max_velocity_fpm=100000, max_friction_inwg_100ft=10, min_width_in=8, max_width_in=12, min_height_in=8, max_height_in=12)
    sized = calculate_duct_network(network, options)
    analysis = analyze_critical_path(network, sized)
    supply = pressure_path_from_critical_path(analysis, airflow_cfm=1000)
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [supply]))
    sized_segment = sized.segments[0].sizing_result
    assert len(result.lines) == 2
    assert result.component_pressure_loss_inwg == pytest.approx(sized_segment.total_modeled_loss_inwg)
    assert result.component_pressure_loss_inwg != pytest.approx(sized_segment.straight_friction_loss_inwg + sized_segment.fitting_loss_inwg + sized_segment.total_modeled_loss_inwg)


def test_critical_path_adapter_rejects_incomplete_analysis():
    from duct_design.network.pressure_analysis import CriticalPathAnalysisResult, PressureAnalysisStatus

    incomplete = CriticalPathAnalysisResult("N", NetworkType.SUPPLY, "in.w.g.", None, (), None, (), (), (), PressureAnalysisStatus.INVALID)
    with pytest.raises(FanPressureCalculationError, match="complete critical"):
        pressure_path_from_critical_path(incomplete, airflow_cfm=1000)


@pytest.mark.parametrize("value", [-1, math.nan, math.inf, -math.inf])
def test_invalid_component_pressure_is_not_converted_to_zero(value):
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY, component("BAD", value))]))
    assert result.status is FanCalculationStatus.INCOMPLETE
    assert any(item.code == "invalid_pressure_loss" for item in result.diagnostics)


def test_duplicate_component_and_path_ids_are_rejected():
    c1 = component("DUP", 0.2)
    result = calculate_fan_pressure(fan_input(FanSystemType.COMPLETE, [
        path("P", FanSystemType.SUPPLY, c1),
        path("P", FanSystemType.RETURN, component("DUP", 0.2)),
    ]))
    codes = {item.code for item in result.diagnostics}
    assert {"duplicate_or_invalid_path_id", "duplicate_component_id"} <= codes


def test_airflow_mismatch_is_explicit():
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY, component("D", 0.2), airflow=900)]))
    assert result.status is FanCalculationStatus.INCOMPLETE
    assert any(item.code == "path_airflow_mismatch" for item in result.diagnostics)


def test_invalid_units_are_explicit():
    with pytest.raises(FanPressureCalculationError, match="Unsupported pressure unit"):
        pressure_to_inwg(1, "psi")
    bad = replace(component("BAD", 1), pressure_unit="psi")
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY, bad)]))
    assert any(item.code == "invalid_component_metadata" for item in result.diagnostics)


def test_zero_and_negative_airflow_are_rejected():
    zero = calculate_fan_pressure(replace(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY, component("D", 0.2))]), design_airflow_cfm=0))
    assert any(item.code == "invalid_design_airflow" for item in zero.diagnostics)
    negative_path = path("S", FanSystemType.SUPPLY, component("D", 0.2), airflow=-10)
    negative = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [negative_path]))
    assert any(item.code == "invalid_path_airflow" for item in negative.diagnostics)


def test_missing_component_is_not_represented_by_an_implicit_zero():
    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY)]))
    assert result.component_pressure_loss_inwg is None
    assert result.fan_total_pressure_inwg is None
    assert result.lines == ()
    assert result.status is FanCalculationStatus.INCOMPLETE
    assert any(item.code == "missing_pressure_contribution" for item in result.diagnostics)


def test_api_exports_and_trace_metadata():
    import duct_design

    result = calculate_fan_pressure(fan_input(FanSystemType.SUPPLY, [path("S", FanSystemType.SUPPLY, component("F", 0.1, kind=PressureComponentType.FILTER))]))
    assert duct_design.calculate_fan_pressure is calculate_fan_pressure
    assert result.lines[0].component_type is PressureComponentType.FILTER
    assert result.lines[0].data_source is PressureDataSource.MANUFACTURER
    assert result.lines[0].network_id == "SYS"
