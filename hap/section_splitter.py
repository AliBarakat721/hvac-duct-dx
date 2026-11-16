"""Split normalized HAP text into named, line-aware sections."""
from __future__ import annotations

import re

from .models import HAPSection

_PATTERNS = (
    ("air_system_information", re.compile(r"^\s*Air\s+System\s+Information\s*$", re.I)),
    ("cooling_sizing", re.compile(r"^\s*Central\s+Cooling\s+Coil\s+Sizing\s+Data\s*$", re.I)),
    ("heating_sizing", re.compile(r"^\s*(?:Central\s+)?Heating\s+Coil\s+Sizing\s+Data\s*$", re.I)),
    ("fan_sizing", re.compile(r"^\s*Supply\s+Fan\s+Sizing\s+Data\s*$", re.I)),
    ("ventilation", re.compile(r"^\s*Outdoor\s+Ventilation\s+Air\s+Data\s*$", re.I)),
    ("zone_sizing", re.compile(r"^\s*Zone\s+Sizing\s+Data\s*$", re.I)),
    ("space_loads", re.compile(r"^\s*Space\s+Loads\s+and\s+Airflows\s*$", re.I)),
)


class SectionSplitter:
    def split(self, text: str) -> dict[str, HAPSection]:
        lines = text.splitlines()
        found: list[tuple[int, str, str]] = []
        consumed: set[int] = set()
        for index, line in enumerate(lines):
            if index in consumed:
                continue
            candidates = [(line, index)]
            if line.strip() and index + 1 < len(lines) and lines[index + 1].strip():
                candidates.append((line + " " + lines[index + 1], index + 1))
            for name, pattern in _PATTERNS:
                match = next(((candidate, next_index) for candidate, next_index in candidates if pattern.match(candidate)), None)
                if match is not None:
                    title, next_index = match
                    found.append((index, name, title.strip()))
                    if next_index != index:
                        consumed.add(next_index)
                    break
        sections: dict[str, HAPSection] = {}
        counts: dict[str, int] = {}
        for position, (start, base_name, title) in enumerate(found):
            counts[base_name] = counts.get(base_name, 0) + 1
            key = base_name if counts[base_name] == 1 else f"{base_name}#{counts[base_name]}"
            end = found[position + 1][0] if position + 1 < len(found) else len(lines)
            sections[key] = HAPSection(
                name=key,
                title=title,
                start_line=start + 1,
                end_line=end,
                text="\n".join(lines[start:end]),
            )
        return sections
