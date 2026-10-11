#!/usr/bin/env python3
"""
Build the unified HVAC SQLite database.

Sources:
    data/hvac_catalog.xlsx
        QDMT equipment, airflow, sound, cooling, and heating data.

    data/Carrier_Miraco_HVAC_Selection_Database_v3.xlsx
        Carrier Miraco data, loaded by miraco_loader.py.

The database is built in a temporary SQLite file and atomically replaced only
after the complete build and validation process succeeds. The existing
production database is therefore preserved if anything fails.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from miraco_loader import build_miraco_tables
except ImportError:  # QDMT-only builds remain possible when Miraco is unavailable.
    build_miraco_tables = None

try:
    from rooftop_loader import build_rooftop_tables
except ImportError:
    build_rooftop_tables = None


LOGGER = logging.getLogger("build_db")

DEFAULT_BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = DEFAULT_BASE_DIR / "data"
DEFAULT_QDMT_XLSX = DEFAULT_DATA_DIR / "hvac_catalog.xlsx"
DEFAULT_MIRACO_XLSX = (
    DEFAULT_DATA_DIR / "Carrier_Miraco_HVAC_Selection_Database_v3.xlsx"
)
DEFAULT_DB_PATH = DEFAULT_BASE_DIR / "hvac_equipment.sqlite"


class BuildError(RuntimeError):
    """Raised when the database cannot be built or validated safely."""


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def configure_logging(verbose: bool = False) -> None:
    """Configure human-readable console logging."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def require_file(path: Path, description: str) -> None:
    """Raise a clear error when a required file is missing."""
    if not path.is_file():
        raise BuildError(f"{description} was not found: {path}")


def quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier safely."""
    return '"' + identifier.replace('"', '""') + '"'


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names without changing the source values."""
    result = df.copy()
    result.columns = [str(column).strip() for column in result.columns]
    return result


