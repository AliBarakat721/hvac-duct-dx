"""Engineering validation for extracted HAP system values.

These checks are import-quality checks, not engineering approval of the design.
"""
from __future__ import annotations

import math

from .models import AirSystem, SourceValue, ValidationIssue, ValidationResult


_EXPECTED_UNITS = {
    "cooling.total_load": "Btu/hr",
    "cooling.sensible_load": "Btu/hr",
    "cooling.latent_load": "Btu/hr",
    "cooling.total_tons": "Btu/hr",
    "cooling.airflow": "CFM",
    "cooling.shr": "ratio",
    "heating.load": "Btu/hr",
    "heating.airflow": "CFM",
    "fan.airflow": "CFM",
    "fan.standard_airflow": "CFM",
    "fan.esp": "in.wg",
    "floor_area": "ft²",
    "ventilation.outdoor_airflow": "CFM",
    "ventilation.cfm_per_area": "CFM/ft²",
    "ventilation.cfm_per_person": "CFM/person",
}

_ALLOWED_SOURCE_UNITS = {
    "cooling.total_load": {"mbh", "kw", "btu/hr", "btu/h", "ton", "tons"},
    "cooling.sensible_load": {"mbh", "kw", "btu/hr", "btu/h"},
    "cooling.latent_load": {"mbh", "kw", "btu/hr", "btu/h"},
    "cooling.total_tons": {"ton", "tons"},
    "cooling.airflow": {"cfm"},
    "cooling.shr": {"", "%", "ratio", "fraction"},
    "heating.load": {"mbh", "kw", "btu/hr", "btu/h"},
    "heating.airflow": {"cfm"},
    "fan.airflow": {"cfm"},
    "fan.standard_airflow": {"cfm"},
    "fan.esp": {"inwg", "in.wg", "pa"},
    "floor_area": {"ft2", "m2"},
    "ventilation.outdoor_airflow": {"cfm"},
    "ventilation.cfm_per_area": {"cfm/ft2"},
    "ventilation.cfm_per_person": {"cfm/person"},
}


def _unit_token(unit: str | None) -> str:
    return (unit or "").lower().replace("²", "2").replace("^", "").replace(" ", "")


