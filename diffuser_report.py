"""
diffuser_report.py
==================
Word-report and Excel-export integration for Al-Andalosia diffuser
calculations.

Fully synchronised with the user's choices in the GUI Diffuser
Selection tab: the exact solved values stored in
``item["diffuser_selection"]`` (keyed by ``app_key``, ``type_label``,
``room_h_ft``, ``max_nc``, ``max_dp``, ``positions``, …) are used
verbatim. If no GUI selection is present, a fresh engineering
calculation is performed from the cart item's airflow and room data.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from diffuser_calculator import (
    RoomLayout,
    get_catalog,
    grid_positions,
    select_diffuser,
)

logger = logging.getLogger(__name__)
from diffuser_calculator import ach as calc_ach

ACH_WARN_THRESHOLD = 15.0
ACH_HIGH_THRESHOLD = 25.0


def _ach_warning(ach: float) -> tuple[str, str]:
    """Return (level, text) for a given ACH value."""
    if ach >= ACH_HIGH_THRESHOLD:
        return ("high",
                (f"Very high ACH ({ach:.1f}) — verify sensible/ventilation "
                f"loads and check for draft/noise"))
    if ach >= ACH_WARN_THRESHOLD:
        return ("warn",
                f"High ACH ({ach:.1f}) — verify sensible/ventilation loads")
    return ("ok", "")


def _solve_supply_diffuser(item: dict[str, Any],
                           default_app: str = "office_priv") -> dict[str, Any]:
    """Return the diffuser result for one cart item.

    Priority 1: reuse the exact values already computed by the GUI
    Diffuser Selection tab (stored on ``item["diffuser_selection"]``).
    Priority 2: perform a fresh calculation using the cart item's
    CFM and any room data supplied (either through the GUI or the
    manual-input dialog).
    """
    name = item.get("system", "?")
    from airflow_utils import design_airflow_cfm
    cfm = design_airflow_cfm(item)
    if cfm <= 0:
        return {"system": name, "error": "Airflow CFM is 0 or missing"}
    sensible_load = float(item.get("sensible_btu", 0) or 0)
    sensible_load_btuh = sensible_load if sensible_load > 0 else None
    ds = item.get("diffuser_selection") or {}
    cad_room = item.get("cad_room_data")
    area_ft2 = float(item.get("floor_area_ft2", 0) or 0)
    side = (max(area_ft2, 1.0) ** 0.5) if area_ft2 > 0 else 20.0
    w = float(ds.get("room_w_ft") or item.get("room_w_ft") or side)
    l = float(ds.get("room_l_ft") or item.get("room_l_ft") or side)
    h = float(ds.get("room_h_ft") or item.get("room_h_ft") or 10.0)
    app_key = (ds.get("app_key")
               or ds.get("application")
               or item.get("app_key")
               or default_app)
    type_label = str(ds.get("type_label")
                     or ds.get("diffuser_type")
                     or item.get("type_label")
                     or "Circular")
    max_nc = ds.get("max_nc") or None
    if max_nc is not None and float(max_nc) <= 0:
        max_nc = None
    max_dp = float(ds.get("max_dp", 0.20) or 0.20)
    reusable_selection = False
    if cad_room is not None:
        from room_geometry import RoomGeometry
        try:
            current_geometry = RoomGeometry.from_dict(cad_room)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            return {"system": name, "error": f"Invalid room geometry: {exc}"}
        geometry_errors = current_geometry.validate()
        if geometry_errors:
            return {
                "system": name,
                "error": "Invalid room geometry: " + "; ".join(geometry_errors),
            }
        saved_layout_errors = list(
            cad_room.get("layout_validation_errors", []) or [])
        if saved_layout_errors:
            return {
                "system": name,
                "error": "Saved diffuser layout requires review: "
                         + "; ".join(str(error) for error in saved_layout_errors),
            }
        saved_diffusers = cad_room.get("diffusers", []) or []
        if saved_diffusers:
            from placement_engine import validate_diffuser_layout

            try:
                saved_positions_m = [
                    (diffuser["x_m"], diffuser["y_m"])
                    for diffuser in saved_diffusers
                ]
            except (KeyError, TypeError):
                return {
                    "system": name,
                    "error": "Saved diffuser layout has malformed coordinates",
                }
            validation = validate_diffuser_layout(
                current_geometry, saved_positions_m)
            if not validation.valid:
                return {
                    "system": name,
                    "error": "Saved diffuser layout is invalid: "
                             + validation.reason,
                }
        current_signature = {
            "vertices": [list(point) for point in current_geometry.vertices],
            "height_m": current_geometry.height_m,
        }
    else:
        from diffuser_calculator import ft_to_m
        current_signature = {
            "vertices": [
                [0.0, 0.0], [ft_to_m(w), 0.0],
                [ft_to_m(w), ft_to_m(l)], [0.0, ft_to_m(l)],
            ],
            "height_m": ft_to_m(h),
        }
    from diffuser_calculator import selection_input_signature
    expected_input_signature = selection_input_signature(
        cfm, sensible_load_btuh, current_signature, type_label, app_key,
        max_nc, max_dp)
    reusable_selection = ds.get("input_signature") == expected_input_signature
    if reusable_selection and cad_room is not None:
        if cad_room.get("layout_stale") or ds.get("layout_stale"):
            reusable_selection = False
        saved_positions = ds.get("positions_m")
        room_positions = cad_room.get("diffusers", [])
        if (reusable_selection and
                (not saved_positions or len(saved_positions) != len(room_positions))) or reusable_selection and any(
                math.dist((float(saved[0]), float(saved[1])),
                          (float(room_position["x_m"]),
                           float(room_position["y_m"]))) > 1e-6
                for saved, room_position in zip(
                        saved_positions, room_positions, strict=True)):
            reusable_selection = False

    if (reusable_selection and ds
            and not ds.get("success", bool(ds.get("model")))):
        return {
            "system": name,
            "error": "; ".join(ds.get("errors", []))
                     or "Diffuser layout is infeasible for current inputs",
        }

    # ── 1. Reuse the GUI-computed result verbatim ─────────────
    if (reusable_selection and ds and "model" in ds and "qty" in ds
            and ds.get("success", True)):
        selection_warnings = list(ds.get("warnings", []))
        report_status = (
            "Fallback Accepted - Review Required"
            if ds.get("fallback_accepted") else ds.get("status", "Valid"))
        if ds.get("fallback_accepted"):
            accepted_note = (
                "Fallback layout accepted by user; failed screening criteria "
                "remain subject to engineering review.")
            if accepted_note not in selection_warnings:
                selection_warnings.append(accepted_note)
        return {
            "system":     name,
            "status":     report_status,
            "fallback_accepted": bool(ds.get("fallback_accepted")),
            "model":      ds["model"],
            "size":       ds["size"],
            "qty":        ds["qty"],
            "cfm_each":   ds["cfm_each"],
            "v_fpm":      ds["v_fpm"],
            "dp_inwg":    ds["dp_inwg"],
            "nc":         ds["nc"],
            "t50_ft":     ds["t50_ft"],
            "ach":        ds.get("ach", 0.0),
            "adpi_ok":    ds.get("adpi_pass", True),
            "adpi_ratio": ds.get("adpi_val", 1.0),
            "adpi_ratio_min": ds.get("adpi_val_min"),
            "delta_t_f":  ds.get("delta_t_f"),
            "delta_t_limit_f": ds.get("delta_t_limit_f"),
            "positions":  ds.get("positions", []),
            "warnings":   selection_warnings,
            "rationale":  ds.get("rationale", {}),
        }

    # ── 2. Fresh calculation ──────────────────────────────────
    room = RoomLayout(room_w_ft=w, room_l_ft=l, room_h_ft=h)
    catalog = get_catalog(type_label)

    if cad_room is not None:
        from diffuser_calculator import (
            room_layout_from_geometry,
            select_layout_for_geometry,
        )
        from room_geometry import RoomGeometry

        geometry = current_geometry
        result = select_layout_for_geometry(
            geometry, cfm, app_key, catalog, max_nc, max_dp,
            sensible_load_btuh=sensible_load_btuh)
        if not result.success or result.performance is None:
            return {
                "system": name,
                "error": "; ".join(result.errors)
                         or "No feasible diffuser layout for room geometry",
            }
        sel = result.performance
        room = room_layout_from_geometry(geometry)
        report_warnings = list(result.warnings)
        report_status = "Valid"
        if cad_room.get("layout_stale"):
            report_status = "Fresh calculation - not saved"
            report_warnings.insert(
                0,
                "The saved diffuser layout was stale; this report uses a "
                "newly calculated layout that has not been saved to the project.")
        return {
            "system": name,
            "status": report_status,
            "model": sel.model,
            "size": sel.size_label,
            "qty": result.placed_quantity,
            "cfm_each": sel.cfm_per_unit,
            "v_fpm": sel.neck_velocity_fpm,
            "dp_inwg": sel.pressure_drop_inwg,
            "nc": sel.nc_estimated,
            "t50_ft": sel.throw_t50_ft,
            "ach": calc_ach(cfm, room),
            "adpi_ok": sel.adpi_ok,
            "adpi_ratio": sel.adpi_ratio,
            "adpi_ratio_min": sel.adpi_ratio_min,
            "delta_t_f": sel.delta_t_f,
            "delta_t_limit_f": sel.delta_t_limit_f,
            "positions": result.positions_ft,
            "warnings": report_warnings,
            "rationale": dict(sel.rationale),
        }

    try:
        sel = select_diffuser(
            cfm, app_key, catalog=catalog,
            max_nc=max_nc, max_dp_inwg=max_dp, room=room,
            sensible_load_btuh=sensible_load_btuh)
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError,
            RuntimeError) as exc:
        logger.warning("Diffuser selection failed for %s: %s", name, exc)
        return {"system": name, "error": str(exc)}

    positions = grid_positions(room, sel.qty, sel.throw_t50_ft)
    ok = getattr(sel, 'adpi_ok', True)
    ratio = getattr(sel, 'adpi_ratio', 1.0)

    return {
        "system":     name,
        "model":      sel.model,
        "size":       sel.size_label,
        "qty":        sel.qty,
        "cfm_each":   sel.cfm_per_unit,
        "v_fpm":      sel.neck_velocity_fpm,
        "dp_inwg":    sel.pressure_drop_inwg,
        "nc":         sel.nc_estimated,
        "t50_ft":     sel.throw_t50_ft,
        "ach":        calc_ach(cfm, room),
        "adpi_ok":    ok,
        "adpi_ratio": ratio,
        "adpi_ratio_min": getattr(sel, "adpi_ratio_min", None),
        "delta_t_f":  getattr(sel, "delta_t_f", None),
        "delta_t_limit_f": getattr(sel, "delta_t_limit_f", None),
        "positions":  positions,
        "warnings":   list(sel.warnings),
        "rationale":  dict(sel.rationale),
    }


def _solve_system_diffuser(item: dict[str, Any],
                           default_app: str = "office_priv") -> dict[str, Any]:
    """Add a return CCD schedule to the existing supply diffuser result."""
    result = _solve_supply_diffuser(item, default_app)
    from airflow_utils import design_airflow_cfm
    supply_cfm = design_airflow_cfm(item)
    raw_return_cfm = item.get("return_cfm", supply_cfm)
    return_cfm = float(
        supply_cfm if raw_return_cfm is None else raw_return_cfm)
    ds = item.get("diffuser_selection") or {}
    app_key = (
        ds.get("app_key") or ds.get("application")
        or item.get("app_key") or default_app)
    max_nc = ds.get("max_nc")
    max_nc = float(max_nc) if max_nc not in (None, 0) else None
    max_dp = float(ds.get("max_dp", 0.20) or 0.20)
    ceiling_height = float(
        ds.get("room_h_ft") or item.get("room_h_ft") or 10.0)
    try:
        from diffuser_calculator import select_return_diffusers

        returned = select_return_diffusers(
            return_cfm, application=app_key, max_nc=max_nc,
            max_dp=max_dp, ceiling_height_ft=ceiling_height)
    except ValueError as exc:
        result["return_error"] = str(exc)
    else:
        result["return_diffuser"] = {
            "model": f"RETURN-CCD-{returned.size_in}",
            "quantity": returned.quantity,
            "size": f'{returned.size_in}"',
            "cfm_each": returned.cfm_each,
            "velocity_fpm": returned.velocity_fpm,
            "dp_inwg": returned.pressure_drop_inwg,
            "nc": returned.nc,
            "nc_label": returned.nc_label,
            "total_cfm": return_cfm,
            "warnings": list(returned.warnings),
        }
    width_mm = float(item.get("vcd_duct_width_mm", 0) or 0)
    height_mm = float(item.get("vcd_duct_height_mm", 0) or 0)
    if width_mm or height_mm:
        if "error" in result or not result.get("cfm_each"):
            result["vcd_error"] = (
                "VCD pressure estimate requires a valid supply branch selection.")
        else:
            try:
                from diffuser_calculator import (
                    vcd_face_velocity_fpm,
                    vcd_pressure_drop,
                    vcd_select,
                )

                vcd = vcd_select(width_mm, height_mm)
                velocity = vcd_face_velocity_fpm(
                    result["cfm_each"], width_mm, height_mm)
                damper_dp = vcd_pressure_drop(velocity)
                result["vcd_estimate"] = {
                    "velocity_fpm": velocity,
                    "dp_inwg": damper_dp,
                    "branch_dp_inwg": result["dp_inwg"] + damper_dp,
                    "warnings": list(vcd.warnings),
                }
            except ValueError as exc:
                result["vcd_error"] = str(exc)
    try:
        from diffuser_calculator import access_door_select

        door = access_door_select(return_cfm)
    except ValueError as exc:
        result["access_door_error"] = str(exc)
    else:
        result["access_door"] = {
            "size": door.size_label,
            "quantity": 1,
            "total_cfm": return_cfm,
            "min_cfm": door.min_cfm,
            "max_cfm": door.max_cfm,
            "area_in2": door.area_in2,
            "blade_angle_deg": 45,
            "blade_pitch_mm": 19,
            "filter_optional_depth_in": 1,
            "notes": list(door.notes),
        }
    diameter = float(item.get("rvcd_diameter_in", 0) or 0)
    if diameter > 0:
        try:
            from diffuser_calculator import rvcd_select

            rvcd = rvcd_select(
                diameter, return_cfm,
                str(item.get("rvcd_pressure_class", "LP")))
        except ValueError as exc:
            result["rvcd_error"] = str(exc)
        else:
            result["rvcd_estimate"] = {
                "pressure_class": rvcd.pressure_class,
                "diameter_in": rvcd.diameter_in,
                "frame_allowance_in": rvcd.frame_allowance_in,
                "overall_length_in": rvcd.overall_length_in,
                "velocity_fpm": rvcd.velocity_fpm,
                "dp_inwg": rvcd.pressure_drop_inwg,
                "leakage_cfm_at_1_inwg": rvcd.leakage_cfm_at_1_inwg,
                "estimated": True,
                "warning": rvcd.warning,
            }
    return result


# ═══════════════════════════════════════════════════════════════
#  Word document integration
# ═══════════════════════════════════════════════════════════════
def add_diffuser_summary(doc, cart_items, application: str = "office_priv"):
    """Append a 'Diffuser Selection & Layout' section to a Word doc."""
    from docx.shared import Pt, RGBColor

    cart_items = list(cart_items)
    results = []
    skipped = []
    for item in cart_items:
        from airflow_utils import design_airflow_cfm
        cfm = design_airflow_cfm(item)
        area = float(item.get("floor_area_ft2", 0) or 0)
        has_custom = bool(item.get("diffuser_selection"))
        # A manual room (no HAP floor area) is still valid if the GUI
        # tab has stored a diffuser_selection with positions.
        if cfm <= 0 or (area <= 0 and not has_custom):
            skipped.append(item.get("system", "?"))
            continue
        results.append(_solve_system_diffuser(item, application))

    if not results:
        doc.add_page_break()
        h = doc.add_heading("Diffuser Selection & Layout", level=1)
        h.paragraph_format.keep_with_next = True
        p = doc.add_paragraph(
            "Diffuser selection was skipped: no airflow or room dimensions "
            "were recorded for any system in this selection. Configure "
            "rooms on the Diffuser Selection tab and regenerate the report.")
        for r in p.runs:
            r.italic = True
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)
        if skipped:
            p2 = doc.add_paragraph(
                "Systems skipped: " + ", ".join(skipped))
            for r in p2.runs:
                r.font.size = Pt(9)
        return

    # ── Heading ──────────────────────────────────────────────
    doc.add_page_break()
    h = doc.add_heading("Diffuser Selection & Layout (Al-Andalosia)",
                        level=1)
    h.paragraph_format.keep_with_next = True
    p = doc.add_paragraph(
        "Air distribution per ASHRAE Fundamentals Ch. 20 and the "
        "Al-Andalosia Air Outlets Catalog. T50/L screening estimate only; "
        "ADPI and occupied-zone comfort are not verified.")
    for r in p.runs:
        r.italic = True
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
    from airflow_utils import airflow_summary_text
    for item in cart_items:
        p = doc.add_paragraph(
            f"{item.get('system', '?')}: {airflow_summary_text(item)}")
        for run in p.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x4B, 0x55, 0x63)

    # ── Summary table ────────────────────────────────────────
    headers = ["System", "Status", "Model", "Size", "Qty", "CFM/ea", "V (fpm)",
               "ΔP (in.wg)", "NC", "Return Qty", "Return Size",
               "Return CFM/ea", "Return V (fpm)", "Return ΔP (in.wg)",
               "Return NC", "VCD ΔP est (in.wg)", "Branch ΔP (in.wg)",
               "T50 (ft)", "ACH",
               "ACH Check", "T50/L Screen (ADPI not verified)"]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, name in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = name
        for pp in cell.paragraphs:
            for rr in pp.runs:
                rr.bold = True
                rr.font.size = Pt(8.5)

    for r in results:
        cells = t.add_row().cells
        if "error" in r:
            cells[0].text = r["system"]
            cells[1].text = "Not feasible"
            cells[2].text = r["error"]
            for i in range(3, len(headers)):
                cells[i].text = "—"
            continue
        ach_level, _ = _ach_warning(r["ach"])
        ach_marker = {"ok": "OK",
                      "warn": "⚠ High",
                      "high": "⛔ Very High"}[ach_level]
        return_data = r.get("return_diffuser", {})
        vcd_data = r.get("vcd_estimate", {})
        vals = [
            r["system"], r.get("status", "Valid"),
            r["model"], r["size"], str(r["qty"]),
            f"{r['cfm_each']:,.0f}", f"{r['v_fpm']:,.0f}",
            f"{r['dp_inwg']:.4f}", f"{r['nc']:.0f}",
            str(return_data.get("quantity", "—")),
            return_data.get("size", r.get("return_error", "—")),
            f"{return_data['cfm_each']:,.0f}" if return_data else "—",
            f"{return_data['velocity_fpm']:,.0f}" if return_data else "—",
            f"{return_data['dp_inwg']:.3f}" if return_data else "—",
            return_data.get("nc_label", "—"),
            f"{vcd_data['dp_inwg']:.3f}" if vcd_data else "—",
            f"{vcd_data['branch_dp_inwg']:.3f}" if vcd_data else "—",
            f"{r['t50_ft']:.1f}", f"{r['ach']:.1f}",
            ach_marker,
            "Within band" if r["adpi_ok"] else "Review",
        ]
        for i, v in enumerate(vals):
            cells[i].text = v
            for pp in cells[i].paragraphs:
                for rr in pp.runs:
                    rr.font.size = Pt(8.5)
                    if i == 19 and ach_level == "warn":
                        rr.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)
                        rr.bold = True
                    elif i == 19 and ach_level == "high":
                        rr.font.color.rgb = RGBColor(0xB9, 0x1C, 0x1C)
                        rr.bold = True

    if skipped:
        p = doc.add_paragraph(
            "Systems skipped (no airflow or room data): "
            + ", ".join(skipped))
        for rr in p.runs:
            rr.italic = True
            rr.font.size = Pt(8)
            rr.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)

    doc.add_paragraph()
    bom_heading = doc.add_heading("Return & Damper Accessories BOM", level=2)
    bom_heading.paragraph_format.keep_with_next = True
    bom = doc.add_table(rows=1, cols=5)
    bom.style = "Light Grid Accent 1"
    for cell, title in zip(
            bom.rows[0].cells,
            ("System", "Component", "Size", "Qty", "Airflow / Notes")):
        cell.text = title
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(8)
    for result in results:
        system = result.get("system", "?")
        rows = []
        returned = result.get("return_diffuser")
        if returned:
            rows.append((
                "Return ceiling diffuser", returned["size"],
                returned["quantity"],
                (f'{returned["total_cfm"]:,.0f} CFM total; '
                 f'{returned["velocity_fpm"]:,.0f} fpm, '
                 f'ΔP {returned["dp_inwg"]:.3f} in.wg, '
                 f'NC {returned["nc_label"]}; no throw')))
        elif result.get("return_error"):
            rows.append(("Return ceiling diffuser", "Unavailable", "—",
                         result["return_error"]))
        door = result.get("access_door")
        if door:
            rows.append((
                "Access/transfer grille", door["size"], door["quantity"],
                f'{door["total_cfm"]:,.0f} CFM; 45° blades, '
                "19 mm pitch; optional 1-inch filter"
                + (("; " + "; ".join(door["notes"])) if door["notes"] else "")))
        elif result.get("access_door_error"):
            rows.append(("Access/transfer grille", "Unavailable", "—",
                         result["access_door_error"]))
        rvcd = result.get("rvcd_estimate")
        if rvcd:
            length = (
                f'{rvcd["overall_length_in"]:.3f}" overall'
                if rvcd["overall_length_in"] is not None
                else "frame allowance unavailable")
            rows.append((
                f'{rvcd["pressure_class"]} RVCD (estimated)',
                f'{rvcd["diameter_in"]:g}"', 1,
                f'{rvcd["velocity_fpm"]:,.0f} fpm; '
                f'ΔP {rvcd["dp_inwg"]:.3f} in.wg; '
                f'leakage {rvcd["leakage_cfm_at_1_inwg"]:.2f} CFM '
                f'at 1 in.wg; {length}'
                + (f'; {rvcd["warning"]}' if rvcd["warning"] else "")))
        elif result.get("rvcd_error"):
            rows.append(("RVCD", "Unavailable", "—", result["rvcd_error"]))
        vcd = result.get("vcd_estimate")
        if vcd:
            rows.append(("VCD (estimated)", "Supply branch", 1,
                         (f'{vcd["velocity_fpm"]:,.0f} fpm; '
                          f'ΔP {vcd["dp_inwg"]:.3f} in.wg (separate from '
                          "diffuser limit)")))
        for component, size, quantity, notes in rows:
            cells = bom.add_row().cells
            for cell, value in zip(
                    cells, (system, component, size, quantity, notes)):
                cell.text = str(value)
                for run in cell.paragraphs[0].runs:
                    run.font.size = Pt(8)

    # ── Per-system positions ─────────────────────────────────
    doc.add_paragraph()
    h2 = doc.add_heading("Suggested Diffuser Grid Positions", level=2)
    h2.paragraph_format.keep_with_next = True

    for r in results:
        if "error" in r:
            continue
        p = doc.add_paragraph()
        returned = r.get("return_diffuser")
        return_summary = (
            f"return {returned['quantity']} × {returned['model']} "
            f"({returned['cfm_each']:,.0f} CFM each, "
            f"{returned['velocity_fpm']:,.0f} fpm, "
            f"ΔP {returned['dp_inwg']:.3f} in.wg, "
            f"NC {returned['nc_label']})"
            + (
                " [review: " + "; ".join(returned.get("warnings", [])) + "], "
                if returned.get("warnings") else ", "
            )
            if returned else
            f"return selection unavailable: {r.get('return_error', 'unknown')}, "
        )
        run = p.add_run(
            f"{r['system']}: {r['qty']} × {r['model']} ({r['size']}) — "
            f"{r['cfm_each']:,.0f} CFM each, {r['v_fpm']:,.0f} fpm, "
            f"ΔP {r['dp_inwg']:.4f} in.wg, NC≈{r['nc']:.0f}, "
            + return_summary
            + f"T50 = {r['t50_ft']:.1f} ft, ACH {r['ach']:.1f}, "
            f"T50/L screening = {'within band' if r['adpi_ok'] else 'review'} "
            "(ADPI not verified)"
            + (f", ΔT {r['delta_t_f']:.1f}°F"
               if r.get("delta_t_f") is not None
               else ", ΔT unverified (sensible load unavailable)"))
        run.bold = True
        run.font.size = Pt(10)
        vcd_data = r.get("vcd_estimate")
        if vcd_data:
            vcd_line = doc.add_paragraph(
                f"{r['system']}: Estimated VCD ΔP = "
                f"{vcd_data['dp_inwg']:.3f} in.wg at "
                f"{vcd_data['velocity_fpm']:,.0f} fpm; supply diffuser ΔP "
                f"remains {r['dp_inwg']:.3f} in.wg, and branch total is "
                f"{vcd_data['branch_dp_inwg']:.3f} in.wg. Damper loss is "
                "reported separately and is not checked against the diffuser "
                "max-ΔP limit."
                + (
                    " Manufacturer review: "
                    + "; ".join(vcd_data.get("warnings", []))
                    if vcd_data.get("warnings") else ""
                )
            )
            for text_run in vcd_line.runs:
                text_run.italic = True
                text_run.font.size = Pt(9)
        elif r.get("vcd_error"):
            doc.add_paragraph(
                f"{r['system']}: VCD estimate unavailable — {r['vcd_error']}")

        ach_level, ach_text = _ach_warning(r["ach"])
        if ach_level != "ok":
            wp = doc.add_paragraph()
            wr = wp.add_run(("⛔ " if ach_level == "high" else "⚠ ")
                            + ach_text)
            wr.italic = True
            wr.bold = (ach_level == "high")
            wr.font.size = Pt(9)
            wr.font.color.rgb = (RGBColor(0xB9, 0x1C, 0x1C)
                                 if ach_level == "high"
                                 else RGBColor(0xB4, 0x53, 0x09))

        for wtxt in r.get("warnings", []):
            wp = doc.add_paragraph()
            wr = wp.add_run("⚠ " + wtxt)
            wr.italic = True
            wr.font.size = Pt(9)
            wr.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)

        pos_tbl = doc.add_table(rows=1, cols=3)
        pos_tbl.style = "Light Grid Accent 1"
        for i, name in enumerate(["#", "X (ft)", "Y (ft)"]):
            c = pos_tbl.rows[0].cells[i]
            c.text = name
            for pp in c.paragraphs:
                for rr in pp.runs:
                    rr.bold = True
                    rr.font.size = Pt(8)
        for i, (x, y) in enumerate(r["positions"], 1):
            cells = pos_tbl.add_row().cells
            cells[0].text = f"D{i}"
            cells[1].text = f"{x:.2f}"
            cells[2].text = f"{y:.2f}"
            for c in cells:
                for pp in c.paragraphs:
                    for rr in pp.runs:
                        rr.font.size = Pt(8)
        doc.add_paragraph()


# ═══════════════════════════════════════════════════════════════
#  Excel workbook integration
# ═══════════════════════════════════════════════════════════════
def add_diffuser_sheet_to_excel(wb, cart_items, application="office_priv"):
    """Add a 'Diffusers' worksheet to an existing openpyxl Workbook."""
    from openpyxl.styles import Alignment, Font, PatternFill

    cart_items = list(cart_items)
    if "Diffusers" in wb.sheetnames:
        del wb["Diffusers"]
    ws = wb.create_sheet("Diffusers")

    BLUE        = PatternFill(start_color="2563EB", end_color="2563EB",
                              fill_type="solid")
    LIGHT       = PatternFill(start_color="EFF6FF", end_color="EFF6FF",
                              fill_type="solid")
    YELLOW_FILL = PatternFill(start_color="FEF3C7", end_color="FEF3C7",
                              fill_type="solid")
    RED_FILL    = PatternFill(start_color="B91C1C", end_color="B91C1C",
                              fill_type="solid")
    WHITE_FONT  = Font(bold=True, color="FFFFFF", size=11)
    CENTER      = Alignment(horizontal="center", vertical="center",
                            wrap_text=True)

    ws["A1"] = "Diffuser Selection & Layout Schedule"
    ws["A1"].font = Font(bold=True, size=14, color="2563EB")
    ws["A2"] = ("Al-Andalosia Air Outlets Sizing per ASHRAE "
                "Fundamentals Ch. 20")
    ws["A2"].font = Font(italic=True, size=10, color="6B7280")

    results = []
    skipped = []
    for item in cart_items:
        from airflow_utils import design_airflow_cfm
        cfm = design_airflow_cfm(item)
        area = float(item.get("floor_area_ft2", 0) or 0)
        has_custom = bool(item.get("diffuser_selection"))
        if cfm <= 0 or (area <= 0 and not has_custom):
            skipped.append(item.get("system", "?"))
            continue
        results.append(_solve_system_diffuser(item, application))

    headers = ["System", "Status", "Model", "Size", "Qty", "CFM/ea", "V (fpm)",
               "dP (in.wg)", "NC", "Return Qty", "Return Size",
               "Return CFM/ea", "Return V (fpm)", "Return dP (in.wg)",
               "Return NC", "VCD dP est (in.wg)", "Branch dP (in.wg)",
               "T50 (ft)", "ACH",
               "ACH Check", "T50/L Screen (ADPI not verified)"]
    from airflow_utils import airflow_summary_text
    ws.merge_cells(f"A3:{chr(64 + len(headers))}3")
    ws["A3"] = "Airflow basis: " + " | ".join(
        f"{item.get('system', '?')}: {airflow_summary_text(item)}"
        for item in cart_items)
    ws["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    hdr_row = 4
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=hdr_row, column=col, value=h)
        c.fill = BLUE
        c.font = WHITE_FONT
        c.alignment = CENTER

    row = hdr_row + 1
    for r in results:
        if "error" in r:
            ws.cell(row=row, column=1, value=r["system"])
            ws.cell(row=row, column=2, value="Not feasible")
            ws.cell(row=row, column=3, value=r["error"])
            row += 1
            continue
        ach_level, _ = _ach_warning(r["ach"])
        return_data = r.get("return_diffuser", {})
        vcd_data = r.get("vcd_estimate", {})
        vals = [
            r["system"], r.get("status", "Valid"),
            r["model"], r["size"], r["qty"],
            round(r["cfm_each"], 0), round(r["v_fpm"], 0),
            round(r["dp_inwg"], 4), round(r["nc"], 0),
            return_data.get("quantity"),
            return_data.get("size", r.get("return_error")),
            round(return_data["cfm_each"], 0) if return_data else None,
            round(return_data["velocity_fpm"], 0) if return_data else None,
            round(return_data["dp_inwg"], 3) if return_data else None,
            return_data.get("nc_label"),
            round(vcd_data["dp_inwg"], 3) if vcd_data else None,
            round(vcd_data["branch_dp_inwg"], 3) if vcd_data else None,
            round(r["t50_ft"], 1), round(r["ach"], 1),
            {"ok": "OK", "warn": "High",
             "high": "Very high"}[ach_level],
            "Within band" if r["adpi_ok"] else "Review",
        ]
        for col, v in enumerate(vals, start=1):
            c = ws.cell(row=row, column=col, value=v)
            c.alignment = CENTER
            if col == 20:
                if ach_level == "warn":
                    c.fill = YELLOW_FILL
                    c.font = Font(bold=True, color="B45309")
                elif ach_level == "high":
                    c.fill = RED_FILL
                    c.font = Font(bold=True, color="FFFFFF")
        row += 1

    if skipped:
        row += 1
        ws.cell(row=row, column=1,
                value="(skipped — no airflow/room data: "
                      + ", ".join(skipped) + ")"
                ).font = Font(italic=True, size=9, color="B45309")
        row += 1

    # ── Per-system positions ─────────────────────────────────
    row += 2
    ws.cell(row=row, column=1,
            value="Suggested Diffuser Positions"
            ).font = Font(bold=True, size=12, color="2563EB")
    row += 2

    for r in results:
        if "error" in r:
            continue
        ws.cell(row=row, column=1,
                value=(f"{r['system']}: {r['qty']} x {r['model']} "
                       f"({r['size']}) - {r['cfm_each']:,.0f} CFM each, "
                       f"{r['v_fpm']:,.0f} fpm, "
                       f"dP {r['dp_inwg']:.4f} in.wg, "
                       f"NC~{r['nc']:.0f}, T50 = {r['t50_ft']:.1f} ft")
                ).font = Font(bold=True)
        row += 1
        for col, h in enumerate(["#", "X (ft)", "Y (ft)"], start=1):
            c = ws.cell(row=row, column=col, value=h)
            c.fill = LIGHT
            c.font = Font(bold=True)
            c.alignment = CENTER
        row += 1
        for i, (x, y) in enumerate(r["positions"], 1):
            ws.cell(row=row, column=1, value=f"D{i}").alignment = CENTER
            ws.cell(row=row, column=2, value=round(x, 2)).alignment = CENTER
            ws.cell(row=row, column=3, value=round(y, 2)).alignment = CENTER
            row += 1
        row += 1

    row += 2
    ws.cell(row=row, column=1,
            value="Return & Damper Accessories BOM"
            ).font = Font(bold=True, size=12, color="2563EB")
    row += 1
    bom_headers = ["System", "Component", "Size", "Qty", "Airflow / Notes"]
    for col, title in enumerate(bom_headers, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.fill = BLUE
        cell.font = WHITE_FONT
        cell.alignment = CENTER
    row += 1
    for result in results:
        system = result.get("system", "?")
        lines = []
        returned = result.get("return_diffuser")
        if returned:
            lines.append((
                "Return ceiling diffuser", returned["size"],
                returned["quantity"],
                (f'{returned["total_cfm"]:,.0f} CFM total; '
                 f'{returned["velocity_fpm"]:,.0f} fpm, '
                 f'ΔP {returned["dp_inwg"]:.3f} in.wg, '
                 f'NC {returned["nc_label"]}; no throw')))
        elif result.get("return_error"):
            lines.append(("Return ceiling diffuser", "Unavailable", "",
                          result["return_error"]))
        door = result.get("access_door")
        if door:
            lines.append((
                "Access/transfer grille", door["size"], door["quantity"],
                f'{door["total_cfm"]:,.0f} CFM; 45° blades, '
                "19 mm pitch; optional 1-inch filter"
                + (("; " + "; ".join(door["notes"])) if door["notes"] else "")))
        elif result.get("access_door_error"):
            lines.append(("Access/transfer grille", "Unavailable", "",
                          result["access_door_error"]))
        rvcd = result.get("rvcd_estimate")
        if rvcd:
            length = (
                f'{rvcd["overall_length_in"]:.3f}" overall'
                if rvcd["overall_length_in"] is not None
                else "frame allowance unavailable")
            lines.append((
                f'{rvcd["pressure_class"]} RVCD (estimated)',
                f'{rvcd["diameter_in"]:g}"', 1,
                f'{rvcd["velocity_fpm"]:,.0f} fpm; '
                f'ΔP {rvcd["dp_inwg"]:.3f} in.wg; '
                f'leakage {rvcd["leakage_cfm_at_1_inwg"]:.2f} CFM '
                f'at 1 in.wg; {length}'
                + (f'; {rvcd["warning"]}' if rvcd["warning"] else "")))
        elif result.get("rvcd_error"):
            lines.append(("RVCD", "Unavailable", "", result["rvcd_error"]))
        vcd = result.get("vcd_estimate")
        if vcd:
            lines.append(("VCD (estimated)", "Supply branch", 1,
                          (f'{vcd["velocity_fpm"]:,.0f} fpm; '
                           f'ΔP {vcd["dp_inwg"]:.3f} in.wg '
                           "(separate from diffuser limit)")))
        for component, size, quantity, notes in lines:
            for col, value in enumerate(
                    (system, component, size, quantity, notes), start=1):
                cell = ws.cell(row=row, column=col, value=value)
                cell.alignment = CENTER
            row += 1

    # ── Auto-fit columns ─────────────────────────────────────
    for col in ws.iter_cols():
        mx = 0
        letter = None
        for cell in col:
            if letter is None:
                letter = cell.column_letter
            if cell.value is not None:
                mx = max(mx, len(str(cell.value)))
        if letter:
            ws.column_dimensions[letter].width = max(12, min(mx + 3, 40))

    return ws
