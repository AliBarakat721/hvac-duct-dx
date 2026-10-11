"""Robust text parsing and engineering validation for HAP reports."""

from __future__ import annotations

import logging
import re

LOGGER = logging.getLogger(__name__)

_NUMBER = re.compile(r"(?<![A-Za-z])([+-]?[0-9Oo]+(?:[,.][0-9Oo]{1,3})?)")
_SYSTEM = re.compile(
    r"Air\s+System\s+Information[\s\S]{0,500}?"
    r"Air\s+System\s+Name\s*:?\s*[.\s_-]*"
    r"([A-Za-z0-9][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)


def _number(text: str) -> tuple[float, bool]:
    """Normalize OCR digits and common decimal/thousands separators."""
    token = text.replace("O", "0").replace("o", "0").replace("'", "")
    if "," in token and "." in token:
        token = (
            token.replace(",", "")
            if token.rfind(".") > token.rfind(",")
            else token.replace(".", "").replace(",", ".")
        )
    elif "," in token:
        before, after = token.rsplit(",", 1)
        token = before + after if len(after) == 3 else before + "." + after
    elif token.count(".") > 1:
        token = token.replace(".", "")
    return float(token), any(char in text for char in "Oo")


def _candidates(text: str, unit_pattern: str):
    """Collect positive numeric values associated with a recognized unit."""
    rows = text.splitlines()
    unit_re = re.compile(unit_pattern, re.IGNORECASE)
    found = []
    for line_index, line in enumerate(rows):
        for unit_match in unit_re.finditer(line):
            before = line[: unit_match.start()]
            numbers = list(_NUMBER.finditer(before))
            if numbers:
                match = numbers[-1]
                try:
                    value, ocr_digit = _number(match.group(1))
                except ValueError:
                    continue
                if value > 0:
                    found.append((value, line_index, match.start(), ocr_digit, line))
            else:
                for next_index in range(line_index + 1, min(line_index + 3, len(rows))):
                    match = _NUMBER.search(rows[next_index])
                    if match:
                        try:
                            value, ocr_digit = _number(match.group(1))
                        except ValueError:
                            break
                        if value > 0:
                            found.append(
                                (
                                    value,
                                    next_index,
                                    match.start(),
                                    ocr_digit,
                                    rows[next_index],
                                )
                            )
                        break
    return rows, found


def _best(
    text: str,
    unit_pattern: str,
    labels: str,
    *,
    factor: float = 1.0,
    excluded: str = r"$^",
    bounds=(0.0, float("inf")),
):
    rows, candidates = _candidates(text, unit_pattern)
    label_re = re.compile(labels, re.IGNORECASE)
    excluded_re = re.compile(excluded, re.IGNORECASE)
    anchors = [
        (index, match.start(), line)
        for index, line in enumerate(rows)
        for match in label_re.finditer(line)
    ]
    scored = []
    for value, index, position, ocr_digit, source in candidates:
        if excluded_re.search(source):
            continue
        if anchors:
            distance, anchor_line, _anchor_position, anchor_text = min(
                (
                    abs(index - line) * 40 + abs(position - pos) // 30,
                    line,
                    pos,
                    line_text,
                )
                for line, pos, line_text in anchors
            )
            score = 100 - distance
            if anchor_line == index:
                score += 25
            elif index > anchor_line:
                score += 1
            if ocr_digit:
                score -= 8
            if excluded_re.search(anchor_text):
                score -= 80
        else:
            score = -8 if ocr_digit else 0
        converted = value * factor
        if not bounds[0] <= converted <= bounds[1]:
            score -= 60
        scored.append((score, converted, source, value, ocr_digit))
    if not scored:
        return None, rows
    score, value, source, raw_value, ocr_digit = max(scored, key=lambda item: item[0])
    return {
        "value": value,
        "score": score,
        "source": source,
        "raw_value": raw_value,
        "ocr_corrected": ocr_digit,
    }, rows


def parse_hap_detailed(text: str) -> dict:
    """Parse HAP text and return values plus engineering validation metadata.

    Values are in Btu/hr, CFM, and in.wg. ``parse_hap`` preserves the historic
    five-item tuple consumed by the desktop UI and external scripts.
    """
    text = "" if text is None else str(text)
    warnings = []
    critical_errors = []
    total_mbh, _ = _best(
        text,
        r"\bMBH\b|\bkW\b",
        r"Total\s+(?:coil\s+)?(?:load|capacity)|Cooling\s+load",
        excluded=r"Sensible|Heating|Htg",
        bounds=(1, 10000),
    )
    total_tons, _ = _best(
        text,
        r"\btons?\b",
        r"Total\s+(?:coil\s+)?(?:load|capacity)|Cooling\s+load",
        excluded=r"Sensible|Heating|Htg",
        bounds=(0.1, 100),
    )
    if total_mbh and re.search(r"\bkW\b", total_mbh["source"], re.IGNORECASE):
        total_mbh["value"] *= 3.412142
    mbh_value = total_mbh["value"] if total_mbh else 0.0
    tons_value = total_tons["value"] if total_tons else 0.0
    total_from_tons = False
    if mbh_value and tons_value:
        tons_mbh = tons_value * 12.0
        discrepancy = abs(mbh_value - tons_mbh) / max(mbh_value, tons_mbh)
        if discrepancy > 0.15:
            total_from_tons = total_tons["score"] > total_mbh["score"]
            selected = (
                f"{tons_value:.2f} tons" if total_from_tons else f"{mbh_value:.1f} MBH"
            )
            warnings.append(
                f"Cooling capacity anomaly: {mbh_value:.1f} MBH conflicts with "
                f"{tons_value:.2f} tons ({tons_mbh:.1f} MBH); used {selected} "
                "based on candidate confidence."
            )
    total_final = (
        tons_value * 12.0 if total_from_tons else mbh_value
    ) or tons_value * 12.0

    sensible, _ = _best(
        text,
        r"\bMBH\b|\bkW\b|\btons?\b|\bBtu\s*/?\s*h(?:r)?\b",
        r"Sensible\s+(?:coil\s+)?(?:load|capacity)",
        excluded=r"Total|Heating|Htg",
        bounds=(0.1, 10000),
    )
    sensible_value = sensible["value"] if sensible else 0.0
    if sensible and re.search(r"\bkW\b", sensible["source"], re.IGNORECASE):
        sensible_value *= 3.412142
    elif sensible and re.search(r"\btons?\b", sensible["source"], re.IGNORECASE):
        sensible_value *= 12.0
    elif sensible and re.search(r"\bBtu\s*/?\s*h(?:r)?\b", sensible["source"], re.IGNORECASE):
        sensible_value /= 1000.0

    shr_values = []
    for line in text.splitlines():
        if re.search(r"Sensible\s+heat\s+ratio|\bSHR\b", line, re.IGNORECASE):
            for match in _NUMBER.finditer(line):
                try:
                    value, _ = _number(match.group(1))
                except ValueError:
                    continue
                if 0 < value <= 1:
                    shr_values.append(value)
    shr = shr_values[0] if shr_values else None
    if shr is not None and not 0.60 <= shr <= 0.98:
        warnings.append(f"SHR {shr:.3f} is outside the expected 0.60-0.98 range.")
        shr = None
    if sensible_value > total_final:
        if shr is not None:
            warnings.append(
                f"Sensible load exceeded total; derived sensible from total x SHR ({shr:.3f})."
            )
            sensible_value = total_final * shr
        else:
            critical_errors.append(
                "Sensible load exceeded total and no valid SHR was available; sensible set to zero."
            )
            sensible_value = 0.0

    cfm, _ = _best(
        text,
        r"\bCFM\b|\bCPM\b|\bL\s*/?\s*s\b",
        r"Coil\s+CFM|Actual\s+max|Air\s*flow|Airflow|Supply\s+air",
        excluded=r"CFM\s*/\s*(?:sq\.?\s*ft|ft2)|/\s*area",
        bounds=(20, 100000),
    )
    cfm_value = cfm["value"] if cfm else 0.0
    if cfm and re.search(r"\bL\s*/?\s*s\b", cfm["source"], re.IGNORECASE):
        cfm_value *= 2.11888

    heating, _ = _best(
        text,
        r"\bMBH\b|\bkW\b|\btons?\b|\bBtu\s*/?\s*h(?:r)?\b",
        r"Heating\s+(?:coil\s+)?(?:load|capacity)|Htg\s+load|Max\s+coil\s+load",
        excluded=r"Sensible|Cooling|Total",
        bounds=(0.1, 10000),
    )
    heating_value = heating["value"] if heating else 0.0
    if heating and re.search(r"\bkW\b", heating["source"], re.IGNORECASE):
        heating_value *= 3.412142
    elif heating and re.search(r"\btons?\b", heating["source"], re.IGNORECASE):
        heating_value *= 12
    elif heating and re.search(r"\bBtu\s*/?\s*h(?:r)?\b", heating["source"], re.IGNORECASE):
        heating_value /= 1000

    esp, _ = _best(
        text,
        r"\bin\.?\s*wg\b|\binches?\s+water\b|\bPa\b",
        r"Fan\s+static|ESP|External\s+static|Static\s+pressure",
        excluded=r"CFM\s*/\s*(?:sq\.?\s*ft|ft2)",
        bounds=(0.05, 2.0),
    )
    if esp is None:
        rows = text.splitlines()
        for index, line in enumerate(rows):
            if not re.search(
                r"Fan\s+static|ESP|External\s+static|Static\s+pressure", line, re.IGNORECASE
            ):
                continue
            if re.search(r"CFM\s*/\s*(?:sq\.?\s*ft|ft2)", line, re.IGNORECASE):
                continue
            for candidate_line in rows[index : index + 3]:
                if re.search(r"CFM\s*/\s*(?:sq\.?\s*ft|ft2)", candidate_line, re.IGNORECASE):
                    continue
                match = _NUMBER.search(candidate_line)
                if not match:
                    continue
                try:
                    value, corrected = _number(match.group(1))
                except ValueError:
                    continue
                if 0.05 <= value <= 2.0:
                    esp = {
                        "value": value,
                        "score": 100,
                        "source": candidate_line,
                        "raw_value": value,
                        "ocr_corrected": corrected,
                    }
                    break
            if esp:
                break
    esp_value = esp["value"] if esp else None
    if esp and re.search(r"\bPa\b", esp["source"], re.IGNORECASE):
        esp_value /= 249.0889
    if esp_value is not None and not 0.05 <= esp_value <= 2.0:
        warnings.append(
            f"Fan static pressure {esp_value:.3f} in.wg is outside 0.05-2.0."
        )
        esp_value = None

    if total_final > 0 and cfm_value > 0:
        cfm_per_ton = cfm_value / (total_final / 12.0)
        if not 250 <= cfm_per_ton <= 600:
            warnings.append(
                f"Airflow sanity warning: {cfm_per_ton:.0f} CFM/ton is outside 250-600."
            )

    for name, candidate in (
        ("total_mbh", total_mbh),
        ("total_tons", total_tons),
        ("sensible_mbh", sensible),
        ("cfm", cfm),
        ("heating_mbh", heating),
        ("esp", esp),
    ):
        if candidate and candidate["ocr_corrected"]:
            warnings.append(f"OCR digit correction applied to {name} candidate.")
    result = {
        "total_btu": total_final * 1000.0,
        "sensible_btu": sensible_value * 1000.0,
        "cfm": round(cfm_value),
        "heating_btu": heating_value * 1000.0,
        "esp": esp_value,
        "shr": shr,
        "warnings": warnings,
        "critical_errors": critical_errors,
        "candidates": {
            key: value
            for key, value in {
                "total_mbh": total_mbh,
                "total_tons": total_tons,
                "sensible_mbh": sensible,
                "cfm": cfm,
                "heating_mbh": heating,
                "esp": esp,
            }.items()
            if value
        },
    }
    for warning in warnings:
        LOGGER.warning("HAP validation: %s", warning)
    for error in critical_errors:
        LOGGER.error("HAP validation: %s", error)
    return result


def parse_hap(text: str):
    """Return ``(total, sensible, cfm, heating, esp)`` as legacy API."""
    result = parse_hap_detailed(text)
    return (
        result["total_btu"],
        result["sensible_btu"],
        result["cfm"],
        result["heating_btu"],
        result["esp"],
    )


def parse_all_systems(text: str) -> list[dict]:
    """Split a multi-system report while preserving the legacy dict contract."""
    text = "" if text is None else str(text)
    matches = list(_SYSTEM.finditer(text))
    systems = []
    seen = set()
    for index, match in enumerate(matches):
        name = match.group(1).strip(" ._-:")
        if not name or name in seen:
            continue
        seen.add(name)
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[max(start, 0) : end]
        result = parse_hap_detailed(chunk)
        area = None
        area_rows = chunk.splitlines()
        for area_index, line in enumerate(area_rows):
            label = re.search(r"(?:Floor|Room)\s+Area", line, re.IGNORECASE)
            if not label:
                continue
            value_match = _NUMBER.search(line, label.end())
            value_row = area_index
            if not value_match:
                for next_index in range(area_index + 1, min(area_index + 3, len(area_rows))):
                    value_match = _NUMBER.search(area_rows[next_index])
                    if value_match:
                        value_row = next_index
                        break
            if not value_match:
                continue
            try:
                area_value, _ = _number(value_match.group(1))
            except ValueError:
                continue
            unit_text = " ".join(
                area_rows[area_index:min(area_index + 3, len(area_rows))]
            )
            area = {
                "value": area_value,
                "source": unit_text,
                "row": value_row,
            }
            break
        floor_area = area["value"] if area else 0.0
        if area and re.search(r"\bm(?:2|²)\b", area["source"], re.IGNORECASE):
            floor_area *= 10.7639
        systems.append(
            {
                "name": name,
                "total_btu": result["total_btu"],
                "sensible_btu": result["sensible_btu"],
                "cfm": result["cfm"],
                "heating_btu": result["heating_btu"],
                "esp": result["esp"],
                "status": "Pending",
                "model": "",
                "floor_area_ft2": floor_area,
                "warnings": result["warnings"],
                "critical_errors": result["critical_errors"],
            }
        )
    return systems
