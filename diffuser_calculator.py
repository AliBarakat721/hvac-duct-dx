"""
diffuser_calculator.py  (v3 – catalog-table driven)
===================================================
Every performance number (CFM, total pressure, throw, NC / noise band) is
interpolated from the AL-ANDALOSIA catalog tables stored in catalog_data.py
(pp. 6, 11, 14).  Nothing is extrapolated silently.

Design screening rules that are NOT in the catalog are isolated in the
constants below and are easy to change:
    ADPI_BANDS, MAX_AREA_PER_DIFFUSER_FT2, APPLICATION_NC.
"""
from __future__ import annotations

import logging
import math
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import catalog_data as _cd
from room_geometry import RoomGeometry

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  Documented selection rules (data: catalog_data.DIFFUSER_RULES)
# ─────────────────────────────────────────────────────────────
_BTUH_PER_FT2_TO_W_PER_M2 = 0.29307107 * 10.7639104


def _rule(rule_id: str) -> dict:
    return _cd.DIFFUSER_RULES[rule_id]


def rules_application_nc() -> dict[str, float]:
    """NC limit per application; policy 'current' (legacy) or 'ashrae_ch48'."""
    nc = _rule("R-NC")
    key = "limit_ch48" if nc.get("policy") == "ashrae_ch48" else "limit"
    return {k: float(v[key]) for k, v in nc["application"].items()}


def rules_tl_target() -> float:
    """T/L giving maximum ADPI for circular ceiling diffusers (Table 4: 0.8)."""
    return float(_rule("R-TL-BAND")["target_t_over_l"])


def rules_load_w_m2(sensible_btuh: float | None,
                    floor_area_ft2: float) -> float | None:
    if sensible_btuh is None or floor_area_ft2 <= 0 or sensible_btuh <= 0:
        return None
    return sensible_btuh / floor_area_ft2 * _BTUH_PER_FT2_TO_W_PER_M2


def rules_tl_band(load: float | None) -> tuple[float, float]:
    """T50/L band (lo, hi) for a cooling load in W/m2 (R-TL-BAND).

    Between tabulated loads the intersection of the two neighbouring bands is
    used (conservative); outside 60-250 W/m2 or with no load the fallback band.
    """
    r = _rule("R-TL-BAND")
    fb = (r["fallback"]["lo"], r["fallback"]["hi"])
    if load is None:
        return fb
    rows = sorted(r["table"], key=lambda x: x["load_w_m2"])
    if load < rows[0]["load_w_m2"] or load > rows[-1]["load_w_m2"]:
        return fb
    for row in rows:
        if abs(load - row["load_w_m2"]) < 1e-9:
            return row["lo"], row["hi"]
    for a, b in zip(rows, rows[1:], strict=False):
        if a["load_w_m2"] <= load <= b["load_w_m2"]:
            return max(a["lo"], b["lo"]), min(a["hi"], b["hi"])
    return fb


def rules_drop_to_occupied_zone_ft(room_h_ft: float) -> float:
    r = _rule("R-CHAR-L")
    if not r.get("include_drop", False):
        return 0.0
    return max(0.0, room_h_ft - float(r["occupied_zone_top_ft"]))


def rules_min_throw_prefilter_enabled() -> bool:
    return bool(_rule("R-MIN-THROW").get("enabled", False))


# ─────────────────────────────────────────────────────────────
#  Catalog rules (Table 4, p.5)  /  design rules
# ─────────────────────────────────────────────────────────────
# ceiling height (ft) -> (max CFM per diffuser, max supply ΔT °F)
CEILING_TABLE = {8: (800, 20), 10: (2000, 26), 12: (3000, 30),
                 14: (5000, 30), 16: (6400, 30)}
CEILING_MAX_CFM = {h: v[0] for h, v in CEILING_TABLE.items()}   # compat

# T50/L screening bands inspired by ADPI guidance. This geometric proxy is
# not a validated ADPI calculation and does not establish ASHRAE Standard 55
# compliance. Confirm actual room-air performance with manufacturer data or
# a validated analysis.
ADPI_BANDS = {"round": (0.7, 1.2), "square": (0.7, 1.2), "square3": (0.7, 1.2)}

# Rule of thumb (NOT from the catalog): one outlet per at most this floor area.
# Sparse coverage (area/n > MAX) is a warning, not a hard search floor — a
# short T50/L ratio is fixed by *fewer, larger* outlets, which this cap
# must not prevent. MIN is a density ceiling (too many tiny outlets).
MAX_AREA_PER_DIFFUSER_FT2 = 400.0
MIN_AREA_PER_DIFFUSER_FT2 = 60.0      # …and not more than one per this area
IRREGULAR_LAYOUT_SAMPLE_STEP_M = 0.20
IRREGULAR_LAYOUT_RELAXATION_ITERATIONS = 30
IRREGULAR_LAYOUT_MAX_ASSIGNMENTS = 20_000

# Catalog / ASHRAE sensible-heat factor: Q (Btu/h) = 1.08 × CFM × ΔT (°F)
SENSIBLE_HEAT_FACTOR = 1.08
# Neck velocity band that still produces useful throw without excess NC.
PREFERRED_NECK_FPM = (400.0, 800.0)

# Upper NC limit by application (ASHRAE guide, catalog Table 2, p.4)
APPLICATION_NC = rules_application_nc()   # R-NC (catalog_data.DIFFUSER_RULES)
BAND_NC = {0: 20.0, 1: 30.0, 2: 40.0, 3: 45.0}       # catalog colour -> NC upper bound
BAND_LABEL = {0: "≤20", 1: "≤30", 2: "≤40", 3: ">40"}

Kind = Literal["round", "square", "square3"]


def m_to_ft(x: float) -> float:
    return x / 0.3048


def ft_to_m(x: float) -> float:
    return x * 0.3048


def m2_to_ft2(area_m2: float) -> float:
    return area_m2 / (0.3048 ** 2)


def ft2_to_m2(area_ft2: float) -> float:
    return area_ft2 * (0.3048 ** 2)


def _resolve_delta_t(total_cfm: float, delta_t_f: float | None,
                     sensible_load_btuh: float | None) -> float | None:
    if delta_t_f is not None:
        if not math.isfinite(delta_t_f) or delta_t_f < 0:
            raise ValueError("delta_t_f must be finite and non-negative")
        return float(delta_t_f)
    if sensible_load_btuh is not None:
        try:
            load = float(sensible_load_btuh)
        except (TypeError, ValueError) as exc:
            raise ValueError("sensible_load_btuh must be numeric") from exc
        if not math.isfinite(load) or load < 0:
            raise ValueError("sensible_load_btuh must be finite and non-negative")
        return calculate_supply_delta_t(load, total_cfm)
    return None


def _reject_total_load(total_load_btuh: float | None) -> None:
    """Prevent total cooling load from being used as sensible heat load."""
    if total_load_btuh is not None:
        raise ValueError(
            "total_load_btuh cannot be used for diffuser ΔT; provide "
            "sensible_load_btuh or delta_t_f instead")


def diffuser_face_half_extent_m(selection: object) -> float:
    """Conservative center-to-face extent for wall-clearance checks."""
    size_label = str(getattr(selection, "size_label", ""))
    match = re.search(r"\d+(?:\.\d+)?", size_label)
    if match is None:
        raise ValueError(f"Cannot determine diffuser face size from {size_label!r}")
    half_width_m = ft_to_m(float(match.group()) / 12.0) / 2.0
    if getattr(selection, "kind", "") == "round":
        return half_width_m
    return half_width_m * math.sqrt(2.0)


def selection_input_signature(
        total_cfm: float, sensible_load_btuh: float | None,
        geometry_signature: dict[str, object], type_label: str,
        application: str, max_nc: float | None,
        max_dp_inwg: float) -> dict[str, object]:
    """Stable inputs required to safely reuse a cached diffuser selection."""
    return {
        "cfm": float(total_cfm),
        "sensible_load_btuh": (
            float(sensible_load_btuh) if sensible_load_btuh is not None else None),
        "geometry": geometry_signature,
        "type_label": str(type_label),
        "application": str(application),
        "max_nc": float(max_nc) if max_nc is not None else None,
        "max_dp_inwg": float(max_dp_inwg),
    }


def selection_signatures_match(saved: object, current: dict[str, object]) -> bool:
    """Only reuse a selection when every recorded input still matches."""
    return isinstance(saved, dict) and saved == current


def polygon_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )) / 2.0


def polygon_bounds(points: Sequence[tuple[float, float]]
                   ) -> tuple[float, float, float, float]:
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs, ys = zip(*points, strict=True)
    return min(xs), max(xs), min(ys), max(ys)


