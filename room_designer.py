"""
Room Designer Pro - CAD / BIM Architecture & HVAC Integration Module
Features:
  - Standalone operation or use as a dialog within the Duct DX project
  - Draw any irregular polygon with doors and windows
  - Import CFM and diffusers, display them, and distribute them in 2D and 3D
  - Export actual room area and diffuser locations to the HVAC system
"""

import logging
import math
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

os.environ["VTK_SILENCE_GET_VOID_POINTER_WARNINGS"] = "1"
os.environ["QT_API"] = "pyside6"

import itertools

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QSurfaceFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from diffuser_calculator import (
    RoomLayout,
    diffuser_face_half_extent_m,
    ft2_to_m2,
    ft_to_m,
    m2_to_ft2,
    m_to_ft,
    place_diffusers_in_geometry,
    room_layout_from_cad,
)
from placement_engine import (
    DEFAULT_MIN_SPACING_M,
    DEFAULT_WALL_CLEARANCE_M,
    LayoutValidationResult,
    validate_diffuser_layout,
)
from room_geometry import CLOSURE_TOLERANCE_M, RoomGeometry

gl_format = QSurfaceFormat()
gl_format.setRenderableType(QSurfaceFormat.OpenGL)
gl_format.setVersion(3, 2)
gl_format.setProfile(QSurfaceFormat.CoreProfile)
QSurfaceFormat.setDefaultFormat(gl_format)

try:
    import vtk
    vtk.vtkObject.GlobalWarningDisplayOff()
    import pyvista as pv
    from pyvistaqt import QtInteractor
    HAS_3D = True
except (ImportError, OSError) as exc:
    HAS_3D = False
    logging.getLogger(__name__).warning(
        "3D visualization is unavailable: %s", exc)

CLOSURE_TOL = CLOSURE_TOLERANCE_M


@dataclass
class Wall:
    length: float
    angle: float


@dataclass
class Opening:
    kind: str  # "DOOR" or "WINDOW"
    wall_index: int
    offset: float
    width: float = 0.90
    height: float = 2.10
    sill_height: float = 0.0


@dataclass
class DiffuserPlacement:
    tag: str
    x_m: float
    y_m: float
    throw_t50_m: float = 2.4


@dataclass
class Room:
    name: str = "Room 1"
    height: float = 2.80
    wall_thickness: float = 0.20
    walls: list = field(default_factory=list)
    openings: list = field(default_factory=list)
    diffusers: list = field(default_factory=list)
    layout_stale: bool = False
    layout_validation_errors: list[str] = field(default_factory=list)
    _geometry_cache: RoomGeometry | None = field(
        default=None, init=False, repr=False, compare=False)
    _geometry_source_signature: tuple = field(
        default=(), init=False, repr=False, compare=False)

    def points(self):
        geometry = self.geometry
        return list(geometry.vertices) + (
            [geometry.vertices[0]] if geometry.is_closed and geometry.vertices else [])

    @property
    def geometry(self) -> RoomGeometry:
        signature = (self.height, tuple(
            (wall.length, wall.angle) for wall in self.walls))
        if (self._geometry_cache is None
                or signature != self._geometry_source_signature):
            self._geometry_cache = RoomGeometry.from_walls(self.walls, self.height)
            self._geometry_source_signature = signature
        return self._geometry_cache

    def closure_error(self):
        return self.geometry.closure_gap_m

    def is_closed(self):
        return self.geometry.is_closed

    def polygon(self):
        return list(self.geometry.vertices)

    def area_m2(self):
        return self.geometry.area_m2 if self.is_closed() else 0.0

    def bounding_box_m(self):
        min_x, max_x, min_y, max_y = self.geometry.bounding_box
        return max_x - min_x, max_y - min_y, self.geometry.area_m2

    def equivalent_rect_ft(self):
        """Return bounding-box dimensions, not floor-plan dimensions for irregular rooms."""
        width_m, length_m, area_m2 = self.bounding_box_m()
        return m_to_ft(width_m), m_to_ft(length_m), m2_to_ft2(area_m2)

    def area_sqft(self):
        return m2_to_ft2(self.geometry.area_m2)

    def perimeter_m(self):
        return self.geometry.perimeter_m

    def volume_m3(self):
        return self.geometry.volume_m3

    def validate(self):
        errors = self.geometry.validate()
        if len(self.walls) < 3:
            errors.insert(0, "The room must have at least three walls.")
        if self.walls and not self.is_closed() and len(self.walls) >= 3:
            errors.append(
                f"Room is not closed (gap: {self.closure_error():.2f} m). "
                "Select a closing point.")
        return errors

    def to_dict(self):
        data = {
            "name": self.name,
            "height": self.height,
            "wall_thickness": self.wall_thickness,
            "walls": [{"length": w.length, "angle": w.angle} for w in self.walls],
            "openings": [
                {
                    "kind": o.kind,
                    "wall_index": o.wall_index,
                    "offset": o.offset,
                    "width": o.width,
                    "height": o.height,
                    "sill_height": o.sill_height
                }
                for o in self.openings
            ],
            "diffusers": [
                {"tag": d.tag, "x_m": d.x_m, "y_m": d.y_m, "throw_t50_m": d.throw_t50_m}
                for d in self.diffusers
            ],
            "layout_stale": self.layout_stale,
            "layout_validation_errors": list(self.layout_validation_errors),
        }
        return data

    @staticmethod
    def from_dict(d):
        r = Room(
            name=d.get("name", "Room"),
            height=float(d.get("height", 2.80)),
            wall_thickness=float(d.get("wall_thickness", 0.20)),
            walls=[Wall(float(w["length"]), float(w["angle"])) for w in d.get("walls", [])],
            layout_stale=bool(d.get("layout_stale", False)),
            layout_validation_errors=list(
                d.get("layout_validation_errors", [])),
        )
        r.openings = [
            Opening(
                kind=op["kind"],
                wall_index=int(op["wall_index"]),
                offset=float(op["offset"]),
                width=float(op.get("width", 0.9)),
                height=float(op.get("height", 2.1)),
                sill_height=float(op.get("sill_height", 0.0))
            )
            for op in d.get("openings", [])
        ]
        r.diffusers = [
            DiffuserPlacement(
                tag=df["tag"],
                x_m=float(df["x_m"]),
                y_m=float(df["y_m"]),
                throw_t50_m=float(df.get("throw_t50_m", 2.4))
            )
            for df in d.get("diffusers", [])
        ]
        return r


def default_cad_room():
    walls = [
        Wall(5.0, 0.0), Wall(3.0, 90.0), Wall(2.5, 180.0),
        Wall(2.0, 90.0), Wall(2.5, 180.0), Wall(5.0, 270.0)
    ]
    r = Room(name="Executive Suite", height=2.80, wall_thickness=0.20, walls=walls)
    r.openings = [
        Opening(kind="DOOR", wall_index=0, offset=1.0, width=0.90, height=2.10, sill_height=0.0),
        Opening(kind="WINDOW", wall_index=1, offset=0.8, width=1.40, height=1.30, sill_height=0.90)
    ]
    r.diffusers = [
        DiffuserPlacement("D1", 1.8, 1.5, 2.2),
        DiffuserPlacement("D2", 1.8, 3.8, 2.2)
    ]
    return r


