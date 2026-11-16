"""Small, dependency-free models for the first structured HAP import slice."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceValue:
    value: Any
    unit: str | None
    normalized_value: float | None
    normalized_unit: str | None
    source_section: str
    source_line: int | None
    extraction_method: str = "key_value"
    confidence: float = 1.0
    state: str = "extracted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoolingData:
    total_load: SourceValue | None = None
    sensible_load: SourceValue | None = None
    total_tons: SourceValue | None = None
    airflow: SourceValue | None = None
    shr: SourceValue | None = None
    latent_load: SourceValue | None = None


@dataclass
class HeatingData:
    load: SourceValue | None = None
    airflow: SourceValue | None = None


@dataclass
class FanData:
    airflow: SourceValue | None = None
    standard_airflow: SourceValue | None = None
    esp: SourceValue | None = None


@dataclass
class VentilationData:
    outdoor_airflow: SourceValue | None = None
    cfm_per_area: SourceValue | None = None
    cfm_per_person: SourceValue | None = None


@dataclass
class LoadData:
    total: SourceValue | None = None
    sensible: SourceValue | None = None
    airflow: SourceValue | None = None


@dataclass
class Space:
    space_id: str
    space_name: str
    zone_id: str
    multiplier: SourceValue | None = None
    area: SourceValue | None = None
    cooling_load: LoadData = field(default_factory=LoadData)
    heating_load: LoadData = field(default_factory=LoadData)
    ventilation: VentilationData = field(default_factory=VentilationData)


@dataclass
class Zone:
    zone_id: str
    zone_name: str
    space_ids: list[str] = field(default_factory=list)
    area: SourceValue | None = None
    cooling_load: LoadData = field(default_factory=LoadData)
    heating_load: LoadData = field(default_factory=LoadData)
    system_id: str | None = None


@dataclass
class AirSystem:
    system_id: str
    system_name: str
    system_type: str | None = None
    floor_area: SourceValue | None = None
    location: str | None = None
    cooling: CoolingData = field(default_factory=CoolingData)
    heating: HeatingData = field(default_factory=HeatingData)
    fan: FanData = field(default_factory=FanData)
    ventilation: VentilationData = field(default_factory=VentilationData)
    zone_ids: list[str] = field(default_factory=list)


@dataclass
class HAPProject:
    project_name: str | None = None
    location: str | None = None
    systems: list[AirSystem] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    spaces: list[Space] = field(default_factory=list)


@dataclass
class HAPSection:
    name: str
    title: str
    start_line: int
    end_line: int
    text: str


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    source_sections: list[str] = field(default_factory=list)
    system_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    status: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == "WARNING"]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": [item.to_dict() for item in self.issues]}


@dataclass
class HAPParseResult:
    project: HAPProject
    sections: dict[str, HAPSection]
    validation: ValidationResult

    @property
    def success(self) -> bool:
        return not self.validation.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": asdict(self.project),
            "sections": {key: asdict(value) for key, value in self.sections.items()},
            "validation": self.validation.to_dict(),
            "success": self.success,
        }
