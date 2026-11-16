"""Experimental duct-design safety helpers; not a complete sizing engine."""

from .duct_design_page_fixed import (
    DuctDesignInputError,
    DuctDesignPageFixed,
    ExportResult,
    append_note_once,
    edge_label_position,
    edge_length,
    output_path,
    safe_export,
    validate_height_limits,
)

__all__ = [
    "DuctDesignInputError",
    "DuctDesignPageFixed",
    "ExportResult",
    "append_note_once",
    "edge_label_position",
    "edge_length",
    "output_path",
    "safe_export",
    "validate_height_limits",
]