class PlanView(QWidget):
    polygon_drawn = Signal(object)
    cursor_info_changed = Signal(float, float, int)
    wall_selected = Signal(int)
    wall_double_clicked = Signal(int)

    def __init__(self, on_change_callback=None):
        super().__init__()
        self.room = default_cad_room()
        self.on_change = on_change_callback

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(500, 480)

        self.mode = "SELECT"
        self.selected_wall_idx = -1
        self.drawing_points = []
        self.typed_buffer = ""

        self.snap_grid = True
        self.grid_size = 0.10
        self.ortho_snap = True
        self.osnap_active = True

        self.zoom = 55.0
        self.pan = QPointF(140.0, 360.0)
        self._last_mouse_pos = None
        self._drag_vertex_idx = None
        self._drag_diffuser_idx = None
        self._drag_diffuser_changed = False
        self._drag_invalid_reason = None
        self._drag_diffuser_original_position = None
        self.active_throw_m = 2.4
        self.face_half_extent_m = None
        self.min_spacing_m = DEFAULT_MIN_SPACING_M
        self.setCursor(Qt.ArrowCursor)
        self.selected_diffuser_idx = -1

        self.cursor_world = (0.0, 0.0)
        self.active_osnap_target = None

    def to_screen(self, pt):
        wx, wy = pt
        return QPointF(self.pan.x() + wx * self.zoom, self.pan.y() - wy * self.zoom)

    def to_world(self, screen_pt):
        wx = (screen_pt.x() - self.pan.x()) / self.zoom
        wy = (self.pan.y() - screen_pt.y()) / self.zoom
        return wx, wy

    def set_room(self, room):
        self.room = room
        if self.selected_wall_idx >= len(self.room.walls):
            self.selected_wall_idx = -1
        self.update()

    def zoom_extents(self):
        pts = self.room.points() if not self.drawing_points else self.drawing_points
        if len(pts) < 2:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max(max_x - min_x, 1.0)
        h = max(max_y - min_y, 1.0)

        margin = 90
        avail_w = max(self.width() - margin * 2, 100)
        avail_h = max(self.height() - margin * 2, 100)
        self.zoom = min(avail_w / w, avail_h / h)
        mid_x = (min_x + max_x) / 2
        mid_y = (min_y + max_y) / 2
        self.pan = QPointF(self.width() / 2 - mid_x * self.zoom,
                           self.height() / 2 + mid_y * self.zoom)
        self.update()

    def get_osnap_points(self):
        targets = []
        pts = self.drawing_points if self.mode == "DRAW" else self.room.polygon()
        if not pts:
            return targets
        for idx, p in enumerate(pts):
            targets.append((p[0], p[1], "END", idx))
        n = len(pts)
        count = n if (self.room.is_closed() or self.mode != "DRAW") else n - 1
        for i in range(count):
            p1, p2 = pts[i], pts[(i + 1) % n]
            targets.append(((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0, "MID", i))
        return targets

    def apply_snapping(self, raw_wx, raw_wy, ref_pt=None):
        self.active_osnap_target = None
        if self.osnap_active:
            for tx, ty, kind, idx in self.get_osnap_points():
                if math.hypot(raw_wx - tx, raw_wy - ty) < (18 / self.zoom):
                    self.active_osnap_target = (tx, ty, kind, idx)
                    return tx, ty

        wx, wy = raw_wx, raw_wy
        if self.ortho_snap and ref_pt is not None:
            rx, ry = ref_pt
            dx, dy = wx - rx, wy - ry
            dist = math.hypot(dx, dy)
            if dist > 0.05:
                ang = (math.degrees(math.atan2(dy, dx))) % 360
                for target_ang in [0, 45, 90, 135, 180, 225, 270, 315, 360]:
                    if abs(ang - target_ang) < 6.0 or abs(ang - target_ang + 360) < 6.0:
                        rad = math.radians(target_ang)
                        wx = rx + dist * math.cos(rad)
                        wy = ry + dist * math.sin(rad)
                        break

        if self.snap_grid:
            wx = round(wx / self.grid_size) * self.grid_size
            wy = round(wy / self.grid_size) * self.grid_size

        return round(wx, 3), round(wy, 3)

    def keyPressEvent(self, event):
        key = event.key()
        text = event.text()

        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_wall_idx >= 0:
            self.delete_selected_wall()
            return
        if key == Qt.Key_Escape:
            if (self._drag_diffuser_idx is not None
                    and self._drag_diffuser_original_position is not None
                    and 0 <= self._drag_diffuser_idx < len(self.room.diffusers)):
                diffuser = self.room.diffusers[self._drag_diffuser_idx]
                original = self._drag_diffuser_original_position
                changed = (diffuser.x_m, diffuser.y_m) != original
                diffuser.x_m, diffuser.y_m = original
                if changed and self.on_change:
                    self.on_change()
            self._drag_diffuser_idx = None
            self._drag_diffuser_changed = False
            self._drag_invalid_reason = None
            self._drag_diffuser_original_position = None
            self.selected_diffuser_idx = -1
            self.selected_wall_idx = -1
            self.mode = "SELECT"
            self.typed_buffer = ""
            self.setCursor(Qt.ArrowCursor)
            self.wall_selected.emit(-1)
            self.update()
            return
        if key == Qt.Key_C and self.mode in ("SELECT", "DRAW"):
            self.mode = "PICK_CLOSE"
            self.update()
            return

        if self.mode == "DRAW":
            if text in "0123456789.":
                self.typed_buffer += text
                self.update()
                return
            elif key in (Qt.Key_Return, Qt.Key_Enter):
                if self.typed_buffer:
                    try:
                        self.add_wall_with_exact_length(float(self.typed_buffer))
                    except ValueError:
                        pass
                    self.typed_buffer = ""
                    self.update()
                    return

        super().keyPressEvent(event)

    def add_wall_with_exact_length(self, exact_len):
        if exact_len <= 0:
            return
        if not self.drawing_points:
            self.drawing_points.append((0.0, 0.0))

        last_p = self.drawing_points[-1]
        dx = self.cursor_world[0] - last_p[0]
        dy = self.cursor_world[1] - last_p[1]
        cur_ang = math.atan2(dy, dx)

        if self.ortho_snap:
            deg = (math.degrees(cur_ang)) % 360
            for t_ang in [0, 45, 90, 135, 180, 225, 270, 315, 360]:
                if abs(deg - t_ang) < 22.5 or abs(deg - t_ang + 360) < 22.5:
                    cur_ang = math.radians(t_ang)
                    break

        new_x = round(last_p[0] + exact_len * math.cos(cur_ang), 3)
        new_y = round(last_p[1] + exact_len * math.sin(cur_ang), 3)
        self.drawing_points.append((new_x, new_y))
        self.update()

    def delete_selected_wall(self):
        idx = self.selected_wall_idx
        if 0 <= idx < len(self.room.walls):
            del self.room.walls[idx]
            self.room.openings = [op for op in self.room.openings if op.wall_index != idx]
            for op in self.room.openings:
                if op.wall_index > idx:
                    op.wall_index -= 1
            self.selected_wall_idx = -1
            self.wall_selected.emit(-1)
            if self.on_change:
                self.on_change()
            self.update()

    def wheelEvent(self, event):
        pos = event.position()
        old_wx, old_wy = self.to_world(pos)
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self.zoom = max(6.0, min(self.zoom * factor, 400.0))
        self.pan = QPointF(pos.x() - old_wx * self.zoom, pos.y() + old_wy * self.zoom)
        self.update()

    def _point_in_polygon(self, x, y, poly):
        return self.room.geometry.contains_point(x, y)

    def validate_diffuser_positions(self, positions_m) -> LayoutValidationResult:
        return validate_diffuser_layout(
            self.room.geometry, positions_m,
            wall_clearance_m=DEFAULT_WALL_CLEARANCE_M,
            min_spacing_m=self.min_spacing_m,
            face_half_extent_m=self.face_half_extent_m,
        )

    def add_diffuser_at(self, x_m: float, y_m: float) -> LayoutValidationResult:
        candidate = (round(x_m, 2), round(y_m, 2))
        proposed = [(d.x_m, d.y_m) for d in self.room.diffusers]
        proposed.append(candidate)
        result = self.validate_diffuser_positions(proposed)
        if result.valid:
            tag = f"D{len(self.room.diffusers) + 1}"
            self.room.diffusers.append(
                DiffuserPlacement(tag, *candidate, self.active_throw_m))
        return result

    def move_diffuser_to(self, index: int, x_m: float,
                         y_m: float) -> LayoutValidationResult:
        if not 0 <= index < len(self.room.diffusers):
            return LayoutValidationResult(
                False, "index", "The selected diffuser no longer exists.",
                invalid_diffuser_index=index)
        proposed = [(d.x_m, d.y_m) for d in self.room.diffusers]
        proposed[index] = (x_m, y_m)
        result = self.validate_diffuser_positions(proposed)
        if result.valid:
            self.room.diffusers[index].x_m = x_m
            self.room.diffusers[index].y_m = y_m
        return result

    def mouseDoubleClickEvent(self, event):
        pos = event.position()
        wx, wy = self.to_world(pos)
        pts = self.room.points()
        for i in range(len(self.room.walls)):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            l2 = dx * dx + dy * dy
            if l2 == 0:
                continue
            t = max(0.0, min(1.0, ((wx - p1[0]) * dx + (wy - p1[1]) * dy) / l2))
            proj = (p1[0] + t * dx, p1[1] + t * dy)
            if math.hypot(wx - proj[0], wy - proj[1]) < 0.25:
                self.wall_double_clicked.emit(i)
                return

        validation = self.add_diffuser_at(wx, wy)
        if not validation.valid:
            QMessageBox.warning(self, "Diffuser placement", validation.reason)
            return
        if validation.limitations:
            QMessageBox.warning(
                self, "Diffuser placement", " ".join(validation.limitations))
        if self.on_change:
            self.on_change()
        self.update()


    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            if event.button() == Qt.RightButton and self.mode == "SELECT":
                for i, d in enumerate(self.room.diffusers):
                    sd = self.to_screen((d.x_m, d.y_m))
                    if math.hypot(pos.x() - sd.x(), pos.y() - sd.y()) < 16:
                        del self.room.diffusers[i]
                        for idx_t, df_t in enumerate(self.room.diffusers):
                            df_t.tag = f"D{idx_t + 1}"
                        if self.on_change:
                            self.on_change()
                        self.update()
                        return
            self._last_mouse_pos = pos
            return

        if event.button() == Qt.LeftButton:
            wx, wy = self.cursor_world

            if self.mode == "PICK_CLOSE":
                target_pt = (wx, wy)
                if self.active_osnap_target and self.active_osnap_target[2] == "END":
                    target_pt = (self.active_osnap_target[0], self.active_osnap_target[1])
                self.close_room_to_point(target_pt)
                self.mode = "SELECT"
                self.update()
                return

            if self.mode == "DRAW":
                self.typed_buffer = ""
                self.drawing_points.append((wx, wy))
                self.update()
                return

            if self.mode == "SELECT":
                # 1) Check diffusers first so dragging takes priority.
                for i, d in enumerate(self.room.diffusers):
                    sd = self.to_screen((d.x_m, d.y_m))
                    if math.hypot(pos.x() - sd.x(), pos.y() - sd.y()) < 16:
                        self._drag_diffuser_idx = i
                        self._drag_diffuser_changed = False
                        self._drag_invalid_reason = None
                        self._drag_diffuser_original_position = (d.x_m, d.y_m)
                        self.selected_diffuser_idx = i
                        self.setCursor(Qt.ClosedHandCursor)
                        self.update()
                        return

                # 2) Then check polygon vertices.
                pts = self.room.points()
                for i, v in enumerate(pts):
                    sv = self.to_screen(v)
                    if math.hypot(pos.x() - sv.x(), pos.y() - sv.y()) < 14:
                        self._drag_vertex_idx = i
                        return

                # 3) Finally, check for a wall selection.
                clicked_wall = self._find_wall_at(wx, wy)
                self.selected_wall_idx = clicked_wall if clicked_wall is not None else -1
                self.wall_selected.emit(self.selected_wall_idx)
                self.update()
                return

            if self.mode in ("ADD_DOOR", "ADD_WINDOW"):
                wall_idx, offset = self._find_nearest_wall(wx, wy)
                if wall_idx is not None:
                    kind = "DOOR" if self.mode == "ADD_DOOR" else "WINDOW"
                    w = 0.90 if kind == "DOOR" else 1.20
                    h = 2.10 if kind == "DOOR" else 1.30
                    sill = 0.0 if kind == "DOOR" else 0.90
                    self.room.openings.append(Opening(kind, wall_idx, round(offset, 2), w, h, sill))
                    self.mode = "SELECT"
                    if self.on_change:
                        self.on_change()
                    self.update()

    def close_room_to_point(self, target_pt):
        if self.drawing_points:
            self.drawing_points.append(target_pt)
            pts = list(self.drawing_points)
            self.drawing_points = []
            self.polygon_drawn.emit(pts)
        else:
            pts = self.room.points()
            if not pts:
                return
            last_p = pts[-1]
            dx = target_pt[0] - last_p[0]
            dy = target_pt[1] - last_p[1]
            dist = math.hypot(dx, dy)
            if dist > 0.02:
                ang = (math.degrees(math.atan2(dy, dx))) % 360.0
                if dist > 0.05:
                    self.room.walls.append(Wall(round(dist, 3), round(ang, 1)))
                if self.on_change:
                    self.on_change()

    def _find_wall_at(self, wx, wy):
        pts = self.room.points()
        for i in range(len(self.room.walls)):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            l2 = dx * dx + dy * dy
            if l2 == 0:
                continue
            t = max(0.0, min(1.0, ((wx - p1[0]) * dx + (wy - p1[1]) * dy) / l2))
            proj = (p1[0] + t * dx, p1[1] + t * dy)
            if math.hypot(wx - proj[0], wy - proj[1]) < 0.28:
                return i
        return None

    def _find_nearest_wall(self, wx, wy):
        pts = self.room.points()
        if len(pts) < 2:
            return None, 0.0
        n = len(self.room.walls)
        best_idx, min_dist, best_offset = None, float("inf"), 0.0
        for i in range(n):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            l2 = dx * dx + dy * dy
            if l2 == 0:
                continue
            t = max(0.0, min(1.0, ((wx - p1[0]) * dx + (wy - p1[1]) * dy) / l2))
            proj_x, proj_y = p1[0] + t * dx, p1[1] + t * dy
            dist = math.hypot(wx - proj_x, wy - proj_y)
            if dist < min_dist and dist < 0.6:
                min_dist = dist
                best_idx = i
                best_offset = t * math.sqrt(l2)
        return best_idx, best_offset

    def mouseMoveEvent(self, event):
        pos = event.position()
        if event.buttons() & (Qt.RightButton | Qt.MiddleButton):
            if self._last_mouse_pos is not None:
                self.pan += (pos - self._last_mouse_pos)
                self._last_mouse_pos = pos
                self.update()
            return

        # 1. Convert to world coordinates and apply snapping first.
        raw_wx, raw_wy = self.to_world(pos)
        ref = None
        if self.mode == "DRAW" and self.drawing_points:
            ref = self.drawing_points[-1]
        elif self.mode == "SELECT" and self._drag_vertex_idx is not None:
            pts = self.room.points()
            ref = pts[self._drag_vertex_idx - 1] if self._drag_vertex_idx > 0 else pts[-1]

        self.cursor_world = self.apply_snapping(raw_wx, raw_wy, ref)

        cur_len, cur_ang = 0.0, 0.0
        if self.mode == "DRAW" and self.drawing_points:
            lp = self.drawing_points[-1]
            dx = self.cursor_world[0] - lp[0]
            dy = self.cursor_world[1] - lp[1]
            cur_len = math.hypot(dx, dy)
            cur_ang = (math.degrees(math.atan2(dy, dx))) % 360
        self.cursor_info_changed.emit(cur_len, cur_ang, len(self.drawing_points))

        # 2. Drag the diffuser smoothly while keeping it inside the room.
        if self._drag_diffuser_idx is not None and (event.buttons() & Qt.LeftButton):
            idx = self._drag_diffuser_idx
            if 0 <= idx < len(self.room.diffusers):
                nx, ny = self.cursor_world
                diffuser = self.room.diffusers[idx]
                changed = (diffuser.x_m, diffuser.y_m) != (nx, ny)
                validation = self.move_diffuser_to(idx, nx, ny)
                if validation.valid:
                    if changed:
                        self._drag_diffuser_changed = True
                        self._drag_invalid_reason = None
                        if self.on_change:
                            self.on_change()
                else:
                    self._drag_invalid_reason = validation.reason
                self.update()
            return

        # 3. Edit polygon vertices.
        if self._drag_vertex_idx is not None and event.buttons() & Qt.LeftButton:
            idx = self._drag_vertex_idx
            pts = self.room.polygon()
            if idx < len(pts):
                pts[idx] = self.cursor_world
                ox, oy = pts[0]
                n_pts = [(x - ox, y - oy) for x, y in pts]
                walls = []
                for i in range(len(n_pts)):
                    x1, y1 = n_pts[i]
                    x2, y2 = n_pts[(i + 1) % len(n_pts)]
                    l = math.hypot(x2 - x1, y2 - y1)
                    a = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360.0
                    walls.append(Wall(round(l, 3), round(a, 1)))
                self.room.walls = walls
                if self.on_change:
                    self.on_change()
                self.update()
            return

        self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse_pos = None
        if self._drag_vertex_idx is not None:
            self._drag_vertex_idx = None
        was_dragging_diffuser = self._drag_diffuser_idx is not None
        invalid_reason = self._drag_invalid_reason
        diffuser_changed = self._drag_diffuser_changed
        self._drag_diffuser_idx = None
        self._drag_diffuser_changed = False
        self._drag_invalid_reason = None
        self._drag_diffuser_original_position = None
        self.setCursor(Qt.ArrowCursor)
        self.selected_diffuser_idx = -1
        if was_dragging_diffuser and invalid_reason:
            QMessageBox.warning(self, "Diffuser placement", invalid_reason)
        if self.on_change and (not was_dragging_diffuser or diffuser_changed):
            self.on_change()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1e222a"))

        self._draw_grid(p)
        self._draw_room(p)
        self._draw_diffusers(p)
        self._draw_drawing_overlay(p)
        self._draw_openings(p)
        self._draw_dimensions(p)
        self._draw_osnap_markers(p)
        self._draw_dynamic_input_hud(p)

    def _draw_grid(self, p):
        w_tl = self.to_world(QPointF(0, 0))
        w_br = self.to_world(QPointF(self.width(), self.height()))
        min_x = math.floor(w_tl[0]) - 1
        max_x = math.ceil(w_br[0]) + 1
        min_y = math.floor(w_br[1]) - 1
        max_y = math.ceil(w_tl[1]) + 1

        p.setPen(QPen(QColor(255, 255, 255, 12), 1))
        x = min_x
        while x <= max_x:
            p.drawLine(self.to_screen((x, min_y)), self.to_screen((x, max_y)))
            x += 1
        y = min_y
        while y <= max_y:
            p.drawLine(self.to_screen((min_x, y)), self.to_screen((max_x, y)))
            y += 1

        p.setPen(QPen(QColor(255, 255, 255, 38), 1.5))
        p.drawLine(self.to_screen((min_x, 0)), self.to_screen((max_x, 0)))
        p.drawLine(self.to_screen((0, min_y)), self.to_screen((0, max_y)))

    def _draw_room(self, p):
        pts = self.room.points()
        if len(pts) < 2:
            return

        s_pts = [self.to_screen(pt) for pt in pts]
        closed = self.room.is_closed()
        pixel_thick = max(self.room.wall_thickness * self.zoom, 4.0)

        if closed:
            p.setBrush(QBrush(QColor(41, 128, 185, 45)))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF(s_pts[:-1]))

        for i in range(len(self.room.walls)):
            is_sel = (i == self.selected_wall_idx)
            color = QColor("#f1c40f") if is_sel else (QColor("#ecf0f1") if closed else QColor("#e74c3c"))
            th = pixel_thick + (4 if is_sel else 0)

            pen_wall = QPen(color, th, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
            p.setPen(pen_wall)
            p.drawLine(s_pts[i], s_pts[i + 1])

        p.setPen(QPen(QColor("#00cec9"), 1, Qt.DashLine))
        for i in range(len(self.room.walls)):
            p.drawLine(s_pts[i], s_pts[i + 1])

        if not closed and len(self.room.walls) >= 1:
            p.setPen(QPen(QColor("#e74c3c"), 2.5, Qt.DashLine))
            p.drawLine(s_pts[-1], s_pts[0])

        for i, q in enumerate(s_pts[:-1] if closed else s_pts):
            p.setBrush(QBrush(QColor("#0984e3") if i == 0 else QColor("#dfe6e9")))
            p.setPen(QPen(QColor("#2d3436"), 2))
            p.drawEllipse(q, 5, 5)
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(q + QPointF(8, -8), f"P{i}")

        if closed:
            cx = sum(q.x() for q in s_pts[:-1]) / (len(s_pts) - 1)
            cy = sum(q.y() for q in s_pts[:-1]) / (len(s_pts) - 1)
            p.setFont(QFont("Segoe UI", 12, QFont.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(cx - 100, cy - 25, 200, 50), Qt.AlignCenter,
                       f"{self.room.name}\n{self.room.area_m2():.2f} m² ({self.room.area_sqft():,.0f} ft²)")

    def _draw_diffusers(self, p):
        for d in self.room.diffusers:
            pt = self.to_screen((d.x_m, d.y_m))
            r_px = d.throw_t50_m * self.zoom

            p.setPen(QPen(QColor(37, 99, 235, 120), 1.2, Qt.DashLine))
            p.setBrush(QBrush(QColor(37, 99, 235, 30)))
            p.drawEllipse(pt, r_px, r_px)

            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.setBrush(QBrush(QColor("#f59e0b")))
            p.drawRect(QRectF(pt.x() - 8, pt.y() - 8, 16, 16))

            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(pt + QPointF(12, 4), f"{d.tag}")

    def _draw_drawing_overlay(self, p):
        if self.mode not in ("DRAW", "PICK_CLOSE"):
            return

        draw_qpts = [self.to_screen(q) for q in self.drawing_points]
        p.setPen(QPen(QColor("#00cec9"), 2.5, Qt.DashLine))
        for a, b in itertools.pairwise(draw_qpts):
            p.drawLine(a, b)

        cur_s = self.to_screen(self.cursor_world)
        if self.mode == "DRAW" and draw_qpts:
            p.setPen(QPen(QColor("#fdcb6e"), 2, Qt.DashLine))
            p.drawLine(draw_qpts[-1], cur_s)
        elif self.mode == "PICK_CLOSE":
            pts = self.room.points()
            if pts:
                p.setPen(QPen(QColor("#2ecc71"), 2.5, Qt.DashLine))
                p.drawLine(self.to_screen(pts[-1]), cur_s)

    def _draw_openings(self, p):
        pts = self.room.points()
        n = len(self.room.walls)
        if n < 2:
            return

        for op in self.room.openings:
            if op.wall_index >= n:
                continue
            p1, p2 = pts[op.wall_index], pts[op.wall_index + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            ln = math.hypot(dx, dy)
            if ln < 0.1:
                continue
            ux, uy = dx / ln, dy / ln
            nx, ny = -uy, ux

            sw = (p1[0] + ux * op.offset, p1[1] + uy * op.offset)
            ew = (sw[0] + ux * op.width, sw[1] + uy * op.width)
            s_start, s_end = self.to_screen(sw), self.to_screen(ew)

            t_px = self.room.wall_thickness * self.zoom + 2
            p.setPen(QPen(QColor("#1e222a"), t_px, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(s_start, s_end)

            if op.kind == "DOOR":
                p.setPen(QPen(QColor("#e67e22"), 2))
                swing_tip = self.to_screen((sw[0] + nx * op.width, sw[1] + ny * op.width))
                p.drawLine(s_start, swing_tip)

                arc_path = QPainterPath()
                arc_path.moveTo(swing_tip)
                arc_path.quadTo(
                    self.to_screen((sw[0] + (ux + nx) * op.width * 0.7, sw[1] + (uy + ny) * op.width * 0.7)),
                    s_end
                )
                p.setPen(QPen(QColor("#e67e22"), 1.5, Qt.DotLine))
                p.drawPath(arc_path)

            elif op.kind == "WINDOW":
                p.setPen(QPen(QColor("#3498db"), 1.5))
                off = 3
                p.drawLine(s_start + QPointF(nx * off, ny * off), s_end + QPointF(nx * off, ny * off))
                p.drawLine(s_start - QPointF(nx * off, ny * off), s_end - QPointF(nx * off, ny * off))
                p.setPen(QPen(QColor("#74b9ff"), 2))
                p.drawLine(s_start, s_end)

    def _draw_dimensions(self, p):
        pts = self.room.points()
        if len(pts) < 2 or not self.room.is_closed():
            return
        p.setFont(QFont("Consolas", 8, QFont.Bold))

        for i, w in enumerate(self.room.walls):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            ln = math.hypot(dx, dy)
            if ln < 0.2:
                continue
            nx, ny = -dy / ln, dx / ln
            dim_dist = 0.55

            dp1 = (p1[0] + nx * dim_dist, p1[1] + ny * dim_dist)
            dp2 = (p2[0] + nx * dim_dist, p2[1] + ny * dim_dist)
            sp1, sp2 = self.to_screen(dp1), self.to_screen(dp2)
            wp1, wp2 = self.to_screen(p1), self.to_screen(p2)

            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawLine(wp1, sp1)
            p.drawLine(wp2, sp2)

            is_sel = (i == self.selected_wall_idx)
            p.setPen(QPen(QColor("#f1c40f") if is_sel else QColor("#fab1a0"), 1.5 if is_sel else 1.2))
            p.drawLine(sp1, sp2)
            for tk in (sp1, sp2):
                p.drawLine(tk + QPointF(-4, 4), tk + QPointF(4, -4))

            mid = (sp1 + sp2) / 2
            box = QRectF(mid.x() - 40, mid.y() - 10, 80, 20)
            p.setBrush(QColor("#1e222a"))
            p.setPen(Qt.NoPen)
            p.drawRect(box)
            p.setPen(QColor("#f1c40f") if is_sel else QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, f"{w.length:.2f} m")

    def _draw_osnap_markers(self, p):
        if self.active_osnap_target:
            tx, ty, kind, _idx = self.active_osnap_target
            s_pt = self.to_screen((tx, ty))
            p.setBrush(Qt.NoBrush)
            if kind == "END":
                p.setPen(QPen(QColor("#2ecc71"), 2.5))
                p.drawRect(QRectF(s_pt.x() - 6, s_pt.y() - 6, 12, 12))
            elif kind == "MID":
                p.setPen(QPen(QColor("#e67e22"), 2.5))
                p.drawPolygon(QPolygonF([
                    s_pt + QPointF(0, -7),
                    s_pt + QPointF(-7, 7),
                    s_pt + QPointF(7, 7)
                ]))

    def _draw_dynamic_input_hud(self, p):
        cx, cy = self.cursor_world
        s_cursor = self.to_screen((cx, cy))
        hud_box = QRectF(s_cursor.x() + 15, s_cursor.y() - 35, 185, 52)
        p.setBrush(QColor(15, 20, 26, 230))
        p.setPen(QPen(QColor("#f1c40f") if self.selected_wall_idx >= 0 else QColor("#00d2d3"), 1.5))
        p.drawRoundedRect(hud_box, 5, 5)

        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor("#ffffff"))

        if self.mode == "PICK_CLOSE":
            p.setPen(QColor("#2ecc71"))
            p.drawText(hud_box.adjusted(6, 4, -4, -25), Qt.AlignLeft, "Click to pick the closing point")
            p.setPen(QColor("#bdc3c7"))
            p.drawText(hud_box.adjusted(6, 26, -4, -4), Qt.AlignLeft, "Press Esc to cancel")
        elif self.mode == "DRAW" and self.drawing_points:
            p0 = self.drawing_points[-1]
            dist = math.hypot(cx - p0[0], cy - p0[1])
            ang = math.degrees(math.atan2(cy - p0[1], cx - p0[0])) % 360
            if self.typed_buffer:
                p.setPen(QColor("#f1c40f"))
                p.drawText(hud_box.adjusted(6, 4, -4, -25), Qt.AlignLeft, f"Length: {self.typed_buffer} m")
                p.setPen(QColor("#bdc3c7"))
                p.drawText(hud_box.adjusted(6, 26, -4, -4), Qt.AlignLeft, "Press Enter to apply")
            else:
                p.drawText(hud_box.adjusted(6, 4, -4, -25), Qt.AlignLeft, f"L: {dist:.2f}m | ∠ {ang:.0f}°")
                p.setPen(QColor("#7f8c8d"))
                p.drawText(hud_box.adjusted(6, 26, -4, -4), Qt.AlignLeft, "Type the length directly")
        elif self.selected_wall_idx >= 0:
            w = self.room.walls[self.selected_wall_idx]
            p.setPen(QColor("#f1c40f"))
            p.drawText(hud_box.adjusted(6, 4, -4, -25), Qt.AlignLeft, f"Selected: W{self.selected_wall_idx + 1} ({w.length:.2f} m)")
            p.setPen(QColor("#e74c3c"))
            p.drawText(hud_box.adjusted(6, 26, -4, -4), Qt.AlignLeft, "Press Delete to remove")
        else:
            p.drawText(hud_box.adjusted(6, 4, -4, -25), Qt.AlignLeft, f"X: {cx:.2f}m | Y: {cy:.2f}m")
            p.setPen(QColor("#bdc3c7"))
            p.drawText(hud_box.adjusted(6, 26, -4, -4), Qt.AlignLeft, "Click a wall to select it")


class View3D(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if HAS_3D:
            self.plotter = QtInteractor(self)
            lay.addWidget(self.plotter.interactor)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.close_plotter)
        else:
            self.plotter = None
            lbl = QLabel("To enable 3D, install:\n  pip install pyvista pyvistaqt")
            lbl.setAlignment(Qt.AlignCenter)
            lay.addWidget(lbl)

    def close_plotter(self):
        plotter = self.plotter
        if plotter is None:
            return
        self.plotter = None
        try:
            plotter.close()
        except RuntimeError:
            logging.getLogger(__name__).warning(
                "VTK plotter was already destroyed during widget teardown.")

    def closeEvent(self, event):
        self.close_plotter()
        super().closeEvent(event)

    def set_room(self, room):
        if not self.plotter:
            return
        self.plotter.clear()

        if room.validate():
            self.plotter.add_text("Close the polygon to display the 3D view", font_size=10, color="orange")
            return

        poly = room.polygon()
        n = len(poly)
        if n < 3:
            return

        pts3 = np.array([[x, y, 0.0] for x, y in poly])
        face = np.hstack([[n], np.arange(n)])
        floor = pv.PolyData(pts3, faces=face).triangulate()
        slab = floor.extrude((0, 0, -0.15), capping=True)
        self.plotter.add_mesh(slab, color="#bdc3c7", show_edges=True, edge_color="#7f8c8d")

        t = room.wall_thickness
        pts = room.points()

        for i, _wall in enumerate(room.walls):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            w_len = math.hypot(dx, dy)
            if w_len < 0.01:
                continue

            angle_deg = math.degrees(math.atan2(dy, dx))
            wall_openings = sorted(
                [op for op in room.openings if op.wall_index == i],
                key=lambda o: o.offset
            )

            segments = []
            cur_x = 0.0
            for op in wall_openings:
                op_start = max(0.0, min(op.offset, w_len))
                op_end = max(0.0, min(op.offset + op.width, w_len))

                if op_start > cur_x:
                    segments.append((cur_x, op_start, 0.0, room.height, "#ecf0f1", 0.9))

                if op.sill_height > 0:
                    segments.append((op_start, op_end, 0.0, op.sill_height, "#ecf0f1", 0.9))
                lintel_start = op.sill_height + op.height
                if lintel_start < room.height:
                    segments.append((op_start, op_end, lintel_start, room.height, "#ecf0f1", 0.9))

                if op.kind == "WINDOW":
                    segments.append((op_start, op_end, op.sill_height, lintel_start, "#74b9ff", 0.35))

                cur_x = op_end

            if cur_x < w_len:
                segments.append((cur_x, w_len, 0.0, room.height, "#ecf0f1", 0.9))

            for x_min, x_max, z_min, z_max, color, opacity in segments:
                if x_max <= x_min or z_max <= z_min:
                    continue
                box = pv.Box(bounds=[x_min, x_max, -t / 2, t / 2, z_min, z_max])
                box.rotate_z(angle_deg, inplace=True)
                box.translate((p1[0], p1[1], 0), inplace=True)
                self.plotter.add_mesh(
                    box, color=color, opacity=opacity,
                    show_edges=(opacity > 0.5), edge_color="#95a5a6"
                )

        for d in room.diffusers:
            diff_box = pv.Box(bounds=[d.x_m - 0.25, d.x_m + 0.25, d.y_m - 0.25, d.y_m + 0.25, room.height - 0.05, room.height])
            self.plotter.add_mesh(diff_box, color="#f59e0b", show_edges=True, edge_color="#b45309")

        self.plotter.show_grid(color="#b2bec3")
        self.plotter.show_axes()
        self.plotter.reset_camera()


class RoomDesignerDialog(QDialog):
    """Interactive Room Designer dialog for integration with the HVAC project."""
    def __init__(self, parent=None, room_data=None, system_cfm=0.0,
                 system_name="", n_target=None,
                 n_target_provider: Callable[[RoomLayout], object | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Room Designer Pro - CAD / BIM Architecture [{system_name}]")
        self.resize(1340, 820)
        self.system_cfm = system_cfm
        self.system_name = system_name
        self.n_target = n_target
        self.n_target_provider = n_target_provider
        self.active_throw_m = 2.4
        self.face_half_extent_m = None
        self.layout_validation_errors = list(
            room_data.get("layout_validation_errors", [])
            if isinstance(room_data, dict) else [])

        if room_data and isinstance(room_data, dict):
            self.room = Room.from_dict(room_data)
            self.layout_stale = self.room.layout_stale
        else:
            self.layout_validation_errors = []
            self.room = default_cad_room()
            self.layout_stale = False
            if system_name:
                self.room.name = system_name

        if self.room.diffusers:
            self.active_throw_m = self.room.diffusers[0].throw_t50_m
        if self.n_target_provider is not None and self.room.geometry.is_valid:
            self._apply_diffuser_spec(self.n_target_provider(
                room_layout_from_cad(self.room.to_dict())))
            if hasattr(self, "plan"):
                self.plan.face_half_extent_m = self.face_half_extent_m

        if self.room.diffusers:
            if not self.room.geometry.is_valid:
                self.layout_validation_errors = [
                    "Saved diffuser layout cannot be verified because room "
                    "geometry is invalid: "
                    + "; ".join(self.room.geometry.validate())
                ]
            else:
                validation = validate_diffuser_layout(
                    self.room.geometry,
                    [(d.x_m, d.y_m) for d in self.room.diffusers],
                    wall_clearance_m=DEFAULT_WALL_CLEARANCE_M,
                    min_spacing_m=DEFAULT_MIN_SPACING_M,
                    face_half_extent_m=self.face_half_extent_m,
                )
                if not validation.valid:
                    self.layout_validation_errors = [validation.reason]
                elif validation.limitations:
                    self.layout_validation_errors.extend(validation.limitations)
            if self.layout_validation_errors:
                self.layout_stale = True
        if self.layout_stale:
            self.room.layout_validation_errors = list(
                self.layout_validation_errors)
        self._syncing = False
        self._last_geometry_signature = self._geometry_signature()
        self._canvas_refresh_timer = QTimer(self)
        self._canvas_refresh_timer.setSingleShot(True)
        self._canvas_refresh_timer.setInterval(50)
        self._canvas_refresh_timer.timeout.connect(
            self._flush_canvas_refresh)
        self.build_ui()

    def done(self, result):
        self._canvas_refresh_timer.stop()
        view3d = getattr(self, "view3d", None)
        if view3d is not None:
            view3d.close_plotter()
        super().done(result)

    def closeEvent(self, event):
        self._canvas_refresh_timer.stop()
        view3d = getattr(self, "view3d", None)
        if view3d is not None:
            view3d.close_plotter()
        super().closeEvent(event)

    def _apply_diffuser_spec(self, spec):
        if spec is None:
            return
        if hasattr(spec, "throw_t50_ft") and hasattr(spec, "size_label"):
            self.active_throw_m = ft_to_m(float(spec.throw_t50_ft))
            self.face_half_extent_m = diffuser_face_half_extent_m(spec)
        else:
            logging.getLogger(__name__).warning(
                "Ignoring diffuser specification with unexpected type: %s",
                type(spec).__name__)

    def _geometry_signature(self):
        geometry = self.room.geometry
        return geometry.vertices, geometry.height_m

    def _invalidate_layout_if_geometry_changed(self):
        signature = self._geometry_signature()
        if signature != self._last_geometry_signature:
            self.room.diffusers = []
            self.layout_stale = True
            self.layout_validation_errors = []
            self._last_geometry_signature = signature

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tb = QToolBar("Drafting Tools")
        self.btn_draw = QPushButton("✏️ Draw Wall")
        self.btn_select = QPushButton("✋ Select / Edit")
        self.btn_pick_close = QPushButton("🔒 Close to Point")
        self.btn_del_wall = QPushButton("🗑️ Delete Wall (Del)")
        self.btn_door = QPushButton("🚪 Place Door")
        self.btn_window = QPushButton("🪟 Place Window")
        self.btn_auto_dist = QPushButton("🎯 Auto-Space (Balanced Distribution)")
        self.btn_fit = QPushButton("🔍 Fit View")
        self.btn_clear = QPushButton("🗑️ Clear")

        self.chk_osnap = QCheckBox("OSNAP")
        self.chk_osnap.setChecked(True)
        self.chk_ortho = QCheckBox("Ortho/45°")
        self.chk_ortho.setChecked(True)

        tb.addWidget(self.btn_draw)
        tb.addWidget(self.btn_select)
        tb.addWidget(self.btn_pick_close)
        tb.addWidget(self.btn_del_wall)
        tb.addSeparator()
        tb.addWidget(self.btn_door)
        tb.addWidget(self.btn_window)
        tb.addSeparator()
        tb.addWidget(self.btn_auto_dist)
        tb.addWidget(self.btn_fit)
        tb.addWidget(self.btn_clear)
        tb.addSeparator()
        tb.addWidget(self.chk_osnap)
        tb.addWidget(self.chk_ortho)
        root.addWidget(tb)

        self.btn_draw.clicked.connect(self.start_draw_mode)
        self.btn_select.clicked.connect(self.set_select_mode)
        self.btn_pick_close.clicked.connect(self.start_pick_close_mode)
        self.btn_del_wall.clicked.connect(self.action_delete_selected_wall)
        self.btn_door.clicked.connect(self.set_door_mode)
        self.btn_window.clicked.connect(self.set_window_mode)
        self.btn_auto_dist.clicked.connect(self.auto_distribute_diffusers)
        self.btn_fit.clicked.connect(lambda: self.plan.zoom_extents())
        self.btn_clear.clicked.connect(self.action_clear)
        self.chk_osnap.toggled.connect(lambda v: setattr(self.plan, 'osnap_active', v))
        self.chk_ortho.toggled.connect(lambda v: setattr(self.plan, 'ortho_snap', v))

        splitter = QSplitter(Qt.Horizontal)

        left_widget = QWidget()
        ll = QVBoxLayout(left_widget)

        form = QFormLayout()
        self.name_edit = QLineEdit(self.room.name)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(1.0, 20.0)
        self.height_spin.setSingleStep(0.1)
        self.height_spin.setValue(self.room.height)
        self.height_spin.setSuffix(" m")

        self.thick_spin = QDoubleSpinBox()
        self.thick_spin.setRange(0.05, 1.0)
        self.thick_spin.setSingleStep(0.05)
        self.thick_spin.setValue(self.room.wall_thickness)
        self.thick_spin.setSuffix(" m")

        form.addRow("Room Name:", self.name_edit)
        form.addRow("Ceiling Height:", self.height_spin)
        form.addRow("Wall Thickness:", self.thick_spin)
        ll.addLayout(form)

        ll.addWidget(QLabel("<b>Wall Table (edit a wall or select one to delete):</b>"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Length (m)", "Direction (°)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.on_table_row_selected)
        ll.addWidget(self.table)

        btn_row = QHBoxLayout()
        b_add_w = QPushButton("+ Add Wall")
        b_del_sel = QPushButton("🗑️ Delete Selected")
        b_add_w.clicked.connect(self.add_manual_wall)
        b_del_sel.clicked.connect(self.action_delete_selected_wall)
        btn_row.addWidget(b_add_w)
        btn_row.addWidget(b_del_sel)
        ll.addLayout(btn_row)

        res_box = QGroupBox("Room & HVAC Metrics")
        res_l = QVBoxLayout(res_box)
        self.lbl_stats = QLabel()
        self.lbl_stats.setWordWrap(True)
        res_l.addWidget(self.lbl_stats)
        ll.addWidget(res_box)

        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color:#e74c3c; font-weight:bold;")
        ll.addWidget(self.lbl_warn)

        btn_apply = QPushButton("✅ Apply to HVAC Project")
        btn_apply.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 10px; font-size: 13px;")
        btn_apply.clicked.connect(self.accept)
        ll.addWidget(btn_apply)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.plan = PlanView(on_change_callback=self.on_canvas_modified)
        self.plan.face_half_extent_m = self.face_half_extent_m
        self.plan.min_spacing_m = DEFAULT_MIN_SPACING_M
        self.plan.polygon_drawn.connect(self.on_free_polygon_finished)
        self.plan.cursor_info_changed.connect(self.on_cursor_info_update)
        self.plan.wall_selected.connect(self.on_wall_selected_from_canvas)
        self.plan.wall_double_clicked.connect(self.prompt_edit_wall_length)
        self.view3d = View3D()

        tabs = QTabWidget()
        tabs.addTab(self.plan, "📐 2D Floor Plan & Diffusers")
        tabs.addTab(self.view3d, "🧊 3D Solid Model")
        right_layout.addWidget(tabs)

        cmd_bar = QWidget()
        cmd_bar.setStyleSheet("background-color: #1a1e24; border-top: 1px solid #2d3436; padding: 4px;")
        cmd_layout = QHBoxLayout(cmd_bar)
        cmd_layout.setContentsMargins(6, 2, 6, 2)

        self.lbl_cmd_info = QLabel("Click a wall to select and delete it, or draw and enter a length:")
        self.lbl_cmd_info.setStyleSheet("color: #00cec9; font-weight: bold;")
        self.cmd_length_input = QDoubleSpinBox()
        self.cmd_length_input.setRange(0.05, 50.0)
        self.cmd_length_input.setDecimals(2)
        self.cmd_length_input.setValue(4.0)
        self.cmd_length_input.setSuffix(" m")

        btn_cmd_apply = QPushButton("➕ Add Wall (Enter)")
        btn_cmd_apply.clicked.connect(self.apply_cmd_length)
        btn_cmd_close = QPushButton("🔒 Pick Closing Point (C)")
        btn_cmd_close.clicked.connect(self.start_pick_close_mode)

        cmd_layout.addWidget(self.lbl_cmd_info)
        cmd_layout.addWidget(QLabel("Length:"))
        cmd_layout.addWidget(self.cmd_length_input)
        cmd_layout.addWidget(btn_cmd_apply)
        cmd_layout.addWidget(btn_cmd_close)
        right_layout.addWidget(cmd_bar)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_container)
        splitter.setSizes([380, 960])
        root.addWidget(splitter)

        self.table.itemChanged.connect(self.on_table_modified)
        self.height_spin.valueChanged.connect(self.on_meta_changed)
        self.thick_spin.valueChanged.connect(self.on_meta_changed)
        self.name_edit.textChanged.connect(self.on_meta_changed)

        self.update_ui_from_room()

    def auto_distribute_diffusers(self):
        """Place diffusers evenly near the room center and clear of the walls."""
        validation_errors = self.room.validate()
        if validation_errors:
            QMessageBox.warning(
                self, "Invalid room geometry", "\n".join(validation_errors))
            return
        self._invalidate_layout_if_geometry_changed()

        n_target = self.n_target
        if n_target is None:
            if self.n_target_provider is not None:
                spec = self.n_target_provider(
                    room_layout_from_cad(self.room.to_dict()))
                if hasattr(spec, "qty"):
                    self._apply_diffuser_spec(spec)
                    self.plan.face_half_extent_m = self.face_half_extent_m
                    n_target = int(spec.qty)
                else:
                    n_target = spec
            if n_target is None:
                max_area_m2 = ft2_to_m2(400.0)
                n_target = max(
                    1, min(12, math.ceil(self.room.area_m2() / max_area_m2)))
        active_throw = self.active_throw_m
        placement = place_diffusers_in_geometry(
            self.room.geometry, n_target, min_wall_clearance_m=0.30,
            min_spacing_m=0.60, throw_t50_m=active_throw,
            face_half_extent_m=self.face_half_extent_m or 0.0)
        if not placement.success:
            if not self.room.diffusers:
                self.layout_stale = True
            QMessageBox.warning(
                self, "Diffuser placement",
                f"Could not place {n_target} diffusers inside the room "
                "polygon with 0.30 m wall clearance and 0.60 m spacing.\n"
                + "\n".join(placement.errors))
            return
        self.room.diffusers = [
            DiffuserPlacement(f"D{i + 1}", x, y, active_throw)
            for i, (x, y) in enumerate(placement.positions_m)
        ]
        self.layout_stale = False
        self.layout_validation_errors = []
        self.on_canvas_modified()
        self.plan.update()

    def start_draw_mode(self):
        self.plan.mode = "DRAW"
        self.plan.drawing_points = []
        self.plan.selected_wall_idx = -1
        self._highlight_btn(self.btn_draw)
        self.plan.setFocus()

    def set_select_mode(self):
        self.plan.mode = "SELECT"
        self._highlight_btn(self.btn_select)

    def start_pick_close_mode(self):
        self.plan.mode = "PICK_CLOSE"
        self._highlight_btn(self.btn_pick_close)

    def set_door_mode(self):
        self.plan.mode = "ADD_DOOR"
        self._highlight_btn(self.btn_door)

    def set_window_mode(self):
        self.plan.mode = "ADD_WINDOW"
        self._highlight_btn(self.btn_window)

    def _highlight_btn(self, target):
        for b in (self.btn_draw, self.btn_select, self.btn_pick_close, self.btn_door, self.btn_window):
            if b == target:
                b.setStyleSheet("background-color: #0984e3; color: white; font-weight: bold;")
            else:
                b.setStyleSheet("")

    def action_delete_selected_wall(self):
        self.plan.delete_selected_wall()

    def on_wall_selected_from_canvas(self, wall_idx):
        if self._syncing:
            return
        self._syncing = True
        if 0 <= wall_idx < self.table.rowCount():
            self.table.selectRow(wall_idx)
            self.btn_del_wall.setText(f"🗑️ Delete Wall W{wall_idx + 1} (Del)")
        else:
            self.table.clearSelection()
            self.btn_del_wall.setText("🗑️ Delete Selected Wall (Del)")
        self._syncing = False

    def on_table_row_selected(self):
        if self._syncing:
            return
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            self.plan.selected_wall_idx = row
            self.btn_del_wall.setText(f"🗑️ Delete Wall W{row + 1} (Del)")
        else:
            self.plan.selected_wall_idx = -1
            self.btn_del_wall.setText("🗑️ Delete Selected Wall (Del)")
        self.plan.update()

    def action_clear(self):
        self.room = Room(name=self.room.name, height=self.room.height, wall_thickness=self.room.wall_thickness)
        self.start_draw_mode()
        self.update_ui_from_room()

    def on_cursor_info_update(self, cur_len, cur_ang, total_pts):
        if self.plan.mode == "DRAW":
            self.lbl_cmd_info.setText(
                f"Direction: {cur_ang:.0f}° | Length: {cur_len:.2f} m | "
                f"Points: {total_pts} | (click or type and press Enter):")
            if cur_len > 0.05:
                self.cmd_length_input.setValue(round(cur_len, 2))

    def apply_cmd_length(self):
        val = self.cmd_length_input.value()
        self.plan.add_wall_with_exact_length(val)

    def prompt_edit_wall_length(self, wall_idx):
        cur_w = self.room.walls[wall_idx]
        new_len, ok = QInputDialog.getDouble(
            self, "Edit Wall Length",
            f"Enter the new length for wall W{wall_idx + 1} (current: {cur_w.length:.2f} m):",
            cur_w.length, 0.1, 100.0, 2
        )
        if ok:
            self.room.walls[wall_idx].length = round(new_len, 3)
            self.update_ui_from_room()

    def on_free_polygon_finished(self, points):
        if len(points) < 3:
            return
        ox, oy = points[0]
        pts = [(x - ox, y - oy) for x, y in points]
        walls = []
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            l = math.hypot(x2 - x1, y2 - y1)
            a = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360.0
            walls.append(Wall(round(l, 3), round(a, 1)))

        self.room.walls = walls
        self.set_select_mode()
        self.update_ui_from_room()
        self.plan.zoom_extents()

    def on_canvas_modified(self):
        if self._syncing:
            return
        self._invalidate_layout_if_geometry_changed()
        self._canvas_refresh_timer.start()

    def _flush_canvas_refresh(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.populate_table()
            self.refresh_metrics()
            self.view3d.set_room(self.room)
        finally:
            self._syncing = False

    def on_table_modified(self, _=None):
        if self._syncing:
            return
        self._syncing = True
        walls = []
        for r in range(self.table.rowCount()):
            try:
                L = float(self.table.item(r, 0).text())
                A = float(self.table.item(r, 1).text())
            except (ValueError, AttributeError):
                L, A = 0.0, 0.0
            walls.append(Wall(L, A))
        self.room.walls = walls
        self._invalidate_layout_if_geometry_changed()
        self.plan.set_room(self.room)
        self.view3d.set_room(self.room)
        self.refresh_metrics()
        self._syncing = False

    def on_meta_changed(self):
        self.room.name = self.name_edit.text()
        self.room.height = self.height_spin.value()
        self.room.wall_thickness = self.thick_spin.value()
        self._invalidate_layout_if_geometry_changed()
        self.plan.update()
        self.view3d.set_room(self.room)
        self.refresh_metrics()

    def update_ui_from_room(self):
        self._invalidate_layout_if_geometry_changed()
        self._syncing = True
        self.name_edit.setText(self.room.name)
        self.height_spin.setValue(self.room.height)
        self.thick_spin.setValue(self.room.wall_thickness)
        self.populate_table()
        self.plan.set_room(self.room)
        self.view3d.set_room(self.room)
        self.refresh_metrics()
        self._syncing = False

    def populate_table(self):
        self.table.setRowCount(len(self.room.walls))
        for i, w in enumerate(self.room.walls):
            self.table.setItem(i, 0, QTableWidgetItem(f"{w.length:.3f}"))
            self.table.setItem(i, 1, QTableWidgetItem(f"{w.angle:.1f}"))
        self.table.setVerticalHeaderLabels([f"W{i + 1}" for i in range(len(self.room.walls))])
        if 0 <= self.plan.selected_wall_idx < self.table.rowCount():
            self.table.selectRow(self.plan.selected_wall_idx)

    def refresh_metrics(self):
        r = self.room
        vol_ft3 = r.volume_m3() * 35.3147
        ach_txt = f"{(self.system_cfm * 60.0 / vol_ft3):.1f}" if (vol_ft3 > 0 and self.system_cfm > 0) else "—"

        self.lbl_stats.setText(
            f"Walls Count: {len(r.walls)}\n"
            f"Net Area: {r.area_m2():.2f} m² ({r.area_sqft():,.0f} ft²)\n"
            f"Perimeter: {r.perimeter_m():.2f} m\n"
            f"Ceiling Height: {r.height:.2f} m ({m_to_ft(r.height):.1f} ft)\n"
            f"Room Volume: {r.volume_m3():.2f} m³\n"
            f"System CFM: {self.system_cfm:,.0f} CFM | ACH: {ach_txt}\n"
            f"Diffusers: {len(r.diffusers)} unit(s) placed\n"
            f"Status: {'Valid Closed Room' if r.geometry.is_valid else 'Invalid Room Geometry'}"
        )
        messages = r.validate()
        messages.extend(self.layout_validation_errors)
        if self.layout_stale:
            messages.append(
                "Diffuser layout is stale or not fully verified; recalculate "
                "or resolve the listed validation warnings.")
        self.lbl_warn.setText("\n".join(messages))

    def add_manual_wall(self):
        self.room.walls.append(Wall(2.0, 0.0))
        self.update_ui_from_room()

    def get_result_data(self):
        room_w_ft, room_l_ft, _ = self.room.equivalent_rect_ft()
        self.room.layout_stale = self.layout_stale
        self.room.layout_validation_errors = list(
            self.layout_validation_errors)
        room_dict = self.room.to_dict()
        room_dict["layout_stale"] = self.layout_stale
        room_dict["layout_validation_errors"] = list(
            self.layout_validation_errors)
        return {
            "room_dict": room_dict,
            "area_sqft": self.room.area_sqft(),
            "area_m2": self.room.area_m2(),
            "room_w_ft": room_w_ft,
            "room_l_ft": room_l_ft,
            "height_ft": m_to_ft(self.room.height),
            "height_m": self.room.height,
            "geometry_valid": self.room.geometry.is_valid,
            "geometry_errors": self.room.geometry.validate(),
            "layout_stale": self.layout_stale,
            "layout_validation_errors": list(self.layout_validation_errors),
            "diffusers": [
                {
                    "tag": d.tag,
                    "x_m": d.x_m,
                    "y_m": d.y_m,
                    "x_ft": m_to_ft(d.x_m),
                    "y_ft": m_to_ft(d.y_m),
                    "throw_t50_m": d.throw_t50_m,
                    "throw_t50_ft": m_to_ft(d.throw_t50_m),
                }
                for d in self.room.diffusers
            ],
        }

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dlg = RoomDesignerDialog(self, system_name="Standalone Test", system_cfm=1200)
        self.setCentralWidget(self.dlg)
        self.setWindowTitle("Room Designer Standalone")
        self.resize(1340, 840)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
