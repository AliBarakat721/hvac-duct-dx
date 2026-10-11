"""
placement_engine.py
===================
Pure-geometry diffuser placement engine and exact verifier.

Scope
-----
    Phase 1:
        - Geometry predicates (point-in-polygon, point-to-segment distance)
        - Polygon validation (simple, non-self-intersecting, non-degenerate)
        - Exact mathematical placement verifier
        - Data contracts (SearchBudget, PlacementResult, VerificationReport)
        - PlacementStatus enum

    Phase 2 (Corrected):
        - Geometric classification (is_axis_aligned_rectangle -> bool, is_orthogonal_polygon -> bool)
        - Exact orthogonal decomposition into non-overlapping sub-rectangles via plane-sweep
        - Safe-core inset rectangular placement (strictly respecting wall clearance by construction)
        - Tier A: Analytical Rectangular Grid Solver
        - Tier B: Orthogonal Polygon Decomposition Solver
        - Honest, unambiguous PlacementStatus semantics
        - Master public entry point: place_diffusers()

NO dependency on:
    Qt / PySide6 / SQLite / HVAC catalog / GUI / reports / exporters
    / application state / global variables.

All coordinates are in METERS. All distances are in METERS.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Point = tuple[float, float]
RectBounds = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)

# ---------------------------------------------------------------------------
# Central numerical tolerance
# ---------------------------------------------------------------------------
EPSILON: float = 1e-6

# Defaults (meters)
DEFAULT_WALL_CLEARANCE_M: float = 0.30
DEFAULT_MIN_SPACING_M: float = 0.60


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
class PlacementStatus(Enum):
    SUCCESS = "success"
    SEARCH_FAILED = "search_failed"
    ANALYTICAL_FAILED = "analytical_failed"          # Analytical solver could not satisfy constraints
    CAPACITY_EXCEEDED = "capacity_exceeded"          # Proven capacity limit exceeded (reserved/Phase 3)
    SPACING_UNSATISFIABLE = "spacing_unsatisfiable"  # Geometry violates spacing constraints
    BUDGET_EXHAUSTED = "budget_exhausted"            # Search computation budget exhausted
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"    # Strategy does not support this polygon class
    INVALID_GEOMETRY = "invalid_geometry"            # Degenerate, self-intersecting or open polygon


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SearchBudget:
    """Upper bounds for search algorithms."""
    max_depth: int = 6
    max_cells: int = 4096
    max_candidates: int = 1000
    timeout_ms: float = 150.0


@dataclass(frozen=True)
class VerificationReport:
    """Immutable result of an exact verification pass."""
    valid: bool
    wall_clearance_verified: bool
    spacing_verified: bool
    containment_verified: bool
    finite_coordinates_verified: bool
    polygon_valid: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LayoutValidationResult:
    """Actionable validation result for an existing diffuser layout."""
    valid: bool
    constraint: str | None = None
    reason: str = ""
    invalid_diffuser_index: int | None = None
    conflicting_diffuser_index: int | None = None
    verification: VerificationReport | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapacityReport:
    """Honest capacity analysis attached to every PlacementResult.

    Fields
    ------
    requested_count         The count the caller asked for.
    lower_bound             Number of diffusers in a verified layout (a
                            constructive lower bound: N_max >= lower_bound).
    upper_bound_loose       Rigorous disk-packing bound:
                            N_max <= floor(A / (pi * r_eff^2)) where
                            r_eff = min(min_spacing_m / 2, wall_clearance_m).
    upper_bound_resolution  What the resolution-bounded search actually
                            produced (informational; NOT a proof).
    resolution_exhausted    True if the search reached its depth target
                            (SEARCH_FAILED is the only honest status).
    is_proven_exceeded      True iff upper_bound_loose < requested_count,
                            i.e., a rigorous proof that the request is
                            impossible in ANY layout, not just this search.
    proof_method            "disk_packing_bound" when is_proven_exceeded,
                            "none" otherwise.
    """
    requested_count: int
    lower_bound: int
    upper_bound_loose: int
    upper_bound_resolution: int | None = None
    resolution_exhausted: bool = False
    is_proven_exceeded: bool = False
    proof_method: str = "none"


@dataclass(frozen=True)
class PlacementResult:
    """Structured result of a diffuser placement attempt."""
    status: PlacementStatus
    requested_count: int
    placed_count: int
    positions_m: tuple[Point, ...]
    wall_clearance_verified: bool
    spacing_verified: bool
    strategy_used: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    capacity_report: CapacityReport | None = None


# ---------------------------------------------------------------------------
# Geometry predicates
# ---------------------------------------------------------------------------
def is_finite_point(point: Sequence[float]) -> bool:
    """True iff point has exactly 2 finite float coordinates."""
    try:
        x, y = point[0], point[1]
    except (TypeError, IndexError, KeyError):
        return False
    try:
        xf, yf = float(x), float(y)
    except (TypeError, ValueError):
        return False
    return math.isfinite(xf) and math.isfinite(yf)


def _polygon_points_finite(polygon: Sequence[Sequence[float]]) -> bool:
    return all(is_finite_point(p) for p in polygon)


def _polygon_edges(polygon: Sequence[Point]):
    n = len(polygon)
    for i in range(n):
        yield polygon[i], polygon[(i + 1) % n]


def _distance_point_to_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """True Euclidean distance from point P to closed segment AB."""
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= EPSILON * EPSILON:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def distance_point_to_segment(point: Point, a: Point, b: Point) -> float:
    """Public wrapper. True point-to-segment distance in meters."""
    return _distance_point_to_segment(
        float(point[0]), float(point[1]),
        float(a[0]), float(a[1]),
        float(b[0]), float(b[1]),
    )


def distance_to_boundary(point: Point, polygon: Sequence[Point]) -> float:
    """Minimum distance from point to any polygon edge (in meters)."""
    if not polygon:
        return float("inf")
    px, py = float(point[0]), float(point[1])
    best = float("inf")
    for a, b in _polygon_edges(polygon):
        d = _distance_point_to_segment(
            px, py,
            float(a[0]), float(a[1]),
            float(b[0]), float(b[1]),
        )
        best = min(best, d)
    return best


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Point-in-polygon test; points on the boundary count as inside."""
    if not polygon or len(polygon) < 3:
        return False
    if not is_finite_point(point):
        return False

    px, py = float(point[0]), float(point[1])
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])

        seg_d = _distance_point_to_segment(px, py, xi, yi, xj, yj)
        if seg_d <= EPSILON:
            return True

        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Polygon validation
