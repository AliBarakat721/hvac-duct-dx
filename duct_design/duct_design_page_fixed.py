"""Safe duct-design page primitives for integration with the desktop UI.

The repository has no ``duct_design_page.py`` or duct-sizing engine. This module
therefore implements the six requested fixes as reusable, tested workflow
primitives; it is not a drop-in replacement for an absent page and does not
claim to provide SMACNA-compliant sizing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")
ErrorHandler = Callable[[str, str], None]


class DuctDesignInputError(ValueError):
    """Raised when user-supplied duct sizing bounds are physically inconsistent."""


def append_note_once(note: str | None, addition: str) -> str:
    """Append a note with clean spacing and without repeated duplicate phrases."""
    current = " ".join((note or "").split())
    added = " ".join(addition.split())
    if not added:
        return current
    if added in current.split(" | "):
        return current
    return f"{current} | {added}" if current else added


def validate_height_limits(
    h_min_mm: float,
    requested_h_max_mm: float,
    void_height_m: float,
    *,
    clearance_mm: float = 100.0,
) -> tuple[float, float]:
    """Return valid min/max heights or reject contradictory/physically impossible input.

    The user's minimum is never silently lowered to make the search loop run.
    The requested maximum is capped by the ceiling void less the clearance.
    """
    values = (h_min_mm, requested_h_max_mm, void_height_m, clearance_mm)
    if not all(math.isfinite(value) for value in values):
        raise DuctDesignInputError("Height limits and void height must be finite numbers.")
    if h_min_mm <= 0 or requested_h_max_mm <= 0:
        raise DuctDesignInputError("Minimum and maximum duct heights must be positive.")
    if void_height_m <= 0 or clearance_mm < 0:
        raise DuctDesignInputError("Void height must be positive and clearance non-negative.")
    if requested_h_max_mm < h_min_mm:
        raise DuctDesignInputError(
            f"Maximum duct height ({requested_h_max_mm:g} mm) is below the "
            f"requested minimum ({h_min_mm:g} mm)."
        )
    physical_max_mm = (void_height_m * 1000.0) - clearance_mm
    if physical_max_mm < h_min_mm:
        raise DuctDesignInputError(
            f"Available void height permits at most {physical_max_mm:g} mm after clearance, "
            f"below the requested minimum {h_min_mm:g} mm."
        )
    h_max_mm = min(requested_h_max_mm, physical_max_mm)
    if h_max_mm < h_min_mm:
        raise DuctDesignInputError("No valid duct-height interval remains after applying limits.")
    return float(h_min_mm), float(h_max_mm)


def edge_length(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Return Euclidean segment length in the same units as the input points."""
    length = math.dist(p1, p2)
    if not math.isfinite(length) or length <= 0:
        raise ValueError("An edge must have two distinct finite endpoints.")
    return length


def edge_label_position(
    p1: tuple[float, float],
    p2: tuple[float, float],
    offset: float,
) -> tuple[float, float]:
    """Place a label at the segment midpoint, offset perpendicular to its axis.

    For horizontal ducts the normal is vertical; for vertical ducts it is
    horizontal. Canvas and DXF adapters should both use this helper.
    """
    if not math.isfinite(offset):
        raise ValueError("Label offset must be finite.")
    length = edge_length(p1, p2)
    dx = (p2[0] - p1[0]) / length
    dy = (p2[1] - p1[1]) / length
    midpoint = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    normal = (-dy, dx)
    return midpoint[0] + normal[0] * offset, midpoint[1] + normal[1] * offset


@dataclass(frozen=True)
class ExportResult:
    """Non-throwing result suitable for display by a GUI message handler."""

    success: bool
    path: str | None = None
    message: str = ""
    error_type: str | None = None


def safe_export(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> ExportResult:
    """Run a DXF/DWG export without allowing common export errors to crash the UI."""
    try:
        result = operation(*args, **kwargs)
        return ExportResult(success=True, path=str(result) if result is not None else None)
    except (ImportError, ModuleNotFoundError) as exc:
        missing = getattr(exc, "name", None)
        if missing == "ezdxf" or "ezdxf" in str(exc).lower():
            message = "DXF export needs ezdxf; install it with: pip install ezdxf."
        else:
            message = f"Export dependency error: {type(exc).__name__}: {exc}"
        return ExportResult(False, message=message, error_type=type(exc).__name__)
    except PermissionError as exc:
        return ExportResult(
            False,
            message=f"Cannot write the export file (permission denied): {exc}",
            error_type=type(exc).__name__,
        )
    except OSError as exc:
        return ExportResult(
            False,
            message=f"Export file error: {exc}",
            error_type=type(exc).__name__,
        )
    except Exception as exc:  # GUI boundary: report, do not crash the application.
        return ExportResult(
            False,
            message=f"Export failed: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )


class DuctDesignPageFixed:
    """Headless calculation/export controller for a future Qt duct-design page.

    Inject the real engine and UI error callback when the missing source page is
    available. Keeping calculation and error state here makes failure behavior
    deterministic and unit-testable without importing or mocking Qt widgets.
    """

    def __init__(
        self,
        run_design: Callable[..., T | None],
        *,
        on_error: ErrorHandler | None = None,
    ) -> None:
        self._run_design = run_design
        self._on_error = on_error
        self.res: T | None = None
        self.last_error: str | None = None

    def calculate(self, *args: Any, **kwargs: Any) -> bool:
        """Run the design calculation; clear stale output and return False on failure."""
        self.res = None
        self.last_error = None
        try:
            result = self._run_design(*args, **kwargs)
            if result is None:
                raise RuntimeError("Design calculation returned no result.")
            self.res = result
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self._on_error is not None:
                self._on_error("Calculation error", self.last_error)
            return False

    def _ensure(self, *args: Any, **kwargs: Any) -> bool:
        """Ensure a result exists and propagate calculation failure as False."""
        if self.res is None and not self.calculate(*args, **kwargs):
            return False
        return self.res is not None

    def export_dxf(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> ExportResult:
        """Safe DXF export boundary; present ``result.message`` in the Qt adapter."""
        return safe_export(operation, *args, **kwargs)

    def export_dwg(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> ExportResult:
        """Safe DWG export boundary; present ``result.message`` in the Qt adapter."""
        return safe_export(operation, *args, **kwargs)


def output_path(path: str | Path) -> Path:
    """Normalize an export path without creating or overwriting the destination."""
    return Path(path).expanduser().resolve()
