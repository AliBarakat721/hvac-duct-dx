from pathlib import Path

import openpyxl


WORKBOOK = Path(__file__).parent / "data" / "Ceiling Concealed Ducted Split Systems.xlsx"
MODEL = "53QDMT30-718"

# Values transcribed from CeilingConcealedDuctedSplitSystems.pdf, page 10
MODEL_VALUES = {
    "system_model": MODEL,
    "nominal_kbtu": 30,
    "indoor_model": "42QDMT30-718",
    "outdoor_model": "38QDMT30-718",
    "cooling_btuh": 30000,
    "cooling_input_w": 3515,
    "cooling_current_a": 16.3,
    "heating_btuh": 31000,
    "eer_btu_wh": 8.53,
    "eer_ww": 2.50,
    "cop_ww": 3.30,
    "max_esp_inwg": 0.32,
    "indoor_weight_kg": 41,
    "indoor_dims_mm": "1140 × 270 × 775",
    "power_supply": "220-240/1/50",
    "compressor_type": "Rotary",
    "refrigerant_type": "R22 / Flare",
    "outdoor_sound_dba": 62,
    "outdoor_dims_mm": "860 × 570 × 350",
    "outdoor_weight_kg": 62.4,
    "pipe_liquid_inch": "3/8",
    "pipe_gas_inch": "3/4",
    "max_pipe_length_m": 25,
    "max_height_diff_m": 10,
    "drain_hose_mm": 25,
    "nominal_airflow_cfm": 910,
    "nominal_airflow_m3h": 1542,
    "indoor_sound_dba": "48.2 / 42.4 / 40.8 @ 0.16 in.wg (High/Medium/Low)",
    "heating_input_w": 2755,
    "heating_current_a": 12.3,
}

# PDF page 20: 43 C outdoor, 27/19 C indoor, High, 790 CFM at 0.16 in.wg.
PERFORMANCE_POINT = {
    "system_model": MODEL,
    "esp_inwg": 0.16,
    "fan_speed": "High",
    "airflow_cfm": 790,
    "indoor_db_c": 27,
    "indoor_wb_c": 19,
    "outdoor_db_c": 43,
    "total_capacity_btuh": 26540,
    "sensible_capacity_btuh": 21765,
    "input_w": 3650,
    "source_page": 20,
}


def _headers(ws):
    return [cell.value for cell in ws[1]]


def _upsert_row(ws, key_index, key_value, values):
    headers = _headers(ws)
    if key_index >= len(headers):
        raise ValueError(f"Missing key column {key_index} in {ws.title}")
    row_number = None
    for row in range(2, ws.max_row + 1):
        if ws.cell(row, key_index + 1).value == key_value:
            row_number = row
            break
    if row_number is None:
        row_number = ws.max_row + 1
    for key, value in values.items():
        if key not in headers:
            raise ValueError(f"Missing column {key!r} in {ws.title}")
        ws.cell(row_number, headers.index(key) + 1).value = value
    return row_number


def main():
    workbook = openpyxl.load_workbook(WORKBOOK)
    models = workbook["Equipment_Models"]
    performance = workbook["Cooling_Performance"]
    _upsert_row(models, 0, MODEL, MODEL_VALUES)

    headers = _headers(performance)
    key = tuple(PERFORMANCE_POINT[column] for column in (
        "system_model", "esp_inwg", "fan_speed", "airflow_cfm",
        "indoor_db_c", "indoor_wb_c", "outdoor_db_c"))
    found = None
    for row in range(2, performance.max_row + 1):
        existing = tuple(performance.cell(row, headers.index(column) + 1).value
                         for column in (
                             "system_model", "esp_inwg", "fan_speed", "airflow_cfm",
                             "indoor_db_c", "indoor_wb_c", "outdoor_db_c"))
        if existing == key:
            found = row
            break
    if found is None:
        found = performance.max_row + 1
    for key_name, value in PERFORMANCE_POINT.items():
        performance.cell(found, headers.index(key_name) + 1).value = value

    workbook.save(WORKBOOK)
    print(f"Updated {WORKBOOK}")
    print(f"Equipment_Models row: {models.max_row}; Cooling_Performance row: {found}")


if __name__ == "__main__":
    main()