# ---------------------------------------------------------------------------
def _polygon_signed_area(polygon: Sequence[Point]) -> float:
    n = len(polygon)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_seg(ax, ay, bx, by, px, py) -> bool:
    return (
        min(ax, bx) - EPSILON <= px <= max(ax, bx) + EPSILON
        and min(ay, by) - EPSILON <= py <= max(ay, by) + EPSILON
        and abs(_orient(ax, ay, bx, by, px, py)) <= EPSILON
    )


def _segments_cross(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    o1 = _orient(ax, ay, bx, by, cx, cy)
    o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay)
    o4 = _orient(cx, cy, dx, dy, bx, by)
    if (((o1 > EPSILON and o2 < -EPSILON)
         or (o1 < -EPSILON and o2 > EPSILON))
            and ((o3 > EPSILON and o4 < -EPSILON)
                 or (o3 < -EPSILON and o4 > EPSILON))):
        return True
    if _on_seg(ax, ay, bx, by, cx, cy):
        return True
    if _on_seg(ax, ay, bx, by, dx, dy):
        return True
    if _on_seg(cx, cy, dx, dy, ax, ay):
        return True
    return bool(_on_seg(cx, cy, dx, dy, bx, by))


def _polygon_self_intersects(polygon: Sequence[Point]) -> bool:
    n = len(polygon)
    if n < 4:
        return False
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (i + 1) % n == j or (j + 1) % n == i:
                continue
            cx, cy = polygon[j]
            dx, dy = polygon[(j + 1) % n]
            if _segments_cross(ax, ay, bx, by, cx, cy, dx, dy):
                return True
    return False


def validate_polygon(polygon: Sequence[Point]) -> tuple[bool, dict]:
    """Pure geometric polygon sanity check."""
    diag: dict = {}

    if polygon is None:
        return False, {"reason": "polygon_is_none"}
    if not isinstance(polygon, (list, tuple)):
        return False, {"reason": "polygon_not_sequence"}

    n = len(polygon)
    diag["vertex_count"] = n
    if n < 3:
        diag["reason"] = "fewer_than_3_vertices"
        return False, diag

    if not _polygon_points_finite(polygon):
        diag["reason"] = "non_finite_polygon_coordinates"
        return False, diag

    for i, (a, b) in enumerate(_polygon_edges(polygon)):
        if math.hypot(float(b[0]) - float(a[0]),
                      float(b[1]) - float(a[1])) <= EPSILON:
            diag["reason"] = "zero_length_edge"
            diag["edge_index"] = i
            return False, diag

    area = abs(_polygon_signed_area(polygon))
    diag["area_m2"] = area
    if area <= EPSILON:
        diag["reason"] = "degenerate_zero_area"
        return False, diag

    if _polygon_self_intersects(polygon):
        diag["reason"] = "self_intersecting_polygon"
        return False, diag

    diag["reason"] = "ok"
    return True, diag


# ---------------------------------------------------------------------------
# Phase 1: Exact verifier
# ---------------------------------------------------------------------------
def verify_placement(
    polygon: Sequence[Point],
    positions_m: Sequence[Point],
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
) -> VerificationReport:
    """Exact, deterministic verification of a placement pass."""
    diag: dict = {
        "wall_clearance_m": float(wall_clearance_m),
        "min_spacing_m": float(min_spacing_m),
        "epsilon": EPSILON,
    }

    # 1. Polygon validity
    poly_ok, poly_diag = validate_polygon(polygon)
    diag["polygon"] = poly_diag
    if not poly_ok:
        return VerificationReport(
            valid=False,
            wall_clearance_verified=False,
            spacing_verified=False,
            containment_verified=False,
            finite_coordinates_verified=False,
            polygon_valid=False,
            diagnostics=diag,
        )

    # 2. Position structure
    if positions_m is None or not isinstance(positions_m, (list, tuple)):
        diag["reason"] = "positions_invalid_sequence"
        return VerificationReport(
            valid=False,
            wall_clearance_verified=False,
            spacing_verified=False,
            containment_verified=False,
            finite_coordinates_verified=False,
            polygon_valid=True,
            diagnostics=diag,
        )

    positions = list(positions_m)
    diag["position_count"] = len(positions)

    # Empty positions set is vacuously valid
    if len(positions) == 0:
        diag["reason"] = "ok_empty_placement"
        return VerificationReport(
            valid=True,
            wall_clearance_verified=True,
            spacing_verified=True,
            containment_verified=True,
            finite_coordinates_verified=True,
            polygon_valid=True,
            diagnostics=diag,
        )

    for idx, p in enumerate(positions):
        if not is_finite_point(p):
            diag["reason"] = "position_non_finite"
            diag["point_index"] = idx
            return VerificationReport(
                valid=False,
                wall_clearance_verified=False,
                spacing_verified=False,
                containment_verified=False,
                finite_coordinates_verified=False,
                polygon_valid=True,
                diagnostics=diag,
            )

    # 3. Containment
    for idx, p in enumerate(positions):
        if not point_in_polygon((float(p[0]), float(p[1])), polygon):
            diag["reason"] = "point_outside_polygon"
            diag["point_index"] = idx
            return VerificationReport(
                valid=False,
                wall_clearance_verified=False,
                spacing_verified=False,
                containment_verified=False,
                finite_coordinates_verified=True,
                polygon_valid=True,
                diagnostics=diag,
            )
    containment_ok = True

    # 4. Wall clearance
    for idx, p in enumerate(positions):
        d = distance_to_boundary((float(p[0]), float(p[1])), polygon)
        if d < wall_clearance_m - EPSILON:
            diag["reason"] = "wall_clearance_violation"
            diag["point_index"] = idx
            diag["distance_m"] = d
            diag["required_m"] = float(wall_clearance_m)
            return VerificationReport(
                valid=False,
                wall_clearance_verified=False,
                spacing_verified=False,
                containment_verified=containment_ok,
                finite_coordinates_verified=True,
                polygon_valid=True,
                diagnostics=diag,
            )
    wall_ok = True

    # 5. Pairwise spacing
    n = len(positions)
    for i in range(n):
        xi, yi = float(positions[i][0]), float(positions[i][1])
        for j in range(i + 1, n):
            xj, yj = float(positions[j][0]), float(positions[j][1])
            d = math.hypot(xi - xj, yi - yj)
            if d < min_spacing_m - EPSILON:
                diag["reason"] = "spacing_violation"
                diag["point_i"] = i
                diag["point_j"] = j
                diag["distance_m"] = d
                diag["required_m"] = float(min_spacing_m)
                return VerificationReport(
                    valid=False,
                    wall_clearance_verified=True,
                    spacing_verified=False,
                    containment_verified=True,
                    finite_coordinates_verified=True,
                    polygon_valid=True,
                    diagnostics=diag,
                )
    spacing_ok = True

    diag["reason"] = "ok"
    return VerificationReport(
        valid=True,
        wall_clearance_verified=wall_ok,
        spacing_verified=spacing_ok,
        containment_verified=containment_ok,
        finite_coordinates_verified=True,
        polygon_valid=True,
        diagnostics=diag,
    )


