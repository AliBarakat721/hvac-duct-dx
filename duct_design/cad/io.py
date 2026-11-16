"""DXF import/export pipeline; no DWG execution or external references."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from .models import (
    CadDiagnostic, CadEntity, CadEntityKind, CadExportResult, CadImport,
    EngineeringCategory, LayerMapping,
)

try:
    import ezdxf
except ImportError:  # pragma: no cover - exercised in minimal installations
    ezdxf = None

_UNIT_CODES = {0: "unitless", 1: "inches", 2: "feet", 4: "millimeters", 5: "centimeters", 6: "meters"}
_UNIT_TO_CODE = {value: key for key, value in _UNIT_CODES.items()}
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
    elif kind == "LWPOLYLINE":
        raw = entity.get_points("xyseb")
        geometry = tuple((float(row[0]) * scale, float(row[1]) * scale, float(entity.dxf.elevation or 0.0) * scale) for row in raw)
        bulges = tuple(float(row[4]) for row in raw)
        return CadEntity(handle, CadEntityKind(kind), layer, geometry, None, None, units, bulges, bool(entity.closed))
    elif kind == "POLYLINE":
        vertices = list(entity.vertices)
        geometry = tuple(_point(vertex.dxf.location, scale) for vertex in vertices)
        bulges = tuple(float(getattr(vertex.dxf, "bulge", 0.0) or 0.0) for vertex in vertices)
        return CadEntity(handle, CadEntityKind(kind), layer, geometry, None, None, units, bulges, bool(entity.is_closed))
    elif kind == "ARC":
        geometry = (_point(entity.dxf.center, scale) + (float(entity.dxf.radius) * scale, float(entity.dxf.start_angle), float(entity.dxf.end_angle)),)
    elif kind == "CIRCLE":
        geometry = (_point(entity.dxf.center, scale) + (float(entity.dxf.radius) * scale,),)
    elif kind == "INSERT":
        geometry = (_point(entity.dxf.insert, scale),)
    elif kind in {"TEXT", "MTEXT"}:
        location = entity.dxf.insert
        text = entity.dxf.text if kind == "TEXT" else entity.text
        geometry = (_point(location, scale),)
        return CadEntity(handle, CadEntityKind(kind), layer, geometry, str(text), None, units)
    else:
        geometry = ()
    block_name = str(entity.dxf.name) if kind == "INSERT" else None
    return CadEntity(handle, CadEntityKind(kind) if kind in _SUPPORTED else CadEntityKind.UNSUPPORTED, layer, geometry, None, block_name, units)


def import_dxf(path: str | Path, *, target_units: str = "inches", max_bytes: int = 100_000_000) -> CadImport:
    """Parse model-space DXF without executing scripts, macros, or external references."""
    if ezdxf is None:
        raise RuntimeError("DXF import requires ezdxf; install it with: pip install ezdxf")
    source = Path(path)
    if source.suffix.lower() != ".dxf":
        raise ValueError("Only DXF import is supported; native DWG is not implemented.")
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > max_bytes:
        raise ValueError(f"DXF exceeds the configured safety limit of {max_bytes} bytes.")
    if target_units not in _UNIT_TO_IN:
        raise ValueError(f"Unsupported target units: {target_units}")
    try:
        doc = ezdxf.readfile(source)
    except Exception as exc:
        raise ValueError(f"Could not parse DXF {source}: {exc}") from exc
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    units = _UNIT_CODES.get(code, "unitless")
    scale = 1.0 if units == "unitless" else _UNIT_TO_IN[units] / _UNIT_TO_IN[target_units]
    entities: list[CadEntity] = []
    diagnostics: list[CadDiagnostic] = []
    for entity in doc.modelspace():
        try:
            item = _entity(entity, scale, units)
            entities.append(item)
            if item.kind is CadEntityKind.UNSUPPORTED:
                diagnostics.append(CadDiagnostic("WARNING", "unsupported_entity", f"Entity type {entity.dxftype()} was preserved as unsupported.", item.handle))
            if item.kind is CadEntityKind.INSERT:
                diagnostics.append(CadDiagnostic("INFO", "block_reference_only", f"INSERT {item.block_name} was preserved as a reference; block geometry was not converted to engineering geometry.", item.handle))
        except (AttributeError, TypeError, ValueError) as exc:
            diagnostics.append(CadDiagnostic("ERROR", "invalid_entity", f"Entity {entity.dxf.handle} could not be extracted: {exc}", str(entity.dxf.handle)))
    if not entities:
        diagnostics.append(CadDiagnostic("WARNING", "empty_drawing", "DXF contains no model-space entities."))
    layers = tuple(sorted({layer.dxf.name for layer in doc.layers} | {item.layer for item in entities}))
    return CadImport(str(source), str(doc.dxfversion), units, scale, tuple(entities), layers, tuple(diagnostics), target_units)


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


def polyline_length(entity: CadEntity) -> float:
    """Return engineering length, including circular bulge arcs and closure."""
    if entity.kind not in {CadEntityKind.LINE, CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE}:
        return 0.0
    points = entity.geometry
    if len(points) < 2:
        return 0.0
    count = len(points) if entity.closed else len(points) - 1
    total = 0.0
    for index in range(count):
        start, end = points[index], points[(index + 1) % len(points)]
        chord = math.dist(start[:3], end[:3])
        bulge = entity.bulges[index] if index < len(entity.bulges) else 0.0
        if abs(bulge) <= 1e-15 or chord == 0.0:
            total += chord
            continue
        angle = 4.0 * math.atan(bulge)
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        total += abs(radius * angle)
    return total


def geometry_diagnostics(cad: CadImport, *, tolerance: float = 1e-6) -> tuple[CadDiagnostic, ...]:
    diagnostics = list(cad.diagnostics)
    if not math.isfinite(tolerance) or tolerance <= 0:
        return (*diagnostics, CadDiagnostic("ERROR", "invalid_tolerance", "Geometry tolerance must be finite and positive."))
    seen: dict[tuple[tuple[float, ...], ...], str] = {}
    for entity in cad.entities:
        if entity.kind in {CadEntityKind.LINE, CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE}:
            if len(entity.geometry) < 2:
                diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", "Duct candidate has fewer than two vertices.", entity.handle))
                continue
            for start, end in zip(entity.geometry, entity.geometry[1:]):
                if math.dist(start[:3], end[:3]) <= tolerance:
                    diagnostics.append(CadDiagnostic("ERROR", "zero_length_geometry", "Duct candidate contains a zero-length segment.", entity.handle))
            key = tuple(tuple(round(value / tolerance) for value in point) for point in entity.geometry)
            if key in seen:
                diagnostics.append(CadDiagnostic("WARNING", "duplicate_geometry", f"Geometry duplicates entity {seen[key]}.", entity.handle))
            else:
                seen[key] = entity.handle
    return tuple(diagnostics)


def export_dxf_report(path: str | Path, cad: CadImport, *, layer_mapping: LayerMapping | None = None) -> CadExportResult:
    """Export supported geometry and explicitly report representation loss."""
    if ezdxf is None:
        raise RuntimeError("DXF export requires ezdxf; install it with: pip install ezdxf")
    destination = Path(path)
    if destination.resolve() == Path(cad.path).resolve():
        raise ValueError("DXF export must write to a separate output file.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = _UNIT_TO_CODE.get(cad.target_units, 0)
    msp = doc.modelspace()
    diagnostics: list[CadDiagnostic] = []
    for entity in cad.entities:
        category = layer_mapping.category_for(entity.layer).value if layer_mapping and layer_mapping.category_for(entity.layer) else entity.layer
        if category not in doc.layers:
            doc.layers.add(category)
        attrs = {"layer": category}
        if entity.kind is CadEntityKind.LINE:
            msp.add_line(entity.geometry[0], entity.geometry[1], dxfattribs=attrs)
        elif entity.kind in {CadEntityKind.LWPOLYLINE, CadEntityKind.POLYLINE}:
            if any(len(point) > 2 and abs(point[2]) > 1e-12 for point in entity.geometry):
                msp.add_polyline3d(entity.geometry, close=entity.closed, dxfattribs=attrs)
                if any(abs(bulge) > 1e-15 for bulge in entity.bulges):
                    diagnostics.append(CadDiagnostic("WARNING", "bulge_not_preserved_3d", "3D polyline exported without bulge arcs.", entity.handle))
            else:
                rows = [tuple(point[:2]) + (0.0, 0.0, entity.bulges[index] if index < len(entity.bulges) else 0.0) for index, point in enumerate(entity.geometry)]
                msp.add_lwpolyline(rows, format="xyseb", close=entity.closed, dxfattribs=attrs)
        elif entity.kind is CadEntityKind.CIRCLE:
            center = entity.geometry[0]
            msp.add_circle(center[:3], center[3], dxfattribs=attrs)
        elif entity.kind is CadEntityKind.ARC:
            center = entity.geometry[0]
            msp.add_arc(center[:3], center[3], center[4], center[5], dxfattribs=attrs)
        elif entity.kind in {CadEntityKind.TEXT, CadEntityKind.MTEXT}:
            msp.add_mtext(entity.text or "", dxfattribs={**attrs, "insert": entity.geometry[0]})
        elif entity.kind is CadEntityKind.INSERT:
            if entity.block_name not in doc.blocks:
                doc.blocks.new(entity.block_name or f"BLOCK_{entity.handle}")
            msp.add_blockref(entity.block_name or f"BLOCK_{entity.handle}", entity.geometry[0], dxfattribs=attrs)
            diagnostics.append(CadDiagnostic("INFO", "block_reference_only", "INSERT block reference exported; original block contents are not reproduced.", entity.handle))
        else:
            diagnostics.append(CadDiagnostic("WARNING", "entity_omitted", f"Unsupported entity {entity.kind} was omitted from export.", entity.handle))
    doc.saveas(destination)
    return CadExportResult(str(destination), tuple(diagnostics))


def export_dxf(path: str | Path, cad: CadImport, *, layer_mapping: LayerMapping | None = None) -> Path:
    """Backward-compatible export wrapper; use :func:`export_dxf_report` for diagnostics."""
    return Path(export_dxf_report(path, cad, layer_mapping=layer_mapping).path)
