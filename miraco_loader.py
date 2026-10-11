"""
miraco_loader.py
=================
Parses Carrier_Miraco_HVAC_Selection_Database_v3.xlsx (50UB / 38BB / 40AB,
June 2013, R22) into its own set of miraco_* tables — one row per
(Cool/Heat -E or Cool Only -K) variant.

Tables produced (all prefixed 'miraco_'):
    miraco_equipment_model
    miraco_airflow_point
    miraco_cooling_performance
    miraco_heating_performance
    miraco_sound_level

Engineering assumptions
-----------------------
1. Split system only (38BB condensing + 40AB evaporator). The 50UB
   vertical-packaged variant is not loaded — the workbook has no
   standalone physical/dimension table for it.
2. Two rows per size in equipment_model: '-E' (Cool/Heat) and '-K'
   (Cool Only). Only '-E' variants get heating_performance rows.
3. cooling_performance is stored at the design point WB=19, DB=27:
     - Total capacity from the WB=19 base row (catalog publishes Total
       only at one DB per WB, per README §E).
     - Sensible capacity from the WB=19 / DB=27 row.
   Both are looked up across all condenser temperatures that appear in
   the workbook (25/30/35/40/43/46 °C).
4. Heating is electric resistance (Physical_Evaporator + System_Technical_
   Data) — one constant row per '-E' variant with outdoor_condition=
   '2/1°C' to match main.py's hardcoded query key.
5. No ESP is published anywhere. All esp_inwg = 0.0 and max_esp_inwg is
   left NULL so main.py can treat it as "no rating on record".
6. Indoor (evaporator) sound → miraco_sound_level. Outdoor (condenser)
   sound → miraco_equipment_model.outdoor_sound_dba.
7. Anomalous source cells (Carrier v3 README §C) are flagged via
   is_anomaly. Only anomaly cells whose (WB, DB) matches the loaded
   design point are actually representable in miraco_cooling_performance.
   For Carrier v3, none of the eight anomalies fall on WB=19/DB=27, so
   the flag stays 0 — but the code now explains *why*, and picks up
   anomalies automatically if the design point or the source table
   ever changes. A build-time log line reports the exact count.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

KW_TO_BTUH = 3412.14

# ─── Design point actually stored in miraco_cooling_performance ───────
# These must match the (indoor_wb_c, indoor_db_c) values used in the
# INSERT loop below. Kept as named constants so the anomaly-detection
# logic can never drift out of sync with the stored rows.
DESIGN_WB_C = 19
DESIGN_DB_C = 27


# From Carrier v3 README section C — 8 cells whose sensible capacity
# breaks the otherwise monotonic trend of the surrounding values.
# Tuple = (size_mbh, indoor_wb_c, indoor_db_c, outdoor_db_c).
ANOMALY_CELLS: set[tuple[int, int, int, int]] = {
    (110, 15, 24, 46),
    (110, 17, 21, 46),
    (110, 21, 28, 46),
    (145, 19, 28, 35),
    (145, 21, 28, 46),
    (250, 21, 25, 40),
    (300, 19, 25, 40),
    (300, 21, 25, 40),
}

# Subset of ANOMALY_CELLS whose (WB, DB) equals the loaded design point,
# so those cells are the only ones that can actually appear as rows in
# miraco_cooling_performance. Keyed by (size_mbh, outdoor_db_c) because
# WB and DB are fixed by the design point.
#
# For Carrier v3 this set is EMPTY — none of the eight known anomalies
# sit at WB=19/DB=27. The is_anomaly column will therefore be 0 for
# every row; that's correct and expected, not a bug. The build log
# confirms it explicitly (see end of build_miraco_tables()).
_ANOMALY_AT_DESIGN: set[tuple[int, int]] = {
    (size, cond)
    for (size, wb, db, cond) in ANOMALY_CELLS
    if wb == DESIGN_WB_C and db == DESIGN_DB_C
}


# ═══════════════════════════════════════════════════════════════
# Small helpers
# ═══════════════════════════════════════════════════════════════

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse whitespace (including embedded newlines) in headers."""
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def _looks_like_int(value) -> bool:
    """True for 55 / 55.0 / '55' but not 'Nominal Size (MBH)' or NaN."""
    try:
        return str(value).strip().replace(".0", "").isdigit()
    except (TypeError, ValueError):
        return False


def _size_from_header(header: str) -> int | None:
    """'38BB-55' → 55, 'Size 90' → 90, 'Parameter' → None."""
    text = str(header).strip()
    tail = text.split("-")[-1] if "-" in text else text.replace("Size", "").strip()
    return int(tail) if tail.isdigit() else None


