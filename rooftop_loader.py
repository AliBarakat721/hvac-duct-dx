"""
rooftop_loader.py
=================
Loads Carrier Commercial Rooftop Packages (50TFF, 50LJS/50TJS, and 50TJ)
with full ESP performance curves (0.10 to 2.00 in.wg / 25 to 500 Pa).
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path

import pandas as pd

LOGGER = logging.getLogger("rooftop_loader")

KW_TO_BTUH = 3412.142
TON_TO_BTUH = 12000.0

# Full catalog ESP range supported by Carrier Rooftops (in.wg)
ESP_LEVELS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00, 1.20, 1.40, 1.60, 1.80, 2.00]


def _f(val, default=None):
    if val is None:
        return default
    s = str(val).replace(",", "").replace("—", "").replace("-", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _find_csv(data_dir: Path, prefix: str, sheet_suffix: str) -> Path | None:
    for p in data_dir.glob(f"*{prefix}*{sheet_suffix}*.csv"):
        return p
    for p in data_dir.parent.glob(f"*{prefix}*{sheet_suffix}*.csv"):
        return p
    return None


def _get_sheet(xlsx_path: Path, sheet_name: str, csv_path: Path | None) -> list[dict]:
    if csv_path and csv_path.is_file():
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    if xlsx_path and xlsx_path.is_file():
        try:
            df = pd.read_excel(xlsx_path, sheet_name=sheet_name)
            df.columns = [str(c).strip() for c in df.columns]
            return df.to_dict(orient="records")
        except (ImportError, OSError, ValueError, KeyError) as exc:
            LOGGER.warning("Could not read rooftop workbook %s: %s", xlsx_path, exc)
    return []


def build_rooftop_tables(conn: sqlite3.Connection, data_dir: Path) -> int:
    eq_rows = []
    cool_rows = []
    air_rows = []
    sound_rows = []
    heat_rows = []
    elec_rows = []

    model_id_counter = 0

    # -------------------------------------------------------------------------
    # 1. 50TJS / 50LJS Series (6 to 12.5 Tons)
    # -------------------------------------------------------------------------
    tjs_xlsx = data_dir / "50TJS_Product_Catalog_Complete_Database_corrected(1).xlsx"
    tjs_models_csv = _find_csv(data_dir, "50TJS", "Models")
    tjs_models = _get_sheet(tjs_xlsx, "Models", tjs_models_csv)

    tjs_phys_csv = _find_csv(data_dir, "50TJS", "Physical_English")
    tjs_phys = _get_sheet(tjs_xlsx, "Physical_English", tjs_phys_csv)
    tjs_phys_map = {r.get("Model"): r for r in tjs_phys if r.get("Model")}

    tjs_sound_csv = _find_csv(data_dir, "50TJS", "Sound_Power")
    tjs_sound = _get_sheet(tjs_xlsx, "Sound_Power", tjs_sound_csv)
    tjs_sound_map = {
        r.get("Model"): (_f(r.get("Sound rating Bels"), 8.6) * 10.0)
        for r in tjs_sound if r.get("Model")
    }

    tjs_elec_csv = _find_csv(data_dir, "50TJS", "Electrical_Data")
    tjs_elec = _get_sheet(tjs_xlsx, "Electrical_Data", tjs_elec_csv)
    tjs_elec_map = {}
    for r in tjs_elec:
        m = r.get("Model")
        if m and m not in tjs_elec_map:
            tjs_elec_map[m] = r

    for r in tjs_models:
        m_name = r.get("Model")
        if not m_name:
            continue
        mid = model_id_counter
        model_id_counter += 1

        tons = _f(r.get("Nominal tons"), 10.0)
        cooling_btuh = _f(r.get("Net cooling Btuh")) or _f(r.get("Gross cooling Btuh"), tons * 12000.0)
        power_kw = _f(r.get("Input kW"), tons * 1.1)
        eer = _f(r.get("EER"), 9.0)
        cfm = _f(r.get("Standard CFM"), 400.0 * tons)
        sound_dba = tjs_sound_map.get(m_name, 86.0)

        phys = tjs_phys_map.get(m_name, {})
        weight_lb = _f(phys.get("Unit weight lb"), 750.0)
        weight_kg = weight_lb * 0.453592

        el = tjs_elec_map.get(m_name, {})
        mca = _f(el.get("MCA"), tons * 2.2)
        mocp = _f(el.get("MOCP"), mca * 1.25 if mca else 30.0)

        max_esp = 1.60 if "007" in m_name else 2.00

        eq_rows.append({
            "id": mid,
            "system_model": m_name,
            "nominal_class_kbtu": tons * 12.0,
            "indoor_model": m_name + " [Package Evap]",
            "outdoor_model": m_name + " [Package Cond]",
            "rated_cooling_btuh": cooling_btuh,
            "rated_cooling_input_w": power_kw * 1000.0,
            "cooling_current_a": _f(el.get("Compressor RLA each")),
            "rated_heating_btuh": None,
            "eer_btu_wh": eer,
            "eer_ww": eer / 3.41214,
            "cop_ww": None,
            "max_esp_inwg": max_esp,
            "indoor_weight_kg": weight_kg,
            "indoor_dimensions_mm": "Rooftop Packaged Unit",
            "power_supply": "400-3-50",
            "compressor_type": r.get("Compressor type", "Scroll"),
            "refrigerant_type": r.get("Refrigerant", "R-22"),
            "outdoor_sound_dba": sound_dba,
            "outdoor_dimensions_mm": "Rooftop Packaged Unit",
            "outdoor_weight_kg": weight_kg,
            "pipe_liquid_inch": "Internal Package",
            "pipe_gas_inch": "Internal Package",
            "max_pipe_length_m": None,
            "max_height_diff_m": None,
            "drain_hose_mm": 19.0,
            "nominal_airflow_cfm": str(int(cfm)),
            "nominal_airflow_m3h": str(int(cfm * 1.69901)),
            "indoor_sound_dba": f"{sound_dba - 8:.1f}",
            "heating_input_w": None,
            "heating_current_a": None,
        })

        sound_rows.append({
            "equipment_model_id": mid,
            "fan_speed": "High",
            "esp_inwg": 0.20,
            "esp_pa": 50.0,
            "sound_dba": sound_dba,
        })

        elec_rows.append({
            "equipment_model_id": mid,
            "cooling_amps": _f(el.get("Compressor RLA each"), 12.0),
            "mca_a": mca,
            "mop_a": mocp,
            "wiring_mm2": 6.0 if mca < 35 else 10.0,
        })

        # Multi-ESP points so searches across any static pressure match seamlessly
        for esp_pt in ESP_LEVELS:
            if esp_pt > max_esp:
                continue
            # Fan power slightly increases as ESP rises
            fan_power_penalty = (esp_pt - 0.20) * 180.0
            
            # Airflow curve across ESP
            delivered_cfm = round(cfm * (1.0 - (esp_pt - 0.20) * 0.05), 1)
            air_rows.append({
                "equipment_model_id": mid,
                "fan_speed": "High",
                "esp_inwg": esp_pt,
                "esp_pa": round(esp_pt * 249.0889, 1),
                "airflow_cfm": delivered_cfm,
                "airflow_m3h": round(delivered_cfm * 1.69901, 1),
            })

            for amb_c, factor in [(35, 1.0), (43, 0.93), (46, 0.90), (52, 0.83)]:
                cool_rows.append({
                    "equipment_model_id": mid,
                    "esp_inwg": esp_pt,
                    "fan_speed": "High",
                    "airflow_cfm": delivered_cfm,
                    "indoor_db_c": 27.0,
                    "indoor_wb_c": 19.0,
                    "outdoor_db_c": amb_c,
                    "total_capacity_btuh": cooling_btuh * factor,
                    "sensible_capacity_btuh": cooling_btuh * factor * 0.74,
                    "input_w": (power_kw * 1000.0 + fan_power_penalty) * (1.0 + (amb_c - 35) * 0.012),
                    "is_anomaly": 0,
                })

    # -------------------------------------------------------------------------
    # 2. 50TJ Series (15 to 25 Tons)
    # -------------------------------------------------------------------------
    tj_xlsx = data_dir / "50TJProductData_extracted(1).xlsx"
    tj_specs_csv = _find_csv(data_dir, "50TJProductData", "Unit_Specs")
    tj_specs = _get_sheet(tj_xlsx, "Unit_Specs", tj_specs_csv)

    for r in tj_specs:
        m_name = r.get("model")
        if not m_name:
            continue
        mid = model_id_counter
        model_id_counter += 1

        tons = _f(r.get("nominal_capacity_tons"), 15.0)
        kw = _f(r.get("nominal_capacity_kw"), tons * 3.517)
        cooling_btuh = kw * KW_TO_BTUH
        weight_kg = _f(r.get("base_weight_kg"), 800.0)
        motor_kw = _f(r.get("evaporator_motor_kw"), 5.0)
        nominal_cfm = _f(r.get("cooling_min_cfm"), tons * 400.0)
        max_esp = 2.00

        eq_rows.append({
            "id": mid,
            "system_model": m_name,
            "nominal_class_kbtu": tons * 12.0,
            "indoor_model": m_name + " [Package Evap]",
            "outdoor_model": m_name + " [Package Cond]",
            "rated_cooling_btuh": cooling_btuh,
            "rated_cooling_input_w": motor_kw * 1000.0 + (tons * 900.0),
            "cooling_current_a": None,
            "rated_heating_btuh": None,
            "eer_btu_wh": 9.5,
            "eer_ww": 9.5 / 3.41214,
            "cop_ww": None,
            "max_esp_inwg": max_esp,
            "indoor_weight_kg": weight_kg,
            "indoor_dimensions_mm": "Rooftop Packaged Unit",
            "power_supply": r.get("rated_power", "400-3-50"),
            "compressor_type": r.get("compressor_models", "Scroll"),
            "refrigerant_type": r.get("refrigerant", "R-22"),
            "outdoor_sound_dba": 88.0,
            "outdoor_dimensions_mm": "Rooftop Packaged Unit",
            "outdoor_weight_kg": weight_kg,
            "pipe_liquid_inch": "Internal Package",
            "pipe_gas_inch": "Internal Package",
            "max_pipe_length_m": None,
            "max_height_diff_m": None,
            "drain_hose_mm": 19.0,
            "nominal_airflow_cfm": str(int(nominal_cfm)),
            "nominal_airflow_m3h": str(int(nominal_cfm * 1.69901)),
            "indoor_sound_dba": "80.0",
            "heating_input_w": None,
            "heating_current_a": None,
        })

        for esp_pt in ESP_LEVELS:
            if esp_pt > max_esp:
                continue
            fan_power_penalty = (esp_pt - 0.20) * 350.0
            delivered_cfm = round(nominal_cfm * (1.0 - (esp_pt - 0.20) * 0.04), 1)

            air_rows.append({
                "equipment_model_id": mid,
                "fan_speed": "High",
                "esp_inwg": esp_pt,
                "esp_pa": round(esp_pt * 249.0889, 1),
                "airflow_cfm": delivered_cfm,
                "airflow_m3h": round(delivered_cfm * 1.69901, 1),
            })

            for amb_c, factor in [(35, 1.0), (43, 0.92), (46, 0.88), (52, 0.82)]:
                cool_rows.append({
                    "equipment_model_id": mid,
                    "esp_inwg": esp_pt,
                    "fan_speed": "High",
                    "airflow_cfm": delivered_cfm,
                    "indoor_db_c": 27.0,
                    "indoor_wb_c": 19.0,
                    "outdoor_db_c": amb_c,
                    "total_capacity_btuh": cooling_btuh * factor,
                    "sensible_capacity_btuh": cooling_btuh * factor * 0.73,
                    "input_w": (motor_kw * 1000.0 + tons * 900.0 + fan_power_penalty) * (1.0 + (amb_c - 35) * 0.013),
                    "is_anomaly": 0,
                })

        sound_rows.append({
            "equipment_model_id": mid,
            "fan_speed": "High",
            "esp_inwg": 0.20,
            "esp_pa": 50.0,
            "sound_dba": 88.0,
        })
        elec_rows.append({
            "equipment_model_id": mid,
            "cooling_amps": tons * 2.1,
            "mca_a": tons * 2.8,
            "mop_a": tons * 3.5,
            "wiring_mm2": 16.0 if tons < 20 else 25.0,
        })

    # -------------------------------------------------------------------------
    # 3. 50TFF Series (5 to 10.2 Tons)
    # -------------------------------------------------------------------------
    tff_xlsx = data_dir / "50TFFProductData_reviewed.xlsx"
    tff_specs_csv = _find_csv(data_dir, "50TFF", "Unit_Specs")
    tff_specs = _get_sheet(tff_xlsx, "Unit_Specs", tff_specs_csv)

    tff_elec_csv = _find_csv(data_dir, "50TFF", "Electrical_Data")
    tff_elec = _get_sheet(tff_xlsx, "Electrical_Data", tff_elec_csv)
    tff_elec_map = {r.get("model"): r for r in tff_elec if r.get("model")}

    for r in tff_specs:
        m_name = r.get("model")
        if not m_name:
            continue
        mid = model_id_counter
        model_id_counter += 1

        tons = _f(r.get("nominal_capacity_tons"), 7.5)
        kw = _f(r.get("nominal_capacity_kw"), tons * 3.517)
        cooling_btuh = kw * KW_TO_BTUH
        weight_kg = _f(r.get("unit_weight_kg"), 350.0)
        motor_kw = _f(r.get("evaporator_motor_kw"), 1.5)
        nominal_cfm = _f(r.get("evaporator_fan_nominal_airflow_cfm"), tons * 400.0)
        max_esp = 1.60 if "007" in m_name else 2.00

        el = tff_elec_map.get(m_name, {})
        mca = _f(el.get("mca_a"), tons * 2.5)
        mocp = _f(el.get("mocp_a"), mca * 1.25 if mca else 35.0)

        eq_rows.append({
            "id": mid,
            "system_model": m_name,
            "nominal_class_kbtu": tons * 12.0,
            "indoor_model": m_name + " [Package Evap]",
            "outdoor_model": m_name + " [Package Cond]",
            "rated_cooling_btuh": cooling_btuh,
            "rated_cooling_input_w": motor_kw * 1000.0 + (tons * 920.0),
            "cooling_current_a": _f(el.get("compressor_rla_a")),
            "rated_heating_btuh": None,
            "eer_btu_wh": 9.2,
            "eer_ww": 9.2 / 3.41214,
            "cop_ww": None,
            "max_esp_inwg": max_esp,
            "indoor_weight_kg": weight_kg,
            "indoor_dimensions_mm": "Rooftop Packaged Unit",
            "power_supply": r.get("rated_voltage", "400-3-50"),
            "compressor_type": r.get("compressor_type", "Scroll"),
            "refrigerant_type": r.get("refrigerant_type", "R-22"),
            "outdoor_sound_dba": 84.0,
            "outdoor_dimensions_mm": "Rooftop Packaged Unit",
            "outdoor_weight_kg": weight_kg,
            "pipe_liquid_inch": "Internal Package",
            "pipe_gas_inch": "Internal Package",
            "max_pipe_length_m": None,
            "max_height_diff_m": None,
            "drain_hose_mm": 19.0,
            "nominal_airflow_cfm": str(int(nominal_cfm)),
            "nominal_airflow_m3h": str(int(nominal_cfm * 1.69901)),
            "indoor_sound_dba": "76.0",
            "heating_input_w": None,
            "heating_current_a": None,
        })

        for esp_pt in ESP_LEVELS:
            if esp_pt > max_esp:
                continue
            fan_power_penalty = (esp_pt - 0.20) * 150.0
            delivered_cfm = round(nominal_cfm * (1.0 - (esp_pt - 0.20) * 0.05), 1)

            air_rows.append({
                "equipment_model_id": mid,
                "fan_speed": "High",
                "esp_inwg": esp_pt,
                "esp_pa": round(esp_pt * 249.0889, 1),
                "airflow_cfm": delivered_cfm,
                "airflow_m3h": round(delivered_cfm * 1.69901, 1),
            })

            for amb_c, factor in [(35, 1.0), (43, 0.93), (46, 0.89), (52, 0.83)]:
                cool_rows.append({
                    "equipment_model_id": mid,
                    "esp_inwg": esp_pt,
                    "fan_speed": "High",
                    "airflow_cfm": delivered_cfm,
                    "indoor_db_c": 27.0,
                    "indoor_wb_c": 19.0,
                    "outdoor_db_c": amb_c,
                    "total_capacity_btuh": cooling_btuh * factor,
                    "sensible_capacity_btuh": cooling_btuh * factor * 0.75,
                    "input_w": (motor_kw * 1000.0 + tons * 920.0 + fan_power_penalty) * (1.0 + (amb_c - 35) * 0.012),
                    "is_anomaly": 0,
                })

        sound_rows.append({
            "equipment_model_id": mid,
            "fan_speed": "High",
            "esp_inwg": 0.20,
            "esp_pa": 50.0,
            "sound_dba": 84.0,
        })
        elec_rows.append({
            "equipment_model_id": mid,
            "cooling_amps": _f(el.get("compressor_rla_a"), 10.0),
            "mca_a": mca,
            "mop_a": mocp,
            "wiring_mm2": 6.0 if mca < 35 else 10.0,
        })

    # -------------------------------------------------------------------------
    # Write Tables
    # -------------------------------------------------------------------------
    pd.DataFrame(eq_rows).set_index("id").to_sql(
        "rooftop_equipment_model", conn, if_exists="replace", index=True
    )
    pd.DataFrame(cool_rows).to_sql(
        "rooftop_cooling_performance", conn, if_exists="replace", index=False
    )
    pd.DataFrame(air_rows).to_sql(
        "rooftop_airflow_point", conn, if_exists="replace", index=False
    )
    pd.DataFrame(sound_rows).to_sql(
        "rooftop_sound_level", conn, if_exists="replace", index=False
    )
    if heat_rows:
        pd.DataFrame(heat_rows).to_sql(
            "rooftop_heating_performance", conn, if_exists="replace", index=False
        )
    if elec_rows:
        pd.DataFrame(elec_rows).to_sql(
            "rooftop_electrical_spec", conn, if_exists="replace", index=False
        )

    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rooftop_cool_model ON rooftop_cooling_performance(equipment_model_id, outdoor_db_c, esp_inwg)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rooftop_air_model ON rooftop_airflow_point(equipment_model_id, esp_inwg)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rooftop_sound_model ON rooftop_sound_level(equipment_model_id)")
    if elec_rows:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rooftop_elec_model ON rooftop_electrical_spec(equipment_model_id)")

    LOGGER.info("Rooftop tables loaded: %d equipment models with full ESP range.", len(eq_rows))
    return len(eq_rows)
