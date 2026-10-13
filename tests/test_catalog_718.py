from pathlib import Path

import openpyxl


WORKBOOK = Path(__file__).resolve().parents[1] / "data" / "Ceiling Concealed Ducted Split Systems.xlsx"
MODEL = "53QDMT30-718"


def _rows(sheet_name):
    workbook = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    sheet = workbook[sheet_name]
    headers = [cell.value for cell in sheet[1]]
    return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]


def test_718_equipment_record_matches_catalog_page_10():
    row = next(row for row in _rows("Equipment_Models")
               if row["system_model"] == MODEL)
    expected = {
        "cooling_btuh": 30000,
        "heating_btuh": 31000,
        "max_esp_inwg": 0.32,
        "indoor_dims_mm": "1140 × 270 × 775",
        "outdoor_dims_mm": "860 × 570 × 350",
        "power_supply": "220-240/1/50",
        "pipe_liquid_inch": "3/8",
        "pipe_gas_inch": "3/4",
        "nominal_airflow_cfm": 910,
        "nominal_airflow_m3h": 1542,
        "heating_input_w": 2755,
        "heating_current_a": 12.3,
    }
    for field, value in expected.items():
        assert row[field] == value


def test_718_790_cfm_performance_includes_sensible_capacity_from_page_20():
    row = next(
        row for row in _rows("Cooling_Performance")
        if row["system_model"] == MODEL
        and row["esp_inwg"] == 0.16
        and row["fan_speed"] == "High"
        and row["airflow_cfm"] == 790
        and row["indoor_db_c"] == 27
        and row["indoor_wb_c"] == 19
        and row["outdoor_db_c"] == 43
    )
    assert row["total_capacity_btuh"] == 26540
    assert row["sensible_capacity_btuh"] == 21765
    assert row["input_w"] == 3650
    assert row["source_page"] == 20
