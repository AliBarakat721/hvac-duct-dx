import json
from pathlib import Path

from hap import StructuredHAPParser

ROOT = Path(__file__).parents[2]


def test_g01_matches_expected_structured_values():
    expected = json.loads((ROOT / "tests/hap/expected/G01.json").read_text())
    result = StructuredHAPParser().parse(ROOT / "tests/hap/fixtures/G01.RTF")
    system = result.project.systems[0]
    assert system.system_name == expected["system_name"]
    assert system.system_type == expected["system_type"]
    assert system.location == expected["location"]
    assert system.cooling.total_load.normalized_value == expected["cooling"]["total_load"]["normalized_value"]
    assert system.cooling.sensible_load.normalized_value == expected["cooling"]["sensible_load"]["normalized_value"]
    assert system.cooling.airflow.normalized_value == expected["cooling"]["airflow"]["normalized_value"]
    assert system.heating.load.normalized_value == expected["heating"]["load"]["normalized_value"]
    assert system.heating.airflow.normalized_value == expected["heating"]["airflow"]["normalized_value"]
    assert system.fan.airflow.normalized_value == expected["fan"]["airflow"]["normalized_value"]
    assert system.fan.esp.normalized_value == expected["fan"]["esp"]["normalized_value"]
    assert system.ventilation.outdoor_airflow.normalized_value == expected["ventilation"]["outdoor_airflow"]["normalized_value"]
    assert system.cooling.total_load.source_line == 48
    assert result.validation.status == "passed"
