"""DXF import/export pipeline; no DWG execution or external references."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from .models import CadDiagnostic, CadEntity, CadEntityKind, CadImport, EngineeringCategory, LayerMapping

try:
    import ezdxf
except ImportError:  # pragma: no cover - exercised in minimal installations
    ezdxf = None

_UNIT_CODES = {0: "unitless", 1: "inches", 2: "feet", 4: "millimeters", 5: "centimeters", 6: "meters"}
_UNIT_TO_IN = {"unitless": 1.0, "inches": 1.0, "feet": 12.0, "millimeters": 1 / 25.4, "centimeters": 10 / 25.4, "meters": 1000 / 25.4}
_SUPPORTED = {kind.value for kind in CadEntityKind if kind is not CadEntityKind.UNSUPPORTED}


def _point(value: Iterable[float], scale: float) -> tuple[float, ...]:
    return tuple(float(x) * scale for x in value)


def _entity(entity, scale: float, units: str) -> CadEntity:
    kind = entity.dxftype()
    layer = str(entity.dxf.layer)
    handle = str(entity.dxf.handle)
    if kind == "LINE":
        geometry = (_point(entity.dxf.start, scale), _point(entity.dxf.end, scale))
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        geometry = tuple(_point(vertex.dxf.location if kind == "POLYLINE" else vertex, scale) for vertex in entity.vertices())
    elif kind == "ARC":
        geometry = (_point(entity.dxf.center, scale) + (float(entity.dxf.radius) * scale, float(entity.dxf.start_angle), float(entity.dxf.end_angle)),)
    elif kind == "CIRCLE":
        geometry = (_point(entity.dxf.center, scale) + (float(entity.dxf.radius) * scale,),)
    elif kind == "INSERT":
        geometry = (_point(entity.dxf.insert, scale),)
    elif kind in {"TEXT", "MTEXT"}:
        location = entity.dxf.insert if kind == "TEXT" else entity.dxf.insert
        text = entity.dxf.text if kind == "TEXT" else entity.text
        geometry = (_point(location, scale),)
        return CadEntity(handle, CadEntityKind(kind), layer, geometry, str(text), None, units)
    else:
        geometry = ()
    return CadEntity(handle, CadEntityKind(kind) if kind in _SUPPORTED else CadEntityKind.UNSUPPORTED, layer, geometry, None, getattr(entity.dxf, "name", None), units)


def import_dxf(path: str | Path, *, target_units: str = "inches", max_bytes: int = 100_000_000) -> CadImport:
    """Parse a DXF without executing scripts, macros, or external references."""
    if ezdxf is None:
        raise RuntimeError("DXF import requires ezdxf; install it with: pip install ezdxf")
    source = Path(path)
    if source.suffix.lower() != ".dxf":
        raise ValueError("Only DXF import is supported; native DWG is not implemented.")
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > max_bytes:
        raise ValueError(f"DXF exceeds the configured safety limit of {max_bytes} bytes.")
    try:
        doc = ezdxf.readfile(source)
    except Exception as exc:
        raise ValueError(f"Could not parse DXF {source}: {exc}") from exc
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    units = _UNIT_CODES.get(code, "unitless")
    if target_units not in _UNIT_TO_IN:
        raise ValueError(f"Unsupported target units: {target_units}")
    if units == "unitless":
        scale = 1.0
    else:
        scale = _UNIT_TO_IN[units] / _UNIT_TO_IN[target_units]
    entities: list[CadEntity] = []
    diagnostics: list[CadDiagnostic] = []
    for entity in doc.modelspace():
        try:
            item = _entity(entity, scale, units)
            entities.append(item)
            if item.kind is CadEntityKind.UNSUPPORTED:
                diagnostics.append(CadDiagnostic("WARNING", "unsupported_entity", f"Entity type {entity.dxftype()} was preserved as unsupported.", item.handle))
        except (AttributeError, TypeError, ValueError) as exc:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_entity", f"Entity {entity.dxf.handle} could not be extracted: {exc}", str(entity.dxf.handle)))
    if not entities:
        diagnostics.append(CadDiagnostic("WARNING", "empty_drawing", "DXF contains no model-space entities."))
    layers = tuple(sorted({layer.dxf.name for layer in doc.layers} | {item.layer for item in entities}))
    return CadImport(str(source), str(doc.dxfversion), units, scale, tuple(entities), layers, tuple(diagnostics))


def suggest_layer_mapping(layers: Iterable[str]) -> LayerMapping:
    """Offer conservative name suggestions; callers must validate/confirm them."""
    mapping: dict[str, EngineeringCategory] = {}
    for layer in layers:
        name = layer.lower().replace("_", " ").replace("-", " ")
        if "return" in name and "duct" in name:
            mapping[layer] = EngineeringCategory.RETURN_DUCT
        elif "supply" in name and "duct" in name:
            mapping[layer] = EngineeringCategory.SUPPLY_DUCT
        elif "center" in name and "duct" in name:
            mapping[layer] = EngineeringCategory.DUCT_CENTERLINE
        elif "diff" in name:
            mapping[layer] = EngineeringCategory.DIFFUSER
        elif "grille" in name or "grill" in name:
            mapping[layer] = EngineeringCategory.GRILLE
        elif "annot" in name or "text" in name:
            mapping[layer] = EngineeringCategory.ANNOTATION
    return LayerMapping(mapping)


def geometry_diagnostics(cad: CadImport, *, tolerance: float = 1e-6) -> tuple[CadDiagnostic, ...]:
    diagnostics = list(cad.diagnostics)
    seen: dict[tuple[tuple[float, ...], ...], str] = {}
    for entity in cad.entities:
        if entity.kind in {CadEntityKind.LINE, CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE}:
            if len(entity.geometry) < 2:
                diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", "Duct candidate has fewer than two vertices.", entity.handle))
                continue
            for start, end in zip(entity.geometry, entity.geometry[1:]):
                if math.dist(start[:2], end[:2]) <= tolerance:
                    diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", "Duct candidate contains a zero-length segment.", entity.handle))
            key = tuple(tuple(round(value / tolerance) for value in point) for point in entity.geometry)
            if key in seen:
                diagnostics.append(CadDiagnostic("WARNING", "duplicate_geometry", f"Geometry duplicates entity {seen[key]}.", entity.handle))
            else:
                seen[key] = entity.handle
    return tuple(diagnostics)


def export_dxf(path: str | Path, cad: CadImport, *, layer_mapping: LayerMapping | None = None) -> Path:
    """Export traceable imported geometry to a separate DXF file."""
    if ezdxf is None:
        raise RuntimeError("DXF export requires ezdxf; install it with: pip install ezdxf")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    for entity in cad.entities:
        category = layer_mapping.category_for(entity.layer).value if layer_mapping and layer_mapping.category_for(entity.layer) else entity.layer
        if category not in doc.layers:
            doc.layers.add(category)
        if entity.kind is CadEntityKind.LINE:
            msp.add_line(entity.geometry[0][:3], entity.geometry[1][:3], dxfattribs={"layer": category})
        elif entity.kind in {CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE}:
            msp.add_lwpolyline([point[:2] for point in entity.geometry], dxfattribs={"layer": category})
        elif entity.kind is CadEntityKind.CIRCLE:
            center = entity.geometry[0]
            msp.add_circle(center[:3], center[3], dxfattribs={"layer": category})
        elif entity.kind is CadEntityKind.ARC:
            center = entity.geometry[0]
            msp.add_arc(center[:3], center[3], center[4], center[5], dxfattribs={"layer": category})
        elif entity.kind in {CadEntityKind.TEXT, CadEntityKind.MTEXT}:
            msp.add_mtext(entity.text or "", dxfattribs={"layer": category, "insert": entity.geometry[0][:3]})
    doc.saveas(destination)
    return destination
