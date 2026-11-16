"""Normalized, traceable records for the diffuser tables currently used by HVAC Duct DX.

The legacy arrays in :mod:`catalog_data` remain the calculation source during
this audit. This module converts those arrays into point records with explicit
geometry, throw direction, units, source pages, acoustic basis, and verification
status. It deliberately does not treat absent vendor values as measured data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import catalog_data as _data


@dataclass(frozen=True)
class DiffuserOperatingPoint:
    """One vendor-table operating point for a particular diffuser direction."""

    model_id: str
    family: str
    face_shape: str
    face_size: str
    neck_shape: str
    neck_size: str
    throw_direction: str
    airflow_cfm: float
    calculator_airflow_cfm: float | None
    neck_velocity_fpm: float
    total_pressure_inwg: float
    throw_min_ft: float | None
    throw_max_ft: float | None
    nc_label: str
    nc_numeric: float | None
    sound_power_lwa_db_a: float | None
    acoustic_basis: str
    printed_page: int
    verification_status: str
    notes: str
    directional_flow_cfm_each: float | None = None
    catalog_ak_raw_ft2: float | None = None
    calculator_ak_ft2: float | None = None


@dataclass(frozen=True)
class CatalogValidationIssue:
    """A concrete integrity problem found while validating a vendor table."""

    model_id: str
    field: str
    message: str
    severity: str = "ERROR"


@dataclass(frozen=True)
class CircularSelectionGuideRow:
    """Metric CCD sizing-guide row; kept distinct from the p.6 CFM table."""

    model_id: str
    size_in: int
    max_flow_m3h: float
    min_flow_m3h: float
    effective_area_m2: float
    c_mm: float
    c1_mm: float
    c2_mm: float
    lwa_max_db_a_label: str
    lwa_min_db_a_label: str
    lwnc_max_label: str
    lwnc_min_label: str
    printed_page: int
    verification_status: str
    notes: str


@dataclass(frozen=True)
class CeilingSelectionLimit:
    """Catalog page 5's ceiling-height airflow and supply-ΔT guide."""

    ceiling_height_ft: float
    max_airflow_cfm_per_diffuser: float
    max_supply_delta_t_f: float
    printed_page: int
    verification_status: str
    notes: str


# Page 11's 4SCD table explicitly lists airflow per outlet side. These are
# transcribed values; total-vs-side flow has small catalog rounding differences.
_4SCD_SIDE_CFM = {
    6: (13, 19, 25, 32, 38, 44, 50, 57),
    9: (28, 42, 56, 70, 84, 98, 112, 126),
    12: (50, 75, 100, 125, 150, 175, 200, 225),
    15: (78, 117, 156, 195, 234, 273, 312, 351),
    18: (113, 169, 225, 281, 338, 394, 450, 507),
    21: (153, 229, 306, 382, 459, 535, 612, 689),
    24: (200, 300, 400, 500, 600, 700, 800, 900),
}

# Total-flow entries as printed in the 4SCD and 3SCD tables (pp.11 and 14).
# They are not replaced with geometric-area × velocity values; the difference
# is retained as an audit signal because the table's AK definition is unclear.
_SQUARE_VENDOR_CFM = {
    6: (50, 75, 100, 125, 150, 175, 200, 225),
    9: (112, 168, 224, 280, 336, 392, 448, 504),
    12: (200, 300, 400, 500, 600, 700, 800, 900),
    15: (312, 468, 624, 780, 936, 1092, 1248, 1404),
    18: (450, 675, 900, 1125, 1350, 1575, 1800, 2025),
    21: (612, 918, 1224, 1530, 1836, 2142, 2448, 2754),
    24: (800, 1200, 1600, 2000, 2400, 2800, 3200, 3600),
}

# AK values from the printed 4SCD table. The 18x18 entry visibly reads 0.13;
# that anomalous raw value is preserved rather than silently changed to 1.13.
_SQUARE_AK_RAW_FT2 = {6: 0.13, 9: 0.28, 12: 0.50, 15: 0.79,
                      18: 0.13, 21: 1.54, 24: 2.00}

_THROW_NOTE = (
    "Vendor throw endpoint/terminal-velocity definitions are inconsistent; "
    "range endpoints are retained as printed and are not asserted to be T100/T50."
)
_SQUARE_ACOUSTIC_NOTE = (
    "NC is represented only by the vendor's colour band; its legend references "
    "room attenuation, so this is not a room NC calculation."
)


def _nc_value_label(value: float) -> tuple[str, float | None]:
    if value < 20:
        return "<20", None
    return f"{value:g}", float(value)