def validate_diffuser_layout(
    geometry: Any,
    positions_m: Sequence[Point],
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
    face_half_extent_m: float | None = None,
) -> LayoutValidationResult:
    """Validate diffuser centers against actual room geometry in meters.

    Face extent, when available, is added once to the minimum center-to-wall
    clearance. Pair spacing remains the configured center-to-center distance.
    """
    geometry_errors = geometry.validate()
    if geometry_errors:
        return LayoutValidationResult(
            False, "geometry",
            "Invalid room geometry: " + "; ".join(geometry_errors))
    try:
        wall_clearance_m = float(wall_clearance_m)
        min_spacing_m = float(min_spacing_m)
        face_half_extent_m = (
            None if face_half_extent_m is None
            else float(face_half_extent_m))
    except (TypeError, ValueError):
        return LayoutValidationResult(
            False, "configuration",
            "Clearance, spacing, and face extent must be numeric.")
    values = (wall_clearance_m, min_spacing_m)
    if any(not math.isfinite(value) or value < 0 for value in values):
        return LayoutValidationResult(
            False, "configuration",
            "Wall clearance and minimum spacing must be finite and non-negative.")
    if face_half_extent_m is not None and (
            not math.isfinite(face_half_extent_m) or face_half_extent_m < 0):
        return LayoutValidationResult(
            False, "configuration",
            "Diffuser face extent must be finite and non-negative.")

    try:
        positions = list(positions_m) if positions_m is not None else []
    except TypeError:
        return LayoutValidationResult(
            False, "coordinates", "Diffuser positions must be a sequence.")
    clearance = wall_clearance_m + (
        face_half_extent_m if face_half_extent_m is not None else 0.0)
    verification = verify_placement(
        geometry.vertices, positions,
        wall_clearance_m=clearance,
        min_spacing_m=min_spacing_m,
    )
    if verification.valid:
        limitations = (() if face_half_extent_m is not None else (
            ("Diffuser face clearance is unverified because face dimensions "
            "were not supplied."),
        ))
        return LayoutValidationResult(
            True, verification=verification, limitations=limitations)

    diagnostics = verification.diagnostics
    reason = str(diagnostics.get("reason", "layout_invalid"))
    invalid_index = diagnostics.get("point_index")
    conflicting_index = None
    if reason == "spacing_violation":
        invalid_index = diagnostics.get("point_i")
        conflicting_index = diagnostics.get("point_j")
        distance = diagnostics.get("distance_m", 0.0)
        constraint = (
            "duplicate_position" if distance <= EPSILON
            else "spacing")
        message = (
            "Diffusers have duplicate or effectively coincident positions."
            if constraint == "duplicate_position" else
            f"Diffusers {invalid_index + 1} and {conflicting_index + 1} "
            f"are closer than the {min_spacing_m:.3f} m minimum center spacing."
        )
    else:
        constraint = {
            "point_outside_polygon": "containment",
            "wall_clearance_violation": "wall_clearance",
            "position_non_finite": "coordinates",
        }.get(reason, "placement")
        if constraint == "containment":
            message = f"Diffuser {invalid_index + 1} is outside the room polygon."
        elif constraint == "wall_clearance":
            message = (
                f"Diffuser {invalid_index + 1} does not meet the "
                f"{clearance:.3f} m wall clearance.")
        elif constraint == "coordinates":
            message = f"Diffuser {invalid_index + 1} has invalid coordinates."
        else:
            message = f"Diffuser layout validation failed: {reason}."
    return LayoutValidationResult(
        False, constraint, message,
        invalid_diffuser_index=invalid_index,
        conflicting_diffuser_index=conflicting_index,
        verification=verification,
    )


# ---------------------------------------------------------------------------
# Phase 2: Classification Helpers (Strict API Preserved)
# ---------------------------------------------------------------------------
def _polygon_bounding_box(polygon: Sequence[Point]) -> RectBounds:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def is_axis_aligned_rectangle(polygon: Sequence[Point]) -> bool:
    """True iff polygon is a valid, 4-vertex axis-aligned rectangle."""
    if len(polygon) != 4:
        return False
    ok, _ = validate_polygon(polygon)
    if not ok:
        return False
    min_x, min_y, max_x, max_y = _polygon_bounding_box(polygon)
    expected_corners = {(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)}
    actual_corners = {(round(x, 6), round(y, 6)) for x, y in polygon}
    expected_rounded = {(round(x, 6), round(y, 6)) for x, y in expected_corners}
    return actual_corners == expected_rounded and (max_x - min_x) > EPSILON and (max_y - min_y) > EPSILON


def is_orthogonal_polygon(polygon: Sequence[Point]) -> bool:
    """True iff polygon is valid and all edges are strictly horizontal or vertical."""
    if len(polygon) < 4:
        return False
    ok, _ = validate_polygon(polygon)
    if not ok:
        return False
    for a, b in _polygon_edges(polygon):
        dx = abs(b[0] - a[0])
        dy = abs(b[1] - a[1])
        if not (dx <= EPSILON or dy <= EPSILON):
            return False
    return True


