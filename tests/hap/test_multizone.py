from hap import StructuredHAPParser


MULTI_ZONE_TEXT = """Air System Information
Air System Name MZ-1
Air System Type VAV
Floor Area 300 ft²
Location Test Building
Central Cooling Coil Sizing Data
Total coil load 30 MBH
Sensible coil load 27 MBH
Coil CFM at Aug 1600 300 CFM
Central Heating Coil Sizing Data
Max coil load 12 MBH
Coil CFM at Des Htg 300 CFM
Supply Fan Sizing Data
Actual max CFM 300 CFM
Fan static 0.50 in wg
Outdoor Ventilation Air Data
Design airflow CFM 60 CFM
Zone Sizing Data
Zone 1
10
100
100
5
100
1.0
Zone 2
20
200
200
7
200
1.0
Space Loads and Airflows
Zone 1
Office A
1
10
100
5
100
Zone 2
Office A
1
20
200
7
200
"""


def test_multiple_zones_and_spaces_remain_scoped():
    result = StructuredHAPParser().parse_text(MULTI_ZONE_TEXT)
    assert len(result.project.zones) == 2
    assert len(result.project.spaces) == 2
    first, second = result.project.zones
    assert first.space_ids == ["MZ-1:zone-1:space-office a"]
    assert second.space_ids == ["MZ-1:zone-2:space-office a"]
    assert first.cooling_load.total.normalized_value == 10000
    assert second.cooling_load.total.normalized_value == 20000
    assert first.cooling_load.airflow.normalized_value == 100
    assert second.cooling_load.airflow.normalized_value == 200
    assert result.project.spaces[0].zone_id == first.zone_id
    assert result.project.spaces[1].zone_id == second.zone_id
    assert result.project.spaces[0].cooling_load.total.normalized_value == 10000
    assert result.project.spaces[1].cooling_load.total.normalized_value == 20000


def test_system_keeps_zone_relationships_without_aggregating_loads():
    result = StructuredHAPParser().parse_text(MULTI_ZONE_TEXT)
    system = result.project.systems[0]
    assert system.zone_ids == ["MZ-1:zone-1", "MZ-1:zone-2"]
    assert system.cooling.total_load.normalized_value == 30000
    assert sum(zone.cooling_load.total.normalized_value for zone in result.project.zones) == 30000
    assert sum(space.cooling_load.total.normalized_value for space in result.project.spaces) == 30000