def operating_points() -> tuple[DiffuserOperatingPoint, ...]:
    """Return normalized points for supply CCD, return CCD, 4SCD, and 3SCD.

    Only diffuser families currently represented by the calculator are included.
    Rectangular 1/2/3/4-way and round-neck 4CCD tables remain unsupported and
    are documented separately rather than silently being mapped to square models.
    """
    records: list[DiffuserOperatingPoint] = []

    # Circular supply table: printed page 6.
    for size, row in sorted(_data.CCD.items()):
        for index, velocity in enumerate(_data.VC):
            nc_label, nc_numeric = _nc_value_label(row["nc"][index])
            throw_min, throw_max = row["th"][index]
            records.append(DiffuserOperatingPoint(
                model_id=f"CCD-{size}", family="CCD-supply",
                face_shape="circular", face_size=f'{size} in diameter',
                neck_shape="circular", neck_size=f'{size} in diameter',
                throw_direction="radial-horizontal", airflow_cfm=float(row["cfm"][index]),
                calculator_airflow_cfm=float(row["cfm"][index]),
                neck_velocity_fpm=float(velocity),
                total_pressure_inwg=float(row["pt"][index]),
                throw_min_ft=float(throw_min), throw_max_ft=float(throw_max),
                nc_label=nc_label, nc_numeric=nc_numeric,
                sound_power_lwa_db_a=None,
                acoustic_basis="Catalog NC column; room attenuation basis unresolved",
                printed_page=6, verification_status="transcribed_needs_vendor_confirmation",
                notes=_THROW_NOTE,
            ))

    # Circular return table: printed page 7; the table contains no throw values.
    for size, row in sorted(_data.RETURN_CCD.items()):
        for index, velocity in enumerate(_data.RETURN_VELOCITY_FPM):
            nc_label, nc_numeric = _nc_value_label(row["nc"][index])
            records.append(DiffuserOperatingPoint(
                model_id=f"RETURN-CCD-{size}", family="CCD-return",
                face_shape="circular", face_size=f'{size} in diameter',
                neck_shape="circular", neck_size=f'{size} in diameter',
                throw_direction="return-no-throw-data",
                airflow_cfm=float(_data.CCD[size]["cfm"][index]),
                calculator_airflow_cfm=float(_data.CCD[size]["cfm"][index]),
                neck_velocity_fpm=float(velocity),
                total_pressure_inwg=float(row["pt"][index]),
                throw_min_ft=None, throw_max_ft=None,
                nc_label=nc_label, nc_numeric=nc_numeric,
                sound_power_lwa_db_a=None,
                acoustic_basis="Catalog NC column; room attenuation basis unresolved",
                printed_page=7, verification_status="transcribed_needs_vendor_confirmation",
                notes="Return table does not provide throw. Airflow is calculated from tabulated neck area and velocity.",
            ))

    # Square/rectangular ceiling-diffuser performance tables use shared velocity
    # and pressure columns, but only square 4-way and 3-way data are in the app.
    for size, row in sorted(_data.SQUARE.items()):
        for index, velocity in enumerate(_data.VEL8):
            total_cfm = float(_SQUARE_VENDOR_CFM[size][index])
            calculator_cfm = float(row["cfm"][index])
            for family, throw_key, band_key, page in (
                ("4SCD", "th4", "band4", 11),
                ("3SCD", "tha", "band3", 14),
            ):
                throws = row[throw_key][index]
                if family == "4SCD":
                    directions = (("each-of-4-sides", throws, 4),)
                else:
                    directions = (
                        ("a", row["tha"][index], 2),
                        ("b", row["thb"][index], 1),
                    )
                for direction, direction_throw, side_count in directions:
                    side_flow = (
                        float(_4SCD_SIDE_CFM[size][index])
                        if family == "4SCD" and size in _4SCD_SIDE_CFM
                        else None
                    )
                    band = int(row[band_key][index])
                    nc_label = ("≤20", "≤30", "≤40", ">40")[band]
                    note = _SQUARE_ACOUSTIC_NOTE + " " + _THROW_NOTE
                    if side_flow is None and family == "4SCD":
                        note += " Per-side airflow was not transcribed for this size."
                    if family == "3SCD":
                        note += " Directional airflow by side is not available in the current data model."
                    if family == "4SCD" and side_flow is not None:
                        delta = abs(side_flow * side_count - total_cfm)
                        if delta > 5:
                            note += f" Vendor total/per-side flow differs by {delta:g} CFM; review source table."
                    records.append(DiffuserOperatingPoint(
                        model_id=f"{family}-{size}", family=family,
                        face_shape="square", face_size=f'{size} x {size} in',
                        neck_shape="square", neck_size=f'{size} x {size} in',
                        throw_direction=direction,
                        airflow_cfm=total_cfm,
                        calculator_airflow_cfm=calculator_cfm,
                        neck_velocity_fpm=float(velocity),
                        total_pressure_inwg=float(_data.PT8[index]),
                        throw_min_ft=float(direction_throw[0]),
                        throw_max_ft=float(direction_throw[1]),
                        nc_label=nc_label, nc_numeric=None,
                        sound_power_lwa_db_a=None,
                        acoustic_basis="Vendor colour-coded NC band; not a numeric sound-power value",
                        printed_page=page,
                        verification_status=(
                            "transcribed_needs_vendor_confirmation"
                            if family == "4SCD" else "transcribed_directional_basis_incomplete"
                        ),
                        notes=(note + (
                            f" Legacy calculator airflow differs from printed total by "
                            f"{abs(calculator_cfm - total_cfm):g} CFM; vendor table value is retained here."
                            if calculator_cfm != total_cfm else "")),
                        directional_flow_cfm_each=side_flow,
                        catalog_ak_raw_ft2=(
                            _SQUARE_AK_RAW_FT2[size] if family == "4SCD" else None
                        ),
                        calculator_ak_ft2=(
                            float(row["AK"])
                            if family == "4SCD" and row.get("AK") is not None else None
                        ),
                    ))

    return tuple(records)