def validate_system(system: AirSystem) -> ValidationResult:
    """Validate extracted values for consistency and traceability.

    Values are compared only after normalization. A validation warning/error is
    not a replacement for manufacturer data or engineer review.
    """
    issues: list[ValidationIssue] = []

    def add(severity: str, code: str, message: str, *sections: str) -> None:
        issues.append(
            ValidationIssue(
                severity, code, message, list(sections), system_id=system.system_id
            )
        )

    cooling = system.cooling
    if cooling.total_load is None:
        add("ERROR", "COOLING_LOAD_MISSING", "Cooling total load is missing.", "cooling_sizing")
    if cooling.airflow is None:
        add("ERROR", "COOLING_AIRFLOW_MISSING", "Cooling coil airflow is missing.", "cooling_sizing")
    if system.heating.load is None:
        add("WARNING", "HEATING_LOAD_MISSING", "Heating load is missing.", "heating_sizing")
    if system.fan.airflow is None:
        add("WARNING", "FAN_AIRFLOW_MISSING", "Supply fan airflow is missing.", "fan_sizing")
    if system.ventilation.outdoor_airflow is None:
        add("WARNING", "VENTILATION_MISSING", "Outdoor ventilation airflow is missing.", "ventilation")

    values: dict[str, SourceValue | None] = {
        "cooling.total_load": cooling.total_load,
        "cooling.sensible_load": cooling.sensible_load,
        "cooling.latent_load": cooling.latent_load,
        "cooling.total_tons": cooling.total_tons,
        "cooling.airflow": cooling.airflow,
        "cooling.shr": cooling.shr,
        "heating.load": system.heating.load,
        "heating.airflow": system.heating.airflow,
        "fan.airflow": system.fan.airflow,
        "fan.standard_airflow": system.fan.standard_airflow,
        "fan.esp": system.fan.esp,
        "floor_area": system.floor_area,
        "ventilation.outdoor_airflow": system.ventilation.outdoor_airflow,
        "ventilation.cfm_per_area": system.ventilation.cfm_per_area,
        "ventilation.cfm_per_person": system.ventilation.cfm_per_person,
    }
    for name, item in values.items():
        if item is None:
            continue
        expected_unit = _EXPECTED_UNITS[name]
        normalized_unit = _unit_token(item.normalized_unit)
        if not normalized_unit:
            add("ERROR", "UNIT_MISSING", f"{name} has no normalized unit.", item.source_section)
        elif normalized_unit != _unit_token(expected_unit):
            add(
                "ERROR", "UNIT_MISMATCH",
                f"{name} is normalized as {item.normalized_unit!r}; expected {expected_unit!r}.",
                item.source_section,
            )
        source_unit = _unit_token(item.unit)
        allowed_source_units = _ALLOWED_SOURCE_UNITS[name]
        if item.unit is None and name != "cooling.shr":
            add("ERROR", "UNIT_MISSING", f"{name} has no source unit.", item.source_section)
        elif source_unit not in allowed_source_units:
            add("ERROR", "UNIT_MISMATCH", f"{name} has unsupported source unit {item.unit!r}.", item.source_section)
        number = item.normalized_value
        if number is None or not math.isfinite(number):
            add("ERROR", "NON_FINITE_VALUE", f"{name} is missing a finite normalized value.", item.source_section)
        elif number < 0:
            add("ERROR", "NEGATIVE_VALUE", f"{name} must not be negative.", item.source_section)

    for name, item in (
        ("cooling", cooling.airflow),
        ("heating", system.heating.airflow),
        ("fan", system.fan.airflow),
    ):
        if item is not None and item.normalized_value is not None and item.normalized_value == 0:
            add("ERROR", "ZERO_AIRFLOW", f"{name} airflow must be greater than zero.", item.source_section)

    total = cooling.total_load.normalized_value if cooling.total_load else None
    sensible = cooling.sensible_load.normalized_value if cooling.sensible_load else None
    latent = cooling.latent_load.normalized_value if cooling.latent_load else None
    if total is not None and sensible is not None and sensible > total * 1.02:
        add("ERROR", "SENSIBLE_EXCEEDS_TOTAL", "Sensible cooling load exceeds total cooling load.", "cooling_sizing")

    if total is not None and sensible is not None and latent is not None:
        component_sum = sensible + latent
        if component_sum > total * 1.02:
            add("ERROR", "LOAD_COMPONENTS_EXCEED_TOTAL", "Sensible plus latent cooling load exceeds total cooling load.", "cooling_sizing")
        elif total > 0 and abs(component_sum - total) / total > 0.05:
            add("WARNING", "LOAD_COMPONENTS_MISMATCH", "Sensible plus latent cooling load differs from total by more than 5%.", "cooling_sizing")

    if cooling.shr is not None:
        shr = cooling.shr.normalized_value
        if shr is not None and (shr < 0 or shr > 1):
            add("ERROR", "SHR_OUT_OF_RANGE", "Sensible heat ratio must be between 0 and 1 after normalization.", cooling.shr.source_section)
        elif shr is not None and total is not None and sensible is not None and total > 0:
            calculated_shr = sensible / total
            if abs(shr - calculated_shr) > 0.05:
                add("WARNING", "SHR_MISMATCH", "Reported sensible heat ratio differs from sensible/total load by more than 0.05.", cooling.shr.source_section, "cooling_sizing")

    if cooling.airflow is not None and system.fan.airflow is not None:
        coil = cooling.airflow.normalized_value
        fan = system.fan.airflow.normalized_value
        if coil is not None and fan is not None and max(coil, fan) > 0:
            if abs(coil - fan) / max(coil, fan) > 0.05:
                add("WARNING", "AIRFLOW_MISMATCH", "Cooling coil airflow differs from supply fan airflow.", "cooling_sizing", "fan_sizing")

    status = "error" if any(item.severity == "ERROR" for item in issues) else "warning" if issues else "passed"
    return ValidationResult(status=status, issues=issues)