# ---------------------------------------------------------------------------
# Phase 2: Exact Decomposition
# ---------------------------------------------------------------------------
def decompose_orthogonal_polygon(polygon: Sequence[Point]) -> list[RectBounds]:
    """Decompose an orthogonal polygon into non-overlapping axis-aligned sub-rectangles."""
    xs = sorted({round(float(p[0]), 6) for p in polygon})
    ys = sorted({round(float(p[1]), 6) for p in polygon})

    inside_cells = []
    for j in range(len(ys) - 1):
        y_bot = ys[j]
        y_top = ys[j + 1]
        if y_top - y_bot <= EPSILON:
            continue
        y_mid = (y_bot + y_top) / 2.0
        for i in range(len(xs) - 1):
            x_left = xs[i]
            x_right = xs[i + 1]
            if x_right - x_left <= EPSILON:
                continue
            x_mid = (x_left + x_right) / 2.0
            if point_in_polygon((x_mid, y_mid), polygon):
                inside_cells.append([x_left, y_bot, x_right, y_top])

    if not inside_cells:
        return []

    # 1. Horizontal merge along identical Y-spans
    y_groups = defaultdict(list)
    for c in inside_cells:
        y_groups[(c[1], c[3])].append((c[0], c[2]))

    merged_strips = []
    for (y_bot, y_top), x_intervals in y_groups.items():
        x_intervals.sort(key=lambda span: span[0])
        cur_x1, cur_x2 = x_intervals[0]
        for nx1, nx2 in x_intervals[1:]:
            if abs(nx1 - cur_x2) <= EPSILON:
                cur_x2 = nx2
            else:
                merged_strips.append([cur_x1, y_bot, cur_x2, y_top])
                cur_x1, cur_x2 = nx1, nx2
        merged_strips.append([cur_x1, y_bot, cur_x2, y_top])

    # 2. Vertical merge along identical X-spans
    x_groups = defaultdict(list)
    for r in merged_strips:
        x_groups[(r[0], r[2])].append((r[1], r[3]))

    final_rects: list[RectBounds] = []
    for (x1, x2), y_intervals in x_groups.items():
        y_intervals.sort(key=lambda span: span[0])
        cur_y1, cur_y2 = y_intervals[0]
        for ny1, ny2 in y_intervals[1:]:
            if abs(ny1 - cur_y2) <= EPSILON:
                cur_y2 = ny2
            else:
                final_rects.append((x1, cur_y1, x2, cur_y2))
                cur_y1, cur_y2 = ny1, ny2
        final_rects.append((x1, cur_y1, x2, cur_y2))

    return final_rects


# ---------------------------------------------------------------------------
# Phase 2: Solvers with True Safe-Core Inset
# ---------------------------------------------------------------------------
def _best_grid_dimensions(w: float, h: float, count: int) -> tuple[int, int]:
    """Find rows x cols == count that produces cell aspect ratio closest to 1.0."""
    best = None
    for rows in range(1, count + 1):
        if count % rows != 0:
            continue
        cols = count // rows
        cell_w = w / cols
        cell_h = h / rows
        aspect = max(cell_w, cell_h) / min(cell_w, cell_h)
        if best is None or aspect < best[0]:
            best = (aspect, rows, cols)
    return best[1], best[2] if best else (1, count)


def place_in_rectangle(
    bounds: RectBounds,
    count: int,
    inset_m: float = DEFAULT_WALL_CLEARANCE_M,
) -> list[Point]:
    """Analytical placement with guaranteed wall clearance.

    Positions points inside the safe core [x_min + inset, x_max - inset],
    ensuring distance to boundary >= inset_m by construction.
    """
    if count <= 0:
        return []
    min_x, min_y, max_x, max_y = bounds
    w = max_x - min_x
    h = max_y - min_y

    # If the room itself cannot accommodate 2 * inset, points cannot fit
    if (w < 2 * inset_m - EPSILON) or (h < 2 * inset_m - EPSILON):
        return []

    # Single diffuser is centered in the room
    if count == 1:
        return [(round((min_x + max_x) / 2.0, 6), round((min_y + max_y) / 2.0, 6))]

    rows, cols = _best_grid_dimensions(w, h, count)

    # Strategy 1: Equal architectural cell partitioning across full room
    cell_w = w / cols
    cell_h = h / rows
    if (cell_w / 2.0 >= inset_m - EPSILON) and (cell_h / 2.0 >= inset_m - EPSILON):
        points = []
        for r in range(rows):
            for c in range(cols):
                px = min_x + (c + 0.5) * cell_w
                py = min_y + (r + 0.5) * cell_h
                points.append((round(px, 6), round(py, 6)))
        return points

    # Strategy 2: Equal cell partitioning within the Safe-Core Inset
    safe_w = w - 2 * inset_m
    safe_h = h - 2 * inset_m
    safe_cell_w = safe_w / cols
    safe_cell_h = safe_h / rows
    points = []
    for r in range(rows):
        for c in range(cols):
            px = min_x + inset_m + (c + 0.5) * safe_cell_w
            py = min_y + inset_m + (r + 0.5) * safe_cell_h
            points.append((round(px, 6), round(py, 6)))
    return points


def _rect_area(rect: RectBounds) -> float:
    return max(0.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))


def _is_significant_rect(
    rect: RectBounds,
    total_area: float,
    wall_clearance_m: float,
) -> bool:
    """True iff a sub-rectangle is a real room wing that should get coverage."""
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    area = width * height
    if total_area > EPSILON and area / total_area >= 0.10:
        return True
    min_span = 2.0 * wall_clearance_m + 0.15
    return width >= min_span and height >= min_span


def significant_subrectangle_count(
    polygon: Sequence[Point],
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
) -> int:
    """Number of orthogonal wings that should each host at least one diffuser."""
    if not is_orthogonal_polygon(polygon):
        return 1
    rects = decompose_orthogonal_polygon(polygon)
    if not rects:
        return 1
    total = sum(_rect_area(r) for r in rects)
    n_sig = sum(
        1 for r in rects
        if _is_significant_rect(r, total, wall_clearance_m)
    )
    return max(1, n_sig)


def _allocate_orthogonal_counts(
    rects: Sequence[RectBounds],
    count: int,
    wall_clearance_m: float,
) -> list[int]:
    """Seed one diffuser per significant wing, then share the rest by area."""
    n_rects = len(rects)
    allocations = [0] * n_rects
    if count <= 0 or n_rects == 0:
        return allocations

    areas = [_rect_area(r) for r in rects]
    total_area = sum(areas)
    if total_area <= EPSILON:
        return allocations

    significant = [
        i for i, rect in enumerate(rects)
        if _is_significant_rect(rect, total_area, wall_clearance_m)
    ]
    if not significant:
        significant = list(range(n_rects))

    remaining = count
    if remaining >= len(significant):
        for i in significant:
            allocations[i] = 1
        remaining -= len(significant)

    if remaining > 0:
        exact_shares = [remaining * (area / total_area) for area in areas]
        extra = [math.floor(share) for share in exact_shares]
        leftover = remaining - sum(extra)
        order = sorted(
            range(n_rects),
            key=lambda i: (exact_shares[i] - extra[i], areas[i]),
            reverse=True,
        )
        for k in range(leftover):
            extra[order[k % n_rects]] += 1
        for i in range(n_rects):
            allocations[i] += extra[i]
    return allocations


