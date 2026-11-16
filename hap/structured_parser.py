"""Structured parser for the first system-level HAP report slice."""
from __future__ import annotations

import re
from pathlib import Path

from extractors import PDFNativeText, RTFNativeText

from .file_detector import FileDetector
from .models import (
    AirSystem,
    CoolingData,
    FanData,
    HAPParseResult,
    HAPProject,
    HeatingData,
    SourceValue,
    VentilationData,
)
from .section_splitter import SectionSplitter
from .validation import validate_system

_NUMBER = r"[+-]?(?:\d+(?:[,.]\d+)?|\.\d+)"


class StructuredHAPParser:
    def __init__(self, detector=None, splitter=None):
        self.detector = detector or FileDetector()
        self.splitter = splitter or SectionSplitter()

    def parse(self, file_path: str | Path) -> HAPParseResult:
        report_format = self.detector.detect(file_path)
        if report_format == "rtf":
            text = RTFNativeText().extract(file_path)
        elif report_format == "pdf":
            text = PDFNativeText().extract(file_path)
        else:
            text = Path(file_path).read_text(encoding="utf-8")
        return self.parse_text(text)

    def parse_text(self, text: str) -> HAPParseResult:
        sections = self.splitter.split(text)
        info = sections.get("air_system_information")
        system_name = self._text_value(info, r"Air\s+System\s+Name", r"([A-Za-z0-9][A-Za-z0-9_-]*)") or "Unnamed"
        system_type = self._text_value(info, r"Air\s+System\s+Type", r"([A-Za-z0-9][A-Za-z0-9_-]*)")
        location = self._text_value(info, r"Location", r"([A-Za-z][A-Za-z0-9 ,_-]+?)(?=\s{2,}[A-Z][A-Za-z ]+\s|$)")
        floor_area = self._measurement(info, r"Floor\s+Area", r"ft(?:²|2|\^2)?|m(?:²|2|\^2)?", "area")
        if floor_area and floor_area.unit and floor_area.unit.lower().startswith("m"):
            floor_area = self._replace_normalized(floor_area, floor_area.normalized_value * 10.7639, "ft²")

        cooling_section = sections.get("cooling_sizing")
        heating_section = sections.get("heating_sizing")
        fan_section = sections.get("fan_sizing")
        ventilation_section = sections.get("ventilation")
        cooling = CoolingData(
            total_load=self._measurement(cooling_section, r"Total\s+coil\s+load", r"MBH|kW|Btu\s*/?\s*h(?:r)?", "load"),
            sensible_load=self._measurement(cooling_section, r"Sensible\s+coil\s+load", r"MBH|kW|Btu\s*/?\s*h(?:r)?", "load"),
            total_tons=self._measurement(cooling_section, r"Total\s+coil\s+load", r"Tons?", "tons"),
            airflow=self._measurement(cooling_section, r"Coil\s+CFM\s+at", r"CFM", "airflow"),
            shr=self._measurement(cooling_section, r"Sensible\s+heat\s+ratio", r"%", "shr", unit_optional=True),
        )
        heating = HeatingData(
            load=self._measurement(heating_section, r"Max\s+coil\s+load", r"MBH|kW|Btu\s*/?\s*h(?:r)?", "load"),
            airflow=self._measurement(heating_section, r"Coil\s+CFM\s+at", r"CFM", "airflow"),
        )
        fan = FanData(
            airflow=self._measurement(fan_section, r"Actual\s+max\s+CFM", r"CFM", "airflow"),
            standard_airflow=self._measurement(fan_section, r"Standard\s+CFM", r"CFM", "airflow"),
            esp=self._measurement(fan_section, r"Fan\s+static", r"in\s*wg|in\.\s*wg|Pa", "pressure"),
        )
        ventilation = VentilationData(
            outdoor_airflow=self._measurement(ventilation_section, r"Design\s+airflow\s+CFM", r"CFM", "airflow"),
            cfm_per_area=self._measurement(ventilation_section, r"CFM/ft²|CFM/ft2", r"CFM/ft²|CFM/ft2", "ratio"),
            cfm_per_person=self._measurement(ventilation_section, r"CFM/person", r"CFM/person", "ratio"),
        )
        system = AirSystem(
            system_id=system_name,
            system_name=system_name,
            system_type=system_type,
            floor_area=floor_area,
            location=location,
            cooling=cooling,
            heating=heating,
            fan=fan,
            ventilation=ventilation,
        )
        validation = validate_system(system)
        return HAPParseResult(
            project=HAPProject(project_name=system_name, location=location, systems=[system]),
            sections=sections,
            validation=validation,
        )

    @staticmethod
    def _text_value(section, label: str, value_pattern: str = r"([A-Za-z][A-Za-z0-9 _,-]*?)") -> str | None:
        if section is None:
            return None
        lines = section.text.splitlines()
        if label == r"Location":
            for line in lines:
                match = re.search(r"Location\s+([A-Za-z][A-Za-z0-9 ,_-]+)", line, re.I)
                if match:
                    return match.group(1).strip()
        pattern = re.compile(label + r"\s*[^\n]*?\s{1,}" + value_pattern, re.I)
        for line in lines:
            match = pattern.search(line)
            if match:
                value = match.group(1).strip(" \t\u00a0:-")
                if value and value.lower() not in {"data", "information"}:
                    return value
        joined = re.sub(r"\s+", " ", section.text)
        match = re.search(label + r"\s+" + value_pattern, joined, re.I)
        return match.group(1).strip() if match else None

    @classmethod
    def _measurement(cls, section, label: str, unit_pattern: str, quantity: str, *, unit_optional: bool = False) -> SourceValue | None:
        if section is None:
            return None
        lines = section.text.splitlines()
        joined = "\n".join(lines)
        unit_part = rf"(?:\s*(?P<unit>{unit_pattern}))?" if unit_optional else rf"\s*(?P<unit>{unit_pattern})"
        pattern = re.compile(rf"(?P<label>{label}).{{0,100}}?(?P<number>{_NUMBER}){unit_part}", re.I | re.S)
        match = pattern.search(joined)
        if not match:
            return None
        raw = match.group("number").replace(",", ".")
        try:
            original = float(raw)
        except ValueError:
            return None
        unit = (match.groupdict().get("unit") or "").strip() or None
        normalized, normalized_unit = cls._normalize(original, unit, quantity)
        line = joined[:match.start("number")].count("\n") + section.start_line
        return SourceValue(original, unit, normalized, normalized_unit, section.name, line)

    @staticmethod
    def _normalize(value: float, unit: str | None, quantity: str) -> tuple[float, str]:
        unit_lower = (unit or "").lower().replace(" ", "")
        if quantity == "load":
            if "mbh" in unit_lower:
                return value * 1000.0, "Btu/hr"
            if "kw" in unit_lower:
                return value * 3412.142, "Btu/hr"
            if "ton" in unit_lower:
                return value * 12000.0, "Btu/hr"
            return value, "Btu/hr"
        if quantity == "tons":
            return value * 12000.0, "Btu/hr"
        if quantity == "pressure":
            if "pa" in unit_lower:
                return value / 249.0889, "in.wg"
            return value, "in.wg"
        if quantity == "area" and unit_lower.startswith("m"):
            return value, "m²"
        if quantity == "shr":
            return value, "ratio"
        if quantity == "ratio":
            return value, unit or "ratio"
        return value, "CFM" if quantity == "airflow" else (unit or "")

    @staticmethod
    def _replace_normalized(item: SourceValue, value: float, unit: str) -> SourceValue:
        return SourceValue(item.value, item.unit, value, unit, item.source_section, item.source_line, item.extraction_method, item.confidence, item.state)
