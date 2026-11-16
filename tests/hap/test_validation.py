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


def test_percent_shr_is_normalized_and_checked_against_loads():
    text = """Air System Information
Air System Name S1
Central Cooling Coil Sizing Data
Total coil load 10 MBH
Sensible coil load 9 MBH
Sensible heat ratio 90%
Coil CFM at Aug 1600 400 CFM
"""
    result = StructuredHAPParser().parse_text(text)
    shr = result.project.systems[0].cooling.shr
    assert shr.normalized_value == 0.9
    assert shr.normalized_unit == "ratio"
    assert not any(issue.code == "SHR_MISMATCH" for issue in result.validation.issues)


def test_units_negative_values_and_load_component_consistency_are_errors():
    def value(number, unit, normalized_unit):
        return SourceValue(number, unit, number, normalized_unit, "test", 1)

    system = AirSystem(
        "S1", "S1",
        cooling=CoolingData(
            total_load=value(10000, "Btu/hr", "Btu/hr"),
            sensible_load=value(7000, "Btu/hr", "kW"),
            latent_load=value(4000, "Btu/hr", "Btu/hr"),
            airflow=value(-500, "CFM", "CFM"),
            shr=value(1.2, "ratio", "ratio"),
        ),
    )
    result = validate_system(system)
    codes = {issue.code for issue in result.errors}
    assert {"UNIT_MISMATCH", "NEGATIVE_VALUE", "LOAD_COMPONENTS_EXCEED_TOTAL", "SHR_OUT_OF_RANGE"} <= codes
    assert all(issue.system_id == "S1" for issue in result.errors)
