"""Structured HAP report import primitives."""
from .file_detector import FileDetector, UnsupportedReportError
from .legacy_adapter import to_legacy_system
from .models import (
    AirSystem,
    CoolingData,
    FanData,
    HAPParseResult,
    HAPProject,
    HAPSection,
    HeatingData,
    SourceValue,
    ValidationIssue,
    ValidationResult,
    VentilationData,
)
from .section_splitter import SectionSplitter
from .structured_parser import StructuredHAPParser
from .validation import validate_system

__all__ = [
    "AirSystem", "CoolingData", "FanData", "FileDetector", "HAPParseResult",
    "HAPProject", "HAPSection", "HeatingData", "SectionSplitter", "SourceValue",
    "StructuredHAPParser", "UnsupportedReportError", "ValidationIssue",
    "ValidationResult", "VentilationData", "to_legacy_system", "validate_system",
]