def circular_selection_guide() -> tuple[CircularSelectionGuideRow, ...]:
    """Return the separate SI sizing guide printed on page 8 of diffuser.pdf."""
    raw_rows = (
        (6, 290, 70, .009, 146, 234, 298, "40", "<20", "31", "<20"),
        (8, 430, 110, .016, 197, 285, 349, "40", "<20", "33", "<20"),
        (10, 650, 180, .026, 248, 336, 400, "40", "<20", "34", "<20"),
        (12, 830, 290, .038, 299, 387, 451, "40", "<20", "35", "<20"),
        (14, 1080, 395, .054, 349, 437, 501, "40", "<20", "35", "<20"),
        (16, 1295, 505, .073, 400, 488, 552, "40", "<20", "36", "<20"),
        (18, 1585, 650, .096, 451, 539, 603, "40", "<20", "37", "<20"),
        (20, 1800, 790, .115, 502, 590, 654, "40", "<20", "37", "<20"),
    )
    return tuple(CircularSelectionGuideRow(
        model_id=f"CCD-{size}-SI-guide", size_in=size,
        max_flow_m3h=float(max_flow), min_flow_m3h=float(min_flow),
        effective_area_m2=float(effective_area), c_mm=float(c),
        c1_mm=float(c1), c2_mm=float(c2),
        lwa_max_db_a_label=lwa_max, lwa_min_db_a_label=lwa_min,
        lwnc_max_label=lwnc_max, lwnc_min_label=lwnc_min,
        printed_page=8, verification_status="transcribed_needs_vendor_confirmation",
        notes=("Separate SI sizing guide: maximum flow assumes LwA 40 dB(A); "
               "minimum flow guidance requires effective discharge velocity >=2 m/s. "
               "Do not merge with the page 6 performance table without reconciliation."),
    ) for (size, max_flow, min_flow, effective_area, c, c1, c2,
           lwa_max, lwa_min, lwnc_max, lwnc_min) in raw_rows)


def ceiling_selection_limits() -> tuple[CeilingSelectionLimit, ...]:
    """Return page 5 limits as rows; interpolation is a separate rule."""
    return tuple(CeilingSelectionLimit(
        ceiling_height_ft=float(height),
        max_airflow_cfm_per_diffuser=float(values[0]),
        max_supply_delta_t_f=float(values[1]),
        printed_page=5,
        verification_status="transcribed_needs_vendor_confirmation",
        notes="Catalog table; implementation interpolates between listed heights.",
    ) for height, values in sorted(_data.CEILING_TABLE.items()))