def point_in_polygon(x: float, y: float,
                     polygon: Sequence[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def distance_to_polygon_walls(x: float, y: float,
                              polygon: Sequence[tuple[float, float]]) -> float:
    if len(polygon) < 2:
        return 0.0
    return min(
        _distance_to_segment(x, y, polygon[i], polygon[(i + 1) % len(polygon)])
        for i in range(len(polygon))
    )


def calculate_supply_delta_t(load_btuh: float, total_cfm: float) -> float | None:
    """Supply-to-room ΔT (°F) from Q = 1.08 × CFM × ΔT.

    ``load_btuh`` must be the sensible coil load. Total cooling load includes
    latent load and cannot be used to infer dry-bulb ΔT. Returns None when
    inputs are not usable.
    """
    try:
        load = float(load_btuh)
        cfm = float(total_cfm)
    except (TypeError, ValueError):
        return None
    if load <= 0 or cfm <= 0 or not math.isfinite(load) or not math.isfinite(cfm):
        return None
    return load / (SENSIBLE_HEAT_FACTOR * cfm)


def distribute_polygon_positions(polygon: Sequence[tuple[float, float]],
                                 quantity: int,
                                 min_wall_distance: float = 0.30,
                                 min_spacing: float = 0.0
                                 ) -> list[tuple[float, float]]:
    """Place an exact quantity inside a valid polygon or return no layout."""
    from placement_engine import validate_polygon, verify_placement

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    try:
        wall_distance = float(min_wall_distance)
        spacing = float(min_spacing)
    except (TypeError, ValueError) as exc:
        raise ValueError("Wall clearance and spacing must be numeric") from exc
    if (not math.isfinite(wall_distance) or wall_distance < 0
            or not math.isfinite(spacing) or spacing < 0):
        raise ValueError(
            "Wall clearance and spacing must be finite and non-negative")
    try:
        points = []
        for point in polygon:
            if isinstance(point, (str, bytes)) or len(point) != 2:
                raise ValueError
            points.append((float(point[0]), float(point[1])))
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(
            "polygon must contain numeric (x, y) vertices") from exc
    valid_polygon, polygon_diagnostic = validate_polygon(points)
    if not valid_polygon:
        raise ValueError(
            "Invalid room polygon: "
            + str(polygon_diagnostic.get("reason", "invalid polygon")))

    engine_spacing = spacing if spacing > 0 else 0.01
    try:
        from placement_engine import PlacementStatus, place_diffusers
        engine = place_diffusers(
            polygon=points,
            count=quantity,
            wall_clearance_m=wall_distance,
            min_spacing_m=engine_spacing,
            strategy="auto",
        )
        contained = [
            (float(x), float(y)) for x, y in engine.positions_m
            if point_in_polygon(float(x), float(y), points)
        ]
        if (engine.status == PlacementStatus.SUCCESS
                and len(contained) == quantity
                and verify_placement(
                    points, contained, wall_distance, engine_spacing).valid):
            return contained
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as exc:
        logger.warning("Placement engine unavailable; using legacy placement: %s", exc)

    min_x, max_x, min_y, max_y = polygon_bounds(points)
    width, length = max_x - min_x, max_y - min_y
    if width <= 0 or length <= 0:
        return []
    if quantity > 160 * 160:
        return []

    best = None
    for candidate_rows in range(1, quantity + 1):
        if quantity % candidate_rows:
            continue
        candidate_cols = quantity // candidate_rows
        cell_w = width / candidate_cols
        cell_l = length / candidate_rows
        aspect = max(cell_w, cell_l) / min(cell_w, cell_l)
        if best is None or aspect < best[0]:
            best = (aspect, candidate_rows, candidate_cols)
    _, rows, cols = best
    ideals = [
        (min_x + (col + 0.5) * width / cols,
         min_y + (row + 0.5) * length / rows)
        for row in range(rows) for col in range(cols)
    ][:quantity]

    divisions = max(15, min(60, math.ceil(max(width, length) / 0.25)))
    valid = []
    for row in range(divisions):
        y = min_y + (row + 0.5) * length / divisions
        for col in range(divisions):
            x = min_x + (col + 0.5) * width / divisions
            if (point_in_polygon(x, y, points)
                    and distance_to_polygon_walls(x, y, points) + 1e-9
                    >= wall_distance):
                rounded = (round(x, 3), round(y, 3))
                if (point_in_polygon(*rounded, points)
                        and distance_to_polygon_walls(*rounded, points) + 1e-9
                        >= wall_distance):
                    valid.append(rounded)

    if not valid:
        return []

    unassigned = set(valid)
    placed = []
    for ideal in ideals:
        ideal_valid = (
            point_in_polygon(*ideal, points)
            and distance_to_polygon_walls(*ideal, points) + 1e-9
            >= wall_distance
            and all(math.dist(ideal, other) + 1e-9 >= engine_spacing
                    for other in placed)
        )
        if ideal_valid:
            candidate = ideal
            unassigned.discard(candidate)
        else:
            available = [
                candidate for candidate in unassigned
                if all(math.dist(candidate, other) + 1e-9 >= engine_spacing
                       for other in placed)
            ]
            if not available:
                return []
            candidate = min(
                available,
                key=lambda point: math.dist(point, ideal),
            )
            unassigned.discard(candidate)
        placed.append(candidate)
    if len(placed) != quantity:
        return []
    verification = verify_placement(
        points, placed, wall_distance, engine_spacing)
    return placed if verification.valid else []


@dataclass
class GeometryPlacementResult:
    success: bool
    requested_quantity: int
    positions_m: list[tuple[float, float]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_clearance_ok: bool = False
    spacing_ok: bool = False
    throw_t50_m: float | None = None


def place_diffusers_in_geometry(
        geometry: RoomGeometry, quantity: int,
        min_wall_clearance_m: float = 0.30,
        min_spacing_m: float = 0.0,
        throw_t50_m: float | None = None,
        face_half_extent_m: float = 0.0) -> GeometryPlacementResult:
    """Find a complete deterministic layout.

    Phase 5 integration
    -------------------
    * Primary path: `placement_engine_adapter.place_via_engine` (which
      calls `placement_engine.place_diffusers` with Tier A/B/C dispatch
      and the exact mathematical verifier).
    * Fallback: the legacy bounded-polygon grid algorithm — used ONLY
      when the engine returns a non-final verdict or raises.
    * FINAL engine verdicts (SUCCESS, CAPACITY_EXCEEDED) are returned
      directly; the legacy path is NOT consulted for them.
    """
    if (not math.isfinite(face_half_extent_m) or face_half_extent_m < 0):
        return GeometryPlacementResult(
            False, quantity,
            errors=["Diffuser face extent must be finite and non-negative."])
    center_clearance_m = min_wall_clearance_m + face_half_extent_m

    # --- Guard rails: mirror legacy semantics on trivial / invalid inputs ---
    if quantity <= 0:
        return _legacy_place_diffusers_in_geometry_impl(
            geometry, quantity, center_clearance_m, min_spacing_m,
            throw_t50_m)
    if (not math.isfinite(min_wall_clearance_m) or min_wall_clearance_m < 0
            or not math.isfinite(min_spacing_m) or min_spacing_m < 0):
        return _legacy_place_diffusers_in_geometry_impl(
            geometry, quantity, center_clearance_m, min_spacing_m,
            throw_t50_m)
    if (throw_t50_m is not None
            and (not math.isfinite(throw_t50_m) or throw_t50_m <= 0)):
        return _legacy_place_diffusers_in_geometry_impl(
            geometry, quantity, center_clearance_m, min_spacing_m,
            throw_t50_m)

    # --- Engine path ---
    try:
        from placement_engine import PlacementStatus
        from placement_engine_adapter import (
            is_engine_verdict_final,
            place_via_engine,
        )

        engine_result = place_via_engine(
            geometry, quantity,
            wall_clearance_m=center_clearance_m,
            min_spacing_m=min_spacing_m,
            throw_t50_m=throw_t50_m,
        )

        if is_engine_verdict_final(engine_result.status):
            errors: list[str] = []
            if engine_result.status != PlacementStatus.SUCCESS:
                errors.append(
                    str(engine_result.diagnostics.get(
                        "reason", "placement_engine_failure")))
            return GeometryPlacementResult(
                success=(engine_result.status == PlacementStatus.SUCCESS),
                requested_quantity=quantity,
                positions_m=[tuple(p) for p in engine_result.positions_m],
                errors=errors,
                wall_clearance_ok=engine_result.wall_clearance_verified,
                spacing_ok=engine_result.spacing_verified,
                throw_t50_m=throw_t50_m,
            )
        # Non-final verdict → fall through to legacy.
    except (ImportError, TypeError, ValueError, RuntimeError) as exc:
        message = (
            "placement_engine_adapter raised "
            f"{type(exc).__name__}: {exc}. Falling back to legacy placement.")
        logger.warning(message, exc_info=True)
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    # --- Legacy fallback ---
    return _legacy_place_diffusers_in_geometry_impl(
        geometry, quantity, center_clearance_m, min_spacing_m,
        throw_t50_m)


def _legacy_place_diffusers_in_geometry_impl(
        geometry: RoomGeometry, quantity: int,
        min_wall_clearance_m: float = 0.30,
        min_spacing_m: float = 0.0,
        throw_t50_m: float | None = None) -> GeometryPlacementResult:
    """Find a complete deterministic layout using a bounded polygon grid."""
    if quantity <= 0:
        return GeometryPlacementResult(
            False, quantity, errors=["Requested diffuser quantity must be positive."])
    if (not math.isfinite(min_wall_clearance_m) or min_wall_clearance_m < 0
            or not math.isfinite(min_spacing_m) or min_spacing_m < 0):
        return GeometryPlacementResult(
            False, quantity,
            errors=["Wall clearance and diffuser spacing must be finite and non-negative."])
    if (throw_t50_m is not None
            and (not math.isfinite(throw_t50_m) or throw_t50_m <= 0)):
        return GeometryPlacementResult(
            False, quantity,
            errors=["Diffuser T50 throw must be finite and greater than zero."])
    geometry_errors = geometry.validate()
    if geometry_errors:
        return GeometryPlacementResult(
            False, quantity,
            errors=["Invalid room geometry: " + "; ".join(geometry_errors)])

    min_x, max_x, min_y, max_y = geometry.bounding_box
    width, depth = max_x - min_x, max_y - min_y
    corners = {
        (min_x, min_y), (max_x, min_y),
        (max_x, max_y), (min_x, max_y),
    }
    axis_aligned_rectangle = (
        len(geometry.vertices) == 4
        and all(any(math.dist(point, corner) <= 1e-8 for corner in corners)
                for point in geometry.vertices)
    )
    if axis_aligned_rectangle:
        room = RoomLayout.from_geometry(geometry)
        grid = best_grid(room, quantity)
        rectangle_positions = [
            (min_x + ft_to_m(grid.cell_w * (col + 0.5)),
             min_y + ft_to_m(grid.cell_l * (row + 0.5)))
            for row in range(grid.rows) for col in range(grid.cols)
        ]
        wall_ok = all(
            geometry.distance_to_walls(*point) + 1e-9 >= min_wall_clearance_m
            for point in rectangle_positions)
        spacing_ok = all(
            math.dist(rectangle_positions[i], rectangle_positions[j]) + 1e-9
            >= min_spacing_m
            for i in range(len(rectangle_positions))
            for j in range(i + 1, len(rectangle_positions)))
        if wall_ok and spacing_ok:
            return GeometryPlacementResult(
                True, quantity, positions_m=rectangle_positions,
                wall_clearance_ok=True, spacing_ok=True,
                throw_t50_m=throw_t50_m)

    divisions = max(15, min(60, math.ceil(max(width, depth) / 0.25)))
    candidates: list[tuple[float, float]] = []
    for row in range(divisions):
        y = min_y + (row + 0.5) * depth / divisions
        for col in range(divisions):
            x = min_x + (col + 0.5) * width / divisions
            if (geometry.contains_point(x, y)
                    and geometry.distance_to_walls(x, y) + 1e-9
                    >= min_wall_clearance_m):
                candidate = (round(x, 6), round(y, 6))
                if (geometry.contains_point(*candidate)
                        and geometry.distance_to_walls(*candidate) + 1e-9
                        >= min_wall_clearance_m):
                    candidates.append(candidate)

    if len(candidates) < quantity:
        return GeometryPlacementResult(
            False, quantity, errors=[
                (f"Geometry-infeasible: requested {quantity} diffusers, "
                f"but only {len(candidates)} valid candidate locations exist.")
            ])

    centroid = geometry.centroid
    chosen: list[tuple[float, float]] = []
    from placement_engine import (
        is_orthogonal_polygon,
        place_in_orthogonal_polygon,
        significant_subrectangle_count,
    )
    if is_orthogonal_polygon(geometry.vertices):
        wing_seed_count = min(
            quantity,
            significant_subrectangle_count(geometry.vertices,
                                           min_wall_clearance_m),
        )
        wing_seeds = place_in_orthogonal_polygon(
            geometry.vertices, wing_seed_count,
            wall_clearance_m=min_wall_clearance_m,
            min_spacing_m=min_spacing_m,
        )
        for point in wing_seeds:
            if (geometry.contains_point(*point)
                    and geometry.distance_to_walls(*point) + 1e-9
                    >= min_wall_clearance_m) and point not in candidates:
                candidates.append(point)
            if (point in candidates and all(
                    math.dist(point, placed) + 1e-9 >= min_spacing_m
                    for placed in chosen)):
                chosen.append(point)
    remaining = candidates
    while len(chosen) < quantity:
        eligible = [
            point for point in remaining
            if all(math.dist(point, placed) + 1e-9 >= min_spacing_m
                   for placed in chosen)
        ]
        if not eligible:
            return GeometryPlacementResult(
                False, quantity, errors=[
                    (f"Geometry-infeasible: requested {quantity} diffusers "
                    f"cannot satisfy {min_spacing_m:.3f} m minimum spacing.")
                ])
        if not chosen:
            point = min(eligible, key=lambda p: (
                math.dist(p, centroid), p[0], p[1]))
        else:
            point = max(eligible, key=lambda p: (
                min(math.dist(p, placed) for placed in chosen), -p[0], -p[1]))
        chosen.append(point)
        remaining.remove(point)

    wall_ok = all(
        geometry.contains_point(*point)
        and geometry.distance_to_walls(*point) + 1e-9 >= min_wall_clearance_m
        for point in chosen)
    spacing_ok = all(
        math.dist(chosen[i], chosen[j]) + 1e-9 >= min_spacing_m
        for i in range(len(chosen)) for j in range(i + 1, len(chosen)))
    if not wall_ok or not spacing_ok:
        return GeometryPlacementResult(
            False, quantity, errors=["Generated layout failed placement validation."])
    return GeometryPlacementResult(
        True, quantity, positions_m=chosen, wall_clearance_ok=wall_ok,
        spacing_ok=spacing_ok, throw_t50_m=throw_t50_m)


def room_layout_from_cad(cad_room: dict[str, object]) -> RoomLayout:
    """Adapt legacy CAD wall data to the feet-based calculator boundary."""
    geometry = RoomGeometry.from_dict(cad_room)
    return room_layout_from_geometry(geometry)


def room_layout_from_geometry(geometry: RoomGeometry) -> RoomLayout:
    """Convert validated metric geometry to the legacy feet-based API once."""
    errors = geometry.validate()
    if errors:
        raise ValueError("Invalid room geometry: " + "; ".join(errors))
    return RoomLayout.from_geometry(geometry)


# ─────────────────────────────────────────────────────────────
#  Model + interpolation
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DiffuserModel:
    model: str
    kind: str
    size_label: str
    neck_dia_inch: float
    neck_area_ft2: float
    v: tuple[float, ...]            # tabulated neck velocities (fpm)
    cfm: tuple[float, ...]
    pt: tuple[float, ...]           # total pressure (in.wg)
    t100: tuple[float, ...]
    t50: tuple[float, ...]
    t50_back: tuple[float, ...]     # 3-way back-side throw (= t50 otherwise)
    nc: tuple[float, ...] | None = None      # numeric NC (round)
    band: tuple[int, ...] | None = None      # colour band (square)

    @property
    def cfm_min(self) -> float: return self.cfm[0]
    @property
    def cfm_max(self) -> float: return self.cfm[-1]


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            f = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + f * (ys[i + 1] - ys[i])
    return ys[-1]


def _build() -> tuple[list[DiffuserModel], list[DiffuserModel], list[DiffuserModel]]:
    ccd, s4, s3 = [], [], []
    for s, d in sorted(_cd.CCD.items()):
        ccd.append(DiffuserModel(
            f"CCD-{s}", "round", f'{s}"', s, d["area"], tuple(_cd.VC),
            tuple(d["cfm"]), tuple(d["pt"]),
            tuple(float(a) for a, _ in d["th"]), tuple(float(b) for _, b in d["th"]),
            tuple(float(b) for _, b in d["th"]), nc=tuple(d["nc"])))
    for s, d in sorted(_cd.SQUARE.items()):
        cfm = tuple(float(c) for c in d["cfm"])
        neck_area = d["area"]
        s4.append(DiffuserModel(
            f"4SCD-{s}", "square", f'{s}×{s}"', s, neck_area, tuple(_cd.VEL8), cfm,
            tuple(_cd.PT8), tuple(float(a) for a, _ in d["th4"]),
            tuple(float(b) for _, b in d["th4"]), tuple(float(b) for _, b in d["th4"]),
            band=tuple(d["band4"])))
        s3.append(DiffuserModel(
            f"3SCD-{s}", "square3", f'{s}×{s}"', s, neck_area, tuple(_cd.VEL8), cfm,
            tuple(_cd.PT8), tuple(float(a) for a, _ in d["tha"]),
            tuple(float(b) for _, b in d["tha"]), tuple(float(b) for _, b in d["thb"]),
            band=tuple(d["band3"])))
    return ccd, s4, s3


ANDALOSIA_SUPPLY_CCD, ANDALOSIA_SUPPLY_4SCD, ANDALOSIA_SUPPLY_3SCD = _build()
SUPPLY_CATALOG = ANDALOSIA_SUPPLY_CCD


def get_catalog(type_label: str) -> list[DiffuserModel]:
    t = (type_label or "").lower()
    if "circular" in t or "ccd" in t or "round" in t:
        return ANDALOSIA_SUPPLY_CCD
    if "3" in t:
        return ANDALOSIA_SUPPLY_3SCD
    return ANDALOSIA_SUPPLY_4SCD


@dataclass
class Perf:
    v_fpm: float
    pt: float
    t100: float
    t50: float
    t50_back: float
    nc: float
    nc_label: str
    in_range: bool


def performance(d: DiffuserModel, q_each: float) -> Perf:
    """Interpolate the catalog table at the actual airflow (CFM)."""
    v = q_each / d.neck_area_ft2
    # tabulated CFM uses the same area, so interpolate on velocity
    pt = _interp(v, d.v, d.pt)
    t100 = _interp(v, d.v, d.t100)
    t50 = _interp(v, d.v, d.t50)
    t50b = _interp(v, d.v, d.t50_back)
    if d.nc is not None:
        nc = _interp(v, d.v, d.nc)
        label = "<20" if nc < 20 else f"{nc:.0f}"
        nc_out = max(nc, 20.0) if nc < 20 else nc
    else:
        i = max(0, min(len(d.v) - 2, sum(1 for x in d.v if x <= v) - 1))
        b = max(d.band[i], d.band[i + 1]) if v > d.v[0] else d.band[0]
        nc_out, label = BAND_NC[b], BAND_LABEL[b]
    ok = d.v[0] <= v <= d.v[-1]
    return Perf(v, pt, t100, t50, t50b, nc_out, label, ok)


def calculate_performance(d: DiffuserModel, q_actual: float):
    """Backward-compatible: (ΔP, T100, T50, NC)."""
    p = performance(d, q_actual)
    return round(p.pt, 4), round(p.t100, 1), round(p.t50, 1), round(p.nc, 1)


def neck_velocity(cfm: float, neck_area_ft2: float) -> float:
    if neck_area_ft2 <= 0:
        raise ValueError("Neck area must be positive")
    return cfm / neck_area_ft2


# ─────────────────────────────────────────────────────────────
#  Room / grid
# ─────────────────────────────────────────────────────────────
@dataclass
class RoomLayout:
    room_w_ft: float
    room_l_ft: float
    room_h_ft: float = 10.0
    polygon_ft: list[tuple[float, float]] | None = None
    area_ft2_override: float | None = None
    geometry: RoomGeometry | None = None
    cooling_load_w_m2: float | None = None   # sensible load / floor area (R-TL-BAND)

    @classmethod
    def from_metric(cls, w_m: float, l_m: float, h_m: float = 3.0) -> RoomLayout:
        return cls(m_to_ft(w_m), m_to_ft(l_m), m_to_ft(h_m))

    @classmethod
    def from_geometry(cls, geometry: RoomGeometry) -> RoomLayout:
        errors = geometry.validate()
        if errors:
            raise ValueError("Invalid room geometry: " + "; ".join(errors))
        min_x, max_x, min_y, max_y = geometry.bounding_box
        return cls(
            room_w_ft=round(m_to_ft(max_x - min_x), 9),
            room_l_ft=round(m_to_ft(max_y - min_y), 9),
            room_h_ft=m_to_ft(geometry.height_m),
            polygon_ft=[(m_to_ft(x), m_to_ft(y))
                        for x, y in geometry.vertices],
            area_ft2_override=m2_to_ft2(geometry.area_m2),
            geometry=geometry,
        )

    @property
    def floor_area_ft2(self) -> float:
        if self.area_ft2_override is not None:
            return self.area_ft2_override
        return self.room_w_ft * self.room_l_ft
    @property
    def volume_ft3(self) -> float: return self.floor_area_ft2 * self.room_h_ft
    @property
    def max_dim_ft(self) -> float: return max(self.room_w_ft, self.room_l_ft)


def ach(cfm: float, room: RoomLayout) -> float:
    return (cfm * 60.0) / max(1.0, room.volume_ft3)


@dataclass
class Grid:
    rows: int
    cols: int
    cell_w: float
    cell_l: float

    @property
    def L_min(self) -> float: return min(self.cell_w, self.cell_l) / 2.0
    @property
    def L_max(self) -> float: return max(self.cell_w, self.cell_l) / 2.0
    @property
    def aspect(self) -> float: return max(self.cell_w, self.cell_l) / min(self.cell_w, self.cell_l)


def best_grid(room: RoomLayout, n: int) -> Grid:
    """rows×cols == n with cells as square as possible (width = x axis)."""
    best = None
    for rows in range(1, n + 1):
        if n % rows:
            continue
        cols = n // rows
        g = Grid(rows, cols, room.room_w_ft / cols, room.room_l_ft / rows)
        if best is None or g.aspect < best.aspect:
            best = g
    return best


def grid_positions(room: RoomLayout, n: int, throw_t50_ft: float | None = None
                   ) -> list[tuple[float, float]]:
    """Cell centres of the same grid used by select_diffuser (x along width, y along length)."""
    if n <= 0:
        return []
    g = best_grid(room, n)
    return [(round(g.cell_w * (j + 0.5), 2), round(g.cell_l * (i + 0.5), 2))
            for i in range(g.rows) for j in range(g.cols)]


def _band(kind: str, room: RoomLayout | None) -> tuple[float, float]:
    """R-TL-BAND: T50/L band for the room's cooling load (fallback 0.7-1.2)."""
    if room is None:
        return ADPI_BANDS.get(kind, ADPI_BANDS["square"])
    return rules_tl_band(getattr(room, "cooling_load_w_m2", None))


def _with_load(room: RoomLayout | None, sensible_btuh: float | None
               ) -> RoomLayout | None:
    if room is None:
        return None
    load = rules_load_w_m2(sensible_btuh, room.floor_area_ft2)
    return replace(room, cooling_load_w_m2=load) if load is not None else room


_RULE_PREFIXES = (
    ("Neck velocity", "R-CATALOG-RANGE"), ("ΔP ", "R-DP"),
    ("Noise NC", "R-NC"), ("Supply ΔT", "R-TABLE4"),
    ("T50/L", "R-TL-BAND"), ("Only ", "R-MIN-QTY"),
)


def violated_rule_ids(warnings: Sequence[str]) -> list[str]:
    ids: list[str] = []
    for w in warnings:
        rid = next((r for pre, r in _RULE_PREFIXES if w.startswith(pre)), None)
        if rid is None and "exceeds Table 4" in w:
            rid = "R-TABLE4"
        if rid is None and ("too dense" in w or "rule of thumb" in w):
            rid = "R-AREA"
        if rid and rid not in ids:
            ids.append(rid)
    return ids


def adpi_check(throw_t50_ft: float, characteristic_L_ft: float,
               kind: str = "square") -> tuple[bool, float]:
    """Screen T50/L against the design band; this does not calculate ADPI."""
    if characteristic_L_ft <= 0:
        return False, float("inf")
    lo, hi = ADPI_BANDS.get(kind, ADPI_BANDS["square"])
    r = throw_t50_ft / characteristic_L_ft
    return (lo <= r <= hi), round(r, 2)


# ─────────────────────────────────────────────────────────────
#  Selection
# ─────────────────────────────────────────────────────────────
@dataclass
class DiffuserSelection:
    model: str
    size_label: str
    qty: int
    cfm_per_unit: float
    neck_velocity_fpm: float
    neck_area_ft2: float
    throw_t50_ft: float
    throw_t100_ft: float
    pressure_drop_inwg: float
    nc_estimated: float
    spacing_max_ft: float
    wall_offset_ft: float
    warnings: list[str] = field(default_factory=list)
    # new – single source of truth for GUI / report / CLI
    kind: str = "square"
    nc_label: str = ""
    rows: int = 1
    cols: int = 1
    char_L_ft: float = 0.0
    adpi_ratio: float = 0.0          # worst case  T50 / L_min
    adpi_ratio_min: float = 0.0      # weakest     T50 / L_max
    adpi_ok: bool = True
    score: float = 0.0
    delta_t_f: float | None = None
    delta_t_limit_f: float | None = None
    max_nc_limit: float | None = None
    rationale: dict[str, object] = field(default_factory=dict)


def ceiling_limits(height_ft: float) -> tuple[int, int, int, list[str]]:
    """Return the conservative Table 4 row and any height-coverage warning."""
    notes = []
    if height_ft > max(CEILING_TABLE):
        notes.append(
            f"Ceiling height {height_ft:.1f} ft exceeds validated Table 4 "
            f"data (maximum {max(CEILING_TABLE)} ft); manufacturer review "
            "is required.")
    rows = [h for h in sorted(CEILING_TABLE) if h <= height_ft]
    h = rows[-1] if rows else 8
    if not rows:
        notes.append("Ceiling lower than 8 ft – Table 4 row for 8 ft used.")
    return h, CEILING_TABLE[h][0], CEILING_TABLE[h][1], notes


@dataclass(frozen=True)
class ReturnDiffuserResult:
    quantity: int
    size_in: int
    cfm_each: float
    velocity_fpm: float
    pressure_drop_inwg: float
    nc: float
    nc_label: str
    warnings: tuple[str, ...] = ()


def return_diffuser_performance(size_in: int, cfm_each: float
                                ) -> ReturnDiffuserResult:
    """Calculate return CCD velocity, pressure, and NC without throw."""
    try:
        row = _cd.RETURN_CCD[size_in]
    except KeyError as exc:
        raise ValueError(f"Unsupported return CCD size: {size_in}") from exc
    if not math.isfinite(cfm_each) or cfm_each <= 0:
        raise ValueError("Return diffuser airflow must be finite and positive")
    velocity = cfm_each / row["area"]
    if not _cd.RETURN_VELOCITY_FPM[0] <= velocity <= _cd.RETURN_VELOCITY_FPM[-1]:
        raise ValueError("Return diffuser velocity is outside catalogue range")
    pressure = _interp(velocity, _cd.RETURN_VELOCITY_FPM, row["pt"])
    nc = _interp(velocity, _cd.RETURN_VELOCITY_FPM, row["nc"])
    nc_label = "<20" if nc < 20 else f"{nc:.0f}"
    return ReturnDiffuserResult(
        quantity=1,
        size_in=size_in,
        cfm_each=cfm_each,
        velocity_fpm=velocity,
        pressure_drop_inwg=pressure,
        nc=nc,
        nc_label=nc_label,
    )


def select_return_diffusers(
        total_return_cfm: float, application: str = "office_priv",
        max_nc: float | None = None, max_dp: float = 0.20,
        ceiling_height_ft: float = 10.0) -> ReturnDiffuserResult:
    """Choose the fewest return CCDs meeting neck velocity, NC, and pressure limits."""
    if not math.isfinite(total_return_cfm) or total_return_cfm <= 0:
        raise ValueError("Total return airflow must be finite and positive")
    if not math.isfinite(max_dp) or max_dp < 0:
        raise ValueError("Maximum return diffuser pressure must be finite and non-negative")
    nc_limit = (
        APPLICATION_NC.get(application, 35) if max_nc is None else max_nc)
    if not math.isfinite(nc_limit) or nc_limit < 0:
        raise ValueError("Maximum return diffuser NC must be finite and non-negative")
    _, table4_cap, _, _ = ceiling_limits(ceiling_height_ft)
    maximum_each = _cd.RETURN_VELOCITY_FPM[-1] * max(
        row["area"] for row in _cd.RETURN_CCD.values())
    minimum_quantity = max(1, math.ceil(total_return_cfm / maximum_each))
    minimum_each = _cd.RETURN_VELOCITY_FPM[0] * min(
        row["area"] for row in _cd.RETURN_CCD.values())
    max_quantity = max(minimum_quantity, int(total_return_cfm / minimum_each))
    candidates = []
    for quantity in range(minimum_quantity, max_quantity + 1):
        cfm_each = total_return_cfm / quantity
        for size_in, row in _cd.RETURN_CCD.items():
            velocity = cfm_each / row["area"]
            if not (_cd.RETURN_VELOCITY_FPM[0] <= velocity
                    <= _cd.RETURN_VELOCITY_FPM[-1]):
                continue
            pressure = _interp(velocity, _cd.RETURN_VELOCITY_FPM, row["pt"])
            nc = _interp(velocity, _cd.RETURN_VELOCITY_FPM, row["nc"])
            if nc <= nc_limit and pressure <= max_dp:
                candidates.append((
                    quantity, pressure, nc, row["area"], size_in,
                    cfm_each, velocity))
    if not candidates:
        raise ValueError(
            "No return CCD selection meets NC, pressure, and catalogue velocity limits")
    quantity, pressure, nc, _, size_in, cfm_each, velocity = min(candidates)
    warnings = ()
    if cfm_each > table4_cap:
        warnings = (
            (f"Return airflow {cfm_each:.0f} CFM per diffuser exceeds the "
             f"Table 4 supply-only reference of {table4_cap} CFM; review required."),
        )
    return ReturnDiffuserResult(
        quantity=quantity,
        size_in=size_in,
        cfm_each=cfm_each,
        velocity_fpm=velocity,
        pressure_drop_inwg=pressure,
        nc=nc,
        nc_label="<20" if nc < 20 else f"{nc:.0f}",
        warnings=warnings,
    )


@dataclass(frozen=True)
class VCDSelection:
    nominal_width_mm: int | None
    nominal_height_mm: int | None
    casing_width_mm: float
    casing_height_mm: float
    face_width_mm: float
    face_height_mm: float
    damper_count: int
    per_damper_width_mm: float
    blade_count: int | None
    drive_arm_x_mm: int | None
    custom_size: bool
    warnings: tuple[str, ...]


def _round_vcd_dimension_up(dimension_mm: float) -> int | None:
    return next(
        (standard for standard in _cd.VCD_STANDARD_L_MM
         if standard >= dimension_mm),
        None,
    )


def vcd_select(duct_w_mm: float, duct_h_mm: float) -> VCDSelection:
    """Select VCD modules, rounding standard sizes up independently per axis."""
    if (not math.isfinite(duct_w_mm) or duct_w_mm <= 0
            or not math.isfinite(duct_h_mm) or duct_h_mm <= 0):
        raise ValueError("VCD duct dimensions must be finite and positive")
    width_nominal = _round_vcd_dimension_up(duct_w_mm)
    height_nominal = _round_vcd_dimension_up(duct_h_mm)
    if width_nominal is not None:
        face_width = float(width_nominal + 4)
        per_damper_width = float(width_nominal)
        damper_count = 1
    elif duct_w_mm > 2000:
        per_damper_width = (duct_w_mm - 60) / 2
        face_width = duct_w_mm
        damper_count = 2
    else:
        per_damper_width = duct_w_mm
        face_width = duct_w_mm + 4
        damper_count = 1
    if height_nominal is not None:
        face_height = float(height_nominal + 4)
    else:
        face_height = duct_h_mm + 4
    custom = width_nominal is None or height_nominal is None
    warnings = (
        ("custom size - confirm with manufacturer",) if custom else ())
    drive_arm_x = (
        _cd.VCD_DRIVE_ARM_X_MM.get(height_nominal)
        if height_nominal is not None else None)
    blade_count = (
        height_nominal // _cd.VCD_BLADE_PITCH_MM
        if height_nominal is not None else None)
    return VCDSelection(
        nominal_width_mm=width_nominal,
        nominal_height_mm=height_nominal,
        casing_width_mm=face_width,
        casing_height_mm=face_height,
        face_width_mm=face_width,
        face_height_mm=face_height,
        damper_count=damper_count,
        per_damper_width_mm=per_damper_width,
        blade_count=blade_count,
        drive_arm_x_mm=drive_arm_x,
        custom_size=custom,
        warnings=warnings,
    )


def vcd_face_velocity_fpm(cfm: float, duct_w_mm: float,
                          duct_h_mm: float) -> float:
    """Calculate VCD face velocity from airflow and selected casing area."""
    if not math.isfinite(cfm) or cfm < 0:
        raise ValueError("VCD airflow must be finite and non-negative")
    selection = vcd_select(duct_w_mm, duct_h_mm)
    area_ft2 = (
        selection.face_width_mm * selection.face_height_mm
        / (304.8 ** 2))
    return cfm / area_ft2


def vcd_pressure_drop(velocity_fpm: float) -> float:
    """Estimated opposed-blade VCD ΔP from the supplied log-log graph."""
    if not math.isfinite(velocity_fpm) or velocity_fpm < 0:
        raise ValueError("VCD face velocity must be finite and non-negative")
    return _cd.VCD_DP_COEFFICIENT * velocity_fpm ** 2


def vcd_pressure_drop_for_duct(cfm: float, duct_w_mm: float,
                               duct_h_mm: float) -> float:
    return vcd_pressure_drop(
        vcd_face_velocity_fpm(cfm, duct_w_mm, duct_h_mm))


def vcd_closed_leakage_cfm(area_ft2: float,
                           pressure_inwg: float) -> float:
    """Estimated closed leakage interpolated from supplied pressure points."""
    if not math.isfinite(area_ft2) or area_ft2 < 0:
        raise ValueError("VCD face area must be finite and non-negative")
    pressures = tuple(_cd.VCD_CLOSED_LEAKAGE_CFM_PER_FT2)
    if not math.isfinite(pressure_inwg) or not pressures[0] <= pressure_inwg <= pressures[-1]:
        raise ValueError("VCD leakage estimate is available from 0.5 to 4 in.wg")
    leakage_per_ft2 = _interp(
        pressure_inwg, pressures,
        tuple(_cd.VCD_CLOSED_LEAKAGE_CFM_PER_FT2[p] for p in pressures))
    return leakage_per_ft2 * area_ft2


@dataclass(frozen=True)
class RVCDSelection:
    pressure_class: str
    diameter_in: float
    frame_allowance_in: float | None
    overall_length_in: float | None
    velocity_fpm: float
    pressure_drop_inwg: float
    leakage_cfm_at_1_inwg: float
    pressure_drop_estimated: bool
    leakage_estimated: bool
    warning: str | None = None


def rvcd_select(diameter_in: float, cfm: float,
                pressure_class: str = "LP") -> RVCDSelection:
    """Calculate RVCD dimensions and estimated loss using the 8-inch anchor."""
    if not math.isfinite(diameter_in) or diameter_in <= 0:
        raise ValueError("RVCD diameter must be finite and positive")
    if not math.isfinite(cfm) or cfm < 0:
        raise ValueError("RVCD airflow must be finite and non-negative")
    pressure_class = pressure_class.upper()
    if pressure_class not in {"LP", "HP"}:
        raise ValueError("RVCD pressure class must be LP or HP")
    frame_allowance = next(
        allowance for upper_bound, allowance in _cd.RVCD_FRAME_ALLOWANCE_IN
        if diameter_in <= upper_bound
    )
    warning = None
    if frame_allowance is None:
        warning = (
            'RVCD frame allowance for diameters 0–9-7/8" is a PDF typo; '
            "dimension must be confirmed with the manufacturer.")
    area_ft2 = math.pi * (diameter_in / 12) ** 2 / 4
    velocity = cfm / area_ft2
    pressure_drop = _cd.RVCD_DP_COEFFICIENT * (
        velocity / _cd.RVCD_REFERENCE_VELOCITY_FPM) ** 2
    leakage = (
        _cd.RVCD_LEAKAGE_CFM_PER_PERIMETER_IN_AT_1_INWG
        * math.pi * diameter_in)
    return RVCDSelection(
        pressure_class=pressure_class,
        diameter_in=diameter_in,
        frame_allowance_in=frame_allowance,
        overall_length_in=(
            diameter_in + frame_allowance
            if frame_allowance is not None else None),
        velocity_fpm=velocity,
        pressure_drop_inwg=pressure_drop,
        leakage_cfm_at_1_inwg=leakage,
        pressure_drop_estimated=True,
        leakage_estimated=True,
        warning=warning,
    )


@dataclass(frozen=True)
class AccessDoorSelection:
    width_in: int
    height_in: int
    min_cfm: int
    max_cfm: int
    area_in2: int
    range_midpoint_cfm: float
    notes: tuple[str, ...] = ()

    @property
    def size_label(self) -> str:
        return f"{self.width_in}×{self.height_in}"


def access_door_select(cfm: float) -> AccessDoorSelection:
    """Select the smallest compatible access-door face area, then nearest range midpoint."""
    if not math.isfinite(cfm) or cfm <= 0:
        raise ValueError("Access-door airflow must be finite and positive")
    candidates = []
    for (width, height), (minimum, maximum) in _cd.ACCESS_DOORS.items():
        if minimum <= cfm <= maximum:
            midpoint = (minimum + maximum) / 2
            candidates.append((
                width * height, abs(cfm - midpoint), width, height,
                minimum, maximum, midpoint))
    if not candidates:
        raise ValueError(
            f"No access-door size is catalogued for {cfm:.0f} CFM")
    area, _, width, height, minimum, maximum, midpoint = min(candidates)
    notes = (
        *_cd.ACCESS_DOOR_CATALOG_WARNINGS,
        *_cd.ACCESS_DOOR_CATALOG_NOTES.get((width, height), ()),
    )
    return AccessDoorSelection(
        width, height, minimum, maximum, area, midpoint, tuple(notes))


def _geometry_min_qty(room: RoomLayout | None) -> int:
    """At least one diffuser per significant L-shape / orthogonal wing."""
    if room is None or room.geometry is None:
        return 1
    try:
        from placement_engine import significant_subrectangle_count
        return max(1, significant_subrectangle_count(room.geometry.vertices))
    except ImportError:
        return 1


def _hard_warning_count(warnings: Sequence[str]) -> int:
    hard_prefixes = (
        "Neck velocity", "ΔP ", "Noise NC", "Supply ΔT",
    )
    hard_needles = ("exceeds Table 4",)
    n = 0
    for warning in warnings:
        if any(warning.startswith(p) for p in hard_prefixes) or any(
                needle in warning for needle in hard_needles):
            n += 1
    return n


def _neck_velocity_penalty(v_fpm: float) -> float:
    """Keep throw-capable neck velocity; do not reward NC 20 from a dead jet."""
    lo, hi = PREFERRED_NECK_FPM
    if v_fpm < lo:
        return 0.18 * (lo - v_fpm)
    if v_fpm > hi:
        return 0.08 * (v_fpm - hi)
    return 0.0


def _evaluate(d: DiffuserModel, n: int, total_cfm: float, room: RoomLayout | None,
              max_nc: float, max_dp: float, cfm_cap: float,
              min_qty: int, delta_t_f: float | None = None,
              include_tl_screening: bool = True
              ) -> DiffuserSelection:
    q = total_cfm / n
    p = performance(d, q)
    viol: list[str] = []
    if not p.in_range:
        viol.append(f"Neck velocity {p.v_fpm:.0f} FPM outside catalog table "
                    f"({d.v[0]:.0f}-{d.v[-1]:.0f})")
    if p.pt > max_dp:
        viol.append(f"ΔP {p.pt:.3f} > limit {max_dp:.2f} in.wg")
    if p.nc > max_nc:
        viol.append(f"Noise NC {p.nc_label} above limit {max_nc:.0f}")
    if q > cfm_cap:
        viol.append(f"{q:.0f} CFM/diffuser exceeds Table 4 limit ({cfm_cap:.0f})")
    if n < min_qty:
        viol.append(f"Only {n} diffuser(s): Table 4 / geometry need ≥{min_qty}")
    if room is not None and room.floor_area_ft2 / n < MIN_AREA_PER_DIFFUSER_FT2:
        viol.append(f"{n} diffusers is too dense (<{MIN_AREA_PER_DIFFUSER_FT2:.0f} ft² each)")
    if room is not None and room.floor_area_ft2 / n > MAX_AREA_PER_DIFFUSER_FT2:
        viol.append(
            f"{n} diffuser(s) cover {room.floor_area_ft2 / n:.0f} ft² each "
            f"(>{MAX_AREA_PER_DIFFUSER_FT2:.0f} ft² rule of thumb)")

    rows = cols = 1
    L = r_hi = r_lo = 0.0
    aspect = 1.0
    ok = True
    spacing = wall = 0.0
    if room is not None:
        g = best_grid(room, n)
        rows, cols, aspect, L = g.rows, g.cols, g.aspect, g.L_min
        lo, hi = _band(d.kind, room)
        r_hi, r_lo, ok, _ = _t50_l_ratios(
            p.t50, p.t50_back, (lo, hi), grid=g)
        if not ok and include_tl_screening:
            viol.append(
                f"T50/L screening {r_lo:.2f}–{r_hi:.2f} outside band {lo}-{hi}")
        spacing = round(2 * p.t50 / lo, 1)
        wall = round(g.L_min, 1)
    adpi_c = (r_hi + r_lo) / 2.0 if room is not None else rules_tl_target()
    lo_b, hi_b = _band(d.kind, room)
    if ok and lo_b <= adpi_c <= hi_b:
        adpi_penalty = 25.0 * abs(adpi_c - rules_tl_target())
    else:
        undershoot = max(0.0, lo_b - r_lo)
        overshoot = max(0.0, r_hi - hi_b)
        adpi_penalty = 800.0 + 250.0 * (undershoot + overshoot)
    # NC compliance is a hard ranking criterion; score only breaks ties.
    score = (
        adpi_penalty
        + 45.0 * n
        + 0.25 * p.nc
        + _neck_velocity_penalty(p.v_fpm)
        + 40.0 * p.pt
        + 6.0 * (aspect - 1.0)
        - 4.0 * d.neck_dia_inch
    )
    return DiffuserSelection(
        model=d.model, size_label=d.size_label, qty=n, cfm_per_unit=round(q, 1),
        neck_velocity_fpm=round(p.v_fpm, 1), neck_area_ft2=d.neck_area_ft2,
        throw_t50_ft=round(p.t50, 1), throw_t100_ft=round(p.t100, 1),
        pressure_drop_inwg=round(p.pt, 4), nc_estimated=round(p.nc, 1),
        spacing_max_ft=spacing, wall_offset_ft=wall, warnings=viol, kind=d.kind,
        nc_label=p.nc_label, rows=rows, cols=cols, char_L_ft=round(L, 2),
        adpi_ratio=round(r_hi, 2), adpi_ratio_min=round(r_lo, 2), adpi_ok=ok,
        score=round(score, 2), delta_t_f=delta_t_f,
        delta_t_limit_f=ceiling_limits(room.room_h_ft)[2]
        if room is not None else None, max_nc_limit=max_nc)


def _distance_to_segment(x: float, y: float, a: tuple[float, float],
                         b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(x - a[0], y - a[1])
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy)
                     / length_squared))
    return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))


