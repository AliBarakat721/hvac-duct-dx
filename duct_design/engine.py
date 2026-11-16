"""Preliminary rectangular HVAC duct sizing using an equal-friction round equivalent.

All public inputs and outputs use explicit customary units. Internal calculations
are SI. This is a segment sizing aid, not a complete duct-network solver or a
SMACNA-compliance check; see ``docs/DUCT_DESIGN_ENGINE.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

CFM_TO_M3_S = 0.0004719474432
FT_TO_M = 0.3048
IN_TO_M = 0.0254
PA_PER_IN_WG = 249.08891
PA_PER_100FT_TO_IN_WG = 30.48 / PA_PER_IN_WG
AIR_GAS_CONSTANT = 287.05  # J/(kg K), dry air


class DuctDesignCalculationError(ValueError):
    """Input or sizing error that should be shown to the user without a traceback."""


@dataclass(frozen=True)
class DuctSegmentInput:
    """One independent, constant-flow straight duct section and its aggregate K."""

    tag: str
    airflow_cfm: float
    length_ft: float
    fitting_k: float = 0.0


@dataclass(frozen=True)
class DuctDesignOptions:
    """Shared design criteria and air/material properties for duct segments."""

    max_velocity_fpm: float = 1500.0
    max_friction_inwg_100ft: float = 0.08
    min_height_in: float = 8.0
    max_height_in: float = 24.0
    min_width_in: float = 8.0
    max_width_in: float = 48.0
    size_step_in: float = 1.0
    max_aspect_ratio: float = 4.0
    air_temperature_c: float = 20.0
    absolute_pressure_kpa: float = 101.325
    roughness_mm: float = 0.09


@dataclass(frozen=True)
class DuctSizingResult:
    tag: str
    airflow_cfm: float
    length_ft: float
    width_in: float
    height_in: float
    area_sq_in: float
    velocity_fpm: float
    equivalent_diameter_in: float
    hydraulic_diameter_in: float
    reynolds_number: float
    darcy_friction_factor: float
    friction_rate_inwg_100ft: float
    straight_friction_loss_inwg: float
    fitting_loss_inwg: float
    total_modeled_loss_inwg: float
    velocity_pressure_inwg: float
    warnings: tuple[str, ...] = ()


def _finite_positive(name: str, value: float, *, allow_zero: bool = False) -> None:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        relation = "non-negative" if allow_zero else "positive"
        raise DuctDesignCalculationError(f"{name} must be finite and {relation}.")


def validate_design_inputs(
    segments: Iterable[DuctSegmentInput], options: DuctDesignOptions
) -> tuple[DuctSegmentInput, ...]:
    rows = tuple(segments)
    if not rows:
        raise DuctDesignCalculationError("Add at least one duct segment before calculating.")
    for i, row in enumerate(rows, start=1):
        tag = row.tag.strip() or f"Segment {i}"
        _finite_positive(f"{tag}: airflow", row.airflow_cfm)
        _finite_positive(f"{tag}: length", row.length_ft)
        _finite_positive(f"{tag}: fitting K", row.fitting_k, allow_zero=True)
    positive_fields = (
        ("maximum velocity", options.max_velocity_fpm),
        ("maximum friction rate", options.max_friction_inwg_100ft),
        ("minimum height", options.min_height_in),
        ("maximum height", options.max_height_in),
        ("minimum width", options.min_width_in),
        ("maximum width", options.max_width_in),
        ("size increment", options.size_step_in),
        ("maximum aspect ratio", options.max_aspect_ratio),
        ("absolute pressure", options.absolute_pressure_kpa),
    )
    for name, value in positive_fields:
        _finite_positive(name, value)
    if not math.isfinite(options.air_temperature_c) or options.air_temperature_c <= -273.15:
        raise DuctDesignCalculationError("Air temperature must be above absolute zero.")
    _finite_positive("absolute roughness", options.roughness_mm, allow_zero=True)
    if options.max_height_in < options.min_height_in:
        raise DuctDesignCalculationError("Maximum height is below minimum height.")
    if options.max_width_in < options.min_width_in:
        raise DuctDesignCalculationError("Maximum width is below minimum width.")
    if options.max_aspect_ratio < 1.0:
        raise DuctDesignCalculationError("Maximum aspect ratio must be at least 1.0.")
    if len({row.tag.strip() for row in rows}) != len(rows):
        raise DuctDesignCalculationError("Each duct segment tag must be unique.")
    return tuple(
        row if row.tag.strip() else DuctSegmentInput(
            f"Segment {i}", row.airflow_cfm, row.length_ft, row.fitting_k
        )
        for i, row in enumerate(rows, start=1)
    )


def air_properties(temperature_c: float, absolute_pressure_kpa: float) -> tuple[float, float]:
    """Return dry-air density (kg/m³) and dynamic viscosity (Pa·s).

    Density uses the ideal-gas relation. Viscosity uses Sutherland's formula.
    """
    _finite_positive("absolute pressure", absolute_pressure_kpa)
    if not math.isfinite(temperature_c) or temperature_c <= -273.15:
        raise DuctDesignCalculationError("Air temperature must be above absolute zero.")
    temp_k = temperature_c + 273.15
    density = absolute_pressure_kpa * 1000.0 / (AIR_GAS_CONSTANT * temp_k)
    mu_ref = 1.716e-5
    sutherland_k = 111.0
    viscosity = mu_ref * (temp_k / 273.15) ** 1.5 * (273.15 + sutherland_k) / (temp_k + sutherland_k)
    return density, viscosity


def rectangular_equivalent_diameter_m(width_m: float, height_m: float) -> float:
    """Huebscher equal-flow/equal-friction round equivalent for a rectangle."""
    _finite_positive("duct width", width_m)
    _finite_positive("duct height", height_m)
    return 1.30 * (width_m * height_m) ** 0.625 / (width_m + height_m) ** 0.25


def rectangular_hydraulic_diameter_m(width_m: float, height_m: float) -> float:
    """Return 4A/P for a rectangular section (used as a geometric diagnostic)."""
    _finite_positive("duct width", width_m)
    _finite_positive("duct height", height_m)
    return 2.0 * width_m * height_m / (width_m + height_m)


def colebrook_darcy_factor(reynolds: float, diameter_m: float, roughness_m: float) -> float:
    """Solve the Colebrook–White equation for the Darcy friction factor."""
    _finite_positive("Reynolds number", reynolds)
    _finite_positive("equivalent diameter", diameter_m)
    _finite_positive("absolute roughness", roughness_m, allow_zero=True)
    if reynolds < 2300.0:
        return 64.0 / reynolds

    relative_roughness = roughness_m / diameter_m
    if relative_roughness >= 0.05:
        raise DuctDesignCalculationError("Relative roughness is outside the supported range.")

    def residual(factor: float) -> float:
        root = math.sqrt(factor)
        term = relative_roughness / 3.7 + 2.51 / (reynolds * root)
        return 1.0 / root + 2.0 * math.log10(term)

    low, high = 0.001, 0.2
    f_low, f_high = residual(low), residual(high)
    if f_low * f_high > 0:
        raise DuctDesignCalculationError("Could not bracket the Colebrook friction-factor root.")
    for _ in range(64):
        middle = (low + high) / 2.0
        f_middle = residual(middle)
        if abs(f_middle) < 1e-12:
            return middle
        if f_low * f_middle > 0:
            low, f_low = middle, f_middle
        else:
            high, f_high = middle, f_middle
    return (low + high) / 2.0


def _friction_factor(reynolds: float, diameter_m: float, roughness_m: float) -> tuple[float, bool]:
    if reynolds < 2300.0:
        return 64.0 / reynolds, False
    if reynolds >= 4000.0:
        return colebrook_darcy_factor(reynolds, diameter_m, roughness_m), False
    # Explicit linear bridge through the transitional region; flagged for review.
    low = 64.0 / 2300.0
    high = colebrook_darcy_factor(4000.0, diameter_m, roughness_m)
    fraction = (reynolds - 2300.0) / (4000.0 - 2300.0)
    return low + fraction * (high - low), True


def evaluate_rectangular_duct(
    segment: DuctSegmentInput,
    width_in: float,
    height_in: float,
    options: DuctDesignOptions,
) -> DuctSizingResult:
    """Evaluate one specified rectangular section using equivalent-round friction."""
    _finite_positive("duct width", width_in)
    _finite_positive("duct height", height_in)
    _finite_positive("airflow", segment.airflow_cfm)
    _finite_positive("duct length", segment.length_ft)
    _finite_positive("fitting K", segment.fitting_k, allow_zero=True)

    width_m, height_m = width_in * IN_TO_M, height_in * IN_TO_M
    area_m2 = width_m * height_m
    flow_m3_s = segment.airflow_cfm * CFM_TO_M3_S
    velocity_m_s = flow_m3_s / area_m2
    eq_d_m = rectangular_equivalent_diameter_m(width_m, height_m)
    hydraulic_d_m = rectangular_hydraulic_diameter_m(width_m, height_m)
    eq_area_m2 = math.pi * eq_d_m**2 / 4.0
    eq_velocity_m_s = flow_m3_s / eq_area_m2
    density, viscosity = air_properties(options.air_temperature_c, options.absolute_pressure_kpa)
    reynolds = density * eq_velocity_m_s * eq_d_m / viscosity
    friction_factor, transitional = _friction_factor(
        reynolds, eq_d_m, options.roughness_mm / 1000.0
    )

    dynamic_rect_pa = 0.5 * density * velocity_m_s**2
    dynamic_eq_pa = 0.5 * density * eq_velocity_m_s**2
    length_m = segment.length_ft * FT_TO_M
    straight_loss_pa = friction_factor * (length_m / eq_d_m) * dynamic_eq_pa
    fitting_loss_pa = segment.fitting_k * dynamic_rect_pa
    gradient_pa_m = straight_loss_pa / length_m
    friction_rate = gradient_pa_m * PA_PER_100FT_TO_IN_WG
    velocity_pressure = dynamic_rect_pa / PA_PER_IN_WG
    warnings = (
        ("Equivalent-round Darcy friction estimate; compare against project-approved duct tables.",)
        if transitional
        else ()
    )
    return DuctSizingResult(
        tag=segment.tag,
        airflow_cfm=segment.airflow_cfm,
        length_ft=segment.length_ft,
        width_in=float(width_in),
        height_in=float(height_in),
        area_sq_in=width_in * height_in,
        velocity_fpm=velocity_m_s * 196.850394,
        equivalent_diameter_in=eq_d_m / IN_TO_M,
        hydraulic_diameter_in=hydraulic_d_m / IN_TO_M,
        reynolds_number=reynolds,
        darcy_friction_factor=friction_factor,
        friction_rate_inwg_100ft=friction_rate,
        straight_friction_loss_inwg=straight_loss_pa / PA_PER_IN_WG,
        fitting_loss_inwg=fitting_loss_pa / PA_PER_IN_WG,
        total_modeled_loss_inwg=(straight_loss_pa + fitting_loss_pa) / PA_PER_IN_WG,
        velocity_pressure_inwg=velocity_pressure,
        warnings=warnings,
    )


def size_rect(segment: DuctSegmentInput, options: DuctDesignOptions) -> DuctSizingResult:
    """Choose the smallest-area grid size meeting both velocity and friction limits."""
    validate_design_inputs((segment,), options)
    step = options.size_step_in
    width_start = math.ceil(options.min_width_in / step - 1e-10)
    width_end = math.floor(options.max_width_in / step + 1e-10)
    height_start = math.ceil(options.min_height_in / step - 1e-10)
    height_end = math.floor(options.max_height_in / step + 1e-10)
    candidates: list[DuctSizingResult] = []
    for height_index in range(height_start, height_end + 1):
        height = height_index * step
        for width_index in range(width_start, width_end + 1):
            width = width_index * step
            aspect = max(width, height) / min(width, height)
            if aspect > options.max_aspect_ratio:
                continue
            result = evaluate_rectangular_duct(segment, width, height, options)
            if (
                result.velocity_fpm <= options.max_velocity_fpm
                and result.friction_rate_inwg_100ft <= options.max_friction_inwg_100ft
            ):
                candidates.append(result)
    if not candidates:
        raise DuctDesignCalculationError(
            f"No size within {options.min_width_in:g}–{options.max_width_in:g} in width and "
            f"{options.min_height_in:g}–{options.max_height_in:g} in height meets both limits. "
            "Expand the size range or relax a design criterion."
        )
    return min(
        candidates,
        key=lambda item: (
            item.area_sq_in,
            abs(math.log(item.width_in / item.height_in)),
            item.total_modeled_loss_inwg,
        ),
    )


def run_design(
    segments: Iterable[DuctSegmentInput], options: DuctDesignOptions
) -> tuple[DuctSizingResult, ...]:
    """Size independent constant-flow segments; does not solve a connected network."""
    rows = validate_design_inputs(segments, options)
    return tuple(size_rect(segment, options) for segment in rows)
