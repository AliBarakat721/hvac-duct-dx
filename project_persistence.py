"""Versioned, JSON-based persistence for Duct DX engineering projects.

Schema ``duct-dx-project`` versions 1-5 store equipment cart inputs, source
system records, UI calculation settings, and room/layout state. Cached
diffuser selections are explicitly stored as calculation snapshots; room
coordinates remain the authoritative editable layout. Unknown fields are
tolerated; unknown fields in the runtime equipment and room records are
preserved, while unknown project/container fields are ignored by the UI.
Non-finite floats use a tagged object so strict JSON remains portable without
losing invalid coordinates.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from airflow_utils import normalize_airflow_fields

PROJECT_FORMAT = "duct-dx-project"
PROJECT_SCHEMA_VERSION = 5
NONFINITE_TAG = "$ductdx_nonfinite_float"
PROJECT_UNITS = {
    "room_geometry": "m",
    "airflow": "CFM",
    "thermal_load": "Btu/hr",
    "room_dimension_inputs": "ft",
    "outdoor_temperature": "degC",
}


class ProjectFileError(ValueError):
    """Invalid, unsupported, or unsafe project file."""


def _encode_special_floats(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        label = "NaN" if math.isnan(value) else (
            "Infinity" if value > 0 else "-Infinity")
        return {NONFINITE_TAG: label}
    if isinstance(value, dict):
        return {str(key): _encode_special_floats(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_special_floats(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ProjectFileError(
        f"Project contains unsupported value type {type(value).__name__}.")


def _decode_special_floats(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {NONFINITE_TAG}:
            labels = {
                "NaN": float("nan"),
                "Infinity": float("inf"),
                "-Infinity": float("-inf"),
            }
            label = value[NONFINITE_TAG]
            if label not in labels:
                raise ProjectFileError(
                    f"Invalid non-finite number marker: {label!r}.")
            return labels[label]
        return {key: _decode_special_floats(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_special_floats(item) for item in value]
    return value


def _reject_constant(value: str):
    raise ProjectFileError(
        f"Non-standard JSON numeric constant {value!r} is not permitted.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProjectFileError(f"Duplicate JSON object key {key!r}.")
        result[key] = value
    return result


def _require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProjectFileError(f"{label} must be a JSON object.")
    return value


def _validate_room(room: Any, label: str) -> None:
    if room is None:
        return
    room = _require_object(room, label)
    has_walls = isinstance(room.get("walls"), list)
    has_vertices = isinstance(room.get("vertices"), list)
    if not (has_walls or has_vertices):
        raise ProjectFileError(
            f"{label} must contain a 'walls' or 'vertices' array.")
    origin = room.get("origin", (0.0, 0.0))
    if (not isinstance(origin, (list, tuple)) or len(origin) != 2
            or any(not isinstance(value, (int, float))
                   or isinstance(value, bool)
                   or not math.isfinite(float(value))
                   for value in origin)):
        raise ProjectFileError(
            f"{label}.origin must contain two finite numeric coordinates.")
    next_diffuser_tag_number = room.get("next_diffuser_tag_number", 1)
    if (not isinstance(next_diffuser_tag_number, int)
            or isinstance(next_diffuser_tag_number, bool)
            or next_diffuser_tag_number < 1):
        raise ProjectFileError(
            f"{label}.next_diffuser_tag_number must be a positive integer.")
    height = room.get("height", room.get("height_m"))
    if (not isinstance(height, (int, float)) or isinstance(height, bool)):
        raise ProjectFileError(f"{label} requires a numeric room height.")
    if has_walls:
        for index, wall in enumerate(room["walls"]):
            wall = _require_object(wall, f"{label}.walls[{index}]")
            if not all(isinstance(wall.get(key), (int, float))
                       and not isinstance(wall.get(key), bool)
                       for key in ("length", "angle")):
                raise ProjectFileError(
                    f"{label}.walls[{index}] requires numeric length and angle.")
    if has_vertices:
        for index, point in enumerate(room["vertices"]):
            if (not isinstance(point, (list, tuple)) or len(point) != 2
                    or any(not isinstance(value, (int, float))
                           or isinstance(value, bool) for value in point)):
                raise ProjectFileError(
                    f"{label}.vertices[{index}] must contain two numbers.")
    diffusers = room.get("diffusers", [])
    if not isinstance(diffusers, list):
        raise ProjectFileError(f"{label}.diffusers must be an array.")
    diffuser_ids = set()
    for index, diffuser in enumerate(diffusers):
        diffuser = _require_object(diffuser, f"{label}.diffusers[{index}]")
        if "x_m" not in diffuser or "y_m" not in diffuser:
            raise ProjectFileError(
                f"{label}.diffusers[{index}] requires x_m and y_m.")
        if any(not isinstance(diffuser[key], (int, float))
               or isinstance(diffuser[key], bool) for key in ("x_m", "y_m")):
            raise ProjectFileError(
                f"{label}.diffusers[{index}] coordinates must be numbers.")
        if "tag" in diffuser and not isinstance(diffuser["tag"], str):
            raise ProjectFileError(
                f"{label}.diffusers[{index}].tag must be a string.")
        diffuser_id = diffuser.get("id")
        if diffuser_id is not None:
            if not isinstance(diffuser_id, str) or not diffuser_id:
                raise ProjectFileError(
                    f"{label}.diffusers[{index}].id must be a non-empty string.")
            if diffuser_id in diffuser_ids:
                raise ProjectFileError(
                    f"{label}.diffusers contains duplicate diffuser id "
                    f"{diffuser_id!r}.")
            diffuser_ids.add(diffuser_id)
    stale = room.get("layout_stale", False)
    if not isinstance(stale, bool):
        raise ProjectFileError(f"{label}.layout_stale must be a boolean.")
    errors = room.get("layout_validation_errors", [])
    if (not isinstance(errors, list)
            or any(not isinstance(error, str) for error in errors)):
        raise ProjectFileError(
            f"{label}.layout_validation_errors must be an array of strings.")


def validate_project_document(document: Any) -> dict:
    """Validate required project structure and return a detached copy.

    Unknown optional fields are accepted. Runtime equipment and room objects
    retain their own additional fields; unknown container fields are ignored
    by the application.
    """
    document = _require_object(document, "Project document")
    if document.get("format") != PROJECT_FORMAT:
        raise ProjectFileError(
            f"Unsupported project format {document.get('format')!r}.")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProjectFileError("schema_version must be an integer.")
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectFileError(
            f"Project schema version {version} is newer than supported "
            f"version {PROJECT_SCHEMA_VERSION}.")
    if version < 1:
        raise ProjectFileError(f"Unsupported project schema version {version}.")

    metadata = _require_object(document.get("metadata"), "metadata")
    if not isinstance(metadata.get("project_name"), str):
        raise ProjectFileError("metadata.project_name must be a string.")
    if not isinstance(metadata.get("saved_at_utc"), str):
        raise ProjectFileError("metadata.saved_at_utc must be a string.")
    if document.get("units") != PROJECT_UNITS:
        raise ProjectFileError(
            "Project units are missing or incompatible with schema version 1.")
    settings = _require_object(document.get("settings"), "settings")
    required_settings = {
        "diffuser_application": str,
        "diffuser_type": str,
        "diffuser_max_nc": (int, float),
        "diffuser_max_dp_inwg": (int, float),
        "energy_hours_per_year": int,
        "energy_tariff": str,
        "energy_currency": str,
        "diffuser_room_width_ft": (int, float),
        "diffuser_room_length_ft": (int, float),
        "diffuser_ceiling_height_ft": (int, float),
    }
    for key, expected in required_settings.items():
        value = settings.get(key)
        if (not isinstance(value, expected)
                or isinstance(value, bool) and expected != str):
            raise ProjectFileError(f"settings.{key} has an invalid or missing value.")
    for key in ("diffuser_max_nc", "diffuser_max_dp_inwg",
                "diffuser_room_width_ft", "diffuser_room_length_ft",
                "diffuser_ceiling_height_ft"):
        if not math.isfinite(float(settings[key])):
            raise ProjectFileError(f"settings.{key} must be finite.")
    if (settings["diffuser_max_nc"] < 0
            or settings["diffuser_max_nc"] > 60
            or settings["diffuser_max_dp_inwg"] < 0.01
            or settings["diffuser_max_dp_inwg"] > 1.0):
        raise ProjectFileError("Diffuser selection limits are outside valid ranges.")
    if not 100 <= settings["energy_hours_per_year"] <= 8760:
        raise ProjectFileError(
            "settings.energy_hours_per_year must be between 100 and 8760.")
    for key in ("diffuser_room_width_ft", "diffuser_room_length_ft"):
        if not 1 <= settings[key] <= 500:
            raise ProjectFileError(f"settings.{key} must be between 1 and 500 ft.")
    if not 6 <= settings["diffuser_ceiling_height_ft"] <= 40:
        raise ProjectFileError(
            "settings.diffuser_ceiling_height_ft must be between 6 and 40 ft.")
    cart = document.get("cart")
    if not isinstance(cart, list):
        raise ProjectFileError("cart must be an array.")
    seen_names = set()
    for index, record in enumerate(cart):
        record = _require_object(record, f"cart[{index}]")
        name = record.get("system_name")
        if not isinstance(name, str) or not name.strip():
            raise ProjectFileError(
                f"cart[{index}].system_name must be a non-empty string.")
        if name in seen_names:
            raise ProjectFileError(f"Duplicate cart system {name!r}.")
        seen_names.add(name)
        equipment = _require_object(
            record.get("equipment"), f"cart[{index}].equipment")
        required_equipment = {
            "model": str,
            "catalog": str,
            "class_kbtu": (int, float),
            "total_btu": (int, float),
            "sensible_btu": (int, float),
            "cfm": (int, float),
            "esp": (int, float),
            "outdoor": (int, float),
            "fan": str,
            "status": str,
        }
        for key, expected in required_equipment.items():
            value = equipment.get(key)
            if (not isinstance(value, expected)
                    or isinstance(value, bool) and expected != str):
                raise ProjectFileError(
                    f"cart[{index}].equipment.{key} has an invalid or missing value.")
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ProjectFileError(
                    f"cart[{index}].equipment.{key} must be finite.")
        optional_airflow_numbers = (
            "required_cfm", "design_cfm", "available_unit_cfm",
            "airflow_esp_inwg", "requested_esp_inwg",
        )
        for key in optional_airflow_numbers:
            value = equipment.get(key)
            if value is not None and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)):
                raise ProjectFileError(
                    f"cart[{index}].equipment.{key} must be a finite number or null.")
        for key in ("available_unit_cfm_status", "airflow_source",
                    "airflow_provenance"):
            if key in equipment and not isinstance(equipment[key], str):
                raise ProjectFileError(
                    f"cart[{index}].equipment.{key} must be a string.")
        if "room" not in record or "diffuser_selection_snapshot" not in record:
            raise ProjectFileError(
                f"cart[{index}] requires room and diffuser_selection_snapshot fields.")
        room = record.get("room")
        _validate_room(room, f"cart[{index}].room")
        snapshot = record.get("diffuser_selection_snapshot")
        if snapshot is not None:
            _require_object(
                snapshot, f"cart[{index}].diffuser_selection_snapshot")
        layout = _require_object(record.get("layout"), f"cart[{index}].layout")
        if layout.get("state") not in {
                "not_present", "valid", "stale", "invalid"}:
            raise ProjectFileError(
                f"cart[{index}].layout.state is unsupported.")
        errors = layout.get("validation_errors", [])
        if (not isinstance(errors, list)
                or any(not isinstance(error, str) for error in errors)):
            raise ProjectFileError(
                f"cart[{index}].layout.validation_errors must be an array of strings.")
    systems = document.get("systems")
    if not isinstance(systems, list) or any(
            not isinstance(system, dict) for system in systems):
        raise ProjectFileError("systems must be an array of objects.")
    selection_inputs = _require_object(
        document.get("selection_inputs"), "selection_inputs")
    text_inputs = (
        "total_btu", "sensible_btu", "cfm", "heating_btu", "esp",
        "max_sound", "ceiling_void_mm", "ceiling_allowance_mm",
        "drain_run_m", "catalog_family", "phase", "outdoor_temperature",
        "fan_speed",
    )
    if any(not isinstance(selection_inputs.get(key), str)
           for key in text_inputs):
        raise ProjectFileError(
            "selection_inputs must contain all required text values.")
    if not isinstance(selection_inputs.get("ceiling_enforce"), bool):
        raise ProjectFileError(
            "selection_inputs.ceiling_enforce must be a boolean.")
    for key in ("capacity_margin_percent", "airflow_margin_percent",
                "sensible_margin_percent", "floor_area_ft2"):
        value = selection_inputs.get(key)
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value)):
            raise ProjectFileError(
                f"selection_inputs.{key} must be a finite number.")
    for key in ("capacity_margin_percent", "airflow_margin_percent",
                "sensible_margin_percent"):
        if not 0 <= selection_inputs[key] <= 50:
            raise ProjectFileError(
                f"selection_inputs.{key} must be between 0 and 50.")
    if selection_inputs["floor_area_ft2"] < 0:
        raise ProjectFileError(
            "selection_inputs.floor_area_ft2 cannot be negative.")
    return deepcopy(document)


def create_project_document(
        cart: Mapping[str, Mapping[str, Any]], settings: Mapping[str, Any],
        systems: list[dict] | None = None, project_name: str = "Untitled",
        selection_inputs: Mapping[str, Any] | None = None) -> dict:
    """Build the versioned document from runtime cart and settings values."""
    records = []
    for name, item_value in cart.items():
        item = normalize_airflow_fields(dict(item_value))
        room = item.pop("cad_room_data", None)
        snapshot = item.pop("diffuser_selection", None)
        if room is not None:
            room = deepcopy(room)
        stale = bool((room or {}).get("layout_stale", False))
        errors = list((room or {}).get("layout_validation_errors", []) or [])
        diffusers = (room or {}).get("diffusers", []) or []
        state = ("invalid" if errors else "stale" if stale else
                 "valid" if diffusers else "not_present")
        records.append({
            "system_name": str(name),
            "equipment": item,
            "room": room,
            "diffuser_selection_snapshot": (
                deepcopy(snapshot) if snapshot is not None else None),
            "layout": {"state": state, "validation_errors": errors},
        })
    document = {
        "format": PROJECT_FORMAT,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "units": deepcopy(PROJECT_UNITS),
        "metadata": {
            "project_name": str(project_name),
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "settings": deepcopy(dict(settings)),
        "systems": deepcopy(systems or []),
        "selection_inputs": deepcopy(dict(selection_inputs or {
            "total_btu": "",
            "sensible_btu": "",
            "cfm": "",
            "heating_btu": "",
            "esp": "0",
            "max_sound": "0",
            "ceiling_void_mm": "0",
            "ceiling_allowance_mm": "100",
            "drain_run_m": "0",
            "catalog_family": "All Catalogs",
            "phase": "Any",
            "outdoor_temperature": "43",
            "fan_speed": "High",
            "ceiling_enforce": False,
            "capacity_margin_percent": 10.0,
            "airflow_margin_percent": 10.0,
            "sensible_margin_percent": 10.0,
            "floor_area_ft2": 0.0,
        })),
        "cart": records,
    }
    return validate_project_document(document)


def restore_project_state(
        document: Mapping[str, Any]) -> tuple[dict, list, dict, dict]:
    """Rebuild runtime state without recalculation or diffuser relocation."""
    validated = validate_project_document(document)
    cart = {}
    for record in validated["cart"]:
        item = deepcopy(record["equipment"])
        normalize_airflow_fields(item)
        item["system"] = record["system_name"]
        room = deepcopy(record["room"])
        if room is not None:
            layout = record["layout"]
            room["layout_stale"] = layout["state"] in ("stale", "invalid")
            room["layout_validation_errors"] = list(
                layout["validation_errors"])
            if layout["state"] == "stale" and not room[
                    "layout_validation_errors"]:
                room["layout_validation_errors"].append(
                    "Saved diffuser layout is marked stale and requires review.")
            if room.get("diffusers"):
                from placement_engine import validate_diffuser_layout
                from room_geometry import RoomGeometry

                try:
                    geometry = RoomGeometry.from_dict(room)
                    positions = [
                        (diffuser["x_m"], diffuser["y_m"])
                        for diffuser in room["diffusers"]
                    ]
                    result = validate_diffuser_layout(geometry, positions)
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    result = None
                    reason = f"Saved diffuser layout could not be validated: {exc}"
                else:
                    reason = result.reason
                if result is None or not result.valid:
                    room["layout_stale"] = True
                    room["layout_validation_errors"] = list(dict.fromkeys(
                        room["layout_validation_errors"] + [reason]))
                    layout["state"] = "invalid"
                    layout["validation_errors"] = list(
                        room["layout_validation_errors"])
        if room is not None:
            item["cad_room_data"] = room
        snapshot = record["diffuser_selection_snapshot"]
        if snapshot is not None:
            item["diffuser_selection"] = deepcopy(snapshot)
            item["diffuser_selection"]["layout_stale"] = (
                record["layout"]["state"] in ("stale", "invalid"))
            if record["layout"]["state"] == "invalid":
                item["diffuser_selection"]["success"] = False
                item["diffuser_selection"]["status"] = "invalid_saved_layout"
        cart[record["system_name"]] = item
    return (
        cart,
        deepcopy(validated["systems"]),
        deepcopy(validated["settings"]),
        deepcopy(validated["selection_inputs"]),
    )


def load_project(path: str | os.PathLike[str]) -> dict:
    """Read and validate a project document without changing application state."""
    try:
        with open(path, "r", encoding="utf-8") as source:
            document = json.load(
                source, object_pairs_hook=_unique_object,
                parse_constant=_reject_constant)
    except ProjectFileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectFileError(f"Could not read project file: {exc}") from exc
    try:
        document = _decode_special_floats(document)
    except (TypeError, KeyError) as exc:
        raise ProjectFileError(f"Malformed project values: {exc}") from exc
    return validate_project_document(document)


def save_project(path: str | os.PathLike[str], document: Mapping[str, Any]) -> None:
    """Atomically write a validated project, preserving old files on failure."""
    validated = validate_project_document(document)
    encoded = _encode_special_floats(validated)
    destination = Path(path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp",
                delete=False) as target:
            temp_name = target.name
            json.dump(encoded, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectFileError(f"Could not save project file: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


__all__ = [
    "NONFINITE_TAG",
    "PROJECT_FORMAT",
    "PROJECT_SCHEMA_VERSION",
    "PROJECT_UNITS",
    "ProjectFileError",
    "create_project_document",
    "load_project",
    "restore_project_state",
    "save_project",
    "validate_project_document",
]