def _ray_distance_to_polygon(x: float, y: float, dx: float, dy: float,
                             polygon: Sequence[tuple[float, float]]) -> float:
    """Distance from an interior point to the nearest positive ray/boundary intersection."""
    distance = float("inf")
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        edge = (b[0] - a[0], b[1] - a[1])
        offset = (a[0] - x, a[1] - y)
        denominator = dx * edge[1] - dy * edge[0]
        if abs(denominator) <= 1e-12:
            continue
        ray_t = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
        edge_t = (offset[0] * dy - offset[1] * dx) / denominator
        if ray_t > 1e-9 and -1e-9 <= edge_t <= 1.0 + 1e-9:
            distance = min(distance, ray_t)
    if math.isinf(distance):
        raise ValueError("No positive polygon boundary crossing found for ray")
    return distance


def _t50_l_ratios(
        t50: float, t50_back: float,
        band: tuple[float, float],
        positions: Sequence[tuple[float, float]] | None = None,
        polygon: Sequence[tuple[float, float]] | None = None,
        drop_ft: float = 0.0,
        grid=None) -> tuple[float, float, bool, float]:
    """Estimate geometric T50/L screening, not actual ADPI."""
    lo, hi = band
    if grid is not None:
        r_hi = t50 / grid.L_min
        r_lo = t50_back / grid.L_max
        return r_hi, r_lo, r_hi <= hi and r_lo >= lo, grid.L_min
    if positions is None or polygon is None or len(polygon) < 3:
        raise ValueError("Polygon T50/L screening requires room boundary points")
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
    hi_lengths = []
    lo_lengths = []
    for i, (x, y) in enumerate(positions):
        directional_lengths = []
        for dx_dir, dy_dir in directions:
            wall_d = _ray_distance_to_polygon(x, y, dx_dir, dy_dir, polygon)
            neighbour_halves = []
            for j, (ox, oy) in enumerate(positions):
                if i == j:
                    continue
                dx, dy = ox - x, oy - y
                in_cone = (
                    (dx_dir > 0 and dx > 0 and abs(dy) <= abs(dx))
                    or (dx_dir < 0 and dx < 0 and abs(dy) <= abs(dx))
                    or (dy_dir > 0 and dy > 0 and abs(dx) <= abs(dy))
                    or (dy_dir < 0 and dy < 0 and abs(dx) <= abs(dy))
                )
                if in_cone:
                    # R-CHAR-L: mixed jets -> half spacing + drop to occupied zone
                    neighbour_halves.append(math.hypot(dx, dy) / 2.0 + drop_ft)
            nb_d = min(neighbour_halves, default=float("inf"))
            directional_lengths.append(min(wall_d, nb_d))
        hi_lengths.append(min(directional_lengths))
        lo_lengths.append(max(directional_lengths))

    r_hi = max((t50 / max(length, 0.1) for length in hi_lengths), default=0.0)
    r_lo = min((t50_back / max(length, 0.1) for length in lo_lengths), default=0.0)
    ok = r_hi <= hi and r_lo >= lo
    characteristic = min(hi_lengths, default=0.0)
    return r_hi, r_lo, ok, characteristic