def _points_inside_polygon(
    polygon: Sequence[Point],
    points: Sequence[Point],
) -> list[Point]:
    return [
        (float(p[0]), float(p[1])) for p in points
        if point_in_polygon((float(p[0]), float(p[1])), polygon)
    ]


def place_in_orthogonal_polygon(
    polygon: Sequence[Point],
    count: int,
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
) -> list[Point]:
    """Decomposition-based placement for orthogonal polygons (Tier B).

    Each significant sub-rectangle (L-shape wing, recessed arm) is seeded
    before leftover count is shared by area. Every returned point is
    strictly inside the parent polygon.
    """
    rects = decompose_orthogonal_polygon(polygon)
    if not rects or count <= 0:
        return []

    allocations = _allocate_orthogonal_counts(rects, count, wall_clearance_m)
    spacing_inset = max(wall_clearance_m, min_spacing_m / 2.0)

    all_points: list[Point] = []
    shortfall = 0
    for rect, rect_count in zip(rects, allocations, strict=True):
        if rect_count <= 0:
            continue
        pts = place_in_rectangle(rect, rect_count, inset_m=spacing_inset)
        if len(pts) < rect_count:
            pts = place_in_rectangle(rect, rect_count, inset_m=wall_clearance_m)
        contained = _points_inside_polygon(polygon, pts)
        shortfall += rect_count - len(contained)
        all_points.extend(contained)

    if shortfall > 0:
        by_area = sorted(enumerate(rects), key=lambda item: _rect_area(item[1]),
                         reverse=True)
        for rect_index, rect in by_area:
            if shortfall <= 0:
                break
            extra = place_in_rectangle(
                rect, allocations[rect_index] + shortfall,
                inset_m=wall_clearance_m,
            )
            extra = _points_inside_polygon(polygon, extra)
            new_pts = [p for p in extra if p not in all_points]
            take = new_pts[:shortfall]
            all_points.extend(take)
            shortfall -= len(take)

    return _points_inside_polygon(polygon, all_points)



# ---------------------------------------------------------------------------
# Phase 3: Adaptive Quadtree Candidate Generator (Step 2b)
# ---------------------------------------------------------------------------
def _cell_intersects_or_inside(
    x1: float, y1: float, x2: float, y2: float, polygon: Sequence[Point]
) -> bool:
    """True iff the rectangular cell intersects the polygon or contains polygon points."""
    # 1. Any cell corner inside?
    if (point_in_polygon((x1, y1), polygon) or point_in_polygon((x2, y1), polygon) or
            point_in_polygon((x2, y2), polygon) or point_in_polygon((x1, y2), polygon)):
        return True

    # 2. Cell center inside?
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    if point_in_polygon((cx, cy), polygon):
        return True

    # 3. Any polygon vertex inside cell?
    for px, py in polygon:
        if x1 - EPSILON <= px <= x2 + EPSILON and y1 - EPSILON <= py <= y2 + EPSILON:
            return True

    # 4. Any polygon edge intersects any cell boundary segment?
    cell_edges = [
        ((x1, y1), (x2, y1)),
        ((x2, y1), (x2, y2)),
        ((x2, y2), (x1, y2)),
        ((x1, y2), (x1, y1)),
    ]
    for c_start, c_end in cell_edges:
        cx1, cy1 = c_start
        cx2, cy2 = c_end
        for p_start, p_end in _polygon_edges(polygon):
            px1, py1 = p_start
            px2, py2 = p_end
            if _segments_cross(cx1, cy1, cx2, cy2, px1, py1, px2, py2):
                return True

    return False


def _quadtree_candidates(
    polygon: Sequence[Point],
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
    budget: SearchBudget | None = None,
) -> tuple[list[Point], dict[str, Any]]:
    """Generate safe candidate points using an adaptive, bounded quadtree.

    Guarantees:
        - Every returned point satisfies containment and distance >= wall_clearance_m - EPSILON.
        - Respects budget.max_cells, budget.max_candidates, and budget.max_depth.
        - Resolution target: min_spacing_m / 4.0.

    Budget semantics:
        The quadtree reports `budget_exhausted=True` in ANY of these cases:
          - cells_visited >= budget.max_cells             (cells_limited)
          - candidates >= budget.max_candidates           (candidates_limited)
          - effective_max_depth < needed_depth            (depth_limited)
        The third case is critical: it means the depth cap prevented us from
        reaching the target resolution, so the result is INCONCLUSIVE.
    """
    min_x, min_y, max_x, max_y = _polygon_bounding_box(polygon)
    diameter = max(max_x - min_x, max_y - min_y)
    target_res = max(min_spacing_m / 4.0, 0.05)

    needed_depth = math.ceil(math.log2(max(diameter / target_res, 1.0)))
    if budget is None:
        budget = SearchBudget(max_depth=needed_depth)
    effective_max_depth = min(needed_depth, budget.max_depth)

    candidates: list[Point] = []
    seen: set[tuple[float, float]] = set()

    cells_stack = [(min_x, min_y, max_x, max_y, 0)]
    cells_visited = 0
    max_depth_reached = 0
    cells_limited = False
    candidates_limited = False

    while cells_stack:
        if cells_visited >= budget.max_cells:
            cells_limited = True
            break

        x1, y1, x2, y2, depth = cells_stack.pop()
        cells_visited += 1
        max_depth_reached = max(max_depth_reached, depth)

        if not _cell_intersects_or_inside(x1, y1, x2, y2, polygon):
            continue

        cx = round((x1 + x2) / 2.0, 6)
        cy = round((y1 + y2) / 2.0, 6)
        pt = (cx, cy)

        if pt not in seen:
            seen.add(pt)
            if point_in_polygon(pt, polygon):
                d = distance_to_boundary(pt, polygon)
                if d >= wall_clearance_m - EPSILON:
                    candidates.append(pt)
                    if len(candidates) >= budget.max_candidates:
                        candidates_limited = True
                        break

        cell_size = max(x2 - x1, y2 - y1)
        if depth < effective_max_depth and cell_size > target_res:
            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0
            cells_stack.extend([
                (x1, y1, mid_x, mid_y, depth + 1),
                (mid_x, y1, x2, mid_y, depth + 1),
                (x1, mid_y, mid_x, y2, depth + 1),
                (mid_x, mid_y, x2, y2, depth + 1),
            ])

    # Did the depth cap prevent reaching target resolution?
    depth_limited = effective_max_depth < needed_depth
    budget_exhausted = cells_limited or candidates_limited or depth_limited

    resolution_achieved = (
        diameter / (2.0 ** max_depth_reached)
        if max_depth_reached > 0
        else diameter
    )

    diag = {
        "cells_visited": cells_visited,
        "max_depth_reached": max_depth_reached,
        "candidates_count": len(candidates),
        "cells_limited": cells_limited,
        "candidates_limited": candidates_limited,
        "depth_limited": depth_limited,
        "budget_exhausted": budget_exhausted,
        "depth_target": needed_depth,
        "target_resolution_m": target_res,
        "resolution_achieved_m": resolution_achieved,
    }
    return candidates, diag


