from pathlib import Path

from extractors import RTFNativeText
from hap import StructuredHAPParser, to_legacy_system
from hap_parser import parse_hap, parse_hap_detailed

ROOT = Path(__file__).parents[2]


def test_legacy_parser_contract_remains_usable():
    text = RTFNativeText().extract(ROOT / "G01.RTF")
    total, sensible, cfm, heating, esp = parse_hap(text)
    detailed = parse_hap_detailed(text)
    assert total == 25300.0
    assert sensible == 22800.0
    assert (total, sensible, cfm, heating, esp) == (
        detailed["total_btu"], detailed["sensible_btu"], detailed["cfm"],
        detailed["heating_btu"], detailed["esp"],
    )


def test_structured_result_adapts_to_existing_system_dictionary():
    system = StructuredHAPParser().parse(ROOT / "G01.RTF").project.systems[0]
    legacy = to_legacy_system(system)
    assert legacy["name"] == "G01"
    assert legacy["total_btu"] == 25300.0
    assert legacy["sensible_btu"] == 22800.0
    assert legacy["cfm"] == 995
    assert legacy["heating_btu"] == 9300.0
    assert legacy["esp"] == 0.0