_ARG_UNSET = object()


def evaluate_layout(total_cfm: float,
                    *args,
                    positions_ft: Sequence[tuple[float, float]] | None | object = _ARG_UNSET,
                    room: RoomLayout | None | object = _ARG_UNSET,
                    application: str | object = _ARG_UNSET,
                    catalog: list[DiffuserModel] | object = _ARG_UNSET,
                    max_nc: float | None | object = _ARG_UNSET,
                    max_dp_inwg: float | object = _ARG_UNSET,
                    delta_t_f: float | None | object = _ARG_UNSET,
                    top: int | object = _ARG_UNSET,
                    total_load_btuh: float | None | object = _ARG_UNSET,
                    sensible_load_btuh: float | None | object = _ARG_UNSET
                    ) -> tuple[DiffuserSelection, list[DiffuserSelection]]:
    """Evaluate a fixed layout using ``(total_cfm, positions_ft, room, ...)``.

    Also accepts the older ``(total_cfm, application, catalog, ...)`` order
    when ``positions_ft`` and ``room`` are supplied by keyword.
    """
    values = {
        "positions_ft": positions_ft,
        "room": room,
        "application": application,
        "catalog": catalog,
        "max_nc": max_nc,
        "max_dp_inwg": max_dp_inwg,
        "delta_t_f": delta_t_f,
        "top": top,
        "total_load_btuh": total_load_btuh,
        "sensible_load_btuh": sensible_load_btuh,
    }
    if args and isinstance(args[0], str):
        if len(args) > 2:
            raise TypeError("Legacy evaluate_layout calls accept application and catalog positionally")
        legacy_names = ("application", "catalog")
        for name, value in zip(legacy_names, args, strict=False):
            if values[name] is not _ARG_UNSET:
                raise TypeError(
                    f"evaluate_layout got multiple values for {name}")
            values[name] = value
    elif args:
        positional_names = (
            "positions_ft", "room", "application", "catalog", "max_nc",
            "max_dp_inwg", "delta_t_f", "top", "total_load_btuh",
            "sensible_load_btuh",
        )
        if len(args) > len(positional_names):
            raise TypeError("evaluate_layout received too many positional arguments")
        for name, value in zip(positional_names, args, strict=False):
            if values[name] is not _ARG_UNSET:
                raise TypeError(
                    f"evaluate_layout got multiple values for {name}")
            values[name] = value
    defaults = {
        "positions_ft": None,
        "room": None,
        "application": "office_priv",
        "catalog": ANDALOSIA_SUPPLY_CCD,
        "max_nc": None,
        "max_dp_inwg": 0.20,
        "delta_t_f": None,
        "top": 5,
        "total_load_btuh": None,
        "sensible_load_btuh": None,
    }
    values = {
        name: default if value is _ARG_UNSET else value
        for name, default in defaults.items()
        for value in (values[name],)
    }
    positions_ft = values["positions_ft"]
    room = values["room"]
    application = values["application"]
    catalog = values["catalog"]
    max_nc = values["max_nc"]
    max_dp_inwg = values["max_dp_inwg"]
    delta_t_f = values["delta_t_f"]
    top = values["top"]
    total_load_btuh = values["total_load_btuh"]
    sensible_load_btuh = values["sensible_load_btuh"]
    if positions_ft is None:
        raise TypeError("evaluate_layout requires positions_ft")
    if room is None:
        raise TypeError("evaluate_layout requires room")
    if not math.isfinite(total_cfm) or total_cfm <= 0:
        raise ValueError("total_cfm must be finite and > 0")
    _reject_total_load(total_load_btuh)
    sensible_load = sensible_load_btuh
    delta_t_f = _resolve_delta_t(total_cfm, delta_t_f, sensible_load)
    room = _with_load(room, sensible_load)
    if not positions_ft:
        raise ValueError("At least one diffuser position is required")
    if room.polygon_ft:
        if room.geometry is not None:
            outside = [
                index + 1 for index, point in enumerate(positions_ft)
                if not room.geometry.contains_point(ft_to_m(point[0]),
                                                    ft_to_m(point[1]))
            ]
        else:
            outside = [
                index + 1 for index, point in enumerate(positions_ft)
                if not point_in_polygon(point[0], point[1], room.polygon_ft)
            ]
        if outside:
            raise ValueError(
                "Diffuser positions outside room polygon: "
                + ", ".join(map(str, outside)))
    if max_nc is None or max_nc <= 0:
        max_nc = APPLICATION_NC.get(application, 35)

    height_row, cfm_cap, dt_max, notes = ceiling_limits(room.room_h_ft)
    min_qty = max(
        1,
        math.ceil(total_cfm / cfm_cap),
        math.ceil(room.floor_area_ft2 / MAX_AREA_PER_DIFFUSER_FT2),
        _geometry_min_qty(room),
    )
    extra = list(notes)
    delta_t_warning = (
        f"Supply ΔT {delta_t_f:.0f}°F exceeds Table 4 limit "
        f"{dt_max}°F for the {height_row} ft row."
        if delta_t_f is not None and delta_t_f > dt_max else None)

    candidates = []
    polygon = room.polygon_ft or [
        (0.0, 0.0), (room.room_w_ft, 0.0),
        (room.room_w_ft, room.room_l_ft), (0.0, room.room_l_ft),
    ]
    for model in catalog:
        sel = _evaluate(
            model, len(positions_ft), total_cfm, room, max_nc,
            max_dp_inwg, cfm_cap, min_qty, delta_t_f,
            include_tl_screening=False)
        p = performance(model, total_cfm / len(positions_ft))
        band = _band(model.kind, room)
        r_hi, r_lo, adpi_ok, char_l = _t50_l_ratios(
            p.t50, p.t50_back, band, positions_ft, polygon,
            rules_drop_to_occupied_zone_ft(room.room_h_ft))
        sel.adpi_ratio = round(r_hi, 2)
        sel.adpi_ratio_min = round(r_lo, 2)
        sel.adpi_ok = adpi_ok
        sel.char_L_ft = round(char_l, 2)
        sel.spacing_max_ft = round(2 * p.t50 / band[0], 1)
        sel.wall_offset_ft = round(char_l, 1)
        if not adpi_ok:
            lo, hi = band
            sel.warnings.append(
                f"T50/L screening {r_lo:.2f}–{r_hi:.2f} outside band {lo}-{hi}")
        sel.warnings.extend(extra)
        sel.delta_t_f = delta_t_f
        sel.delta_t_limit_f = float(dt_max)
        geometry = room.geometry
        is_rectangular_polygon = False
        if geometry is not None and len(geometry.vertices) == 4:
            min_x, max_x, min_y, max_y = geometry.bounding_box
            corners = {
                (min_x, min_y), (max_x, min_y),
                (max_x, max_y), (min_x, max_y),
            }
            is_rectangular_polygon = all(
                any(math.dist(point, corner) <= 1e-8 for corner in corners)
                for point in geometry.vertices)
        aspect = (
            max(room.room_w_ft, room.room_l_ft) / max(
                min(room.room_w_ft, room.room_l_ft), 0.1)
            if geometry is None or is_rectangular_polygon else 1.0)
        adpi_c = (r_hi + r_lo) / 2.0
        lo_b, hi_b = band
        if adpi_ok and lo_b <= adpi_c <= hi_b:
            adpi_penalty = 25.0 * abs(adpi_c - rules_tl_target())
        else:
            undershoot = max(0.0, lo_b - r_lo)
            overshoot = max(0.0, r_hi - hi_b)
            adpi_penalty = 800.0 + 250.0 * (undershoot + overshoot)
        sel.score = round(
            adpi_penalty
            + 45.0 * sel.qty
            + 0.25 * p.nc
            + _neck_velocity_penalty(p.v_fpm)
            + 40.0 * p.pt
            + 6.0 * (aspect - 1.0)
            - 4.0 * model.neck_dia_inch,
            2)
        sel.delta_t_f = delta_t_f
        sel.delta_t_limit_f = dt_max
        sel.max_nc_limit = float(max_nc)
        sel.rationale = _selection_rationale(sel)
        sel.rationale.update(tl_band=band, violated_rules=violated_rule_ids(sel.warnings))
        candidates.append(sel)

    candidates.sort(key=_selection_rank_key)
    if not candidates:
        raise RuntimeError("No catalog diffuser can handle this airflow.")
    selected = candidates[:max(0, top)]
    if delta_t_warning:
        # A room-level warning must not influence ranking among models.
        for candidate in selected:
            candidate.warnings.append(delta_t_warning)
            candidate.rationale["violated_rules"] = violated_rule_ids(
                candidate.warnings)
    return selected[0] if selected else candidates[0], selected


