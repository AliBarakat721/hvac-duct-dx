"""Application metadata and resource paths shared by runtime and reports."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "HVAC Duct DX"
__version__ = "0.1.0"
VERSION = __version__


def application_root() -> Path:
    """Return the source/bundled application root."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def database_path() -> Path:
    """Resolve the equipment database, allowing packaged/external overrides."""
    override = os.environ.get("HVAC_DX_DATABASE")
    if override:
        return Path(override).expanduser().resolve()
    return application_root() / "hvac_equipment.sqlite"


def version_label() -> str:
    return f"{APP_NAME} {__version__}"