def _read_wide_sheet(
    xlsx: Path, sheet: str
) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Read a wide 'Parameter | 38BB-55 | 38BB-90 | ...' sheet.
    Returns (df with Parameter as index, {column_name: size_mbh}).
    """
    df = pd.read_excel(xlsx, sheet_name=sheet, header=2)
    df = _clean(df)
    df = df.set_index(df.columns[0])

    sizes: dict[str, int] = {}
    for col in df.columns:
        size = _size_from_header(col)
        if size is not None:
            sizes[col] = size
    return df, sizes


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════

def build_miraco_tables(conn, xlsx_path) -> int:
    """
    Populate every miraco_* table in `conn`.
    Returns the number of equipment_model rows inserted.
    """
    xlsx = Path(xlsx_path)

    # ─── 1) Model_List ────────────────────────────────────────
    ml = pd.read_excel(xlsx, sheet_name="Model_List", header=2)
    ml = _clean(ml)
    ml = ml[ml.iloc[:, 0].apply(_looks_like_int)].copy()
    ml.iloc[:, 0] = ml.iloc[:, 0].astype(float).astype(int)
    ml = ml.iloc[:, :8]
    ml.columns = [
        "size_mbh", "vp_e", "vp_k",
        "cond_e", "cond_k", "evap_e", "evap_k", "catalog_mbh",
    ]

    # ─── 2) System_Technical_Data ─────────────────────────────
    std = pd.read_excel(xlsx, sheet_name="System_Technical_Data", header=2)
    std = _clean(std)
    std = std[std.iloc[:, 0].apply(_looks_like_int)].copy()
    std.iloc[:, 0] = std.iloc[:, 0].astype(float).astype(int)
    std.columns = [
        "size_mbh", "vp_model", "cond_model", "evap_model",
        "rated_cooling_btuh", "rated_cooling_kw", "refrigerant",
        "charge_kg", "circuits", "gas_line", "liquid_line", "heating_kw",
    ]
    std = std.set_index("size_mbh")

    # ─── 3) Physical sheets ──────────────────────────────────
    cond_df, cond_sizes = _read_wide_sheet(xlsx, "Physical_Condensing")
    evap_df, evap_sizes = _read_wide_sheet(xlsx, "Physical_Evaporator")

    # ─── 4) Electrical_Data ──────────────────────────────────
    elec = pd.read_excel(xlsx, sheet_name="Electrical_Data", header=2)
    elec = _clean(elec)
    elec = elec.set_index(elec.columns[0])
    elec_sizes: dict[str, int] = {}
    for col in elec.columns:
        size = _size_from_header(col)
        if size is not None:
            elec_sizes[col] = size

    # ─── 5) Cooling_Capacity_Data ────────────────────────────
    cc = pd.read_excel(xlsx, sheet_name="Cooling_Capacity_Data", header=3)
    cc = _clean(cc)
    cc = cc[cc["Unit Size"].apply(_looks_like_int)].copy()
    cc["Unit Size"] = cc["Unit Size"].astype(float).astype(int)

    wb_col = "Evap. Entering WB (°C)"
    db_col = "Evap. Entering DB (°C)"
    cond_col = "Condenser Entering Air Temp (°C)"

    # Total capacity: WB=19 base rows (catalog publishes Total at one DB
    # per WB only — see README §E).
    total_rows = cc[(cc["Row Type"] == "Total+Input") & (cc[wb_col] == DESIGN_WB_C)]
    # Sensible capacity: the exact design point WB=19 / DB=27.
    sensible_rows = cc[
        (cc["Row Type"] == "Sensible")
        & (cc[wb_col] == DESIGN_WB_C)
        & (cc[db_col] == DESIGN_DB_C)
    ]

    # (size, cond_temp) → (total_btuh, input_kW, nominal_cfm)
    total_map: dict[tuple[int, int], tuple] = {}
    for _, r in total_rows.iterrows():
        key = (int(r["Unit Size"]), int(r[cond_col]))
        total_map[key] = (
            r["Total Capacity (Btu/hr)"],
            r["Total Input Power (kW)"],
            r["Evap. Air Flow (cfm, nominal)"],
        )

    # (size, cond_temp) → sensible_btuh
    sensible_map: dict[tuple[int, int], float] = {}
    for _, r in sensible_rows.iterrows():
        key = (int(r["Unit Size"]), int(r[cond_col]))
        sensible_map[key] = r["Sensible Capacity (Btu/hr)"]

    # Nominal cfm per size (constant in the workbook)
    nominal_cfm: dict[int, float] = {}
    for (size, _), values in total_map.items():
        nominal_cfm.setdefault(size, values[2])

    cond_temps = sorted({k[1] for k in total_map})
    sizes = sorted(ml["size_mbh"].unique())

    # ─── 6) Build all rows ───────────────────────────────────
    eq_rows: list[dict] = []
    air_rows: list[dict] = []
    elec_rows: list[dict] = []
    cool_rows: list[dict] = []
    heat_rows: list[dict] = []
    sound_rows: list[dict] = []

    next_id = 0
    for size in sizes:
        model_row = ml[ml["size_mbh"] == size].iloc[0]
        tech = std.loc[size]

        cond_col = next((c for c, s in cond_sizes.items() if s == size), None)
        evap_col = next((c for c, s in evap_sizes.items() if s == size), None)
        elec_col = next((c for c, s in elec_sizes.items() if s == size), None)

        if cond_col is None:
            raise ValueError(
                f"Size {size} MBH appears in Model_List but has no column "
                f"in Physical_Condensing. Available: {sorted(cond_sizes.values())}"
            )
        if evap_col is None:
            raise ValueError(
                f"Size {size} MBH appears in Model_List but has no column "
                f"in Physical_Evaporator. Available: {sorted(evap_sizes.values())}"
            )
        if elec_col is None:
            raise ValueError(
                f"Size {size} MBH appears in Model_List but has no column "
                f"in Electrical_Data. Available: {sorted(elec_sizes.values())}"
            )

        cond_phys = cond_df[cond_col]
        evap_phys = evap_df[evap_col]
        elec_data = elec[elec_col]

        rated_cooling_btuh = float(tech["rated_cooling_btuh"])
        cool_input_kw = float(elec_data["Cooling Consumption (kW)"])
        heat_output_kw = float(tech["heating_kw"])
        heat_input_kw = float(elec_data["Heating Consumption (kW)"])

        for variant in ("E", "K"):
            cond_model = str(model_row[f"cond_{variant.lower()}"]).strip()
            evap_model = str(model_row[f"evap_{variant.lower()}"]).strip()
            system_model = cond_model.replace(" ", "-")

            mid = next_id
            next_id += 1
            is_heat = variant == "E"

            rated_heat_btuh = heat_output_kw * KW_TO_BTUH if is_heat else 0.0
            cop = (
                heat_output_kw / heat_input_kw
                if (is_heat and heat_input_kw) else None
            )

            eq_rows.append({
                "id": mid,
                "system_model": system_model,
                "nominal_class_kbtu": int(size),
                "indoor_model": evap_model.replace(" ", "-"),
                "outdoor_model": cond_model.replace(" ", "-"),
                "rated_cooling_btuh": rated_cooling_btuh,
                "rated_cooling_input_w": cool_input_kw * 1000.0,
                "rated_heating_btuh": rated_heat_btuh,
                "eer_btu_wh": rated_cooling_btuh / (cool_input_kw * 1000.0),
                "cop_ww": cop,
                "max_esp_inwg": None,
                "indoor_weight_kg": float(evap_phys["Net Weight (kg)"]),
                "indoor_dimensions_mm": (
                    f"{int(evap_phys['Width (mm)'])}x"
                    f"{int(evap_phys['Height (mm)'])}x"
                    f"{int(evap_phys['Depth (mm)'])}"
                ),
                "power_supply": "380-400/3/50",
                "compressor_type": cond_phys["Compressor Type"],
                "refrigerant_type": tech["refrigerant"],
                "outdoor_sound_dba": float(cond_phys["Sound Level (dB(A))"]),
                "outdoor_dimensions_mm": (
                    f"{int(cond_phys['Width (mm)'])}x"
                    f"{int(cond_phys['Height (mm)'])}x"
                    f"{int(cond_phys['Depth (mm)'])}"
                ),
                "outdoor_weight_kg": float(cond_phys["Net Weight (kg)"]),
                "pipe_liquid_inch": tech["liquid_line"],
                "pipe_gas_inch": tech["gas_line"],
                "max_pipe_length_m": None,
                "max_height_diff_m": None,
                "drain_hose_mm": None,
                "nominal_airflow_cfm": None,
                "nominal_airflow_m3h": None,
                "indoor_sound_dba": float(evap_phys["Sound Level (dB(A))"]),
                "heating_input_w": (heat_input_kw * 1000.0) if is_heat else None,
                "heating_current_a": None,
            })

            elec_rows.append({
                "equipment_model_id": mid,
                "cooling_amps": float(elec_data["Cooling Consumption (Amps)"]),
                "heating_amps": float(elec_data["Heating Consumption (Amps)"]),
                "starting_amps": float(elec_data["Starting Current (Amps)"]),
                "mains_fuse_a": float(elec_data["Mains Fuse Rating (Amps)"]),
                "mains_switch_a": float(elec_data["Mains Power Switch (Amps)"]),
                "wiring_mm2": float(elec_data["Wiring Section (mm2)"]),
                "min_volts": float(elec_data["Minimum Volts"]),
                "max_volts": float(elec_data["Maximum Volts"]),
            })

            cfm = nominal_cfm.get(int(size))
            m3h = float(evap_phys["Nominal Air Flow (m3/hr)"])

            air_rows.append({
                "equipment_model_id": mid,
                "fan_speed": "High",
                "esp_inwg": 0.0,
                "esp_pa": 0.0,
                "airflow_cfm": cfm,
                "airflow_m3h": m3h,
            })

            sound_rows.append({
                "equipment_model_id": mid,
                "fan_speed": "High",
                "esp_inwg": 0.0,
                "esp_pa": 0.0,
                "sound_dba": float(evap_phys["Sound Level (dB(A))"]),
            })

            for ct in cond_temps:
                t = total_map.get((int(size), ct))
                if t is None:
                    continue
                sens = sensible_map.get((int(size), ct))

                # is_anomaly is True only when the source cell carrying the
                # sensible value at exactly this (WB, DB, cond) point is
                # in the known-anomaly list. For Carrier v3 this is always
                # False because _ANOMALY_AT_DESIGN is empty — see the log
                # line emitted at the end of this function.
                is_anomaly = int((int(size), ct) in _ANOMALY_AT_DESIGN)

                cool_rows.append({
                    "equipment_model_id": mid,
                    "esp_inwg": 0.0,
                    "fan_speed": "High",
                    "airflow_cfm": t[2],
                    "indoor_db_c": DESIGN_DB_C,
                    "indoor_wb_c": DESIGN_WB_C,
                    "outdoor_db_c": ct,
                    "total_capacity_btuh": t[0],
                    "sensible_capacity_btuh": sens,
                    "input_w": t[1] * 1000.0,
                    "is_anomaly": is_anomaly,
                })

            if is_heat:
                heat_rows.append({
                    "equipment_model_id": mid,
                    "esp_inwg": 0.0,
                    "fan_speed": "High",
                    "airflow_cfm": cfm,
                    "indoor_db_c": None,
                    "outdoor_condition": "2/1°C",
                    "total_capacity_btuh": rated_heat_btuh,
                    "input_w": heat_input_kw * 1000.0,
                })

    # ─── 7) Write tables ─────────────────────────────────────
    pd.DataFrame(eq_rows).set_index("id").to_sql(
        "miraco_equipment_model", conn, if_exists="replace", index=True,
    )
    pd.DataFrame(elec_rows).to_sql(
        "miraco_electrical_spec", conn, if_exists="replace", index=False,
    )
    pd.DataFrame(air_rows).to_sql(
        "miraco_airflow_point", conn, if_exists="replace", index=False,
    )
    pd.DataFrame(cool_rows).to_sql(
        "miraco_cooling_performance", conn, if_exists="replace", index=False,
    )
    pd.DataFrame(heat_rows).to_sql(
        "miraco_heating_performance", conn, if_exists="replace", index=False,
    )
    pd.DataFrame(sound_rows).to_sql(
        "miraco_sound_level", conn, if_exists="replace", index=False,
    )

    # ─── 8) Indexes ──────────────────────────────────────────
    cur = conn.cursor()
    cur.execute(
        "CREATE INDEX idx_miraco_elec_model "
        "ON miraco_electrical_spec(equipment_model_id)"
    )
    cur.execute(
        "CREATE INDEX idx_miraco_air_model "
        "ON miraco_airflow_point(equipment_model_id)"
    )
    cur.execute(
        "CREATE INDEX idx_miraco_cool_model "
        "ON miraco_cooling_performance(equipment_model_id)"
    )
    cur.execute(
        "CREATE INDEX idx_miraco_heat_model "
        "ON miraco_heating_performance(equipment_model_id)"
    )
    cur.execute(
        "CREATE INDEX idx_miraco_sound_model "
        "ON miraco_sound_level(equipment_model_id)"
    )

    # ─── 9) Build-time anomaly report ────────────────────────
    flagged = sum(row["is_anomaly"] for row in cool_rows)
    if _ANOMALY_AT_DESIGN:
        print(
            f"[miraco_loader] {flagged} cooling row(s) flagged anomalous "
            f"at design point (WB={DESIGN_WB_C}, DB={DESIGN_DB_C}). "
            f"main.py will exclude them from selection."
        )
    else:
        print(
            f"[miraco_loader] No anomaly cells at design point "
            f"(WB={DESIGN_WB_C}, DB={DESIGN_DB_C}). "
            f"is_anomaly = 0 for all {len(cool_rows)} rows — expected, "
            f"not a bug. See README §C for the full 8-cell anomaly list."
        )

    return len(eq_rows)
