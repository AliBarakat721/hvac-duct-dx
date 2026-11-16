"""DXF inspection, validated mapping, and engineering-network conversion."""
from .io import export_dxf, export_dxf_report, geometry_diagnostics, import_dxf, polyline_length, suggest_layer_mapping
from .mapping import build_network_from_cad
from .models import (
    CadDiagnostic,
    CadEntity,
    CadEntityKind,
    CadExportResult,
    CadImport,
    CadNetworkMapping,
    CadTerminalMapping,
    EngineeringCategory,
    LayerMapping,
)

__all__ = [
    "CadDiagnostic", "CadEntity", "CadEntityKind", "CadExportResult", "CadImport", "CadNetworkMapping",
    "CadTerminalMapping", "EngineeringCategory", "LayerMapping", "build_network_from_cad",
    "export_dxf", "export_dxf_report", "geometry_diagnostics", "import_dxf", "polyline_length",
    "suggest_layer_mapping",
]
