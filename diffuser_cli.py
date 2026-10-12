#!/usr/bin/env python3
"""
diffuser_cli.py — diffuser selection from the terminal.
    python diffuser_cli.py --cfm 995 --width 33.5 --length 21.6 --height 9.2 --type square --application conference
    python diffuser_cli.py --cfm 995 --metric --width 10.2 --length 6.6 --height 2.8 --options 5
    python diffuser_cli.py --batch systems.csv        (csv: name,cfm,area  -> assumes a square room!)
"""
import argparse
import csv
import math
import sys
from pathlib import Path

from diffuser_calculator import (
    APPLICATION_NC,
    RoomLayout,
    access_door_select,
    format_report,
    ft_to_m,
    get_catalog,
    rank_options,
    rvcd_select,
    select_layout_for_geometry,
    select_return_diffusers,
    vcd_face_velocity_fpm,
    vcd_pressure_drop,
    vcd_select,
)

TYPES = {"round": "Circular (CCD)", "square": "Square (4SCD)", "square3": "Square 3-way (3SCD)"}


def _finite_positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and greater than zero")
    return number


def solve(name, cfm, room, application, type_key, delta_t=None,
          return_cfm=None, vcd_width_mm=None, vcd_height_mm=None,
          rvcd_diameter_in=None, rvcd_pressure_class="LP"):
    cfm = _finite_positive(cfm, "Supply airflow")
    if type_key not in TYPES:
        raise ValueError(f"Unsupported diffuser type: {type_key!r}")
    if application not in APPLICATION_NC:
        raise ValueError(f"Unsupported application: {application!r}")
    if not isinstance(room, RoomLayout):
        raise TypeError("room must be a RoomLayout")
    _finite_positive(room.room_w_ft, "Room width")
    _finite_positive(room.room_l_ft, "Room length")
    _finite_positive(room.room_h_ft, "Room height")
    _finite_positive(room.floor_area_ft2, "Room floor area")
    if delta_t is not None:
        try:
            delta_t = float(delta_t)
        except (TypeError, ValueError) as exc:
            raise ValueError("Supply temperature difference must be numeric") from exc
        if not math.isfinite(delta_t) or delta_t < 0:
            raise ValueError(
                "Supply temperature difference must be finite and non-negative")
    if return_cfm is not None:
        return_cfm = _finite_positive(return_cfm, "Return airflow")
    if (vcd_width_mm is None) != (vcd_height_mm is None):
        raise ValueError("VCD width and height must be supplied together")
    if vcd_width_mm is not None:
        vcd_width_mm = _finite_positive(vcd_width_mm, "VCD width")
        vcd_height_mm = _finite_positive(vcd_height_mm, "VCD height")
    if rvcd_diameter_in is not None:
        rvcd_diameter_in = _finite_positive(rvcd_diameter_in, "RVCD diameter")
    if rvcd_pressure_class not in {"LP", "HP"}:
        raise ValueError("RVCD pressure class must be LP or HP")

    opts = rank_options(cfm, application, get_catalog(TYPES[type_key]), room=room,
                        delta_t_f=delta_t, top=5)
    if not opts:
        return {"system": name, "status": "❌ no catalog diffuser fits"}
    from room_geometry import RoomGeometry
    geometry = RoomGeometry.from_vertices(
        [(0.0, 0.0), (ft_to_m(room.room_w_ft), 0.0),
         (ft_to_m(room.room_w_ft), ft_to_m(room.room_l_ft)),
         (0.0, ft_to_m(room.room_l_ft))],
        ft_to_m(room.room_h_ft))
    layout = select_layout_for_geometry(
        geometry, cfm, application=application,
        catalog=get_catalog(TYPES[type_key]), max_dp_inwg=0.20,
        delta_t_f=delta_t)
    if not layout.success or layout.performance is None:
        return {
            "system": name,
            "status": "⚠ no fully compliant geometry-aware layout",
            "layout": layout,
            "options": opts,
        }
    s = layout.performance
    return_airflow = cfm if return_cfm is None else return_cfm
    return_error = None
    try:
        returned = select_return_diffusers(
            return_airflow, application=application,
            ceiling_height_ft=room.room_h_ft)
    except ValueError as exc:
        returned = None
        return_error = str(exc)
    try:
        access_door = access_door_select(return_airflow)
        access_door_error = None
    except ValueError as exc:
        access_door = None
        access_door_error = str(exc)
    rvcd = None
    rvcd_error = None
    if rvcd_diameter_in is not None:
        try:
            rvcd = rvcd_select(
                rvcd_diameter_in, return_airflow, rvcd_pressure_class)
        except ValueError as exc:
            rvcd_error = str(exc)
    vcd = None
    vcd_error = None
    if vcd_width_mm is not None or vcd_height_mm is not None:
        try:
            selected_vcd = vcd_select(vcd_width_mm, vcd_height_mm)
            velocity = vcd_face_velocity_fpm(
                s.cfm_per_unit, vcd_width_mm, vcd_height_mm)
            damper_dp = vcd_pressure_drop(velocity)
        except ValueError as exc:
            vcd_error = str(exc)
        else:
            vcd = {
                "selection": selected_vcd,
                "velocity_fpm": velocity,
                "dp_inwg": damper_dp,
                "branch_dp_inwg": s.pressure_drop_inwg + damper_dp,
            }
    return {"system": name, "status": "OK", "sel": s, "options": opts,
            "layout": layout, "positions": layout.positions_ft,
            "report": format_report(s, room, cfm, application),
            "return": returned, "return_error": return_error,
            "access_door": access_door,
            "access_door_error": access_door_error,
            "rvcd": rvcd, "rvcd_error": rvcd_error,
            "vcd": vcd, "vcd_error": vcd_error}