def read_sheet(
    workbook: Path,
    sheet_name: str,
    required_columns: Sequence[str],
) -> pd.DataFrame:
    """Read and validate one Excel worksheet."""
    require_file(workbook, f"Workbook for sheet {sheet_name!r}")

    try:
        dataframe = pd.read_excel(workbook, sheet_name=sheet_name)
    except ValueError as exc:
        raise BuildError(
            f"Could not read worksheet {sheet_name!r} from {workbook.name}: {exc}"
        ) from exc
    except (OSError, ImportError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        raise BuildError(
            f"Failed to read worksheet {sheet_name!r} from {workbook}: {exc}"
        ) from exc

    dataframe = normalize_columns(dataframe)
    missing = sorted(set(required_columns) - set(dataframe.columns))
    if missing:
        raise BuildError(
            f"Worksheet {sheet_name!r} in {workbook.name} is missing columns: "
            f"{', '.join(missing)}"
        )

    # Remove fully empty rows. Other missing values are preserved and validated
    # by the table-specific checks below.
    dataframe = dataframe.dropna(how="all").reset_index(drop=True)
    if dataframe.empty:
        raise BuildError(
            f"Worksheet {sheet_name!r} in {workbook.name} contains no data rows"
        )

    LOGGER.debug(
        "Read %s / %s: %d rows x %d columns",
        workbook.name,
        sheet_name,
        len(dataframe),
        len(dataframe.columns),
    )
    return dataframe


def assert_unique_values(
    dataframe: pd.DataFrame,
    column: str,
    context: str,
) -> None:
    """Require a column to contain non-null unique values."""
    if dataframe[column].isna().any():
        raise BuildError(f"{context}: column {column!r} contains NULL values")
    if dataframe[column].duplicated().any():
        duplicates = sorted(dataframe.loc[dataframe[column].duplicated(), column].unique())
        raise BuildError(
            f"{context}: column {column!r} contains duplicate values: {duplicates}"
        )


def assert_no_nulls(
    dataframe: pd.DataFrame,
    columns: Iterable[str],
    context: str,
) -> None:
    """Require selected columns to contain no NULL values."""
    for column in columns:
        if dataframe[column].isna().any():
            count = int(dataframe[column].isna().sum())
            raise BuildError(f"{context}: {count} NULL value(s) in required column {column!r}")


def attach_equipment_fk(
    dataframe: pd.DataFrame,
    model_map: Mapping[str, int],
    context: str,
) -> pd.DataFrame:
    """Attach a validated equipment_model_id foreign key."""
    result = dataframe.copy()
    if "system_model" not in result.columns:
        raise BuildError(f"{context}: missing system_model column")

    unknown = sorted(
        set(result["system_model"].dropna().astype(str)) - set(model_map)
    )
    if unknown:
        raise BuildError(f"{context}: unknown system_model value(s): {unknown}")

    if result["system_model"].isna().any():
        raise BuildError(f"{context}: system_model contains NULL values")

    result["equipment_model_id"] = (
        result["system_model"].map(model_map).astype("int64")
    )
    return result


def create_temp_database(final_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a temporary SQLite database beside the final database path."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.stem}.",
        suffix=".tmp.sqlite",
        dir=final_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)

    try:
        connection = sqlite3.connect(temporary_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA user_version = 1")
        connection.execute("PRAGMA application_id = 0x48564143")
        return temporary_path, connection
    except (OSError, sqlite3.Error):
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_replace_database(temporary_path: Path, final_path: Path) -> None:
    """Atomically replace the final database after a successful build."""
    os.replace(temporary_path, final_path)
    LOGGER.info("Database replaced successfully: %s", final_path)


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return a streaming SHA-256 hash for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_timestamp() -> str:
    """Return a reproducible timestamp when SOURCE_DATE_EPOCH is provided."""
    source_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_epoch is not None:
        try:
            return datetime.fromtimestamp(int(source_epoch), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise BuildError("SOURCE_DATE_EPOCH must be a valid Unix timestamp") from exc
    return datetime.now(timezone.utc).isoformat()


def create_metadata_table(connection: sqlite3.Connection, qdmt_xlsx: Path, miraco_xlsx: Path | None, miraco_loaded: bool, rooftop_loaded: bool = False) -> None:
    """Record source files and build metadata inside the database."""
    connection.execute(
        """
        CREATE TABLE _metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    rows = [
        ("schema_version", "1"),
        ("build_timestamp_utc", build_timestamp()),
        ("qdmt_file", qdmt_xlsx.name),
        ("qdmt_sha256", sha256_file(qdmt_xlsx)),
        ("miraco_loaded", "1" if miraco_loaded else "0"),
        ("rooftop_loaded", "1" if rooftop_loaded else "0"),
    ]
    if miraco_loaded and miraco_xlsx is not None and miraco_xlsx.is_file():
        rows.extend(
            [
                ("miraco_file", miraco_xlsx.name),
                ("miraco_sha256", sha256_file(miraco_xlsx)),
            ]
        )
    connection.executemany("INSERT INTO _metadata (key, value) VALUES (?, ?)", rows)
    LOGGER.info("Metadata table created")


def run_integrity_check(connection: sqlite3.Connection) -> None:
    """Run SQLite's full database integrity check."""
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise BuildError(f"SQLite integrity check failed: {result}")
    LOGGER.info("SQLite integrity_check: ok")


# ---------------------------------------------------------------------------
# SQLite schema and loading helpers
# ---------------------------------------------------------------------------

def create_equipment_model_table(connection: sqlite3.Connection) -> None:
    """Create the parent QDMT equipment table with a real primary key."""
    connection.execute(
        """
        CREATE TABLE equipment_model (
            id INTEGER PRIMARY KEY,
            system_model TEXT NOT NULL UNIQUE,
            nominal_class_kbtu REAL NOT NULL,
            indoor_model TEXT NOT NULL,
            outdoor_model TEXT NOT NULL,
            rated_cooling_btuh REAL NOT NULL,
            rated_cooling_input_w REAL NOT NULL,
            cooling_current_a REAL NOT NULL,
            rated_heating_btuh REAL,
            eer_btu_wh REAL,
            eer_ww REAL,
            cop_ww REAL,
            max_esp_inwg REAL NOT NULL,
            indoor_weight_kg REAL NOT NULL,
            indoor_dimensions_mm TEXT NOT NULL,
            power_supply TEXT NOT NULL,
            compressor_type TEXT,
            refrigerant_type TEXT,
            outdoor_sound_dba REAL,
            outdoor_dimensions_mm TEXT,
            outdoor_weight_kg REAL,
            pipe_liquid_inch TEXT,
            pipe_gas_inch TEXT,
            max_pipe_length_m REAL,
            max_height_diff_m REAL,
            drain_hose_mm REAL,
            nominal_airflow_cfm TEXT,
            nominal_airflow_m3h TEXT,
            indoor_sound_dba TEXT,
            heating_input_w REAL,
            heating_current_a REAL
        )
        """
    )


def load_equipment_models(
    connection: sqlite3.Connection,
    qdmt_workbook: Path,
) -> dict[str, int]:
    """Load QDMT equipment models and return system_model -> primary key."""
    dataframe = read_sheet(
        qdmt_workbook,
        "Equipment_Models",
        [
            "system_model",
            "nominal_kbtu",
            "indoor_model",
            "outdoor_model",
            "cooling_btuh",
            "cooling_input_w",
            "cooling_current_a",
            "heating_btuh",
            "max_esp_inwg",
            "indoor_weight_kg",
            "indoor_dims_mm",
            "power_supply",
        ],
    ).rename(
        columns={
            "nominal_kbtu": "nominal_class_kbtu",
            "cooling_btuh": "rated_cooling_btuh",
            "cooling_input_w": "rated_cooling_input_w",
            "heating_btuh": "rated_heating_btuh",
            "indoor_dims_mm": "indoor_dimensions_mm",
            "outdoor_dims_mm": "outdoor_dimensions_mm",
        }
    )

    assert_unique_values(dataframe, "system_model", "Equipment_Models")
    assert_no_nulls(
        dataframe,
        [
            "system_model",
            "nominal_class_kbtu",
            "indoor_model",
            "outdoor_model",
            "rated_cooling_btuh",
            "rated_cooling_input_w",
            "cooling_current_a",
            "max_esp_inwg",
            "indoor_weight_kg",
            "indoor_dimensions_mm",
            "power_supply",
        ],
        "Equipment_Models",
    )

    allowed_columns = [
        "system_model",
        "nominal_class_kbtu",
        "indoor_model",
        "outdoor_model",
        "rated_cooling_btuh",
        "rated_cooling_input_w",
        "cooling_current_a",
        "rated_heating_btuh",
        "eer_btu_wh",
        "eer_ww",
        "cop_ww",
        "max_esp_inwg",
        "indoor_weight_kg",
        "indoor_dimensions_mm",
        "power_supply",
        "compressor_type",
        "refrigerant_type",
        "outdoor_sound_dba",
        "outdoor_dimensions_mm",
        "outdoor_weight_kg",
        "pipe_liquid_inch",
        "pipe_gas_inch",
        "max_pipe_length_m",
        "max_height_diff_m",
        "drain_hose_mm",
        "nominal_airflow_cfm",
        "nominal_airflow_m3h",
        "indoor_sound_dba",
        "heating_input_w",
        "heating_current_a",
    ]
    dataframe = dataframe.reindex(columns=allowed_columns)

    create_equipment_model_table(connection)
    dataframe.to_sql(
        "equipment_model",
        connection,
        if_exists="append",
        index=False,
    )

    model_rows = connection.execute(
        "SELECT id, system_model FROM equipment_model ORDER BY id"
    ).fetchall()
    model_map = {str(system_model): int(model_id) for model_id, system_model in model_rows}
    LOGGER.info("equipment_model: %d rows", len(model_map))
    return model_map


def sqlite_type_for_series(series: pd.Series) -> str:
    """Map a pandas series to a conservative SQLite storage type."""
    if pd.api.types.is_bool_dtype(series):
        return "INTEGER"
    if pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def create_child_table_with_fk(
    connection: sqlite3.Connection,
    table_name: str,
    dataframe: pd.DataFrame,
    columns: Sequence[str],
) -> None:
    """Create a child table explicitly and insert rows with a real FK."""
    if not table_name.replace("_", "").isalnum():
        raise BuildError(f"Unsafe SQLite table name: {table_name!r}")

    selected = dataframe[list(columns)].copy()
    definitions = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
    for column in selected.columns:
        if column == "is_anomaly":
            definitions.append(
                f"{quote_identifier(column)} INTEGER NOT NULL DEFAULT 0"
            )
            continue
        sql_type = "INTEGER" if column == "equipment_model_id" else sqlite_type_for_series(selected[column])
        nullability = " NOT NULL" if column == "equipment_model_id" else ""
        definitions.append(f"{quote_identifier(column)} {sql_type}{nullability}")

    definitions.append(
        "FOREIGN KEY (\"equipment_model_id\") "
        "REFERENCES equipment_model(id) ON UPDATE CASCADE ON DELETE CASCADE"
    )
    connection.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
    connection.execute(
        f"CREATE TABLE {quote_identifier(table_name)} ({', '.join(definitions)})"
    )

    placeholders = ", ".join("?" for _ in selected.columns)
    column_sql = ", ".join(quote_identifier(column) for column in selected.columns)
    insert_sql = (
        f"INSERT INTO {quote_identifier(table_name)} ({column_sql}) "
        f"VALUES ({placeholders})"
    )

    rows = []
    for values in selected.itertuples(index=False, name=None):
        rows.append(tuple(None if pd.isna(value) else value for value in values))
    connection.executemany(insert_sql, rows)


def write_child_table(
    connection: sqlite3.Connection,
    table_name: str,
    dataframe: pd.DataFrame,
    columns: Sequence[str],
    model_map: Mapping[str, int],
    context: str,
) -> None:
    """Validate, reduce, and write a QDMT child table."""
    attached = attach_equipment_fk(dataframe, model_map, context)
    selected = attached[list(columns)].copy()

    if selected["equipment_model_id"].isna().any():
        raise BuildError(f"{context}: NULL equipment_model_id after model mapping")

    create_child_table_with_fk(connection, table_name, selected, columns)
    LOGGER.info("%s: %d rows", table_name, len(selected))


def load_qdmt_tables(
    connection: sqlite3.Connection,
    qdmt_workbook: Path,
) -> dict[str, int]:
    """Load all QDMT tables and return the equipment model map."""
    model_map = load_equipment_models(connection, qdmt_workbook)

    airflow = read_sheet(
        qdmt_workbook,
        "Airflow",
        ["system_model", "fan_speed", "esp_inwg", "esp_pa", "airflow_cfm", "airflow_m3h"],
    )
    write_child_table(
        connection,
        "airflow_point",
        airflow,
        [
            "equipment_model_id",
            "fan_speed",
            "esp_inwg",
            "esp_pa",
            "airflow_cfm",
            "airflow_m3h",
        ],
        model_map,
        "Airflow",
    )

    cooling = read_sheet(
        qdmt_workbook,
        "Cooling_Performance",
        [
            "system_model",
            "esp_inwg",
            "fan_speed",
            "airflow_cfm",
            "indoor_db_c",
            "indoor_wb_c",
            "outdoor_db_c",
            "total_capacity_btuh",
            "sensible_capacity_btuh",
            "input_w",
        ],
    )
    cooling_columns = [
        "equipment_model_id",
        "esp_inwg",
        "fan_speed",
        "airflow_cfm",
        "indoor_db_c",
        "indoor_wb_c",
        "outdoor_db_c",
        "total_capacity_btuh",
        "sensible_capacity_btuh",
        "input_w",
    ]
    if "source_page" in cooling.columns:
        cooling_columns.append("source_page")
    cooling["is_anomaly"] = 0
    cooling_columns.append("is_anomaly")
    write_child_table(
        connection,
        "cooling_performance",
        cooling,
        cooling_columns,
        model_map,
        "Cooling_Performance",
    )

    heating = read_sheet(
        qdmt_workbook,
        "Heating_Performance",
        [
            "system_model",
            "esp_inwg",
            "fan_speed",
            "airflow_cfm",
            "indoor_db_c",
            "outdoor_condition",
            "total_capacity_btuh",
            "input_w",
        ],
    )
    heating_columns = [
        "equipment_model_id",
        "esp_inwg",
        "fan_speed",
        "airflow_cfm",
        "indoor_db_c",
        "outdoor_condition",
        "total_capacity_btuh",
        "input_w",
    ]
    if "source_page" in heating.columns:
        heating_columns.append("source_page")
    write_child_table(
        connection,
        "heating_performance",
        heating,
        heating_columns,
        model_map,
        "Heating_Performance",
    )

    sound = read_sheet(
        qdmt_workbook,
        "Sound_Level",
        ["system_model", "fan_speed", "esp_inwg", "esp_pa", "sound_dba"],
    )
    write_child_table(
        connection,
        "sound_level",
        sound,
        ["equipment_model_id", "fan_speed", "esp_inwg", "esp_pa", "sound_dba"],
        model_map,
        "Sound_Level",
    )

    return model_map


# ---------------------------------------------------------------------------
# Miraco loading, indexes, and validation
# ---------------------------------------------------------------------------

def load_miraco_tables(
    connection: sqlite3.Connection,
    miraco_workbook: Path,
    required: bool,
) -> bool:
    """Load Miraco tables when available and return whether they were loaded."""
    if not miraco_workbook.exists():
        if required:
            raise BuildError(f"Required Miraco workbook was not found: {miraco_workbook}")
        LOGGER.warning("Skipping Miraco: workbook not found: %s", miraco_workbook)
        return False

    if build_miraco_tables is None:
        raise BuildError(
            "Miraco workbook is present, but miraco_loader.py could not be imported"
        )

    try:
        count = build_miraco_tables(connection, miraco_workbook)
    except (OSError, ImportError, TypeError, KeyError, sqlite3.Error) as exc:
        raise BuildError(f"Miraco table loading failed: {exc}") from exc

    LOGGER.info("Miraco tables loaded: %s equipment rows", count)
    return True


def create_indexes(connection: sqlite3.Connection) -> None:
    """Create indexes used by application queries."""
    statements = [
        "CREATE INDEX idx_airflow_model ON airflow_point(equipment_model_id)",
        "CREATE INDEX idx_sound_model ON sound_level(equipment_model_id)",
        "CREATE INDEX idx_equipment_class ON equipment_model(nominal_class_kbtu)",
        "CREATE INDEX idx_equipment_family ON equipment_model(nominal_class_kbtu, system_model)",
        """
        CREATE INDEX idx_cooling_lookup ON cooling_performance(
            equipment_model_id,
            indoor_db_c,
            indoor_wb_c,
            outdoor_db_c,
            fan_speed,
            esp_inwg
        )
        """,
        """
        CREATE INDEX idx_heating_lookup ON heating_performance(
            equipment_model_id,
            indoor_db_c,
            outdoor_condition,
            fan_speed,
            esp_inwg
        )
        """,
    ]

    for statement in statements:
        connection.execute(statement)
    connection.execute("ANALYZE")
    LOGGER.info("Indexes created and analyzed")


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite database."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    """Return a table row count using a quoted identifier."""
    row = connection.execute(
        f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"
    ).fetchone()
    return int(row[0])


def validate_miraco_tables(connection: sqlite3.Connection) -> None:
    """Validate the Miraco parent table and any child tables exposing the FK."""
    parent = "miraco_equipment_model"
    if not table_exists(connection, parent):
        raise BuildError(f"Miraco validation failed; missing table: {parent}")
    if table_row_count(connection, parent) == 0:
        raise BuildError("Miraco validation failed; parent table is empty")

    duplicate_models = connection.execute(
        f"""
        SELECT system_model, COUNT(*)
        FROM {quote_identifier(parent)}
        GROUP BY system_model
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicate_models:
        raise BuildError(
            "Miraco validation failed; duplicate system_model values: "
            f"{[row[0] for row in duplicate_models]}"
        )

    miraco_tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'miraco_%'"
        ).fetchall()
    ]
    for table_name in miraco_tables:
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            ).fetchall()
        }
        if table_name == parent or "equipment_model_id" not in columns:
            continue

        orphan_count = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(table_name)} AS child
            LEFT JOIN {quote_identifier(parent)} AS parent_model
              ON parent_model.id = child.equipment_model_id
            WHERE parent_model.id IS NULL
            """
        ).fetchone()[0]
        if orphan_count:
            raise BuildError(
                f"Miraco validation failed; {table_name!r} has "
                f"{orphan_count} orphan row(s)"
            )

    LOGGER.info(
        "Miraco validation passed: %d equipment rows, %d miraco table(s)",
        table_row_count(connection, parent),
        len(miraco_tables),
    )


def validate_database(
    connection: sqlite3.Connection,
    expect_miraco: bool,
    expected_models: int | None = None,
) -> None:
    """Run structural and referential-integrity checks before commit."""
    required_tables = {
        "equipment_model",
        "airflow_point",
        "cooling_performance",
        "heating_performance",
        "sound_level",
    }
    missing = sorted(table for table in required_tables if not table_exists(connection, table))
    if missing:
        raise BuildError(f"Validation failed; missing required table(s): {missing}")

    run_integrity_check(connection)

    model_count = table_row_count(connection, "equipment_model")
    if expected_models is not None and model_count != expected_models:
        raise BuildError(
            f"Validation failed; expected {expected_models} QDMT equipment models, "
            f"found {model_count}"
        )
    if model_count < 5:
        raise BuildError(
            f"Validation failed; suspiciously few QDMT equipment models ({model_count})"
        )

    child_tables = [
        "airflow_point",
        "cooling_performance",
        "heating_performance",
        "sound_level",
    ]
    for table_name in child_tables:
        count = table_row_count(connection, table_name)
        if count == 0:
            raise BuildError(f"Validation failed; table {table_name!r} is empty")

        orphan_count = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(table_name)} AS child
            LEFT JOIN equipment_model AS parent
              ON parent.id = child.equipment_model_id
            WHERE parent.id IS NULL
            """
        ).fetchone()[0]
        if orphan_count:
            raise BuildError(
                f"Validation failed; {table_name!r} contains {orphan_count} orphan row(s)"
            )

    # Ensure the actual SQLite foreign-key enforcement is active.
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if foreign_keys != 1:
        raise BuildError("Validation failed; SQLite foreign_keys pragma is disabled")

    if expect_miraco:
        validate_miraco_tables(connection)

    LOGGER.info(
        "Validation passed: %d models, %d airflow, %d cooling, %d heating, %d sound rows",
        model_count,
        table_row_count(connection, "airflow_point"),
        table_row_count(connection, "cooling_performance"),
        table_row_count(connection, "heating_performance"),
        table_row_count(connection, "sound_level"),
    )