# ---------------------------------------------------------------------------
# Phase 3: Farthest-Point Greedy Selection (Step 2c)
# ---------------------------------------------------------------------------
def _polygon_centroid(polygon: Sequence[Point]) -> Point:
    """Calculate the true geometric centroid of a simple polygon via Shoelace formula."""
    n = len(polygon)
    if n < 3:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return sum(xs) / max(n, 1), sum(ys) / max(n, 1)

    signed_area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = polygon[i]
        x1, y1 = polygon[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        signed_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    area = signed_area / 2.0
    if abs(area) <= EPSILON:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return sum(xs) / n, sum(ys) / n

    factor = 1.0 / (6.0 * area)
    return round(cx * factor, 6), round(cy * factor, 6)


def _farthest_point_select(
    candidates: Sequence[Point],
    count: int,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
    polygon: Sequence[Point] | None = None,
    target_spacing_m: float | None = None,
) -> tuple[list[Point], dict[str, Any]]:
    """Deterministically select up to `count` points from candidates maximizing inter-point distance.

    Guarantees:
        - Every pair of selected points satisfies distance >= min_spacing_m - EPSILON.
        - First point is closest to polygon centroid.
        - Lexicographic tie-breaking guarantees 100% determinism.
        - Stops safely and returns placed < count if spacing cannot be satisfied.
    """
    diag: dict[str, Any] = {
        "requested_count": count,
        "candidates_input": len(candidates),
        "target_spacing_m": target_spacing_m,
    }

    if count == 0:
        diag.update({
            "reason": "zero_count",
            "placed_count": 0,
            "fully_satisfied": True,
        })
        return [], diag

    if not candidates:
        diag.update({
            "reason": "no_candidates",
            "placed_count": 0,
            "fully_satisfied": False,
        })
        return [], diag

    # 1. Determine centroid reference
    if polygon is not None and len(polygon) >= 3:
        centroid = _polygon_centroid(polygon)
    else:
        xs = [p[0] for p in candidates]
        ys = [p[1] for p in candidates]
        centroid = (sum(xs) / len(candidates), sum(ys) / len(candidates))

    # 2. First point: closest to centroid (lexicographic tie-break: min x, min y)
    def first_key(pt: Point):
        d = math.hypot(pt[0] - centroid[0], pt[1] - centroid[1])
        return (round(d, 6), pt[0], pt[1])

    first_point = min(candidates, key=first_key)
    placed: list[Point] = [first_point]

    remaining = [p for p in candidates if p != first_point]

    # 3. Subsequent points: Max-Min distance with sum-distance and lexicographic tie-breaks
    while len(placed) < count and remaining:
        best_candidate: Point | None = None
        best_key: tuple[float, float, float, float] | None = None

        for c in remaining:
            # Pairwise distances to all placed points
            distances = [math.hypot(c[0] - p[0], c[1] - p[1]) for p in placed]
            min_d = min(distances)

            # Must satisfy spacing constraint
            if min_d < min_spacing_m - EPSILON:
                continue

            sum_d = sum(distances)
            coverage_distance = (
                min(min_d, target_spacing_m)
                if target_spacing_m is not None else min_d
            )
            # Tie-break priority:
            # 1. Spread points toward the throw-derived target spacing
            # 2. Maximize sum of distances to all placed points
            # 3. Lexicographic order: smaller x, smaller y (-c[0], -c[1] in max)
            key = (round(coverage_distance, 6), round(sum_d, 6),
                   -c[0], -c[1])

            if best_key is None or key > best_key:
                best_key = key
                best_candidate = c

        if best_candidate is None:
            # No remaining candidate satisfies spacing
            break

        placed.append(best_candidate)
        remaining = [p for p in remaining if p != best_candidate]

    fully_satisfied = len(placed) == count
    diag.update({
        "reason": "ok" if fully_satisfied else "insufficient_candidates_at_spacing",
        "placed_count": len(placed),
        "fully_satisfied": fully_satisfied,
    })
    return placed, diag

# ---------------------------------------------------------------------------
# Master Entry Point (Pure Geometry, Decoupled)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 4: Rigorous Capacity Bounds (Step 4a)
# ---------------------------------------------------------------------------
def _capacity_upper_bound_loose(
    polygon: Sequence[Point],
    min_spacing_m: float,
    wall_clearance_m: float | None = None,
) -> int:
    """Rigorous disk-packing upper bound on the number of diffusers.

    Every diffuser sits in the polygon at >= wall_clearance_m from the
    boundary. Place at each center a disk of radius
        r_eff = min(min_spacing_m / 2, wall_clearance_m).
    These disks are:
      - pairwise disjoint (2 * r_eff <= min_spacing_m),
      - fully inside the polygon (r_eff <= wall_clearance_m).
    Therefore N * pi * r_eff^2 <= Area(polygon), giving the rigorous
    upper bound N <= floor(Area / (pi * r_eff^2)). Not a heuristic.
    """
    A = abs(_polygon_signed_area(polygon))
    if wall_clearance_m is None:
        wall_clearance_m = min_spacing_m / 2.0
    r_eff = min(min_spacing_m / 2.0, wall_clearance_m)
    if r_eff <= EPSILON:
        return 0
    return math.floor(A / (math.pi * r_eff * r_eff))


def _build_capacity_report(
    polygon: Sequence[Point],
    requested_count: int,
    placed_count: int,
    min_spacing_m: float,
    wall_clearance_m: float,
    *,
    upper_bound_resolution: int | None = None,
    resolution_exhausted: bool = False,
) -> CapacityReport:
    """Assemble an honest CapacityReport for one placement attempt."""
    upper_loose = _capacity_upper_bound_loose(
        polygon, min_spacing_m, wall_clearance_m
    )
    is_proven = (requested_count > 0) and (upper_loose < requested_count)
    return CapacityReport(
        requested_count=requested_count,
        lower_bound=placed_count,
        upper_bound_loose=upper_loose,
        upper_bound_resolution=upper_bound_resolution,
        resolution_exhausted=resolution_exhausted,
        is_proven_exceeded=is_proven,
        proof_method="disk_packing_bound" if is_proven else "none",
    )


def place_diffusers(
    polygon: Sequence[Point],
    count: int,
    wall_clearance_m: float = DEFAULT_WALL_CLEARANCE_M,
    min_spacing_m: float = DEFAULT_MIN_SPACING_M,
    strategy: str = "auto",
    budget: SearchBudget | None = None,
    throw_t50_m: float | None = None,
) -> PlacementResult:
    """Master public entry point for pure-geometry diffuser placement.

    Status hierarchy (strict, no false claims):
        CAPACITY_EXCEEDED  - only when a rigorous proof applies
        SUCCESS            - verified layout of exactly `count` diffusers
        BUDGET_EXHAUSTED   - search stopped early, result INCONCLUSIVE
        SEARCH_FAILED      - search completed, no solution found
        ANALYTICAL_FAILED  - Tier A/B grid could not satisfy constraints
        UNSUPPORTED_GEOMETRY - strategy or geometry not handled
        INVALID_GEOMETRY   - polygon rejected
    """
    # --- count == 0 : vacuously valid, no capacity check needed ---
    if count == 0:
        return PlacementResult(
            status=PlacementStatus.SUCCESS,
            requested_count=0,
            placed_count=0,
            positions_m=(),
            wall_clearance_verified=True,
            spacing_verified=True,
            strategy_used="vacuous_zero",
            diagnostics={"reason": "ok_zero_count_vacuously_valid"},
        )

    if count < 0:
        return PlacementResult(
            status=PlacementStatus.SPACING_UNSATISFIABLE,
            requested_count=count,
            placed_count=0,
            positions_m=(),
            wall_clearance_verified=False,
            spacing_verified=False,
            strategy_used="none",
            diagnostics={"reason": "count_must_be_non_negative"},
        )

    # --- 1. Validate polygon ---
    poly_ok, poly_diag = validate_polygon(polygon)
    if not poly_ok:
        return PlacementResult(
            status=PlacementStatus.INVALID_GEOMETRY,
            requested_count=count,
            placed_count=0,
            positions_m=(),
            wall_clearance_verified=False,
            spacing_verified=False,
            strategy_used="none",
            diagnostics={"reason": "invalid_polygon", "details": poly_diag},
        )

    if budget is None:
        min_x, min_y, max_x, max_y = _polygon_bounding_box(polygon)
        diameter = max(max_x - min_x, max_y - min_y)
        target_res = max(min_spacing_m / 4.0, 0.05)
        # Match the quadtree's requested resolution instead of imposing a
        # fixed depth that declares ordinary large rooms inconclusive.
        max_depth = math.ceil(math.log2(max(diameter / target_res, 1.0)))
        budget = SearchBudget(max_depth=max_depth)

    # --- 2. Rigorous capacity check (BEFORE any search) ---
    upper_loose = _capacity_upper_bound_loose(
        polygon, min_spacing_m, wall_clearance_m
    )
    if upper_loose < count:
        return PlacementResult(
            status=PlacementStatus.CAPACITY_EXCEEDED,
            requested_count=count,
            placed_count=0,
            positions_m=(),
            wall_clearance_verified=False,
            spacing_verified=False,
            strategy_used="capacity_proof",
            diagnostics={
                "reason": "disk_packing_bound_proves_infeasibility",
                "upper_bound_loose": upper_loose,
                "requested_count": count,
                "note": (
                    "Rigorous disk-packing proof: no arrangement of "
                    f"{count} diffusers with min_spacing={min_spacing_m} m "
                    f"and wall_clearance={wall_clearance_m} m can fit in "
                    "this polygon."
                ),
            },
            capacity_report=CapacityReport(
                requested_count=count,
                lower_bound=0,
                upper_bound_loose=upper_loose,
                is_proven_exceeded=True,
                proof_method="disk_packing_bound",
            ),
        )

    # --- 3. Strategy dispatch ---
    is_rect = is_axis_aligned_rectangle(polygon)
    is_ortho = is_orthogonal_polygon(polygon)

    candidate_points: list[Point] = []
    strategy_used = "none"
    tier_c_diag: dict[str, Any] = {}
    analytical_attempted = False

    def _run_tier_c() -> list[Point]:
        nonlocal tier_c_diag
        pool, q_diag = _quadtree_candidates(
            polygon=polygon,
            wall_clearance_m=wall_clearance_m,
            min_spacing_m=min_spacing_m,
            budget=budget,
        )
        tier_c_diag.update(q_diag)
        tier_c_diag["depth_achieved"] = q_diag.get("max_depth_reached", 0)
        selected, s_diag = _farthest_point_select(
            candidates=pool,
            count=count,
            min_spacing_m=min_spacing_m,
            polygon=polygon,
            target_spacing_m=(
                2.0 * throw_t50_m / 0.95
                if throw_t50_m is not None and throw_t50_m > 0 else None
            ),
        )
        tier_c_diag["throw_t50_m"] = throw_t50_m
        tier_c_diag.update(s_diag)
        return _points_inside_polygon(polygon, selected)

    if strategy in ("auto", "analytical") and is_rect:
        strategy_used = "analytical_rectangle"
        analytical_attempted = True
        bounds = _polygon_bounding_box(polygon)
        candidate_points = _points_inside_polygon(
            polygon,
            place_in_rectangle(bounds, count, inset_m=wall_clearance_m),
        )
    elif strategy in ("auto", "decomposition") and is_ortho:
        strategy_used = "orthogonal_decomposition"
        analytical_attempted = True
        candidate_points = place_in_orthogonal_polygon(
            polygon, count,
            wall_clearance_m=wall_clearance_m,
            min_spacing_m=min_spacing_m,
        )
    elif strategy in ("auto", "adaptive", "tier_c"):
        strategy_used = "tier_c_adaptive_search"
        candidate_points = _run_tier_c()
    else:
        return PlacementResult(
            status=PlacementStatus.UNSUPPORTED_GEOMETRY,
            requested_count=count,
            placed_count=0,
            positions_m=(),
            wall_clearance_verified=False,
            spacing_verified=False,
            strategy_used="none",
            diagnostics={"reason": "unsupported_strategy_or_geometry"},
            capacity_report=_build_capacity_report(
                polygon, count, 0, min_spacing_m, wall_clearance_m
            ),
        )

    # --- 4. Exact Verification Audit ---
    report = verify_placement(
        polygon=polygon,
        positions_m=candidate_points,
        wall_clearance_m=wall_clearance_m,
        min_spacing_m=min_spacing_m,
    )

    if report.valid and len(candidate_points) == count:
        merged_diag = {**report.diagnostics, **tier_c_diag}
        return PlacementResult(
            status=PlacementStatus.SUCCESS,
            requested_count=count,
            placed_count=count,
            positions_m=tuple(candidate_points),
            wall_clearance_verified=True,
            spacing_verified=True,
            strategy_used=strategy_used,
            diagnostics=merged_diag,
            capacity_report=_build_capacity_report(
                polygon, count, count, min_spacing_m, wall_clearance_m
            ),
        )

    # Auto mode: a failed rectangle/L-shape grid must not keep bounding-box
    # points. Fall through to the polygon-constrained adaptive search.
    if strategy == "auto" and analytical_attempted:
        strategy_used = "tier_c_adaptive_search"
        candidate_points = _run_tier_c()
        report = verify_placement(
            polygon=polygon,
            positions_m=candidate_points,
            wall_clearance_m=wall_clearance_m,
            min_spacing_m=min_spacing_m,
        )
        if report.valid and len(candidate_points) == count:
            merged_diag = {**report.diagnostics, **tier_c_diag}
            merged_diag["analytical_fallback"] = True
            return PlacementResult(
                status=PlacementStatus.SUCCESS,
                requested_count=count,
                placed_count=count,
                positions_m=tuple(candidate_points),
                wall_clearance_verified=True,
                spacing_verified=True,
                strategy_used=strategy_used,
                diagnostics=merged_diag,
                capacity_report=_build_capacity_report(
                    polygon, count, count, min_spacing_m, wall_clearance_m
                ),
            )

    # --- 5. Failure taxonomy (Tier C vs Tier A/B) ---
    if strategy_used == "tier_c_adaptive_search":
        depth_achieved = tier_c_diag.get("depth_achieved", 0)
        depth_target = tier_c_diag.get("depth_target", 0)
        budget_exhausted = tier_c_diag.get("budget_exhausted", False)

        merged_diag = {**report.diagnostics, **tier_c_diag}
        merged_diag["requested_count"] = count
        merged_diag["placed_count"] = len(candidate_points)

        placed = len(candidate_points)

        if budget_exhausted or depth_achieved < depth_target:
            # Search halted early -> INCONCLUSIVE
            merged_diag["reason"] = "budget_exhausted"
            merged_diag["note"] = (
                "Search stopped before reaching target resolution. "
                "Result is INCONCLUSIVE, not negative."
            )
            return PlacementResult(
                status=PlacementStatus.BUDGET_EXHAUSTED,
                requested_count=count,
                placed_count=placed,
                positions_m=tuple(candidate_points),
                wall_clearance_verified=report.wall_clearance_verified,
                spacing_verified=report.spacing_verified,
                strategy_used=strategy_used,
                diagnostics=merged_diag,
                capacity_report=_build_capacity_report(
                    polygon, count, placed,
                    min_spacing_m, wall_clearance_m,
                    upper_bound_resolution=placed,
                    resolution_exhausted=False,
                ),
            )
        else:
            # Search completed at target resolution -> SEARCH_FAILED
            merged_diag["reason"] = "search_exhausted_at_target_resolution"
            merged_diag["note"] = (
                "Resolution-bounded search only. "
                "Not a mathematical proof of infeasibility."
            )
            return PlacementResult(
                status=PlacementStatus.SEARCH_FAILED,
                requested_count=count,
                placed_count=placed,
                positions_m=tuple(candidate_points),
                wall_clearance_verified=report.wall_clearance_verified,
                spacing_verified=report.spacing_verified,
                strategy_used=strategy_used,
                diagnostics=merged_diag,
                capacity_report=_build_capacity_report(
                    polygon, count, placed,
                    min_spacing_m, wall_clearance_m,
                    upper_bound_resolution=placed,
                    resolution_exhausted=True,
                ),
            )

    # --- 6. Tier A / Tier B analytical failure ---
    placed = len(candidate_points)
    return PlacementResult(
        status=PlacementStatus.ANALYTICAL_FAILED,
        requested_count=count,
        placed_count=placed,
        positions_m=tuple(candidate_points),
        wall_clearance_verified=report.wall_clearance_verified,
        spacing_verified=report.spacing_verified,
        strategy_used=strategy_used,
        diagnostics=report.diagnostics,
        capacity_report=_build_capacity_report(
            polygon, count, placed, min_spacing_m, wall_clearance_m
        ),
    )

__all__ = [
    "DEFAULT_MIN_SPACING_M",
    "DEFAULT_WALL_CLEARANCE_M",
    "EPSILON",
    "LayoutValidationResult",
    "PlacementResult",
    "PlacementStatus",
    "Point",
    "RectBounds",
    "SearchBudget",
    "VerificationReport",
    "_capacity_upper_bound_loose",
    "_farthest_point_select",
    "_quadtree_candidates",
    "decompose_orthogonal_polygon",
    "distance_point_to_segment",
    "distance_to_boundary",
    "is_axis_aligned_rectangle",
    "is_finite_point",
    "is_orthogonal_polygon",
    "place_diffusers",
    "place_in_orthogonal_polygon",
    "place_in_rectangle",
    "point_in_polygon",
    "significant_subrectangle_count",
    "validate_diffuser_layout",
    "validate_polygon",
    "verify_placement",
]
