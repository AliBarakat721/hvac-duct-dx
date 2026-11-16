from hap import StructuredHAPParser
from hap.models import AirSystem, CoolingData, FanData, SourceValue
from hap.validation import validate_system


def test_airflow_mismatch_is_warning():
    def value(number, unit, normalized_unit):
        return SourceValue(number, unit, number, normalized_unit, "test", 1)
    system = AirSystem(
        "S1", "S1", cooling=CoolingData(
            total_load=value(25000, "Btu/hr", "Btu/hr"),
            airflow=value(500, "CFM", "CFM"),
        ), fan=FanData(airflow=value(995, "CFM", "CFM")),
    )
    result = validate_system(system)
    assert result.status == "warning"
    assert any(item.code == "AIRFLOW_MISMATCH" for item in result.warnings)


def test_optional_sections_do_not_abort_import():
    text = """Air System Information\nAir System Name G01\nAir System Type SZCAV\nFloor Area 100 ft²\nCentral Cooling Coil Sizing Data\nTotal coil load 10 MBH\nSensible coil load 9 MBH\nCoil CFM at Aug 1600 400 CFM\n"""
    result = StructuredHAPParser().parse_text(text)
    assert result.project.systems[0].cooling.total_load.normalized_value == 10000
    assert result.project.systems[0].heating.load is None
    assert any(item.code == "HEATING_LOAD_MISSING" for item in result.validation.warnings)