def log_build_summary(database_path: Path) -> None:
    """Log row counts from the completed database file."""
    tables = [
        "equipment_model",
        "airflow_point",
        "cooling_performance",
        "heating_performance",
        "sound_level",
    ]
    with closing(sqlite3.connect(database_path)) as connection:
        LOGGER.info("Build summary:")
        for table_name in tables:
            LOGGER.info(
                "  %-22s %6d rows",
                table_name,
                table_row_count(connection, table_name),
            )
        miraco_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'miraco_%'"
        ).fetchone()[0]
        LOGGER.info("  %-22s %6d tables", "miraco_*", miraco_count)
        rooftop_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'rooftop_%'"
        ).fetchone()[0]
        LOGGER.info("  %-22s %6d tables", "rooftop_*", rooftop_count)


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory containing the Excel sources (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--qdmt-xlsx",
        type=Path,
        default=None,
        help="Override the QDMT workbook path",
    )
    parser.add_argument(
        "--miraco-xlsx",
        type=Path,
        default=None,
        help="Override the Miraco workbook path",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Output SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--skip-miraco",
        action="store_true",
        help="Build QDMT tables only, even when the Miraco workbook exists",
    )
    parser.add_argument(
        "--require-miraco",
        action="store_true",
        help="Fail if the Miraco workbook or loader is unavailable",
    )
    parser.add_argument(
        "--expected-models",
        type=int,
        default=None,
        help="Fail if the QDMT model count differs from this value",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate a temporary database without replacing the output DB",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing SQLite database without rebuilding it",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)
    if args.skip_miraco and args.require_miraco:
        parser.error("--skip-miraco and --require-miraco are mutually exclusive")
    if args.validate_only and args.dry_run:
        parser.error("--validate-only and --dry-run are mutually exclusive")
    if args.expected_models is not None and args.expected_models < 1:
        parser.error("--expected-models must be a positive integer")
    return args


