"""Airflow field normalization and reconciliation helpers.

Cart entries represent room/system requirements; they do not model a shared
physical unit's ownership across multiple rooms. Never infer room allocations
from a selected unit's available airflow.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def design_airflow_cfm(item: Mapping[str, Any]) -> float:
    """Return explicit room design flow, falling back to legacy requirement."""
    for key in ("design_cfm", "required_cfm", "cfm"):
        value = item.get(key)
        if value is not None:
            try:
                flow = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(flow):
                return flow
    return 0.0


def normalize_airflow_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Add explicit fields while preserving legacy ``cfm`` semantics."""
    required = item.get("required_cfm")
    if required is None:
        required = item.get("cfm")
    if required is not None:
        item.setdefault("required_cfm", required)
        item.setdefault("design_cfm", required)
    item.setdefault("available_unit_cfm", None)
    if not item.get("available_unit_cfm_status"):
        item["available_unit_cfm_status"] = "unknown_unverified"
    if not item.get("airflow_source"):
        item["airflow_source"] = "not recorded"
    if not item.get("airflow_provenance"):
        item["airflow_provenance"] = "unknown_unverified"
    return item


def reconcile_airflow(item: Mapping[str, Any]) -> dict[str, Any]:
    """Report required/design/available flow without reconciling implicitly."""
    required = item.get("required_cfm")
    if required is None:
        required = item.get("cfm")
    try:
        required = float(required) if required is not None else None
    except (TypeError, ValueError):
        required = None
    if required is not None and not math.isfinite(required):
        required = None

    design = design_airflow_cfm(item)
    available = item.get("available_unit_cfm")
    try:
        available = float(available) if available is not None else None
    except (TypeError, ValueError):
        available = None
    if available is not None and (
            not math.isfinite(available) or available <= 0):
        available = None

    provenance = str(item.get(
        "available_unit_cfm_status", "unknown_unverified"))
    difference = None
    if required is None or available is None:
        status = "unverified"
    else:
        difference = available - required
        verified = (provenance == "verified"
                    or provenance.startswith("verified_"))
        if abs(difference) < 0.5:
            status = "match" if verified else "match_unverified"
        elif difference < 0:
            status = "shortfall" if verified else "shortfall_unverified"
        else:
            status = "excess" if verified else "excess_unverified"
    return {
        "required_cfm": required,
        "available_unit_cfm": available,
        "design_cfm": design,
        "difference_cfm": difference,
        "status": status,
        "reconciled": False,
        "provenance": provenance,
    }


def combine_selected_unit_airflow(
        units: Sequence[Mapping[str, Any]],
        esp_tolerance_inwg: float = 0.005) -> dict[str, Any]:
    """Sum catalog-point flows only when recorded selection conditions align."""
    records = []
    for unit in units:
        try:
            quantity = int(unit.get("qty", 1))
        except (TypeError, ValueError):
            quantity = 0
        flow = unit.get("airflow_cfm", unit.get("flow"))
        esp = unit.get("airflow_esp_inwg")
        if flow is None or quantity <= 0:
            return {
                "available_unit_cfm": None,
                "status": "unknown_missing_airflow_point",
            }
        try:
            flow = float(flow)
            esp = float(esp) if esp is not None else None
        except (TypeError, ValueError):
            return {
                "available_unit_cfm": None,
                "status": "unknown_unverified_operating_point",
            }
        if not math.isfinite(flow) or flow <= 0 or esp is None or not math.isfinite(esp):
            return {
                "available_unit_cfm": None,
                "status": "unknown_unverified_operating_point",
            }
        fan_speed = unit.get("fan_speed_resolved")
        catalog_prefix = unit.get("catalog_prefix")
        if fan_speed is None or catalog_prefix is None:
            return {
                "available_unit_cfm": None,
                "status": "unknown_unverified_operating_point",
            }
        records.append((flow, quantity, esp, fan_speed, catalog_prefix))
    if not records:
        return {"available_unit_cfm": None, "status": "unknown_no_units"}
    reference = records[0]
    if any(
            entry[3] != reference[3]
            or entry[4] != reference[4]
            or abs(entry[2] - reference[2]) > esp_tolerance_inwg
            for entry in records[1:]):
        return {
            "available_unit_cfm": None,
            "status": "unknown_incompatible_operating_points",
        }
    return {
        "available_unit_cfm": sum(flow * quantity
                                  for flow, quantity, *_ in records),
        "status": "unverified_catalog_points",
    }


def airflow_summary_text(item: Mapping[str, Any]) -> str:
    """Format a concise, explicit airflow reconciliation for the UI."""
    result = reconcile_airflow(item)
    required = result["required_cfm"]
    available = result["available_unit_cfm"]
    design = result["design_cfm"]
    required_text = f"{required:,.0f} CFM" if required is not None else "Unknown"
    available_text = (
        f"{available:,.0f} CFM" if available is not None
        else "Unknown / unverified")
    design_text = f"{design:,.0f} CFM"
    esp = item.get("airflow_esp_inwg")
    esp_text = f"{float(esp):.3f} in.wg" if esp is not None else "unknown ESP"
    requested_esp = item.get("requested_esp_inwg")
    requested_esp_text = (
        f"{float(requested_esp):.3f} in.wg"
        if requested_esp is not None else "unknown requested ESP")
    status = result["status"]
    if status.startswith("shortfall"):
        detail = f"{abs(result['difference_cfm']):,.0f} CFM shortfall"
    elif status.startswith("excess"):
        detail = f"{result['difference_cfm']:,.0f} CFM excess / unallocated"
    elif status.startswith("match"):
        detail = "airflows numerically match; not reconciled"
    else:
        detail = "availability unverified"
    return (
        f"Required: {required_text} | Available unit: {available_text} "
        f"({esp_text} point, requested {requested_esp_text}; "
        f"{result['provenance']}) | Design: {design_text} "
        f"| {detail}")
