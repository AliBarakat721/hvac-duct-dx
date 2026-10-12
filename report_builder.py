"""
report_builder.py
=================
Builds a comprehensive Word report for ONE selected equipment model.

Supports both catalogs in the database:
  • ''        — the original QDMT Duct Split tables (equipment_model,
                cooling_performance, heating_performance, sound_level)
  • 'miraco_' — the Carrier 50UB/38BB/40AB tables (miraco_equipment_model,
                miraco_cooling_performance, ...)

If the requested prefix doesn't contain the model, the function automatically
retries with the other prefix, so callers that don't know which catalog a
model belongs to still work.
"""
import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PATH = Path(__file__).with_name('hvac_equipment.sqlite')


QDMT_KEY_FEATURES = [
    "Medium Static Pressure (up to 100 Pa) — suitable for ducted applications",
    "Tropical compressor — reliable operation up to 52°C outdoor ambient",
    "Slim design — minimal ceiling void (from 210 mm indoor unit height)",
    "Efficient anti-dust washable aluminum air filters for clean IAQ",
    "Smart LED display panel with error-code self-diagnostics",
    "Auto restart with backup memory after power failure",
    "3-minute safety time delay for compressor protection",
    "Anti-freezing protection of indoor coil",
    "Refrigerant leak detection sensors (indoor + outdoor)",
    "ECO function and Independent Dehumidification mode",
    "Programmable timer (on/off/on-off) with auto fan speed",
    "Optional drain pump lifting condensate up to 750 mm",
    "Optional fresh-air knockout panel for ventilation",
    "Compatible with wired controller and wireless remote",
]

ROOFTOP_KEY_FEATURES = [
    "Commercial single-package rooftop cooling with electric heat option (400-3-50 Hz)",
    "Dual independent refrigeration circuits with scroll / reciprocating compressors",
    "High-ambient capability: standard operation up to 52°C (125°F) outdoor ambient",
    "Belt-driven evaporator centrifugal fans with adjustable variable-pitch pulleys",
    "Sloped corrosion-resistant condensate drain pan meeting ASHRAE 62 guidelines",
    "Galvanized steel cabinet with baked enamel powder paint (500-hour salt spray ASTM B117)",
    "Commercial 51 mm (2-inch) air filters with tool-less access panels",
    "Single-point electrical entry and thru-the-curb power/control routing",
    "Optional integrated economizer with barometric relief or power exhaust",
]

MIRACO_KEY_FEATURES = [
    "Heavy-duty R22 hermetic reciprocating compressor (sizes 55-145) or scroll compressor (sizes 190-300)",
    "Two independent refrigerant circuits from size 110 upward - partial-load redundancy",
    "Thermostatic expansion valve with external equalizer on every circuit",
    "Air-cooled split (38BB + 40AB) or vertical packaged (50UB) configurations",
    "Electric-resistance heating with finned aluminum elements and thermal protection (Cool/Heat '-E' variants only)",
    "Complies with CE safety regulations - LVD, EMC, PED",
    "Coils leak-tested to 450 PSI / 30 bar test pressure",
    "Long-life cleanable aluminum air filters (polyurethane on size 300)",
    "Dual high/low pressure control and internal pressure-relief valve",
    "Phase-reversal / phase-loss voltage protector",
    "4-minute anti-short-cycle delay and 1-minute inter-compressor start delay",
    "Optional crankcase heater and pre-coated anti-corrosion condenser fins",
]



# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _get_model_details(conn, system_model, prefix=''):
    """Return the parent-row dict for a system_model, or None."""
    table = f'{prefix}equipment_model'
    row = conn.execute(f"""
        SELECT id, system_model, nominal_class_kbtu, indoor_model, outdoor_model,
               rated_cooling_btuh, rated_cooling_input_w, rated_heating_btuh,
               eer_btu_wh, cop_ww, max_esp_inwg, indoor_weight_kg,
               indoor_dimensions_mm, power_supply, compressor_type,
               refrigerant_type, outdoor_sound_dba, outdoor_dimensions_mm,
               outdoor_weight_kg, pipe_liquid_inch, pipe_gas_inch,
               max_pipe_length_m, max_height_diff_m, drain_hose_mm,
               nominal_airflow_cfm, indoor_sound_dba
        FROM {table}
        WHERE system_model = ?
    """, (system_model,)).fetchone()
    if not row:
        return None
    cols = ['id', 'system_model', 'nominal_class_kbtu', 'indoor_model',
            'outdoor_model', 'rated_cooling_btuh', 'rated_cooling_input_w',
            'rated_heating_btuh', 'eer_btu_wh', 'cop_ww', 'max_esp_inwg',
            'indoor_weight_kg', 'indoor_dimensions_mm', 'power_supply',
            'compressor_type', 'refrigerant_type', 'outdoor_sound_dba',
            'outdoor_dimensions_mm', 'outdoor_weight_kg', 'pipe_liquid_inch',
            'pipe_gas_inch', 'max_pipe_length_m', 'max_height_diff_m',
            'drain_hose_mm', 'nominal_airflow_cfm', 'indoor_sound_dba']
    return dict(zip(cols, row, strict=True))


def _resolve_fan(conn, table, mid, requested):
    """Return a fan_speed that actually exists for this model.

    Falls back to 'High' when the requested speed isn't published (this is
    common for the Carrier Miraco catalog, which only tabulates High).
    """
    requested = requested or 'High'
    row = conn.execute(
        f'SELECT 1 FROM {table} '
        f'WHERE equipment_model_id=? AND fan_speed=? LIMIT 1',
        (mid, requested)).fetchone()
    if row:
        return requested
    row = conn.execute(
        f"SELECT 1 FROM {table} "
        f"WHERE equipment_model_id=? AND fan_speed='High' LIMIT 1",
        (mid,)).fetchone()
    return 'High' if row else requested


