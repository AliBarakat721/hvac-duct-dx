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


def test_multiple_air_systems_keep_loads_zones_and_spaces_separate():
    second_system = (
        MULTI_ZONE_TEXT
        .replace("MZ-1", "MZ-2")
        .replace("Total coil load 30 MBH", "Total coil load 60 MBH")
        .replace("Sensible coil load 27 MBH", "Sensible coil load 54 MBH")
        .replace("Aug 1600 300 CFM", "Aug 1600 600 CFM")
        .replace("Actual max CFM 300 CFM", "Actual max CFM 600 CFM")
        .replace("Design airflow CFM 60 CFM", "Design airflow CFM 120 CFM")
    )
    result = StructuredHAPParser().parse_text(MULTI_ZONE_TEXT + "\n" + second_system)

    assert [system.system_id for system in result.project.systems] == ["MZ-1", "MZ-2"]
    first, second = result.project.systems
    assert first.cooling.total_load.normalized_value == 30000
    assert second.cooling.total_load.normalized_value == 60000
    assert first.cooling.airflow.normalized_value == 300
    assert second.cooling.airflow.normalized_value == 600
    assert first.zone_ids == ["MZ-1:zone-1", "MZ-1:zone-2"]
    assert second.zone_ids == ["MZ-2:zone-1", "MZ-2:zone-2"]
    assert [zone.system_id for zone in result.project.zones] == ["MZ-1", "MZ-1", "MZ-2", "MZ-2"]
    assert [space.zone_id for space in result.project.spaces] == [
        "MZ-1:zone-1", "MZ-1:zone-2", "MZ-2:zone-1", "MZ-2:zone-2",
    ]
    assert first.system_id not in {zone.system_id for zone in result.project.zones[2:]}


def test_ambiguous_shared_system_section_is_not_reused_for_each_system():
    second_system = MULTI_ZONE_TEXT.replace("MZ-1", "MZ-2")
    text = MULTI_ZONE_TEXT + "\n" + second_system
    # Keep only the first cooling section: its ownership is unknowable.
    marker = "Central Cooling Coil Sizing Data"
    first = text.find(marker)
    second = text.find(marker, first + len(marker))
    assert second >= 0
    text = text[:second] + text[second:].replace(marker, "Cooling Data Omitted", 1)

    result = StructuredHAPParser().parse_text(text)
    assert len(result.project.systems) == 2
    assert all(system.cooling.total_load is None for system in result.project.systems)
    assert any(issue.code == "SYSTEM_SECTION_MAPPING_AMBIGUOUS" for issue in result.validation.warnings)
    assert any(issue.code == "COOLING_LOAD_MISSING" for issue in result.validation.errors)
