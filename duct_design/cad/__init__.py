"""DXF inspection, validated mapping, and engineering-network conversion."""
from .io import export_dxf, geometry_diagnostics, import_dxf, suggest_layer_mapping
from .mapping import build_network_from_cad
from .models import (
    CadDiagnostic,
    CadEntity,
    CadEntityKind,
    CadImport,
    CadNetworkMapping,
    CadTerminalMapping,
    EngineeringCategory,
    LayerMapping,
)

__all__ = [
    "CadDiagnostic", "CadEntity", "CadEntityKind", "CadImport", "CadNetworkMapping",
    "CadTerminalMapping", "EngineeringCategory", "LayerMapping", "build_network_from_cad",
    "export_dxf", "geometry_diagnostics", "import_dxf", "suggest_layer_mapping",
]
