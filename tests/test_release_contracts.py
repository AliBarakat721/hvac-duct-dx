import json
import sqlite3
import zipfile
from pathlib import Path

from app_meta import __version__, database_path, version_label
from exporters import export_json, export_markdown
from project_persistence import (
    PROJECT_SCHEMA_VERSION,
    PROJECT_UNITS,
    create_project_document,
    load_project,
    restore_project_state,
    save_project,
)

ROOT = Path(__file__).resolve().parents[1]


def _settings():
    return {
        "diffuser_application": "office_priv",
        "diffuser_type": "Circular (CCD)",
        "diffuser_max_nc": 35.0,
        "diffuser_max_dp_inwg": 0.20,
        "energy_hours_per_year": 2000,
        "energy_tariff": "0.20",
        "energy_currency": "USD",
        "diffuser_room_width_ft": 20.0,
        "diffuser_room_length_ft": 20.0,
        "diffuser_ceiling_height_ft": 10.0,
    }


def test_release_metadata_is_available():
    assert __version__ == "1.0.0"
    assert __version__ in version_label()
    assert database_path().name == "hvac_equipment.sqlite"


def test_database_is_available_as_a_packaged_runtime_resource():
    packaged_database = (
        Path(__file__).resolve().parents[1]
        / "hap" / "resources" / "hvac_equipment.sqlite"
    )
    assert packaged_database.is_file()
    with sqlite3.connect(packaged_database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_database_path_falls_back_to_packaged_resource(monkeypatch, tmp_path):
    import app_meta

    monkeypatch.delenv("HVAC_DX_DATABASE", raising=False)
    monkeypatch.setattr(app_meta, "application_root", lambda: tmp_path)
    expected = Path(app_meta.files("hap").joinpath("resources", "hvac_equipment.sqlite"))
    assert app_meta.database_path() == expected
    assert expected.is_file()


def test_built_wheel_includes_the_equipment_database(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", "--no-deps",
            "--no-build-isolation", "--wheel-dir", str(tmp_path), str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        resource = "hap/resources/hvac_equipment.sqlite"
        assert resource in wheel.namelist()
        assert wheel.getinfo(resource).file_size > 0


def test_project_round_trip_preserves_schema_and_runtime_state(tmp_path):
    document = create_project_document({}, _settings(), project_name="QA")
    assert document["schema_version"] == PROJECT_SCHEMA_VERSION
    assert document["units"] == PROJECT_UNITS
    path = tmp_path / "qa.ductdx"
    save_project(path, document)
    loaded = load_project(path)
    cart, systems, settings, inputs = restore_project_state(loaded)
    assert cart == {}
    assert systems == []
    assert settings["diffuser_application"] == "office_priv"
    assert inputs["ceiling_enforce"] is False


def test_exporters_write_readable_json_and_markdown(tmp_path):
    json_path = tmp_path / "selection.json"
    md_path = tmp_path / "selection.md"
    export_json(json_path, ["Model", "Status"], [["53QDMT30-718", "Suitable"]])
    export_markdown(md_path, ["Model", "Status"], [["53QDMT30-718", "Suitable"]])
    assert json.loads(json_path.read_text(encoding="utf-8"))["rows"]
    assert "53QDMT30-718" in md_path.read_text(encoding="utf-8")


def test_manual_override_keeps_unprovided_performance_fields_null():
    with sqlite3.connect(ROOT / "hvac_equipment.sqlite") as connection:
        row = connection.execute(
            "SELECT rated_heating_btuh, outdoor_sound_dba FROM equipment_model "
            "WHERE system_model = ?",
            ("53QDMT30-708",),
        ).fetchone()
    assert row == (31000.0, None)