def _selection_rank_key(sel: DiffuserSelection) -> tuple:
    """Prefer catalog-valid, throw-screened options before acoustic ties."""
    catalog_warning = any(
        warning.startswith("Neck velocity ") for warning in sel.warnings)
    table4_airflow_warning = any(
        "CFM/diffuser exceeds Table 4" in warning for warning in sel.warnings)
    acoustic_warning = any(
        warning.startswith("Noise NC ") for warning in sel.warnings)
    return (
        catalog_warning,
        table4_airflow_warning,
        0 if sel.adpi_ok else 1,
        acoustic_warning,
        sel.qty,
        -sel.neck_area_ft2,
        _hard_warning_count(sel.warnings),
        sel.score,
    )


def _selection_rationale(sel: DiffuserSelection) -> dict[str, object]:
    """Structured explanation of the criteria met by a ranked candidate."""
    noise_ok = not any(
        warning.startswith("Noise NC ") for warning in sel.warnings)
    table4_ok = not any(
        "CFM/diffuser exceeds Table 4" in warning for warning in sel.warnings)
    return {
        "catalog_valid": not any(
            warning.startswith("Neck velocity ") for warning in sel.warnings),
        "noise_compliant": noise_ok,
        "t50_l_screening_pass": sel.adpi_ok,
        "table4_airflow_compliant": table4_ok,
        "quantity": sel.qty,
        "summary": (
            f"Selected {sel.qty} × {sel.size_label} for "
            f"T50/L screening {'pass' if sel.adpi_ok else 'review'}; "
            f"NC {'meets' if noise_ok else 'exceeds'} the configured limit."
        ),
    }


