from __future__ import annotations

import sqlite3
import inspect
from pathlib import Path

from openpyxl import load_workbook

import build_db

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "53QDMT12-718": ("42QDMT12-718", "38QDMT12-718", "700 x 210 x 635 mm", "780 x 540 x 250 mm"),
    "53QDMT18-718": ("42QDMT18-718", "38QDMT18-718", "920 x 210 x 635 mm", "760 x 590 x 285 mm"),
    "53QDMT24-718": ("42QDMT24-718", "38QDMT24-718", "920 x 270 x 635 mm", "860 x 570 x 350 mm"),
    "53QDMT30-718": ("42QDMT30-718", "38QDMT30-718", "1140 x 270 x 775 mm", "860 x 570 x 350 mm"),
    "53QDMT36-718": ("42QDMT36-718", "38QDMT36-718", "1140 x 270 x 775 mm", "860 x 720 x 350 mm"),
    "53QDMT42-718": ("42QDMT42-718", "38QDMT42-718", "1200 x 300 x 865 mm", "860 x 720 x 350 mm"),
    "53QDMT48-518": ("42QDMT48-718", "38QDMT48-518", "1200 x 300 x 865 mm", "860 x 1150 x 350 mm"),
    "53QDMT60-518": ("42QDMT60-718", "38QDMT60-518", "1200 x 300 x 865 mm", "860 x 1150 x 350 mm"),
}


def test_qdmt_excel_source_contains_exact_verified_dimensions():
    workbook = load_workbook(ROOT / "data/Ceiling Concealed Ducted Split Systems.xlsx", data_only=True)
    sheet = workbook["Equipment_Models"]
    headers = {cell.value: cell.column for cell in sheet[1]}
    rows = {
        row[headers["system_model"] - 1]: row
        for row in sheet.iter_rows(min_row=2, values_only=True)
    }
    assert set(rows) == set(EXPECTED)
    for model, (indoor_model, outdoor_model, indoor_dims, outdoor_dims) in EXPECTED.items():
        row = rows[model]
        assert row[headers["indoor_model"] - 1] == indoor_model
        assert row[headers["outdoor_model"] - 1] == outdoor_model
        assert row[headers["indoor_dims_mm"] - 1] == indoor_dims
        assert row[headers["outdoor_dims_mm"] - 1] == outdoor_dims


def test_qdmt_database_contains_exact_verified_dimensions():
    with sqlite3.connect(ROOT / "hvac_equipment.sqlite") as connection:
        rows = connection.execute(
            "SELECT system_model, indoor_model, outdoor_model, "
            "indoor_dimensions_mm, outdoor_dimensions_mm "
            "FROM equipment_model WHERE system_model IN ("
            + ",".join("?" for _ in EXPECTED)
            + ")",
            tuple(EXPECTED),
        ).fetchall()
    actual = {row[0]: row[1:] for row in rows}
    assert actual == EXPECTED


def test_qdmt_dimension_validation_is_explicit_and_family_scoped():
    assert set(build_db.EXPECTED_QDMT_DIMENSIONS) == set(EXPECTED)
    source = inspect.getsource(build_db.validate_qdmt_dimensions)
    assert "equipment_model" in source
    assert "EXPECTED_QDMT_DIMENSIONS" in source
    assert "miraco" not in source.lower()


def test_selection_query_and_result_headers_carry_both_dimensions():
    source_text = (ROOT / "main.py").read_text(encoding="utf-8")
    source = source_text[source_text.index("    def _search_family("):source_text.index("    def _sorted_for_display(")]
    render = source_text
    assert "indoor_dimensions_mm, outdoor_dimensions_mm, eer_btu_wh" in source
    assert '"Indoor Dims"' in render
    assert '"Outdoor Dims"' in render


def test_packaged_database_matches_root_dimensions():
    with sqlite3.connect(ROOT / "hvac_equipment.sqlite") as root, sqlite3.connect(
        ROOT / "hap/resources/hvac_equipment.sqlite"
    ) as packaged:
        query = (
            "SELECT system_model, indoor_dimensions_mm, outdoor_dimensions_mm "
            "FROM equipment_model WHERE system_model LIKE '53QDMT%' ORDER BY system_model"
        )
        assert root.execute(query).fetchall() == packaged.execute(query).fetchall()
