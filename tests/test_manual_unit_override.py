import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manual_53qdmt30_708_override_matches_user_specification():
    spec = json.loads(
        (ROOT / "data" / "manual_unit_overrides.json").read_text(
            encoding="utf-8"
        )
    )[0]
    with sqlite3.connect(ROOT / "hvac_equipment.sqlite") as connection:
        model = connection.execute(
            """
            SELECT id, rated_cooling_btuh, max_esp_inwg, power_supply,
                   pipe_gas_inch, pipe_liquid_inch
            FROM equipment_model
            WHERE system_model=?
            """,
            (spec["system_model"],),
        ).fetchone()
        assert model is not None
        model_id, capacity, max_esp, power, gas, liquid = model
        assert capacity == spec["rated_cooling_btuh"]
        assert max_esp == spec["max_esp_inwg"]
        assert power == spec["power_supply"]
        assert gas == spec["pipe_gas_inch"]
        assert liquid == spec["pipe_liquid_inch"]

        point = connection.execute(
            """
            SELECT fan_speed, esp_inwg, airflow_cfm
            FROM airflow_point
            WHERE equipment_model_id=?
            """,
            (model_id,),
        ).fetchall()
        assert point == [("High", 0.16, 790.0)]
