"""CAD interchange models with stable source-entity traceability."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CadEntityKind(StrEnum):
    LINE = "LINE"
    LWPOLYLINE = "LWPOLYLINE"
    POLYLINE = "POLYLINE"
    ARC = "ARC"
    CIRCLE = "CIRCLE"
    INSERT = "INSERT"
    TEXT = "TEXT"
    MTEXT = "MTEXT"
    UNSUPPORTED = "UNSUPPORTED"


class EngineeringCategory(StrEnum):
    SUPPLY_DUCT = "supply_duct"
    RETURN_DUCT = "return_duct"
    EXHAUST_DUCT = "exhaust_duct"
    DUCT_CENTERLINE = "duct_centerline"
    DUCT_OUTLINE = "duct_outline"
    DIFFUSER = "diffuser"
    GRILLE = "grille"
    DAMPER = "damper"
    ELBOW = "elbow"
    TEE = "tee"
    WYE = "wye"
    EQUIPMENT = "equipment"
    ANNOTATION = "annotation"


@dataclass(frozen=True)
class CadDiagnostic:
    severity: str
    code: str
    message: str
    handle: str | None = None


@dataclass(frozen=True)
class CadEntity:
    handle: str
    kind: CadEntityKind
    layer: str
    geometry: tuple[tuple[float, ...], ...] = ()
    text: str | None = None
    block_name: str | None = None
    source_units: str = "unitless"
    bulges: tuple[float, ...] = ()
    closed: bool = False


@dataclass(frozen=True)
class CadImport:
    path: str
    version: str
    units: str
    scale_to_target: float
    entities: tuple[CadEntity, ...]
    layers: tuple[str, ...]
    diagnostics: tuple[CadDiagnostic, ...] = ()
    target_units: str = "inches"


@dataclass(frozen=True)
class CadExportResult:
    path: str
    diagnostics: tuple[CadDiagnostic, ...] = ()


@dataclass(frozen=True)
class LayerMapping:
    layer_to_category: dict[str, EngineeringCategory] = field(default_factory=dict)

    def category_for(self, layer: str) -> EngineeringCategory | None:
        return self.layer_to_category.get(layer)


@dataclass(frozen=True)
class CadTerminalMapping:
    handle: str
    terminal_id: str
    airflow_cfm: float
    node_coordinate: tuple[float, float]
    category: EngineeringCategory


@dataclass(frozen=True)
class CadNetworkMapping:
    network_id: str
    network_type: str
    source_or_sink_coordinate: tuple[float, float]
    segment_handles: tuple[str, ...]
    terminal_mappings: tuple[CadTerminalMapping, ...]
    tolerance: float = 1e-6