def _get_performance(conn, mid, esp, fan, outdoor_db,
                     indoor_db=27, indoor_wb=19, prefix=''):
    """Return the cooling-performance row closest to the design point."""
    table = f'{prefix}cooling_performance'
    fan = _resolve_fan(conn, table, mid, fan)

    # Same conservative policy as the selection search (main.py):
    # prefer the first catalog point AT OR ABOVE the design outdoor
    # temperature / ESP, falling back to the nearest point below only when
    # nothing is above. (The old exact-match on outdoor_db fell through to
    # an arbitrary row whenever the design temperature was off-grid.)
    EPS = 1e-9
    cols_sql = ("esp_inwg, fan_speed, airflow_cfm, indoor_db_c, indoor_wb_c, "
                "outdoor_db_c, total_capacity_btuh, sensible_capacity_btuh, "
                "input_w")

    row = conn.execute(f"""
        SELECT {cols_sql}
        FROM {table}
        WHERE equipment_model_id=? AND fan_speed=?
              AND indoor_db_c=? AND indoor_wb_c=?
        ORDER BY (outdoor_db_c < ?), ABS(outdoor_db_c - ?),
                 (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
    """, (mid, fan, indoor_db, indoor_wb,
          outdoor_db - EPS, outdoor_db, esp - EPS, esp)).fetchone()

    # Widen: keep WB fixed, allow indoor DB to drift (some catalogs only
    # tabulate one DB per WB for Total capacity).
    if not row:
        row = conn.execute(f"""
            SELECT {cols_sql}
            FROM {table}
            WHERE equipment_model_id=? AND fan_speed=?
                  AND indoor_wb_c=?
            ORDER BY (outdoor_db_c < ?), ABS(outdoor_db_c - ?),
                     ABS(indoor_db_c - ?),
                     (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
        """, (mid, fan, indoor_wb, outdoor_db - EPS, outdoor_db,
              indoor_db, esp - EPS, esp)).fetchone()

    # Last resort: whatever row exists for this (model, fan).
    if not row:
        row = conn.execute(f"""
            SELECT {cols_sql}
            FROM {table}
            WHERE equipment_model_id=? AND fan_speed=?
            ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
        """, (mid, fan, esp - EPS, esp)).fetchone()

    if not row:
        return None
    cols = ['esp_inwg', 'fan_speed', 'airflow_cfm', 'indoor_db_c',
            'indoor_wb_c', 'outdoor_db_c', 'total_capacity_btuh',
            'sensible_capacity_btuh', 'input_w']
    return dict(zip(cols, row, strict=True))


def _get_sound(conn, mid, fan, esp, prefix=''):
    """Return the closest sound-level row."""
    table = f'{prefix}sound_level'
    fan = _resolve_fan(conn, table, mid, fan)
    row = conn.execute(f"""
        SELECT fan_speed, esp_inwg, sound_dba
        FROM {table}
        WHERE equipment_model_id=? AND fan_speed=?
        ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
    """, (mid, fan, esp - 1e-9, esp)).fetchone()
    if not row:
        return None
    return {'fan_speed': row[0], 'esp_inwg': row[1], 'sound_dba': row[2]}


def _get_heating(conn, mid, fan, esp, prefix=''):
    """Return the heating row.

    QDMT publishes heating at three outdoor conditions (2/1, 7/6, 24/18 °C);
    we report the worst case (2/1 °C) so the summary is conservative.
    Miraco publishes one row keyed '2/1°C' as well (electric resistance heat
    — capacity is T-independent). '-K' (Cool Only) variants have no heating
    rows and return None immediately.
    """
    table = f'{prefix}heating_performance'

    # Quick existence check — avoids a wasteful query for Cool-Only variants.
    count = conn.execute(
        f'SELECT COUNT(*) FROM {table} WHERE equipment_model_id=?', (mid,)
    ).fetchone()[0]
    if not count:
        return None

    fan = _resolve_fan(conn, table, mid, fan)

    # Preferred: worst-case outdoor_condition (2/1 °C).
    row = conn.execute(f"""
        SELECT esp_inwg, fan_speed, airflow_cfm, outdoor_condition,
               total_capacity_btuh, input_w
        FROM {table}
        WHERE equipment_model_id=? AND fan_speed=?
              AND outdoor_condition='2/1°C'
        ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
    """, (mid, fan, esp - 1e-9, esp)).fetchone()

    # Fallback: any condition row (some future catalog may not use 2/1°C).
    if not row:
        row = conn.execute(f"""
            SELECT esp_inwg, fan_speed, airflow_cfm, outdoor_condition,
                   total_capacity_btuh, input_w
            FROM {table}
            WHERE equipment_model_id=? AND fan_speed=?
            ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
        """, (mid, fan, esp - 1e-9, esp)).fetchone()

    if not row:
        return None
    cols = ['esp_inwg', 'fan_speed', 'airflow_cfm', 'outdoor_condition',
            'total_capacity_btuh', 'input_w']
    return dict(zip(cols, row, strict=True))


def _kv_table(doc, items, style="Light Grid Accent 1"):
    """Add a two-column key/value table.

    Each row is marked w:cantSplit so Word never breaks a label/value pair
    across two pages — this keeps short tables (e.g. Design Conditions)
    visually intact.
    """
    from docx.oxml import OxmlElement
    from docx.shared import Pt

    t = doc.add_table(rows=0, cols=2)
    t.style = style
    for label, value in items:
        row = t.add_row()
        # Prevent the row from being split across pages.
        trPr = row._tr.get_or_add_trPr()
        cant_split = OxmlElement('w:cantSplit')
        trPr.append(cant_split)

        r = row.cells
        r[0].text = str(label)
        r[1].text = str(value)
        for p in r[0].paragraphs:
            for rn in p.runs:
                rn.bold = True
                rn.font.size = Pt(10)
        for p in r[1].paragraphs:
            for rn in p.runs:
                rn.font.size = Pt(10)
    return t


def _airflow_reconciliation_label(conditions):
    from airflow_utils import reconcile_airflow

    result = reconcile_airflow(conditions)
    difference = result["difference_cfm"]
    if difference is None:
        return "Unverified; no automatic reconciliation"
    if difference < -0.5:
        amount = f"{abs(difference):,.0f} CFM shortfall"
    elif difference > 0.5:
        amount = f"{difference:,.0f} CFM excess / unallocated"
    else:
        amount = "Numerically matched"
    return f"{amount}; not automatically reconciled ({result['status']})"


# ═══════════════════════════════════════════════════════════════
#  Cart reuse helpers  —  load once, render many
# ═══════════════════════════════════════════════════════════════

