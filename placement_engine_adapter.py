"""
placement_engine_adapter.py
===========================
Bridge between the legacy `RoomGeometry` interface (used by
`diffuser_calculator.py`, `room_designer.py`, GUI) and the pure-geometry
`placement_engine`.

Responsibilities
----------------
1. Convert a `RoomGeometry`-like object (duck-typed: needs `.vertices`
   as a sequence of (x, y) pairs in metres) into the `Sequence[Point]`
   form expected by the placement engine.
2. Provide a single high-level call `place_via_engine()`.
3. Classify engine verdicts as FINAL or NON-FINAL for the caller's
   fallback logic.

The adapter is a pure module: no globals, no side effects, no
GUI / DB / HVAC catalog dependencies.
"""

from __future__ import annotations

from placement_engine import (
    PlacementResult,
    PlacementStatus,
    Point,
    SearchBudget,
    place_diffusers,
)

# The engine assumes strictly-positive spacing for its disk-packing
# argument. Non-positive inputs are clamped to this tiny value.
MIN_USABLE_SPACING_M: float = 0.01


def geometry_to_polygon(geometry) -> list[Point]:
    """Extract the vertex list from a RoomGeometry-like object.

    Works with `room_geometry.RoomGeometry` and any object exposing
    `.vertices` as a sequence of (x, y) pairs in metres.
    """
    vertices = getattr(geometry, "vertices", None)
    if vertices is None:
        raise TypeError(
            "geometry must expose a `.vertices` sequence of (x, y) pairs")
    polygon: list[Point] = []
    for v in vertices:
        polygon.append((float(v[0]), float(v[1])))
    return polygon


def place_via_engine(
    geometry,
    count: int,
    wall_clearance_m: float = 0.30,
    min_spacing_m: float = 0.60,
    budget: SearchBudget | None = None,
    throw_t50_m: float | None = None,
) -> PlacementResult:
    """Run `placement_engine.place_diffusers` on a RoomGeometry-like object.

    A tiny `MIN_USABLE_SPACING_M` is substituted for non-positive spacing
    so the engine's disk-packing bound stays well-defined.
    """
    polygon = geometry_to_polygon(geometry)

    effective_spacing = float(min_spacing_m)
    if effective_spacing <= 0.0:
        import warnings
        warnings.warn(
            f"place_via_engine: non-positive min_spacing_m="
            f"{min_spacing_m!r} substituted by MIN_USABLE_SPACING_M="
            f"{MIN_USABLE_SPACING_M} m (silently changing caller intent "
            "would hide bugs).",
            RuntimeWarning,
            stacklevel=2,
        )
        effective_spacing = MIN_USABLE_SPACING_M

    return place_diffusers(
        polygon=polygon,
        count=int(count),
        wall_clearance_m=float(wall_clearance_m),
        min_spacing_m=effective_spacing,
        strategy="auto",
        budget=budget,
        throw_t50_m=throw_t50_m,
    )


def is_engine_verdict_final(status: PlacementStatus) -> bool:
    """Return True iff the engine has produced a DEFINITE answer.

    FINAL verdicts (caller should NOT fall back to legacy):
      * SUCCESS           — verified layout of exactly `count` diffusers
      * CAPACITY_EXCEEDED — rigorous disk-packing proof of infeasibility

    NON-FINAL verdicts (caller MAY fall back to legacy):
      * SEARCH_FAILED, BUDGET_EXHAUSTED, ANALYTICAL_FAILED,
        UNSUPPORTED_GEOMETRY, INVALID_GEOMETRY
    """
    return status in (
        PlacementStatus.SUCCESS,
        PlacementStatus.CAPACITY_EXCEEDED,
    )


__all__ = [
    "MIN_USABLE_SPACING_M",
    "geometry_to_polygon",
    "is_engine_verdict_final",
    "place_via_engine",
]
