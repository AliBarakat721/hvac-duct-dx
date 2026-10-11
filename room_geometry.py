"""Canonical room geometry in metric coordinates.

Vertices are ordered around the boundary and do not repeat the first point.
The closing edge is implicit. ``closed`` records whether the source boundary
was explicitly closed; open boundaries are never silently repaired.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

Point = tuple[float, float]
CLOSURE_TOLERANCE_M = 0.05
_EPSILON = 1e-9


def _cross(a: Point, b: Point, c: Point) -> float:
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (min(a[0], b[0]) - _EPSILON <= p[0] <= max(a[0], b[0]) + _EPSILON
            and min(a[1], b[1]) - _EPSILON <= p[1] <= max(a[1], b[1]) + _EPSILON
            and abs(_cross(a, b, p)) <= _EPSILON)


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if (((ab_c > _EPSILON and ab_d < -_EPSILON)
         or (ab_c < -_EPSILON and ab_d > _EPSILON))
            and ((cd_a > _EPSILON and cd_b < -_EPSILON)
                 or (cd_a < -_EPSILON and cd_b > _EPSILON))):
        return True
    return (_on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


@dataclass(frozen=True)
class WallGeometry:
    index: int
    start_point: Point
    end_point: Point
    length_m: float
    angle_deg: float


@dataclass(frozen=True)
class RoomGeometry:
    """Closed polygon room boundary and height, all lengths in meters."""

    vertices: tuple[Point, ...]
    height_m: float
    closed: bool = True
    closure_gap_m: float = 0.0
    input_errors: tuple[str, ...] = ()

    @classmethod
    def from_vertices(cls, points: Iterable[Sequence[float]], height_m: float,
                      *, closed: bool = True) -> RoomGeometry:
        vertices = tuple((float(point[0]), float(point[1])) for point in points)
        if len(vertices) > 1 and vertices[-1] == vertices[0]:
            vertices = vertices[:-1]
        return cls(vertices=vertices, height_m=float(height_m), closed=closed)

    @classmethod
    def from_walls(cls, walls: Iterable[Any], height_m: float,
                   *, closure_tolerance_m: float = CLOSURE_TOLERANCE_M
                   ) -> RoomGeometry:
        """Adapt legacy length/angle walls using the Room Designer closure policy."""
        vertices: list[Point] = [(0.0, 0.0)]
        valid_input = True
        input_errors = []
        for index, wall in enumerate(walls):
            x, y = vertices[-1]
            try:
                length = float(wall["length"] if isinstance(wall, dict)
                               else wall.length)
                angle = float(wall["angle"] if isinstance(wall, dict)
                              else wall.angle)
            except (KeyError, TypeError, ValueError, AttributeError):
                valid_input = False
                input_errors.append(f"Wall {index + 1} has invalid length or angle.")
                vertices.append((x, y))
                continue
            if not math.isfinite(length) or length <= 0:
                valid_input = False
                input_errors.append(f"Wall {index + 1} length must be finite and positive.")
            if not math.isfinite(angle):
                valid_input = False
                input_errors.append(f"Wall {index + 1} angle must be finite.")
            if not math.isfinite(length) or not math.isfinite(angle):
                vertices.append((x, y))
                continue
            radians = math.radians(angle)
            next_point = (x + length * math.cos(radians),
                          y + length * math.sin(radians))
            if not all(math.isfinite(value) for value in next_point):
                valid_input = False
                input_errors.append(
                    f"Wall {index + 1} produces non-finite coordinates.")
                next_point = (x, y)
            vertices.append(next_point)
        if len(vertices) < 2:
            return cls(tuple(vertices), float(height_m), False)
        gap = math.dist(vertices[-1], vertices[0])
        is_closed = (valid_input and len(vertices) >= 4
                     and gap <= closure_tolerance_m)
        boundary_vertices = vertices[:-1] if is_closed else vertices
        return cls(tuple(boundary_vertices), float(height_m), is_closed, gap,
                   tuple(input_errors))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomGeometry:
        if "vertices" in data:
            geometry = cls.from_vertices(
                data["vertices"], data.get("height_m", data.get("height", 0)),
                closed=bool(data.get("closed", True)))
            return cls(geometry.vertices, geometry.height_m, geometry.closed,
                       float(data.get("closure_gap_m", 0)),
                       tuple(data.get("input_errors", ())))
        return cls.from_walls(data.get("walls", []),
                              data.get("height_m", data.get("height", 0)))

    @property
    def walls(self) -> tuple[WallGeometry, ...]:
        if len(self.vertices) < 2:
            return ()
        result = []
        edge_count = len(self.vertices) if self.closed else len(self.vertices) - 1
        for index in range(edge_count):
            start = self.vertices[index]
            end = self.vertices[(index + 1) % len(self.vertices)]
            dx, dy = end[0] - start[0], end[1] - start[1]
            result.append(WallGeometry(
                index=index, start_point=start, end_point=end,
                length_m=math.hypot(dx, dy),
                angle_deg=math.degrees(math.atan2(dy, dx)) % 360.0))
        return tuple(result)

    @property
    def area_m2(self) -> float:
        if not self.closed or len(self.vertices) < 3:
            return 0.0
        return abs(sum(
            self.vertices[i][0] * self.vertices[(i + 1) % len(self.vertices)][1]
            - self.vertices[(i + 1) % len(self.vertices)][0] * self.vertices[i][1]
            for i in range(len(self.vertices))
        )) / 2.0

    @property
    def perimeter_m(self) -> float:
        return sum(wall.length_m for wall in self.walls)

    @property
    def volume_m3(self) -> float:
        return self.area_m2 * self.height_m

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        if not self.vertices:
            return 0.0, 0.0, 0.0, 0.0
        xs, ys = zip(*self.vertices, strict=True)
        return min(xs), max(xs), min(ys), max(ys)

    @property
    def centroid(self) -> Point:
        if not self.vertices or self.area_m2 <= 0:
            return (0.0, 0.0)
        cross_sum = 0.0
        cx = cy = 0.0
        for index, point in enumerate(self.vertices):
            nxt = self.vertices[(index + 1) % len(self.vertices)]
            cross = point[0] * nxt[1] - nxt[0] * point[1]
            cross_sum += cross
            cx += (point[0] + nxt[0]) * cross
            cy += (point[1] + nxt[1]) * cross
        factor = 1.0 / (3.0 * cross_sum)
        return cx * factor, cy * factor

    @property
    def is_closed(self) -> bool:
        return self.closed

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    def validate(self) -> list[str]:
        errors: list[str] = list(self.input_errors)
        if len(self.vertices) < 3:
            errors.append("Room boundary must contain at least 3 vertices.")
        if not self.closed:
            errors.append("Room boundary is open.")
        if not math.isfinite(self.height_m) or self.height_m <= 0:
            errors.append("Room height must be finite and greater than zero.")
        if any(not (math.isfinite(x) and math.isfinite(y))
               for x, y in self.vertices):
            errors.append("Room boundary contains non-finite coordinates.")
            return errors
        walls = self.walls
        if any(not math.isfinite(wall.length_m) or wall.length_m <= _EPSILON
               for wall in walls):
            errors.append("Room boundary contains a zero-length or invalid wall.")
        if self.area_m2 <= _EPSILON:
            errors.append("Room boundary must have positive area.")
        if len(self.vertices) >= 3 and self._self_intersects():
            errors.append("Room boundary self-intersects.")
        return errors

    def _self_intersects(self) -> bool:
        count = len(self.vertices)
        for i in range(count):
            a, b = self.vertices[i], self.vertices[(i + 1) % count]
            for j in range(i + 1, count):
                if j == i or j == (i + 1) % count or (j + 1) % count == i:
                    continue
                c, d = self.vertices[j], self.vertices[(j + 1) % count]
                if _segments_intersect(a, b, c, d):
                    return True
        return False

    def contains_point(self, x_m: float, y_m: float) -> bool:
        if len(self.vertices) < 3 or not self.closed:
            return False
        point = (float(x_m), float(y_m))
        if not all(math.isfinite(value) for value in point):
            return False
        inside = False
        previous = self.vertices[-1]
        for current in self.vertices:
            if _on_segment(previous, current, point):
                return True
            if ((current[1] > point[1]) != (previous[1] > point[1])
                    and point[0] < (previous[0] - current[0])
                    * (point[1] - current[1])
                    / (previous[1] - current[1]) + current[0]):
                inside = not inside
            previous = current
        return inside

    def distance_to_walls(self, x_m: float, y_m: float) -> float:
        if not self.walls:
            return 0.0
        x, y = float(x_m), float(y_m)
        distances = []
        for wall in self.walls:
            ax, ay = wall.start_point
            bx, by = wall.end_point
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq <= _EPSILON:
                distances.append(math.hypot(x - ax, y - ay))
                continue
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy)
                             / length_sq))
            distances.append(math.hypot(x - (ax + t * dx), y - (ay + t * dy)))
        return min(distances)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertices": [list(point) for point in self.vertices],
            "height_m": self.height_m,
            "closed": self.closed,
            "closure_gap_m": self.closure_gap_m,
            "input_errors": list(self.input_errors),
        }