def validate_operating_points(
    records: Iterable[DiffuserOperatingPoint] | None = None,
) -> tuple[CatalogValidationIssue, ...]:
    """Validate ordering, aligned point data, and declared 4SCD flow balance.

    The flow-balance check permits 5 CFM total error for source-table rounding;
    it never fabricates directional flow for families where the source data are
    absent. Throw ranges are checked for ordering but not interpreted as T100/T50.
    """
    points = tuple(operating_points() if records is None else records)
    issues: list[CatalogValidationIssue] = []
    groups: dict[tuple[str, str], list[DiffuserOperatingPoint]] = {}
    ids: set[tuple[str, str, float]] = set()
    for point in points:
        key = (point.model_id, point.throw_direction)
        groups.setdefault(key, []).append(point)
        point_id = (*key, point.neck_velocity_fpm)
        if point_id in ids:
            issues.append(CatalogValidationIssue(point.model_id, "velocity", "duplicate operating point"))
        ids.add(point_id)
        if point.airflow_cfm <= 0:
            issues.append(CatalogValidationIssue(point.model_id, "airflow_cfm", "must be positive"))
        if point.neck_velocity_fpm <= 0:
            issues.append(CatalogValidationIssue(point.model_id, "neck_velocity_fpm", "must be positive"))
        if point.total_pressure_inwg < 0:
            issues.append(CatalogValidationIssue(point.model_id, "total_pressure_inwg", "must be non-negative"))
        if point.throw_min_ft is not None and point.throw_max_ft is not None:
            if point.throw_min_ft < 0 or point.throw_max_ft < point.throw_min_ft:
                issues.append(CatalogValidationIssue(point.model_id, "throw", "invalid throw range"))

    for (model_id, _direction), group in groups.items():
        ordered = sorted(group, key=lambda item: item.neck_velocity_fpm)
        velocities = [item.neck_velocity_fpm for item in ordered]
        if any(right <= left for left, right in zip(velocities, velocities[1:])):
            issues.append(CatalogValidationIssue(model_id, "neck_velocity_fpm", "must be strictly increasing and unique"))
        flows = [item.airflow_cfm for item in ordered]
        if any(right <= left for left, right in zip(flows, flows[1:])):
            issues.append(CatalogValidationIssue(model_id, "airflow_cfm", "must be strictly increasing"))
        pressures = [item.total_pressure_inwg for item in ordered]
        if any(right < left for left, right in zip(pressures, pressures[1:])):
            issues.append(CatalogValidationIssue(model_id, "total_pressure_inwg", "decreases as velocity increases"))

    for point in points:
        if (point.catalog_ak_raw_ft2 is not None
                and point.calculator_ak_ft2 is not None
                and point.catalog_ak_raw_ft2 != point.calculator_ak_ft2):
            issues.append(CatalogValidationIssue(
                point.model_id, "AK",
                f"printed AK {point.catalog_ak_raw_ft2:g} ft² differs from "
                f"legacy metadata {point.calculator_ak_ft2:g} ft²",
                severity="WARNING",
            ))
        if (point.calculator_airflow_cfm is not None
                and point.calculator_airflow_cfm != point.airflow_cfm):
            issues.append(CatalogValidationIssue(
                point.model_id, "calculator_airflow_cfm",
                f"legacy calculator value {point.calculator_airflow_cfm:g} CFM differs from "
                f"printed table value {point.airflow_cfm:g} CFM",
                severity="WARNING",
            ))
        if point.directional_flow_cfm_each is None:
            continue
        if point.family != "4SCD":
            issues.append(CatalogValidationIssue(point.model_id, "directional_flow_cfm_each", "unexpected directional flow for non-4SCD record"))
            continue
        difference = abs(4 * point.directional_flow_cfm_each - point.airflow_cfm)
        if 0 < difference <= 5:
            issues.append(CatalogValidationIssue(
                point.model_id, "directional_flow_cfm_each",
                f"rounded side totals differ from printed total by {difference:g} CFM (within 5 CFM audit tolerance)",
                severity="WARNING",
            ))
        if difference > 5:
            issues.append(CatalogValidationIssue(
                point.model_id, "directional_flow_cfm_each",
                f"four sides sum to {4 * point.directional_flow_cfm_each:g} CFM, "
                f"not total {point.airflow_cfm:g} CFM (difference {difference:g} CFM; tolerance 5 CFM)",
            ))
    return tuple(dict.fromkeys(issues))


def cfm_balance_issue(
    total_cfm: float,
    directional_flows_cfm: Iterable[float],
    *,
    tolerance_cfm: float = 5.0,
) -> str | None:
    """Return a diagnostic when available directional flow does not reconcile."""
    flows = tuple(float(value) for value in directional_flows_cfm)
    if total_cfm <= 0 or not flows or any(value < 0 for value in flows):
        return "total and directional flows must be positive/non-negative values"
    delta = abs(sum(flows) - total_cfm)
    if delta > tolerance_cfm:
        return f"directional flow sum differs from total by {delta:g} CFM"
    return None
