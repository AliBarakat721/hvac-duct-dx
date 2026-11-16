"""Engineering validation for the first structured HAP slice."""
from __future__ import annotations

from .models import AirSystem, ValidationIssue, ValidationResult


def validate_system(system: AirSystem) -> ValidationResult:
    issues: list[ValidationIssue] = []
    cooling = system.cooling
    if cooling.total_load is None:
        issues.append(ValidationIssue("ERROR", "COOLING_LOAD_MISSING", "Cooling total load is missing.", ["cooling_sizing"]))
    if cooling.airflow is None:
        issues.append(ValidationIssue("ERROR", "COOLING_AIRFLOW_MISSING", "Cooling coil airflow is missing.", ["cooling_sizing"]))
    if system.heating.load is None:
        issues.append(ValidationIssue("WARNING", "HEATING_LOAD_MISSING", "Heating load is missing.", ["heating_sizing"]))
    if system.fan.airflow is None:
        issues.append(ValidationIssue("WARNING", "FAN_AIRFLOW_MISSING", "Supply fan airflow is missing.", ["fan_sizing"]))
    if system.ventilation.outdoor_airflow is None:
        issues.append(ValidationIssue("WARNING", "VENTILATION_MISSING", "Outdoor ventilation airflow is missing.", ["ventilation"]))

    if cooling.total_load and cooling.sensible_load:
        total = cooling.total_load.normalized_value
        sensible = cooling.sensible_load.normalized_value
        if total is not None and sensible is not None and sensible > total * 1.02:
            issues.append(ValidationIssue("ERROR", "SENSIBLE_EXCEEDS_TOTAL", "Sensible cooling load exceeds total cooling load.", ["cooling_sizing"]))
    if cooling.airflow and system.fan.airflow:
        coil = cooling.airflow.normalized_value
        fan = system.fan.airflow.normalized_value
        if coil and fan and abs(coil - fan) / max(coil, fan) > 0.05:
            issues.append(ValidationIssue("WARNING", "AIRFLOW_MISMATCH", "Cooling coil airflow differs from supply fan airflow.", ["cooling_sizing", "fan_sizing"]))
    status = "error" if any(item.severity == "ERROR" for item in issues) else "warning" if issues else "passed"
    return ValidationResult(status=status, issues=issues)