def load_model_data(db_path, system_model, design_conditions, table_prefix=''):
    """Fetch every value needed to render a system block for one model.

    Returns a dict with keys:
        model        — parent-row dict
        perf         — cooling performance row (or None)
        sound        — sound-level row (or None)
        heat         — heating row (or None)
        nominal_cfm  — string/number (or None)
        table_prefix — resolved prefix ('', 'miraco_')
        is_miraco    — True when table_prefix == 'miraco_'

    Returns None if the model isn't found in either catalog.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        model = _get_model_details(conn, system_model, table_prefix)
        if not model:
            for alt in ('', 'miraco_', 'rooftop_'):
                if alt == table_prefix:
                    continue
                model = _get_model_details(conn, system_model, alt)
                if model:
                    table_prefix = alt
                    break
        if not model:
            return None

        esp = design_conditions.get('esp', 0.20)
        fan = design_conditions.get('fan_speed', 'High')
        outdoor = design_conditions.get('outdoor_db', 43)

        perf = _get_performance(conn, model['id'], esp, fan, outdoor,
                                prefix=table_prefix)
        sound = _get_sound(conn, model['id'], fan, esp, prefix=table_prefix)
        heat = _get_heating(conn, model['id'], fan, esp, prefix=table_prefix)

        nominal_cfm = model['nominal_airflow_cfm']
        if not nominal_cfm:
            air_row = conn.execute(
                f'SELECT airflow_cfm FROM {table_prefix}airflow_point '
                f'WHERE equipment_model_id=? ORDER BY esp_inwg LIMIT 1',
                (model['id'],)).fetchone()
            if air_row and air_row[0] is not None:
                val = air_row[0]
                try:
                    nominal_cfm = f"{float(val):.0f}"
                except (TypeError, ValueError):
                    nominal_cfm = str(val)

        return {
            'model': model,
            'perf': perf,
            'sound': sound,
            'heat': heat,
            'nominal_cfm': nominal_cfm,
            'table_prefix': table_prefix,
            'is_miraco': table_prefix == 'miraco_',
        }
    finally:
        conn.close()


def render_system_block(doc, system_name, data, design_conditions,
                        heading_level=1, *, hours_per_year=2000,
                        tariff=2.0, currency='EGP'):
    """Add a complete report block for one system to an existing doc.

    heading_level is used for the system title; every subsection is one
    level deeper. Reuses the same helpers as generate_detailed_report()
    so the two report types stay visually identical.
    """
    from docx.shared import Pt, RGBColor

    table_prefix = data.get('table_prefix', '')
    model = data['model']
    perf = data['perf']
    sound = data['sound']
    heat = data['heat']
    nominal_cfm = data['nominal_cfm']
    is_miraco = data['is_miraco']
    dc = design_conditions
    sub = heading_level + 1

    # ── System heading ────────────────────────────────────────
    h = doc.add_heading(f"{system_name} — {model['system_model']}",
                        level=heading_level)
    h.paragraph_format.keep_with_next = True

    # ── 1. Design Conditions ──────────────────────────────────
    _h = doc.add_heading("Design Conditions", level=sub)
    _h.paragraph_format.keep_with_next = True

    items = [
        ("Indoor Conditions", "27 °C DB / 19 °C WB"),
        ("Outdoor Ambient", f"{dc.get('outdoor_db', 43)} °C DB"),
        ("Required Total Cooling Load", f"{dc.get('total_btu', 0):,.0f} Btu/hr"),
        ("Required Sensible Cooling Load",
         f"{dc.get('sensible_btu', 0):,.0f} Btu/hr"),
    ]
    if dc.get('heating_btu', 0) > 0:
        items.append(("Required Heating Load",
                      f"{dc['heating_btu']:,.0f} Btu/hr"))
    required_cfm = dc.get("required_cfm", dc.get("cfm", 0))
    design_cfm = dc.get("design_cfm", required_cfm)
    available_cfm = dc.get("available_unit_cfm")
    available_label = (
        f"{available_cfm:,.0f} CFM (catalog point; unverified delivery)"
        if available_cfm is not None else "Unknown / unverified")
    airflow_esp = dc.get("airflow_esp_inwg")
    items.extend([
        ("Required Airflow", f"{required_cfm:,.0f} CFM"),
        ("Diffuser Design Airflow", f"{design_cfm:,.0f} CFM"),
        ("Selected Unit Catalog-Point Airflow", available_label),
        ("Selected Airflow-Point ESP",
         f"{airflow_esp:.3f} in.wg" if airflow_esp is not None
         else "Unknown"),
        ("Airflow Data Source",
         dc.get("airflow_source", "not recorded")),
        ("Airflow Provenance",
         dc.get("airflow_provenance", "unknown_unverified")),
        ("Airflow Reconciliation", _airflow_reconciliation_label(dc)),
        ("External Static Pressure (ESP)", f"{dc.get('esp', 0.20):.3f} in.wg"),
        ("Fan Speed", dc.get('fan_speed', 'High')),
    ])
    _kv_table(doc, items)
    doc.add_paragraph()

    # ── 2. Selected Equipment ─────────────────────────────────
    _h = doc.add_heading("Selected Equipment", level=sub)
    _h.paragraph_format.keep_with_next = True
    p = doc.add_paragraph()
    run = p.add_run(f"Model:  {model['system_model']}")
    run.bold = True
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x05, 0x96, 0x69)

    _kv_table(doc, [
        ("Indoor Unit Model", model['indoor_model'] or "—"),
        ("Outdoor Unit Model", model['outdoor_model'] or "—"),
        ("Power Supply", model['power_supply'] or "—"),
        ("Compressor Type", model['compressor_type'] or "—"),
        ("Refrigerant Type", model['refrigerant_type'] or "—"),
        ("Rated Cooling Capacity",
         f"{model['rated_cooling_btuh']:,.0f} Btu/hr"
         if model['rated_cooling_btuh'] else "—"),
        ("Rated Heating Capacity",
         f"{model['rated_heating_btuh']:,.0f} Btu/hr"
         if model['rated_heating_btuh'] else "—"),
        ("EER (Cooling)",
         f"{model['eer_btu_wh']:.2f} Btu/Wh" if model['eer_btu_wh'] else "—"),
        ("COP (Heating)",
         f"{model['cop_ww']:.2f} W/W" if model['cop_ww'] else "—"),
        ("Maximum ESP",
         f"{model['max_esp_inwg']:.2f} in.wg"
         if model['max_esp_inwg'] else "—"),
    ])
    doc.add_paragraph()

    # ── 3. Performance ────────────────────────────────────────
    _h = doc.add_heading("Performance at Design Conditions", level=sub)
    _h.paragraph_format.keep_with_next = True
    if perf:
        perf_items = [
            ("Total Cooling Capacity",
             f"{perf['total_capacity_btuh']:,.0f} Btu/hr"),
            ("Sensible Cooling Capacity",
             f"{perf['sensible_capacity_btuh']:,.0f} Btu/hr"
             if perf['sensible_capacity_btuh'] else "—"),
            ("Cooling Input Power",
             f"{perf['input_w']:,.0f} W" if perf['input_w'] else "—"),
            ("Catalog Airflow Point at Design ESP (delivery unverified)",
             f"{perf['airflow_cfm']:,.0f} CFM"
             if perf['airflow_cfm'] else "—"),
            ("Actual Operating ESP", f"{perf['esp_inwg']:.3f} in.wg"),
            ("Fan Speed", perf['fan_speed']),
        ]
    else:
        perf_items = [("Performance Data",
                       "Not available for these conditions")]
    if heat:
        perf_items.append(("Heating Capacity (2/1 °C outdoor)",
                           f"{heat['total_capacity_btuh']:,.0f} Btu/hr"))
        if heat['input_w']:
            perf_items.append(("Heating Input Power",
                               f"{heat['input_w']:,.0f} W"))
    if sound:
        perf_items.append(("Sound Pressure Level",
                           f"{sound['sound_dba']:.1f} dB(A)"))
    _kv_table(doc, perf_items)
    doc.add_paragraph()

    # ── 3.5 Energy Analysis ───────────────────────────────────
    eer_val = model.get('eer_btu_wh')
    cap_btuh = (perf or {}).get('total_capacity_btuh') or \
               (model.get('rated_cooling_btuh') or 0)
    if eer_val and cap_btuh:
        kw_val = cap_btuh / float(eer_val) / 1000.0
        annual = kw_val * float(hours_per_year) * float(tariff)
        _h = doc.add_heading("Energy Analysis", level=sub)
        _h.paragraph_format.keep_with_next = True
        _kv_table(doc, [
            ("Rated Cooling Capacity", f"{cap_btuh:,.0f} Btu/hr"),
            ("EER (Btu/Wh)", f"{float(eer_val):.2f}"),
            ("Power Input (cooling)", f"{kw_val:.2f} kW"),
            ("Annual Operating Hours", f"{int(hours_per_year):,} h/year"),
            ("Electricity Tariff", f"{float(tariff):.3f} {currency}/kWh"),
            ("Estimated Annual Cost", f"{annual:,.0f} {currency}/year"),
        ])
        doc.add_paragraph()

    # ── 4. Physical & Installation ────────────────────────────
    _h = doc.add_heading("Physical & Installation Data", level=sub)
    _h.paragraph_format.keep_with_next = True
    _kv_table(doc, [
        ("Indoor Unit Dimensions (W×H×D)",
         model['indoor_dimensions_mm'] or "—"),
        ("Indoor Unit Weight",
         f"{model['indoor_weight_kg']:.0f} kg"
         if model['indoor_weight_kg'] else "—"),
        ("Outdoor Unit Dimensions (W×H×D)",
         model['outdoor_dimensions_mm'] or "—"),
        ("Outdoor Unit Weight",
         f"{model['outdoor_weight_kg']:.0f} kg"
         if model['outdoor_weight_kg'] else "—"),
        ("Outdoor Sound Pressure",
         f"{model['outdoor_sound_dba']:.0f} dB(A)"
         if model['outdoor_sound_dba'] else "—"),
        ("Maximum Pipe Length",
         f"{model['max_pipe_length_m']:.0f} m"
         if model['max_pipe_length_m'] else "—"),
        ("Maximum Height Difference",
         f"{model['max_height_diff_m']:.0f} m"
         if model['max_height_diff_m'] else "—"),
        ("Condensate Drain Hose",
         f"{model['drain_hose_mm']:.0f} mm"
         if model['drain_hose_mm'] else "—"),
        ("Nominal Airflow", nominal_cfm or "—"),
        ("Indoor Sound Level", model['indoor_sound_dba'] or "—"),
    ])
    doc.add_paragraph()

    _h = doc.add_heading("Electrical & Site Specifications", level=sub)
    _h.paragraph_format.keep_with_next = True
    _kv_table(doc, report_rows(
        _DB_PATH, 'miraco_' if is_miraco else '', model['system_model']))
    doc.add_paragraph()

    # ── 5. Key Features ───────────────────────────────────────
    _h = doc.add_heading("Key Features", level=sub)
    _h.paragraph_format.keep_with_next = True
    feature_list = (
        MIRACO_KEY_FEATURES if is_miraco
        else ROOFTOP_KEY_FEATURES if table_prefix == 'rooftop_'
        else QDMT_KEY_FEATURES
    )
    for feature in feature_list:
        p = doc.add_paragraph(feature, style='List Bullet')
        for run in p.runs:
            run.font.size = Pt(10)
    doc.add_paragraph()

    # ── 6. Notes ──────────────────────────────────────────────
    _h = doc.add_heading("Notes & Remarks", level=sub)
    _h.paragraph_format.keep_with_next = True
    notes = [
        "All capacities are based on EN 14511 / ISO 5151 standards.",
        "Cooling capacities are valid for indoor conditions 27 °C DB / 19 °C WB.",
    ]
    if is_miraco:
        notes.extend([
            ("This Carrier Miraco 50UB / 38BB / 40AB unit does not publish an "
            "external static pressure rating in the source catalog; it is "
            "specified at a fixed nominal ESP."),
            ("Heating is electric-resistance (mounted in the evaporator unit) "
            "— its output does not vary with outdoor temperature."),
            ("Capacities are taken from the June 2013 Carrier Miraco "
            "technical manual (R22 refrigerant)."),
        ])
    else:
        notes.append(
            "The unit is capable of operating at outdoor temperatures up to 52 °C."
        )
    notes.extend([
        "Verify electrical supply availability at site.",
        "Confirm ceiling void height is sufficient for the indoor unit.",
        "Refer to the installation manual for detailed piping and wiring.",
    ])
    for note in notes:
        p = doc.add_paragraph(note, style='List Number')
        for run in p.runs:
            run.font.size = Pt(10)


# ═══════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════

def generate_detailed_report(db_path, system_model, design_conditions,
                             output_path,
                             project_name="HVAC Project",
                             prepared_by="Engineering Dept.",
                             table_prefix=''):
    """Generate a rich Word report for ONE selected model.

    table_prefix selects the catalog table set:
        ''        → QDMT (equipment_model, cooling_performance, ...)
        'miraco_' → Carrier Miraco (miraco_equipment_model, ...)

    If the model isn't found under the requested prefix, the other prefix is
    tried automatically so older callers keep working.

    Returns ``(success: bool, message: str)``.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    conn = sqlite3.connect(str(db_path))
    try:
        model = _get_model_details(conn, system_model, table_prefix)
        if not model:
            for alt in ('', 'miraco_', 'rooftop_'):
                if alt == table_prefix:
                    continue
                model = _get_model_details(conn, system_model, alt)
                if model:
                    table_prefix = alt
                    break
        if not model:
            return False, f"Model {system_model} not found in database"

        esp = design_conditions.get('esp', 0.20)
        fan = design_conditions.get('fan_speed', 'High')
        outdoor = design_conditions.get('outdoor_db', 43)

        perf = _get_performance(conn, model['id'], esp, fan, outdoor,
                                prefix=table_prefix)
        sound = _get_sound(conn, model['id'], fan, esp, prefix=table_prefix)
        heat = _get_heating(conn, model['id'], fan, esp, prefix=table_prefix)

        # Miraco leaves nominal_airflow_cfm NULL in the parent row — pull it
        # from the airflow_point child table instead.
        nominal_cfm = model['nominal_airflow_cfm']
        if not nominal_cfm:
            air_row = conn.execute(
                f'SELECT airflow_cfm FROM {table_prefix}airflow_point '
                f'WHERE equipment_model_id=? ORDER BY esp_inwg LIMIT 1',
                (model['id'],)).fetchone()
            if air_row and air_row[0] is not None:
                val = air_row[0]
                try:
                    nominal_cfm = f"{float(val):.0f}"
                except (TypeError, ValueError):
                    nominal_cfm = str(val)
    finally:
        conn.close()

    is_miraco = table_prefix == 'miraco_'

    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)

    # ── Title block ────────────────────────────────────────────
    title = doc.add_heading("HVAC Equipment Selection Report", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.color.rgb = RGBColor(0x1E, 0x40, 0xAF)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run(f"Project: {project_name}")
    run.bold = True
    run.font.size = Pt(12)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(
        f"Prepared by: {prepared_by}   |   "
        f"Date: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
    doc.add_paragraph()

    # ── 1. Design Conditions ───────────────────────────────────
    # Group rows logically so related values are adjacent:
    #   A) Environment   : Indoor / Outdoor
    #   B) Loads         : Total / Sensible / Heating
    #   C) Air delivery  : Airflow / ESP / Fan Speed
    # This prevents "Required Heating Load" from being stranded at the
    # bottom of the table (where it used to appear on its own page).
    dc = design_conditions
    heading = doc.add_heading("1. Design Conditions", level=1)
    # Keep the heading glued to the first table row.
    heading.paragraph_format.keep_with_next = True

    items = [
        ("Indoor Conditions", "27 °C DB / 19 °C WB"),
        ("Outdoor Ambient", f"{dc.get('outdoor_db', 43)} °C DB"),
        ("Required Total Cooling Load", f"{dc.get('total_btu', 0):,.0f} Btu/hr"),
        ("Required Sensible Cooling Load", f"{dc.get('sensible_btu', 0):,.0f} Btu/hr"),
    ]
    if dc.get('heating_btu', 0) > 0:
        items.append(("Required Heating Load",
                      f"{dc['heating_btu']:,.0f} Btu/hr"))
    required_cfm = dc.get("required_cfm", dc.get("cfm", 0))
    design_cfm = dc.get("design_cfm", required_cfm)
    available_cfm = dc.get("available_unit_cfm")
    available_label = (
        f"{available_cfm:,.0f} CFM (catalog point; unverified delivery)"
        if available_cfm is not None else "Unknown / unverified")
    airflow_esp = dc.get("airflow_esp_inwg")
    items.extend([
        ("Required Airflow", f"{required_cfm:,.0f} CFM"),
        ("Diffuser Design Airflow", f"{design_cfm:,.0f} CFM"),
        ("Selected Unit Catalog-Point Airflow", available_label),
        ("Selected Airflow-Point ESP",
         f"{airflow_esp:.3f} in.wg" if airflow_esp is not None
         else "Unknown"),
        ("Airflow Data Source",
         dc.get("airflow_source", "not recorded")),
        ("Airflow Provenance",
         dc.get("airflow_provenance", "unknown_unverified")),
        ("Airflow Reconciliation", _airflow_reconciliation_label(dc)),
        ("External Static Pressure (ESP)", f"{dc.get('esp', 0.20):.3f} in.wg"),
        ("Fan Speed", dc.get('fan_speed', 'High')),
    ])
    _kv_table(doc, items)
    doc.add_paragraph()

    # ── 2. Selected Equipment ──────────────────────────────────
    _h = doc.add_heading("2. Selected Equipment", level=1)
    _h.paragraph_format.keep_with_next = True
    p = doc.add_paragraph()
    run = p.add_run(f"Model:  {model['system_model']}")
    run.bold = True
    run.font.size = Pt(14)
    run.font.color.rgb = RGBColor(0x05, 0x96, 0x69)

    p2 = doc.add_paragraph()
    run = p2.add_run(
        f"Nominal Class:  {model['nominal_class_kbtu']:.0f} kBTU/hr  "
        f"({model['nominal_class_kbtu'] * 1000:,.0f} Btu/hr)")
    run.font.size = Pt(11)

    _kv_table(doc, [
        ("Indoor Unit Model", model['indoor_model'] or "—"),
        ("Outdoor Unit Model", model['outdoor_model'] or "—"),
        ("Power Supply", model['power_supply'] or "—"),
        ("Compressor Type", model['compressor_type'] or "—"),
        ("Refrigerant Type", model['refrigerant_type'] or "—"),
        ("Rated Cooling Capacity",
         f"{model['rated_cooling_btuh']:,.0f} Btu/hr"
         if model['rated_cooling_btuh'] else "—"),
        ("Rated Heating Capacity",
         f"{model['rated_heating_btuh']:,.0f} Btu/hr"
         if model['rated_heating_btuh'] else "—"),
        ("EER (Cooling)",
         f"{model['eer_btu_wh']:.2f} Btu/Wh" if model['eer_btu_wh'] else "—"),
        ("COP (Heating)",
         f"{model['cop_ww']:.2f} W/W" if model['cop_ww'] else "—"),
        ("Maximum ESP",
         f"{model['max_esp_inwg']:.2f} in.wg"
         if model['max_esp_inwg'] else "—"),
    ])
    doc.add_paragraph()

    # ── 3. Performance ─────────────────────────────────────────
    _h = doc.add_heading("3. Performance at Design Conditions", level=1)
    _h.paragraph_format.keep_with_next = True
    if perf:
        perf_items = [
            ("Total Cooling Capacity",
             f"{perf['total_capacity_btuh']:,.0f} Btu/hr"),
            ("Sensible Cooling Capacity",
             f"{perf['sensible_capacity_btuh']:,.0f} Btu/hr"
             if perf['sensible_capacity_btuh'] else "—"),
            ("Cooling Input Power",
             f"{perf['input_w']:,.0f} W" if perf['input_w'] else "—"),
            ("Catalog Airflow Point at Design ESP (delivery unverified)",
             f"{perf['airflow_cfm']:,.0f} CFM"
             if perf['airflow_cfm'] else "—"),
            ("Actual Operating ESP", f"{perf['esp_inwg']:.3f} in.wg"),
            ("Fan Speed", perf['fan_speed']),
        ]
    else:
        perf_items = [("Performance Data", "Not available for these conditions")]

    if heat:
        perf_items.append(("Heating Capacity (2/1 °C outdoor)",
                           f"{heat['total_capacity_btuh']:,.0f} Btu/hr"))
        if heat['input_w']:
            perf_items.append(("Heating Input Power",
                               f"{heat['input_w']:,.0f} W"))
    if sound:
        perf_items.append(("Sound Pressure Level",
                           f"{sound['sound_dba']:.1f} dB(A)"))
    _kv_table(doc, perf_items)
    doc.add_paragraph()

    # ── 4. Physical & Installation ─────────────────────────────
    _h = doc.add_heading("4. Physical & Installation Data", level=1)
    _h.paragraph_format.keep_with_next = True
    _kv_table(doc, [
        ("Indoor Unit Dimensions (W×H×D)",
         model['indoor_dimensions_mm'] or "—"),
        ("Indoor Unit Weight",
         f"{model['indoor_weight_kg']:.0f} kg"
         if model['indoor_weight_kg'] else "—"),
        ("Outdoor Unit Dimensions (W×H×D)", model['outdoor_dimensions_mm'] or "—"),
        ("Outdoor Unit Weight",
         f"{model['outdoor_weight_kg']:.0f} kg"
         if model['outdoor_weight_kg'] else "—"),
        ("Outdoor Sound Pressure",
         f"{model['outdoor_sound_dba']:.0f} dB(A)"
         if model['outdoor_sound_dba'] else "—"),
        ("Maximum Pipe Length",
         f"{model['max_pipe_length_m']:.0f} m"
         if model['max_pipe_length_m'] else "—"),
        ("Maximum Height Difference",
         f"{model['max_height_diff_m']:.0f} m"
         if model['max_height_diff_m'] else "—"),
        ("Condensate Drain Hose",
         f"{model['drain_hose_mm']:.0f} mm"
         if model['drain_hose_mm'] else "—"),
        ("Nominal Airflow", nominal_cfm or "—"),
        ("Indoor Sound Level", model['indoor_sound_dba'] or "—"),
    ])
    doc.add_paragraph()

    _h = doc.add_heading("5. Electrical & Site Specifications", level=1)
    _h.paragraph_format.keep_with_next = True
    _kv_table(doc, report_rows(
        db_path, table_prefix, model['system_model']))
    doc.add_paragraph()

    # ── 5. Key Features ────────────────────────────────────────
    _h = doc.add_heading("6. Key Features", level=1)
    _h.paragraph_format.keep_with_next = True
    feature_list = MIRACO_KEY_FEATURES if is_miraco else (ROOFTOP_KEY_FEATURES if table_prefix == 'rooftop_' else QDMT_KEY_FEATURES)
    for feature in feature_list:
        p = doc.add_paragraph(feature, style='List Bullet')
        for run in p.runs:
            run.font.size = Pt(10)
    doc.add_paragraph()

    # ── 6. Notes ───────────────────────────────────────────────
    _h = doc.add_heading("7. Notes & Remarks", level=1)
    _h.paragraph_format.keep_with_next = True
    notes = [
        "All capacities are based on EN 14511 / ISO 5151 standards.",
        "Cooling capacities are valid for indoor conditions 27 °C DB / 19 °C WB.",
    ]
    if is_miraco:
        notes.extend([
            ("This Carrier Miraco 50UB / 38BB / 40AB unit does not publish an "
            "external static pressure rating in the source catalog; it is "
            "specified at a fixed nominal ESP."),
            ("Heating is electric-resistance (mounted in the evaporator unit) — "
            "its output does not vary with outdoor temperature."),
            ("Capacities are taken from the June 2013 Carrier Miraco technical "
            "manual (R22 refrigerant)."),
        ])
    else:
        notes.append(
            "The unit is capable of operating at outdoor temperatures up to 52 °C."
        )
    notes.extend([
        "Verify electrical supply availability at site.",
        "Confirm ceiling void height is sufficient for the indoor unit.",
        "Refer to the installation manual for detailed piping and wiring.",
    ])

    for note in notes:
        p = doc.add_paragraph(note, style='List Number')
        for run in p.runs:
            run.font.size = Pt(10)

    # ── Footer ─────────────────────────────────────────────────
    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run(
        "Generated by Duct DX Equipment Selector — "
        "Carrier / Miraco Satellite Series")
    run.font.size = Pt(8)
    run.italic = True
    run.font.color.rgb = RGBColor(0x9C, 0xA3, 0xAF)

    doc.save(str(output_path))
    return True, f"Report saved: {output_path}"


# ═══════════════════════════════════════════════════════════════
#  Electrical / refrigerant piping / ceiling-void check
#  Every value carries a source tag: Catalog (in the DB), Override
#  (optional site_specs_override.csv next to the DB, wins over all),
#  Estimated (computed from published running current - confirm on the
#  nameplate) or "—" (not published).
# ═══════════════════════════════════════════════════════════════
OVERRIDE_FILE = "site_specs_override.csv"

# Standard breaker / fuse ratings (A)
STD_OCPD = [6, 10, 15, 16, 20, 25, 30, 32, 35, 40, 45, 50, 60, 63, 70,
            80, 90, 100, 110, 125, 150, 160, 175, 200, 225, 250]

# Copper cable ampacity, PVC insulated, reference method B1 (IEC 60364-5-52),
# {mm2: (2 loaded conductors, 3 loaded conductors)}
CABLE_AMPACITY = {
    1.5: (17.5, 15.5), 2.5: (24, 21), 4: (32, 28), 6: (41, 36),
    10: (57, 50), 16: (76, 68), 25: (101, 89), 35: (125, 110),
    50: (151, 134), 70: (192, 171), 95: (232, 207),
}
# Ambient (Egypt summer, 45 C) + grouping derating applied before choosing
CABLE_DERATING = 0.80
MIN_POWER_CABLE_MM2 = 2.5   # practical minimum for fixed power wiring

# Ceiling-void defaults (mm)
DEFAULT_CLEARANCE_MM = 100      # hanger rods + slab + ceiling-tile build-up
DRAIN_FALL_MM_PER_M = 10        # 1:100 condensate fall


# ─────────────────────────── helpers ────────────────────────────
def parse_dims(dims):
    """'700 × 210 × 635' or '890x536x753' -> (W, H, D) mm, or None.
    Order in BOTH catalogs is Width x Height x Depth; the 2nd value is the
    height the unit occupies inside the ceiling void."""
    nums = re.findall(r"\d+(?:\.\d+)?", str(dims or ""))
    if len(nums) < 3:
        return None
    return tuple(float(n) for n in nums[:3])


def _fmt_pipe(text):
    """'1/2"-2' -> '1/2" × 2' ; '3/8”' -> '3/8"' ; None -> ''."""
    if not text:
        return ""
    t = str(text).strip().replace("”", '"').replace("“", '"')
    m = re.match(r'^(.*?")\s*[-–]\s*(\d+)$', t)
    if m:
        return f'{m.group(1)} × {m.group(2)}'
    return t


def _std_up(x):
    return next((s for s in STD_OCPD if s >= x), None)


def _std_down(x):
    c = [s for s in STD_OCPD if s <= x]
    return c[-1] if c else None


def estimate_mca_mop(amps):
    """MCA = 1.25 x load ; MOP = largest standard size <= 2.25 x load,
    but never below MCA.  Single-figure estimate (the catalogs publish only
    the total running current), so treat as indicative."""
    if not amps or amps <= 0:
        return None, None
    mca = 1.25 * amps
    mop = _std_down(2.25 * amps)
    if mop is None or mop < mca:
        mop = _std_up(mca)
    return round(mca, 1), mop


def estimate_cable_mm2(current_a, three_phase):
    if not current_a or current_a <= 0:
        return None
    idx = 1 if three_phase else 0
    for mm2, amp in sorted(CABLE_AMPACITY.items()):
        if mm2 < MIN_POWER_CABLE_MM2:
            continue
        if amp[idx] * CABLE_DERATING >= current_a:
            return mm2
    return None


# ───────────────────────── override file ────────────────────────
_override_cache = {}


def load_overrides(db_path):
    """site_specs_override.csv next to the DB.  Columns:
    system_model, liquid_line, gas_line, mca_a, mop_a, cable_mm2, note"""
    path = Path(db_path).with_name(OVERRIDE_FILE)
    key = (str(path), path.stat().st_mtime if path.exists() else 0)
    if key in _override_cache:
        return _override_cache[key]
    data = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                m = (row.get("system_model") or "").strip()
                if m:
                    data[m] = {k: (v or "").strip() for k, v in row.items()}
    _override_cache.clear()
    _override_cache[key] = data
    return data


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ─────────────────────────── main API ───────────────────────────
def get_site_specs(conn, db_path, prefix, system_model):
    """Return a dict with piping, electrical and dimension data for ONE
    system model (prefix '' = QDMT, 'miraco_' = Miraco)."""
    spec = {'model': system_model, 'liquid': "", 'gas': "", 'mca': None, 'mop': None,
                'cable': None, 'elec_source': "", 'pipe_source': "",
                'width': None, 'height': None, 'depth': None, 'power': "", 'note': ""}
    amps_col = "cooling_current_a" if prefix == "" else "NULL"
    row = conn.execute(
        f"SELECT pipe_liquid_inch, pipe_gas_inch, indoor_dimensions_mm, "
        f"power_supply, {amps_col} FROM {prefix}equipment_model "
        f"WHERE system_model=?", (system_model,)).fetchone()
    if not row:
        return spec
    liq, gas, dims, power, cool_a = row
    spec["power"] = power or ""
    d = parse_dims(dims)
    if d:
        spec["width"], spec["height"], spec["depth"] = d
    if liq or gas:
        spec["liquid"], spec["gas"] = _fmt_pipe(liq), _fmt_pipe(gas)
        spec["pipe_source"] = "Catalog"
    three = bool(power) and ("/3/" in power or "3Ph" in power or "/3" in power or "380" in power)

    amps = None
    if prefix == "":
        r_heat = conn.execute(
            "SELECT heating_current_a FROM equipment_model "
            "WHERE system_model=?", (system_model,)).fetchone()
        heat_a = r_heat[0] if (r_heat and r_heat[0] is not None) else 0
        amps = max(x for x in (cool_a or 0, heat_a or 0))
        spec["mca"], spec["mop"] = estimate_mca_mop(amps)
        if spec["mca"]:
            spec["elec_source"] = "Estimated"
    elif prefix == "miraco_":
        el = _miraco_electrical(conn, system_model)
        if el:
            is_heat = system_model.rstrip().endswith("-E")
            amps = max(el["cooling_a"] or 0,
                       (el["heating_a"] or 0) if is_heat else 0)
            spec["mca"], _ = estimate_mca_mop(amps)
            spec["mop"] = el["fuse_a"]
            spec["cable"] = el["wiring_mm2"]
            spec["elec_source"] = "Catalog (MCA est.)"
            spec["note"] = "Cable: copper 3-core, max 20 m run"
    elif prefix == "rooftop_":
        el = _rooftop_electrical(conn, system_model)
        if el:
            spec["mca"] = el.get("mca")
            spec["mop"] = el.get("mop")
            spec["cable"] = el.get("wiring_mm2")
            spec["elec_source"] = "Catalog"
            spec["note"] = "Single-package commercial rooftop unit"
        if not spec["mca"]:
            c_amps = (el.get("cooling_a") if el else None) or cool_a
            if c_amps:
                spec["mca"], spec["mop"] = estimate_mca_mop(c_amps)
                spec["elec_source"] = "Estimated" 
    if spec["cable"] is None and spec["mca"]:
        spec["cable"] = estimate_cable_mm2(spec["mca"], three)

    ov = load_overrides(db_path).get(system_model)
    if ov:
        if ov.get("liquid_line"):
            spec["liquid"], spec["pipe_source"] = ov["liquid_line"], "Override"
        if ov.get("gas_line"):
            spec["gas"], spec["pipe_source"] = ov["gas_line"], "Override"
        for key, col in (("mca", "mca_a"), ("mop", "mop_a"),
                         ("cable", "cable_mm2")):
            if _f(ov.get(col)) is not None:
                spec[key] = _f(ov[col])
                spec["elec_source"] = "Override"
        if ov.get("note"):
            spec["note"] = ov["note"]
    return spec


def _rooftop_electrical(conn, system_model):
    try:
        r = conn.execute(
            "SELECT e.cooling_amps, e.mca_a, e.mop_a, e.wiring_mm2 "
            "FROM rooftop_electrical_spec e "
            "JOIN rooftop_equipment_model m ON m.id=e.equipment_model_id "
            "WHERE m.system_model=?", (system_model,)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    return {'cooling_a': r[0], 'mca': r[1], 'mop': r[2], 'wiring_mm2': r[3]}


def _miraco_electrical(conn, system_model):
    try:
        r = conn.execute(
            "SELECT e.cooling_amps, e.heating_amps, e.mains_fuse_a, "
            "e.wiring_mm2 FROM miraco_electrical_spec e "
            "JOIN miraco_equipment_model m ON m.id=e.equipment_model_id "
            "WHERE m.system_model=?", (system_model,)).fetchone()
    except sqlite3.Error:       # table missing -> DB not rebuilt yet
        return None
    if not r:
        return None
    return {'cooling_a': r[0], 'heating_a': r[1], 'fuse_a': r[2], 'wiring_mm2': r[3]}


def split_combo_model(name):
    """'2× A + B' -> [('A',2), ('B',1)] so combined cart rows work."""
    out = []
    for part in str(name).split(" + "):
        m = re.match(r"^\s*(?:(\d+)\s*[×x]\s*)?(.+?)\s*$", part)
        if m:
            out.append((m.group(2), int(m.group(1) or 1)))
    return out


# ────────────────────────── ceiling void ────────────────────────
def check_ceiling_void(height_mm, void_mm, clearance_mm=DEFAULT_CLEARANCE_MM,
                       drain_run_m=0.0):
    """Return dict(level, text, required, spare).
    level: 'na' (check off / no data) | 'ok' | 'warn' | 'error'."""
    if not void_mm or void_mm <= 0:
        return {'level': "na", 'text': "—", 'required': None, 'spare': None}
    if not height_mm:
        return {'level': "na", 'text': "No height data", 'required': None,
                    'spare': None}
    drain_fall = (drain_run_m or 0) * DRAIN_FALL_MM_PER_M
    required = height_mm + clearance_mm + drain_fall
    if height_mm > void_mm:
        return {'level': "error", 'required': required,
                    'spare': void_mm - height_mm,
                    'text': f"⛔ Unit {height_mm:.0f} mm > void {void_mm:.0f} mm "
                         f"(short {height_mm - void_mm:.0f} mm)"}
    if required > void_mm:
        parts = [f"hangers/ceiling {clearance_mm:.0f}"]
        if drain_fall:
            parts.append(f"drain fall {drain_fall:.0f}")
        return {'level': "warn", 'required': required,
                    'spare': void_mm - required,
                    'text': f"⚠ Fits ({height_mm:.0f} mm) but needs "
                         f"{required:.0f} mm incl. {' + '.join(parts)} — "
                         f"short {required - void_mm:.0f} mm; drain slope "
                         f"/ hangers may be obstructed"}
    return {'level': "ok", 'required': required, 'spare': void_mm - required,
                'text': f"✅ OK — {void_mm - required:.0f} mm spare"}


# ───────────────────── table presentation helpers ───────────────
SPEC_HEADERS = ["Liquid Line", "Gas/Suction Line", "MCA (A)", "MOP (A)",
                "Min Cable (mm²)", "Elec. Source", "Unit Height (mm)",
                "Ceiling Void Check"]


def spec_cells(spec, check):
    def n(v, nd=1):
        if v is None:
            return "—"
        return f"{v:g}" if nd == 0 else f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return [spec["liquid"] or "—", spec["gas"] or "—",
            n(spec["mca"]), n(spec["mop"], 0), n(spec["cable"], 0),
            spec["elec_source"] or "—",
            f"{spec['height']:.0f}" if spec["height"] else "—",
            check["text"]]


# ───────────── shared ceiling parameters (set by the UI) ────────
# The Word reports are built far from the widgets, so the UI publishes the
# current ceiling inputs here and the report code reads them.
_CEILING = {'void': 0.0, 'clearance': DEFAULT_CLEARANCE_MM, 'drain_run': 0.0}


def set_ceiling_params(void=0.0, clearance=DEFAULT_CLEARANCE_MM,
                       drain_run=0.0):
    _CEILING.update(void=void or 0.0, clearance=clearance or 0.0,
                    drain_run=drain_run or 0.0)


def get_ceiling_params():
    return dict(_CEILING)


def check_for_spec(spec):
    p = get_ceiling_params()
    return check_ceiling_void(spec.get("height"), p["void"],
                              p["clearance"], p["drain_run"])


def report_rows(db_path, prefix, system_model):
    """(label, value) rows for the Word reports' installation table."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        sp = get_site_specs(conn, db_path, prefix, system_model)
    finally:
        conn.close()
    ck = check_for_spec(sp)

    def src(tag):
        return f" ({tag})" if tag else ""

    def v(x, unit=""):
        return f"{x:g}{unit}" if x is not None else "—"

    rows = [
        ("Liquid Line (refrigerant)",
         (sp["liquid"] + src(sp["pipe_source"])) if sp["liquid"]
         else "— (not published in catalog)"),
        ("Gas / Suction Line (refrigerant)",
         (sp["gas"] + src(sp["pipe_source"])) if sp["gas"]
         else "— (not published in catalog)"),
        ("Power Supply", sp["power"] or "—"),
        ("Min. Circuit Ampacity (MCA)",
         v(sp["mca"], " A") + src(sp["elec_source"]) if sp["mca"] else "—"),
        ("Max. Overcurrent Protection (MOP)",
         v(sp["mop"], " A") + src(sp["elec_source"]) if sp["mop"] else "—"),
        ("Min. Supply Cable (Cu)",
         v(sp["cable"], " mm²") if sp["cable"] else "—"),
        ("Indoor Unit Height in Ceiling", v(sp["height"], " mm")
         if sp["height"] else "—"),
    ]
    if ck["level"] != "na":
        p = get_ceiling_params()
        rows.append((f"Ceiling Void Check (void {p['void']:.0f} mm)",
                     ck["text"]))
    if sp["note"]:
        rows.append(("Site Note", sp["note"]))
    rows.append(("Electrical Data Note",
                 ("Values marked Estimated are indicative (1.25 × running "
                 "current for MCA). Confirm against the unit nameplate.")))
    return rows