def print_options(opts):
    print("\nTop catalog options (✔ = no catalog warnings; primary layout is geometry-aware):")
    print(f"{'':2s}{'Qty×Model':14s}{'CFM/ea':>7s}{'FPM':>6s}{'ΔP':>7s}{'NC':>5s}{'T50':>6s}{'T50/L':>11s}")
    for o in opts:
        mark = "✔" if not o.warnings else "✖"
        print(f"{mark:2s}{str(o.qty)+'×'+o.model:14s}{o.cfm_per_unit:7.0f}{o.neck_velocity_fpm:6.0f}"
              f"{o.pressure_drop_inwg:7.3f}{o.nc_label:>5s}{o.throw_t50_ft:6.1f}"
              f"{o.adpi_ratio_min:6.2f}-{o.adpi_ratio:.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cfm", type=float)
    p.add_argument("--return-cfm", type=float,
                   help="return airflow (defaults to --cfm)")
    p.add_argument("--vcd-width-mm", type=float,
                   help="optional supply branch VCD duct width (mm)")
    p.add_argument("--vcd-height-mm", type=float,
                   help="optional supply branch VCD duct height (mm)")
    p.add_argument("--rvcd-diameter-in", type=float,
                   help="optional return round VCD diameter (in)")
    p.add_argument("--rvcd-pressure-class", choices=["LP", "HP"], default="LP")
    p.add_argument("--width", type=float)
    p.add_argument("--length", type=float)
    p.add_argument("--area", type=float, help="assume a SQUARE room (inaccurate for long rooms)")
    p.add_argument("--height", type=float, default=9.0, help="ceiling height (false-ceiling face), default 9 ft")
    p.add_argument("--metric", action="store_true", help="width/length/height are in metres")
    p.add_argument("--application", default="office_priv", choices=sorted(APPLICATION_NC))
    p.add_argument("--type", choices=sorted(TYPES), default="round")
    p.add_argument("--delta-t", type=float, help="supply ΔT in °F (Table 4 check)")
    p.add_argument("--options", type=int, default=0, help="also print the top N alternatives")
    p.add_argument("--batch", help="CSV with columns name,cfm,area")
    p.add_argument("--output", "-o")
    a = p.parse_args()

    try:
        a.height = _finite_positive(a.height, "--height")
        if a.delta_t is not None and (
                not math.isfinite(a.delta_t) or a.delta_t < 0):
            raise ValueError("--delta-t must be finite and non-negative")
        if a.return_cfm is not None:
            a.return_cfm = _finite_positive(a.return_cfm, "--return-cfm")
        if a.rvcd_diameter_in is not None:
            a.rvcd_diameter_in = _finite_positive(
                a.rvcd_diameter_in, "--rvcd-diameter-in")
        if a.vcd_width_mm is not None or a.vcd_height_mm is not None:
            if (a.vcd_width_mm is None) != (a.vcd_height_mm is None):
                raise ValueError(
                    "--vcd-width-mm and --vcd-height-mm must be supplied together")
            a.vcd_width_mm = _finite_positive(
                a.vcd_width_mm, "--vcd-width-mm")
            a.vcd_height_mm = _finite_positive(
                a.vcd_height_mm, "--vcd-height-mm")
        if a.options < 0:
            raise ValueError("--options must be non-negative")
    except ValueError as exc:
        p.error(str(exc))

    if a.batch:
        src = Path(a.batch)
        if not src.is_file():
            print(f"❌ CSV not found: {src}", file=sys.stderr); return 2
        print("⚠ batch mode assumes SQUARE rooms from the area column – check long rooms manually.")
        print(f"\n{'System':8s}{'Qty×Model':14s}{'CFM/ea':>7s}{'FPM':>6s}{'ΔP':>7s}{'NC':>5s}{'T50':>6s}  Status")
        rows = []
        with src.open(encoding="utf-8-sig") as f:
            for row_number, r in enumerate(csv.DictReader(f), start=2):
                try:
                    cfm = _finite_positive(r["cfm"], "CFM")
                    area = _finite_positive(r["area"], "Area")
                except (KeyError, ValueError) as exc:
                    p.error(f"{src}:{row_number}: {exc}")
                side = area ** 0.5
                res = solve(
                    r.get("name") or "?", cfm,
                    RoomLayout(side, side, a.height), a.application, a.type,
                    a.delta_t, return_cfm=a.return_cfm,
                    vcd_width_mm=a.vcd_width_mm,
                    vcd_height_mm=a.vcd_height_mm,
                    rvcd_diameter_in=a.rvcd_diameter_in,
                    rvcd_pressure_class=a.rvcd_pressure_class)
                rows.append(res)
                if res["status"] != "OK":
                    print(f"{res['system']:8s}{res['status']}"); continue
                s = res["sel"]
                print(f"{res['system']:8s}{str(s.qty)+'×'+s.model:14s}{s.cfm_per_unit:7.0f}{s.neck_velocity_fpm:6.0f}"
                      f"{s.pressure_drop_inwg:7.3f}{s.nc_label:>5s}{s.throw_t50_ft:6.1f}  "
                      f"{'OK' if not s.warnings else '⚠ ' + '; '.join(s.warnings)}")
                returned = res["return"]
                if returned:
                    print(
                        f"  Return: {returned.quantity}×RETURN-CCD-{returned.size_in} "
                        f"at {returned.cfm_each:.0f} CFM, "
                        f"{returned.velocity_fpm:.0f} fpm, "
                        f"ΔP {returned.pressure_drop_inwg:.3f} in.wg, "
                        f"NC {returned.nc_label}")
                    for warning in returned.warnings:
                        print(f"  Return review: {warning}")
                else:
                    print(f"  Return: unavailable — {res['return_error']}")
                if res["vcd"]:
                    print(
                        f"  Estimated VCD ΔP: {res['vcd']['dp_inwg']:.3f} "
                        f"in.wg at {res['vcd']['velocity_fpm']:.0f} fpm; "
                        f"branch total {res['vcd']['branch_dp_inwg']:.3f} "
                        "in.wg (not checked against diffuser limit).")
                    for warning in res["vcd"]["selection"].warnings:
                        print(f"  VCD review: {warning}")
                elif res["vcd_error"]:
                    print(f"  VCD estimate unavailable — {res['vcd_error']}")
                if res["access_door"]:
                    door = res["access_door"]
                    print(
                        f"  Access/transfer grille option: "
                        f"{door.size_label} (1 ×; "
                        f"{door.min_cfm}-{door.max_cfm} CFM), "
                        "45° blades, 19 mm pitch; optional 1-inch filter.")
                    for note in door.notes:
                        print(f"  Access-door review: {note}")
                elif res["access_door_error"]:
                    print(f"  Access-door option unavailable — "
                          f"{res['access_door_error']}")
                if res["rvcd"]:
                    damper = res["rvcd"]
                    frame = (
                        f"{damper.overall_length_in:.3f} in overall length"
                        if damper.overall_length_in is not None
                        else "frame allowance unavailable")
                    print(
                        f"  Estimated {damper.pressure_class} RVCD: "
                        f"{damper.diameter_in:g} in, {frame}; "
                        f"{damper.velocity_fpm:.0f} fpm, "
                        f"ΔP {damper.pressure_drop_inwg:.3f} in.wg; "
                        f"leakage {damper.leakage_cfm_at_1_inwg:.2f} CFM "
                        "at 1 in.wg.")
                    if damper.warning:
                        print(f"  RVCD review: {damper.warning}")
                elif res["rvcd_error"]:
                    print(f"  RVCD estimate unavailable — {res['rvcd_error']}")
        out = a.output or "diffusers_result.csv"
        with Path(out).open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["System", "Model", "Qty", "CFM_each", "V_fpm", "DP_inwg", "NC", "T50_ft", "Grid", "Warnings",
                        "Return_Model", "Return_Qty", "Return_CFM_each", "Return_V_fpm", "Return_DP_inwg", "Return_NC",
                        "Estimated_VCD_DP_inwg", "Branch_DP_inwg",
                        "Access_Door_Size", "RVCD_Class", "RVCD_Diameter_in",
                        "Estimated_RVCD_DP_inwg"])
            for res in rows:
                if res["status"] != "OK":
                    w.writerow([res["system"], res["status"]]); continue
                s = res["sel"]
                returned = res["return"]
                vcd = res["vcd"]
                w.writerow([res["system"], s.model, s.qty, s.cfm_per_unit, s.neck_velocity_fpm,
                            s.pressure_drop_inwg, s.nc_label, s.throw_t50_ft, f"{s.rows}x{s.cols}",
                            "; ".join(s.warnings),
                            f"RETURN-CCD-{returned.size_in}" if returned else res["return_error"],
                            returned.quantity if returned else "",
                            returned.cfm_each if returned else "",
                            returned.velocity_fpm if returned else "",
                            returned.pressure_drop_inwg if returned else "",
                            returned.nc_label if returned else "",
                            vcd["dp_inwg"] if vcd else "",
                            vcd["branch_dp_inwg"] if vcd else "",
                            res["access_door"].size_label
                            if res["access_door"] else "",
                            res["rvcd"].pressure_class if res["rvcd"] else "",
                            res["rvcd"].diameter_in if res["rvcd"] else "",
                            res["rvcd"].pressure_drop_inwg if res["rvcd"] else ""])
        print(f"\n✅ CSV saved: {out}"); return 0

    if a.cfm is None:
        p.error("--cfm is required")
    try:
        cfm = _finite_positive(a.cfm, "--cfm")
        has_width = a.width is not None
        has_length = a.length is not None
        if has_width != has_length:
            raise ValueError("--width and --length must be supplied together")
        if has_width:
            w = _finite_positive(a.width, "--width")
            l = _finite_positive(a.length, "--length")
            h = a.height
        elif a.area is not None:
            area = _finite_positive(a.area, "--area")
            w = l = area ** 0.5
            h = a.height
        else:
            raise ValueError("provide --width and --length (or --area)")
    except ValueError as exc:
        p.error(str(exc))
    if has_width:
        room = RoomLayout.from_metric(w, l, h) if a.metric else RoomLayout(w, l, h)
    else:
        room = RoomLayout(w, l, h)
        print("⚠ --area assumes a square room.")

    return_cfm = cfm if a.return_cfm is None else a.return_cfm
    res = solve(
        "SPACE", cfm, room, a.application, a.type, a.delta_t,
        return_cfm=return_cfm, vcd_width_mm=a.vcd_width_mm,
        vcd_height_mm=a.vcd_height_mm,
        rvcd_diameter_in=a.rvcd_diameter_in,
        rvcd_pressure_class=a.rvcd_pressure_class)
    if res["status"] != "OK":
        print(res["status"]); return 1
    print(res["report"])
    returned = res["return"]
    if returned:
        print(
            f"\nReturn: {returned.quantity} × RETURN-CCD-{returned.size_in} "
            f"({return_cfm:,.0f} CFM total; {returned.cfm_each:,.0f} CFM each), "
            f"{returned.velocity_fpm:,.0f} fpm, "
            f"ΔP {returned.pressure_drop_inwg:.3f} in.wg, "
            f"NC {returned.nc_label}; no throw.")
        for warning in returned.warnings:
            print(f"Return review: {warning}")
    else:
        print(f"\nReturn selection unavailable: {res['return_error']}")
    if res["access_door"]:
        door = res["access_door"]
        print(
            f"Access/transfer grille option: {door.size_label} (1 ×; "
            f"{door.min_cfm}-{door.max_cfm} CFM), 45° blades, 19 mm pitch; "
            "optional 1-inch filter.")
        for note in door.notes:
            print(f"Access-door review: {note}")
    elif res["access_door_error"]:
        print(f"Access-door option unavailable: {res['access_door_error']}")
    if res["rvcd"]:
        damper = res["rvcd"]
        frame = (
            f"{damper.overall_length_in:.3f} in overall length"
            if damper.overall_length_in is not None
            else "frame allowance unavailable")
        print(
            f"Estimated {damper.pressure_class} RVCD: "
            f"{damper.diameter_in:g} in, {frame}; "
            f"{damper.velocity_fpm:,.0f} fpm, "
            f"ΔP {damper.pressure_drop_inwg:.3f} in.wg; leakage "
            f"{damper.leakage_cfm_at_1_inwg:.2f} CFM at 1 in.wg.")
        if damper.warning:
            print(f"RVCD review: {damper.warning}")
    elif res["rvcd_error"]:
        print(f"RVCD estimate unavailable: {res['rvcd_error']}")
    if res["vcd"]:
        print(
            f"\nEstimated VCD ΔP: {res['vcd']['dp_inwg']:.3f} in.wg at "
            f"{res['vcd']['velocity_fpm']:,.0f} fpm; branch total "
            f"{res['vcd']['branch_dp_inwg']:.3f} in.wg. This separate damper "
            "loss is not checked against the diffuser max-ΔP limit.")
        for warning in res["vcd"]["selection"].warnings:
            print(f"VCD review: {warning}")
    elif res["vcd_error"]:
        print(f"\nVCD estimate unavailable: {res['vcd_error']}")
    print("\nPositions (x along width, y along length, ft from the corner):")
    for i, (x, y) in enumerate(res["positions"], 1):
        print(f"  D{i}: ({x}, {y})")
    if a.options:
        print_options(res["options"][:a.options])
    if a.output:
        Path(a.output).write_text(res["report"], encoding="utf-8")
        print(f"\n✅ Saved: {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
