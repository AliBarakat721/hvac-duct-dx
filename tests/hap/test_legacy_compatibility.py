from pathlib import Path

from extractors import RTFNativeText
from hap import StructuredHAPParser, to_legacy_system
from hap.models import AirSystem, CoolingData, SourceValue, ValidationIssue, ValidationResult
from hap_parser import parse_hap, parse_hap_detailed

ROOT = Path(__file__).parents[2]


def test_legacy_parser_contract_remains_usable():
    text = RTFNativeText().extract(ROOT / "tests" / "hap" / "fixtures" / "G01.RTF")
    total, sensible, cfm, heating, esp = parse_hap(text)
    detailed = parse_hap_detailed(text)
    assert total == 25300.0
    assert sensible == 22800.0
    assert (total, sensible, cfm, heating, esp) == (
        detailed["total_btu"], detailed["sensible_btu"], detailed["cfm"],
        detailed["heating_btu"], detailed["esp"],
    )


def test_structured_result_adapts_to_existing_system_dictionary():
    system = StructuredHAPParser().parse(ROOT / "tests" / "hap" / "fixtures" / "G01.RTF").project.systems[0]
    legacy = to_legacy_system(system)
    assert legacy["name"] == "G01"
    assert legacy["total_btu"] == 25300.0
    assert legacy["sensible_btu"] == 22800.0
    assert legacy["cfm"] == 995
    assert legacy["heating_btu"] == 9300.0
    assert legacy["esp"] == 0.0


def test_adapter_preserves_validation_and_missing_vs_zero_provenance():
    actual_zero = SourceValue(0.0, "Btu/hr", 0.0, "Btu/hr", "cooling_sizing", 8)
    system = AirSystem("S1", "S1", cooling=CoolingData(total_load=actual_zero))
    validation = ValidationResult(
        "error",
        [
            ValidationIssue("ERROR", "COOLING_AIRFLOW_MISSING", "Airflow is missing.", ["cooling_sizing"], "S1"),
            ValidationIssue("WARNING", "FAN_AIRFLOW_MISSING", "Fan airflow is missing.", ["fan_sizing"], "S1"),
            ValidationIssue("ERROR", "OTHER_SYSTEM_ERROR", "Belongs elsewhere.", ["cooling_sizing"], "S2"),
        ],
    )

    legacy = to_legacy_system(system, validation)
    assert legacy["total_btu"] == 0.0
    assert legacy["sensible_btu"] is None
    assert legacy["cfm"] is None
    assert legacy["heating_btu"] is None
    assert legacy["value_states"]["total_btu"]["state"] == "extracted"
    assert legacy["value_states"]["total_btu"]["normalized_value"] == 0.0
    assert legacy["value_states"]["sensible_btu"]["state"] == "missing"
    assert [issue["code"] for issue in legacy["critical_errors"]] == ["COOLING_AIRFLOW_MISSING"]
    assert [issue["code"] for issue in legacy["warnings"]] == ["FAN_AIRFLOW_MISSING"]