def build_database(
    qdmt_xlsx: Path,
    miraco_xlsx: Path,
    db_path: Path,
    *,
    skip_miraco: bool = False,
    require_miraco: bool = False,
    expected_models: int | None = None,
    dry_run: bool = False,
) -> Path:
    """Build and validate the database, returning the final database path."""
    require_file(qdmt_xlsx, "QDMT workbook")

    if skip_miraco:
        load_miraco = False
    else:
        load_miraco = miraco_xlsx.exists() or require_miraco

    temporary_path, connection = create_temp_database(db_path)
    completed = False

    miraco_loaded = False
    try:
        load_qdmt_tables(connection, qdmt_xlsx)

        if load_miraco:
            miraco_loaded = load_miraco_tables(
                connection,
                miraco_xlsx,
                required=require_miraco,
            )

        rooftop_loaded = False
        if build_rooftop_tables is not None:
            try:
                rt_count = build_rooftop_tables(connection, qdmt_xlsx.parent)
                rooftop_loaded = rt_count > 0
                LOGGER.info("Carrier Rooftop tables loaded: %d models", rt_count)
            except (OSError, ImportError, ValueError, TypeError, KeyError,
                    sqlite3.Error):
                LOGGER.exception("Could not load optional Rooftop tables")

        create_metadata_table(connection, qdmt_xlsx, miraco_xlsx if miraco_loaded else None, miraco_loaded, rooftop_loaded=rooftop_loaded)
        create_indexes(connection)
        validate_database(
            connection,
            expect_miraco=miraco_loaded,
            expected_models=expected_models,
        )
        connection.commit()
        completed = True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
        if not completed:
            temporary_path.unlink(missing_ok=True)

    if not completed:
        raise BuildError("Database build did not complete")

    if dry_run:
        temporary_path.unlink(missing_ok=True)
        LOGGER.info("Dry-run completed; output database was not replaced")
        return db_path

    try:
        atomic_replace_database(temporary_path, db_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise

    return db_path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    configure_logging(args.verbose)

    qdmt_xlsx = args.qdmt_xlsx or args.data_dir / "hvac_catalog.xlsx"
    miraco_xlsx = args.miraco_xlsx or (
        args.data_dir / "Carrier_Miraco_HVAC_Selection_Database_v3.xlsx"
    )

    if args.validate_only:
        if not args.db.is_file():
            LOGGER.error("--validate-only: database not found: %s", args.db)
            return 2
        try:
            with closing(sqlite3.connect(args.db)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                validate_database(
                    connection,
                    expect_miraco=table_exists(connection, "miraco_equipment_model"),
                    expected_models=args.expected_models,
                )
            LOGGER.info("Validation-only succeeded: %s", args.db)
            return 0
        except (BuildError, OSError, sqlite3.Error, ValueError) as exc:
            LOGGER.error("Validation-only failed: %s", exc)
            return 1

    if not qdmt_xlsx.is_file():
        LOGGER.error(
            "QDMT workbook not found: %s\n"
            "Make sure --data-dir points to the folder containing hvac_catalog.xlsx.",
            qdmt_xlsx,
        )
        return 2

    try:
        output_path = build_database(
            qdmt_xlsx=qdmt_xlsx,
            miraco_xlsx=miraco_xlsx,
            db_path=args.db,
            skip_miraco=args.skip_miraco,
            require_miraco=args.require_miraco,
            expected_models=args.expected_models,
            dry_run=args.dry_run,
        )
    except (BuildError, OSError, sqlite3.Error, ValueError) as exc:
        LOGGER.error("Build failed: %s", exc)
        return 1

    if not args.dry_run:
        log_build_summary(output_path)
    LOGGER.info("Build completed successfully: %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
