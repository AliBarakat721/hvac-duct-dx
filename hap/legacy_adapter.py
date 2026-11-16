"""Adapt structured HAP data to the existing application dictionary contract."""
from __future__ import annotations

from .models import AirSystem, SourceValue, ValidationResult
from .validation import validate_system


def to_legacy_system(system: AirSystem, validation: ValidationResult | None = None) -> dict:
    validation = validation or validate_system(system)

    def value(item):
        return None if item is None or item.normalized_value is None else item.normalized_value

    def provenance(item: SourceValue | None) -> dict:
        if item is None:
            return {"state": "missing", "value": None, "normalized_value": None}
        return item.to_dict()

    issues = [
        item for item in validation.issues
        if item.system_id is None or item.system_id == system.system_id
    ]
    status = "error" if any(item.severity == "ERROR" for item in issues) else "warning" if issues else "passed"
    cfm = value(system.cooling.airflow)
    return {
        "name": system.system_name,
        "total_btu": value(system.cooling.total_load),
        "sensible_btu": value(system.cooling.sensible_load),
        "cfm": None if cfm is None else round(cfm),
        "heating_btu": value(system.heating.load),
        "esp": None if system.fan.esp is None else system.fan.esp.normalized_value,
        "status": "Pending",
        "model": "",
        "floor_area_ft2": value(system.floor_area),
        "warnings": [item.to_dict() for item in issues if item.severity == "WARNING"],
        "critical_errors": [item.to_dict() for item in issues if item.severity == "ERROR"],
        "validation_status": status,
        "value_states": {
            "total_btu": provenance(system.cooling.total_load),
            "sensible_btu": provenance(system.cooling.sensible_load),
            "cfm": provenance(system.cooling.airflow),
            "heating_btu": provenance(system.heating.load),
            "esp": provenance(system.fan.esp),
            "floor_area_ft2": provenance(system.floor_area),
        },
    }