def _projection_axis(points: list[tuple[float, float]]) -> tuple[float, float]:
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    xx = sum((x - mean_x) ** 2 for x, _ in points)
    xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    yy = sum((y - mean_y) ** 2 for _, y in points)
    eigenvalue = (
        xx + yy + math.sqrt((xx - yy) ** 2 + 4.0 * xy ** 2)
    ) / 2.0
    axis = (xy, eigenvalue - xx)
    if math.hypot(*axis) <= 1e-12:
        axis = (1.0, 0.0) if xx >= yy else (0.0, 1.0)
    length = math.hypot(*axis)
    axis = (axis[0] / length, axis[1] / length)
    if axis[0] < 0 or (abs(axis[0]) < 1e-12 and axis[1] < 0):
        axis = (-axis[0], -axis[1])
    return axis


def _alternative_irregular_layouts(
        geometry: RoomGeometry, quantity: int, wall_clearance_m: float,
        min_spacing_m: float,
        existing: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Generate deterministic, strictly verified layouts for irregular rooms."""
    import placement_engine

    vertices = geometry.vertices
    min_x, max_x, min_y, max_y = geometry.bounding_box
    step = IRREGULAR_LAYOUT_SAMPLE_STEP_M
    samples = []
    y = min_y + step / 2.0
    while y <= max_y - step / 2.0 + 1e-9:
        x = min_x + step / 2.0
        while x <= max_x - step / 2.0 + 1e-9:
            point = (round(x, 6), round(y, 6))
            if (placement_engine.point_in_polygon(point, vertices)
                    and placement_engine.distance_to_boundary(point, vertices)
                    >= wall_clearance_m - 1e-6):
                samples.append(point)
            x += step
        y += step
    samples = sorted(set(samples))
    if len(samples) < quantity:
        return []

    axis = _projection_axis(samples)
    projection = [
        x * axis[0] + y * axis[1] for x, y in samples
    ]

    def snap_to_cells(
            centers: list[tuple[float, float]],
            labels: list[int]) -> list[tuple[float, float]] | None:
        per_cell = []
        for index, center in enumerate(centers):
            members = [
                point for point, label in zip(samples, labels, strict=True)
                if label == index
            ]
            if not members:
                return None
            members.sort(key=lambda point: (
                (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2,
                point,
            ))
            per_cell.append(members[:32])

        cell_order = sorted(
            range(len(centers)),
            key=lambda i: (centers[i][0], centers[i][1], i),
        )
        chosen: list[tuple[float, float]] = []
        assignments = 0

        def assign(position: int) -> list[tuple[float, float]] | None:
            nonlocal assignments
            if position == len(cell_order):
                return chosen.copy()
            for point in per_cell[cell_order[position]]:
                assignments += 1
                if assignments > IRREGULAR_LAYOUT_MAX_ASSIGNMENTS:
                    return None
                if all(math.dist(point, prior) >= min_spacing_m - 1e-6
                       for prior in chosen):
                    chosen.append(point)
                    result = assign(position + 1)
                    if result is not None:
                        return result
                    chosen.pop()
            return None

        return assign(0)

    seeds: list[list[tuple[float, float]]] = []
    if len(existing) == quantity:
        seeds.append(list(existing))
    ordered = sorted(range(len(samples)), key=lambda i: (projection[i], i))
    seeds.append([
        samples[ordered[min(
            len(ordered) - 1,
            int((index + 0.5) * len(ordered) / quantity),
        )]]
        for index in range(quantity)
    ])
    center = (
        sum(point[0] for point in samples) / len(samples),
        sum(point[1] for point in samples) / len(samples),
    )
    first = max(
        range(len(samples)),
        key=lambda i: (
            (samples[i][0] - center[0]) ** 2
            + (samples[i][1] - center[1]) ** 2,
            -i,
        ),
    )
    farthest = [samples[first]]
    while len(farthest) < quantity:
        next_index = max(
            range(len(samples)),
            key=lambda i: (
                min(math.dist(samples[i], point) ** 2 for point in farthest),
                -i,
            ),
        )
        farthest.append(samples[next_index])
    seeds.append(farthest)

    layouts: list[list[tuple[float, float]]] = []
    seen: set[tuple[tuple[float, float], ...]] = set()

    def add_verified(points: list[tuple[float, float]] | None) -> None:
        if points is None:
            return
        layout = tuple(sorted(
            (round(point[0], 6), round(point[1], 6)) for point in points
        ))
        if layout in seen:
            return
        seen.add(layout)
        if placement_engine.verify_placement(
                vertices, layout, wall_clearance_m, min_spacing_m).valid:
            layouts.append(list(layout))

    balanced_labels = [0] * len(samples)
    balanced_centers = []
    for index in range(quantity):
        base_size, remainder = divmod(len(samples), quantity)
        start = index * base_size + min(index, remainder)
        end = start + base_size + int(index < remainder)
        indices = ordered[start:end]
        for sample_index in indices:
            balanced_labels[sample_index] = index
        balanced_centers.append((
            sum(samples[i][0] for i in indices) / len(indices),
            sum(samples[i][1] for i in indices) / len(indices),
        ))
    add_verified(snap_to_cells(balanced_centers, balanced_labels))

    for initial in seeds:
        centers = initial.copy()
        for _ in range(IRREGULAR_LAYOUT_RELAXATION_ITERATIONS):
            labels = [
                min(
                    range(quantity),
                    key=lambda i: (
                        (point[0] - centers[i][0]) ** 2
                        + (point[1] - centers[i][1]) ** 2,
                        i,
                    ),
                )
                for point in samples
            ]
            updated = []
            for index, center_point in enumerate(centers):
                members = [
                    point for point, label in zip(samples, labels, strict=True)
                    if label == index
                ]
                updated.append(
                    (sum(point[0] for point in members) / len(members),
                     sum(point[1] for point in members) / len(members))
                    if members else center_point
                )
            if max(math.dist(a, b) for a, b in zip(
                    updated, centers, strict=True)) < 1e-4:
                centers = updated
                break
            centers = updated
        labels = [
            min(
                range(quantity),
                key=lambda i: (
                    (point[0] - centers[i][0]) ** 2
                    + (point[1] - centers[i][1]) ** 2,
                    i,
                ),
            )
            for point in samples
        ]
        add_verified(snap_to_cells(centers, labels))

    return layouts


def rank_options(total_cfm: float, application: str = "office_priv",
                 catalog: list[DiffuserModel] = ANDALOSIA_SUPPLY_CCD,
                 max_nc: float | None = None, max_dp_inwg: float = 0.20,
                 room: RoomLayout | None = None, delta_t_f: float | None = None,
                 max_qty: int = 24, top: int = 8,
                 total_load_btuh: float | None = None,
                 sensible_load_btuh: float | None = None,
                 strict: bool = False
                 ) -> list[DiffuserSelection]:
    """All catalog candidates, best first (strict=True drops any candidate that violates a rule).  Candidates with warnings=[] pass every rule.

    Search starts at the *minimum* count allowed by Table 4 CFM (ceiling
    height), 400 ft² coverage guidance, and L-shape wing coverage, then walks
    upward. Ranking prefers ADPI T50/L in 0.7–1.2 and the fewest / largest
    necks that still throw.
    """
    if not math.isfinite(total_cfm) or total_cfm <= 0:
        raise ValueError("total_cfm must be finite and > 0")
    _reject_total_load(total_load_btuh)
    sensible_load = sensible_load_btuh
    delta_t_f = _resolve_delta_t(total_cfm, delta_t_f, sensible_load)
    room = _with_load(room, sensible_load)
    if max_nc is None or max_nc <= 0:
        max_nc = APPLICATION_NC.get(application, 35)
    height = room.room_h_ft if room is not None else 10.0
    h_row, cfm_cap, dt_max, notes = ceiling_limits(height)
    extra = list(notes)
    delta_t_warning = (
        f"Supply ΔT {delta_t_f:.1f}°F exceeds Table 4 limit "
        f"{dt_max}°F for the {h_row} ft row."
        if delta_t_f is not None and delta_t_f > dt_max else None)
    if strict and delta_t_warning:
        return []

    min_qty_capacity = max(1, math.ceil(total_cfm / cfm_cap))
    min_qty_area = (
        max(1, math.ceil(room.floor_area_ft2 / MAX_AREA_PER_DIFFUSER_FT2))
        if room is not None else 1)
    min_qty_wings = _geometry_min_qty(room)
    qty_lo = max(1, min_qty_capacity, min_qty_area, min_qty_wings)

    qty_hi = max_qty
    if room is not None and room.floor_area_ft2 > 0:
        density_ceiling = max(
            qty_lo,
            math.floor(room.floor_area_ft2 / MIN_AREA_PER_DIFFUSER_FT2),
        )
        qty_hi = min(max_qty, density_ceiling)
    qty_hi = max(qty_lo, qty_hi)

    models = sorted(catalog, key=lambda model: -model.neck_dia_inch)
    cands: list[DiffuserSelection] = []
    for d in models:
        for n in range(qty_lo, qty_hi + 1):
            q = total_cfm / n
            v = q / d.neck_area_ft2
            if v < d.v[0] * 0.75 or v > d.v[-1] * 1.25:
                continue
            c = _evaluate(d, n, total_cfm, room, max_nc, max_dp_inwg,
                          cfm_cap, qty_lo)
            c.warnings = c.warnings + extra
            c.delta_t_f = delta_t_f
            c.delta_t_limit_f = float(dt_max)
            c.max_nc_limit = float(max_nc)
            c.rationale = _selection_rationale(c)
            c.rationale.update(tl_band=_band(d.kind, room),
                               violated_rules=violated_rule_ids(c.warnings))
            cands.append(c)
    if strict:
        cands = [c for c in cands if not c.rationale["violated_rules"]]
    cands.sort(key=_selection_rank_key)
    selected = cands[:top]
    if delta_t_warning:
        # This room-level violation is reported after ranking so it cannot
        # bias every candidate's otherwise-equivalent hard-warning score.
        for candidate in selected:
            candidate.warnings.append(delta_t_warning)
            candidate.rationale["violated_rules"] = violated_rule_ids(
                candidate.warnings)
    return selected


def select_diffuser(total_cfm: float, application: str = "office_priv",
                    catalog: list[DiffuserModel] = ANDALOSIA_SUPPLY_CCD,
                    max_nc: float | None = None, max_dp_inwg: float = 0.20,
                    room: RoomLayout | None = None,
                    delta_t_f: float | None = None,
                    total_load_btuh: float | None = None,
                    sensible_load_btuh: float | None = None,
                    strict: bool = False
                    ) -> DiffuserSelection:
    """Best option.  If nothing satisfies every rule the least-bad one is returned
    with the violated rules listed in .warnings (never silent)."""
    if room is not None and room.room_h_ft > max(CEILING_TABLE):
        raise ValueError(
            f"Ceiling height {room.room_h_ft:.1f} ft exceeds validated "
            f"Table 4 data (maximum {max(CEILING_TABLE)} ft); manufacturer "
            "review is required.")
    opts = rank_options(total_cfm, application, catalog, max_nc, max_dp_inwg,
                        room, delta_t_f, top=1,
                        total_load_btuh=total_load_btuh,
                        sensible_load_btuh=sensible_load_btuh, strict=strict)
    if not opts:
        raise RuntimeError("No catalog diffuser can handle this airflow"
                           + (" without violating a selection rule." if strict else "."))
    return opts[0]


@dataclass
class DiffuserLayoutResult:
    success: bool
    geometry: RoomGeometry
    requested_quantity: int
    recommended_quantity: int = 0
    placed_quantity: int = 0
    required_cfm: float = 0.0
    actual_total_cfm: float = 0.0
    selected_model: DiffuserSelection | None = None
    positions_m: list[tuple[float, float]] = field(default_factory=list)
    positions_ft: list[tuple[float, float]] = field(default_factory=list)
    performance: DiffuserSelection | None = None
    wall_clearance_ok: bool = False
    spacing_ok: bool = False
    failure_kind: str | None = None
    fallback_available: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def select_layout_for_geometry(
        geometry: RoomGeometry, total_cfm: float,
        application: str = "office_priv",
        catalog: list[DiffuserModel] | None = None,
        max_nc: float | None = None, max_dp_inwg: float = 0.20,
        delta_t_f: float | None = None,
        min_wall_clearance_m: float = 0.30,
        min_spacing_m: float = 0.60,
        total_load_btuh: float | None = None,
        sensible_load_btuh: float | None = None) -> DiffuserLayoutResult:
    """Select, place, and evaluate one complete layout for actual geometry."""
    errors = geometry.validate()
    if errors:
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="invalid_geometry",
            errors=["Invalid room geometry: " + "; ".join(errors)])
    if total_cfm <= 0 or not math.isfinite(total_cfm):
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="airflow_infeasible",
            errors=["Required airflow must be finite and greater than zero."])
    try:
        _reject_total_load(total_load_btuh)
        sensible_load = sensible_load_btuh
        delta_t_f = _resolve_delta_t(total_cfm, delta_t_f, sensible_load)
    except ValueError as exc:
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="airflow_infeasible", errors=[str(exc)])

    active_catalog = ANDALOSIA_SUPPLY_CCD if catalog is None else catalog
    room = room_layout_from_geometry(geometry)
    height_row, _, delta_t_limit_f, height_notes = ceiling_limits(
        room.room_h_ft)
    if room.room_h_ft > max(CEILING_TABLE):
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="unsupported_height",
            warnings=height_notes, errors=height_notes)
    if delta_t_f is not None and delta_t_f > delta_t_limit_f:
        message = (
            f"Calculated sensible ΔT {delta_t_f:.1f}°F exceeds the Table 4 "
            f"limit of {delta_t_limit_f}°F for the {height_row} ft row; "
            "review system airflow or supply-air temperature.")
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="delta_t_exceeded", errors=[message])

    options = rank_options(
        total_cfm, application, active_catalog, max_nc, max_dp_inwg,
        room, delta_t_f, max_qty=24,
        # Bound expensive geometry placement while retaining alternate
        # quantities for the ranked catalog candidates.
        top=24,
        sensible_load_btuh=sensible_load)
    if not options:
        return DiffuserLayoutResult(
            False, geometry, 0, required_cfm=total_cfm,
            failure_kind="airflow_infeasible",
            errors=["No diffuser catalog option satisfies the airflow constraints."])

    recommended_quantity = options[0].qty
    feasible_layouts = []
    tested_options = set()
    placement_errors = []
    from placement_engine import is_orthogonal_polygon
    irregular_room = not is_orthogonal_polygon(geometry.vertices)
    is_rectangular_room = False
    if len(geometry.vertices) == 4:
        min_x, max_x, min_y, max_y = geometry.bounding_box
        corners = {
            (min_x, min_y), (max_x, min_y),
            (max_x, max_y), (min_x, max_y),
        }
        is_rectangular_room = all(
            any(math.dist(point, corner) <= 1e-8 for corner in corners)
            for point in geometry.vertices)
    # R-MIN-THROW is disabled by default: max(W,L)/2 has no basis in the docs.
    minimum_throw_ft = (room.max_dim_ft / 2.0 if is_rectangular_room
                        and rules_min_throw_prefilter_enabled() else 0.0)
    longest_throw_ft = max(option.throw_t50_ft for option in options)
    for option in options:
        if option.throw_t50_ft < minimum_throw_ft:
            continue
        quantity = option.qty
        option_key = (quantity, option.model, option.size_label)
        if option_key in tested_options:
            continue
        tested_options.add(option_key)
        model = next(
            (candidate for candidate in active_catalog
             if (candidate.model, candidate.size_label)
             == (option.model, option.size_label)),
            None,
        )
        if model is None:
            continue
        # Placement enforces physical clearances; verified layouts are ranked
        # by the selected model's throw-aware actual-polygon screening.
        face_half_extent_m = diffuser_face_half_extent_m(option)
        placement = place_diffusers_in_geometry(
            geometry, quantity, min_wall_clearance_m, min_spacing_m,
            throw_t50_m=ft_to_m(option.throw_t50_ft),
            face_half_extent_m=face_half_extent_m)
        if not placement.success:
            placement_errors.extend(placement.errors)
            continue
        positions_ft = [(m_to_ft(x), m_to_ft(y))
                        for x, y in placement.positions_m]
        try:
            evaluated, _ = evaluate_layout(
                total_cfm, positions_ft=positions_ft, room=room,
                application=application, catalog=[model], max_nc=max_nc,
                max_dp_inwg=max_dp_inwg, delta_t_f=delta_t_f, top=1,
                sensible_load_btuh=sensible_load)
        except (RuntimeError, ValueError) as exc:
            placement_errors.append(str(exc))
            continue
        feasible_layouts.append((evaluated, placement, positions_ft))

    configured_nc = (
        max_nc if max_nc is not None and max_nc > 0
        else APPLICATION_NC.get(application, 35))
    default_fully_compliant = any(
        candidate[0].adpi_ok
        and candidate[0].nc_estimated <= configured_nc
        and not any("CFM/diffuser exceeds Table 4" in warning
                    for warning in candidate[0].warnings)
        and not any(warning.startswith("Neck velocity ")
                    for warning in candidate[0].warnings)
        for candidate in feasible_layouts
    )
    if irregular_room and not default_fully_compliant:
        for evaluated, placement, _ in tuple(feasible_layouts):
            if evaluated.adpi_ok:
                continue
            model = next(
                (candidate for candidate in active_catalog
                 if (candidate.model, candidate.size_label)
                 == (evaluated.model, evaluated.size_label)),
                None,
            )
            if model is None:
                continue
            face_half_extent_m = diffuser_face_half_extent_m(evaluated)
            wall_clearance_m = min_wall_clearance_m + face_half_extent_m
            default_positions = tuple(sorted(placement.positions_m))
            for alternative_positions in _alternative_irregular_layouts(
                    geometry, evaluated.qty, wall_clearance_m,
                    min_spacing_m, placement.positions_m):
                if tuple(alternative_positions) == default_positions:
                    continue
                positions_ft = [
                    (m_to_ft(x), m_to_ft(y))
                    for x, y in alternative_positions
                ]
                try:
                    alternative, _ = evaluate_layout(
                        total_cfm, positions_ft=positions_ft, room=room,
                        application=application, catalog=[model],
                        max_nc=max_nc, max_dp_inwg=max_dp_inwg,
                        delta_t_f=delta_t_f, top=1,
                        sensible_load_btuh=sensible_load)
                except (RuntimeError, ValueError) as exc:
                    placement_errors.append(str(exc))
                    continue
                fully_compliant_alternative = (
                    alternative.adpi_ok
                    and alternative.nc_estimated <= configured_nc
                    and not any(
                        "CFM/diffuser exceeds Table 4" in warning
                        for warning in alternative.warnings
                    )
                    and not any(
                        warning.startswith("Neck velocity ")
                        for warning in alternative.warnings
                    )
                )
                if fully_compliant_alternative:
                    placement_result = GeometryPlacementResult(
                        True, evaluated.qty,
                        positions_m=alternative_positions,
                        wall_clearance_ok=True,
                        spacing_ok=True,
                        throw_t50_m=ft_to_m(alternative.throw_t50_ft),
                    )
                    feasible_layouts.append(
                        (alternative, placement_result, positions_ft))

    if not feasible_layouts:
        if (minimum_throw_ft > 0
                and all(option.throw_t50_ft < minimum_throw_ft
                        for option in options)):
            return DiffuserLayoutResult(
                False, geometry, recommended_quantity,
                recommended_quantity=recommended_quantity,
                required_cfm=total_cfm, failure_kind="insufficient_throw",
                errors=[
                    (f"No catalog option provides the required minimum T50 "
                    f"throw of {minimum_throw_ft:.1f} ft (half the room's "
                    f"{room.max_dim_ft:.1f} ft longest dimension); the "
                    f"longest available T50 is {longest_throw_ft:.1f} ft.")
                ])
        return DiffuserLayoutResult(
            False, geometry, recommended_quantity,
            recommended_quantity=recommended_quantity,
            required_cfm=total_cfm,
            failure_kind="geometry_infeasible",
            errors=[
                (f"Geometry-infeasible: no complete layout could place the "
                f"recommended {recommended_quantity} diffusers or an "
                "airflow-feasible alternative."),
                ("Note: placement requires each diffuser inside the room "
                "polygon with wall clearance. In irregular rooms this may "
                "be unsatisfiable — consider a longer-throw outlet or "
                "place diffusers manually in Room Designer."),
                *placement_errors[:3],
            ])

    selection, placement, positions_ft = min(
        feasible_layouts,
        key=lambda item: _selection_rank_key(item[0]))
    fully_compliant = [
        candidate for candidate in feasible_layouts
        if candidate[0].adpi_ok
        and candidate[0].nc_estimated <= configured_nc
        and not any("CFM/diffuser exceeds Table 4" in warning
                    for warning in candidate[0].warnings)
        and not any(warning.startswith("Neck velocity ")
                    for warning in candidate[0].warnings)
    ]
    if not fully_compliant:
        any_noise_compliant = any(
            candidate[0].nc_estimated <= configured_nc
            for candidate in feasible_layouts)
        acoustic_failure = not any_noise_compliant
        reason = (
            f"No candidate meets the configured NC limit of "
            f"{configured_nc:.0f}; no acoustically compliant layout is available."
            if acoustic_failure else
            "No candidate meets all T50/L, Table 4, and acoustic screening criteria."
        )
        status = (
            "no_acoustically_compliant_option"
            if acoustic_failure else "no_fully_compliant_option"
        )
        return DiffuserLayoutResult(
            False, geometry, recommended_quantity,
            recommended_quantity=recommended_quantity,
            placed_quantity=len(positions_ft),
            required_cfm=total_cfm, actual_total_cfm=total_cfm,
            selected_model=selection, positions_m=placement.positions_m,
            positions_ft=positions_ft, wall_clearance_ok=placement.wall_clearance_ok,
            spacing_ok=placement.spacing_ok, fallback_available=True,
            performance=selection, failure_kind=status,
            warnings=list(selection.warnings),
            errors=[reason, selection.rationale.get("summary", "")],
        )
    selection, placement, positions_ft = min(
        fully_compliant, key=lambda item: _selection_rank_key(item[0]))
    result = DiffuserLayoutResult(
        False, geometry, recommended_quantity,
        recommended_quantity=recommended_quantity, required_cfm=total_cfm,
        selected_model=selection, warnings=list(selection.warnings),
        failure_kind="geometry_infeasible")
    result.success = True
    result.failure_kind = None
    result.placed_quantity = len(positions_ft)
    result.actual_total_cfm = total_cfm
    result.positions_m = placement.positions_m
    result.positions_ft = positions_ft
    result.selected_model = selection
    result.performance = selection
    result.wall_clearance_ok = placement.wall_clearance_ok
    result.spacing_ok = placement.spacing_ok
    result.warnings = list(selection.warnings)
    if selection.qty != recommended_quantity:
        result.warnings.insert(
            0,
            f"Geometry-aware selection used {selection.qty} diffusers instead "
            f"of the nominal {recommended_quantity}-diffuser recommendation; "
            "the full required airflow is retained.")
    return result


def wall_proximity_advice(sel: DiffuserSelection, distance_to_wall_ft: float) -> str:
    """4-way vs 3-way: does the throw overshoot a wall at this distance?"""
    _, hi = ADPI_BANDS.get("square", (0.7, 1.2))
    r = sel.throw_t50_ft / max(distance_to_wall_ft, 0.1)
    if r > hi:
        return (f"T50/L = {r:.2f} > {hi}: jet would hit the wall – block that side "
                f"(use 3-way 3SCD) or move the diffuser {sel.throw_t50_ft/hi - distance_to_wall_ft:.1f} ft away.")
    return f"T50/L = {r:.2f} ≤ {hi}: 4-way is fine at this distance."


def format_report(sel: DiffuserSelection, room: RoomLayout, total_cfm: float,
                  application: str) -> str:
    ac = ach(total_cfm, room)
    lines = [
        f"═══ Al-Andalosia Diffuser Selection — {application} ═══",
        "Manufacturer         : AL-ANDALOSIA FOR AIR OUTLETS",
        f"Total airflow        : {total_cfm:,.0f} CFM",
        f"Room Dimensions      : {room.room_w_ft:.1f} × {room.room_l_ft:.1f} × {room.room_h_ft:.1f} ft ({room.floor_area_ft2:,.0f} ft²)",
        f"Air changes per hour : {ac:.1f} ACH",
        f"Layout               : {sel.rows} × {sel.cols}  (L = {sel.char_L_ft:.1f} ft)",
        (f"T50/L screening     : {sel.adpi_ratio_min:.2f} – {sel.adpi_ratio:.2f}  "
        f"(screening band 0.7–1.2, "
        f"{'within band' if sel.adpi_ok else 'review'}; ADPI not verified)"),
        "Supply ΔT            : "
        + (f"{sel.delta_t_f:.1f}°F (Table 4 max {sel.delta_t_limit_f:.0f}°F)"
           if sel.delta_t_f is not None and sel.delta_t_limit_f is not None
           else "n/a"),
        "",
        "── Selected Al-Andalosia Model ──",
        f"Model / Type         : {sel.model} ({sel.size_label})",
        f"Quantity             : {sel.qty} unit(s)",
        f"Airflow per diffuser : {sel.cfm_per_unit:,.0f} CFM",
        f"Neck velocity        : {sel.neck_velocity_fpm:,.0f} FPM",
        f"Total Pressure Drop  : {sel.pressure_drop_inwg:.4f} in.wg",
        f"Sound (catalog)      : NC {sel.nc_label}",
        f"Throw Range (T100-T50): {sel.throw_t100_ft:.1f} - {sel.throw_t50_ft:.1f} ft",
        f"Max Spacing          : {sel.spacing_max_ft:.1f} ft",
    ]
    for w in sel.warnings:
        lines.append(f"⚠ {w}")
    return "\n".join(lines)
