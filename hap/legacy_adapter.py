"""Adapt structured HAP data to the existing application dictionary contract."""
from __future__ import annotations

from .models import AirSystem


def to_legacy_system(system: AirSystem) -> dict:
    def value(item, default=0.0):
        return default if item is None or item.normalized_value is None else item.normalized_value

    return {
        "name": system.system_name,
        "total_btu": value(system.cooling.total_load),
        "sensible_btu": value(system.cooling.sensible_load),
        "cfm": round(value(system.cooling.airflow)),
        "heating_btu": value(system.heating.load),
        "esp": None if system.fan.esp is None else system.fan.esp.normalized_value,
        "status": "Pending",
        "model": "",
        "floor_area_ft2": value(system.floor_area),
        "warnings": [],
        "critical_errors": [],
    }
