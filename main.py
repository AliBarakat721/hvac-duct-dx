#!/usr/bin/env python3
"""
Duct DX Equipment Selector
==========================
- Analyzes a HAP report (image or PDF)
- Extracts: Total Load, Sensible Load, CFM, ESP, Heating Load
- Asks the user for: ESP (if missing), Power Supply, Fan Speed,
  Sound Level, and Design Margins
- Selects suitable models from the Carrier/Miraco equipment database
"""

import csv
import importlib.util
import logging
import math
import re
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from hap_parser import parse_all_systems, parse_hap, parse_hap_detailed

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QBrush, QColor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "PySide6 is required to run Duct DX. Install it with: python -m pip install PySide6"
    ) from exc

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

import report_builder
import ui_shell
from exporters import FORMATS, export_table
from extractors import pick_extractor
from manual_input import ManualInputDialog
from project_persistence import (
    ProjectFileError,
    create_project_document,
    restore_project_state,
)
from project_persistence import (
    load_project as read_project_file,
)
from project_persistence import (
    save_project as write_project_file,
)
from report_builder import generate_detailed_report


def _cad_diffuser_records(cad_room, positions_m, throw_t50_m):
    """Serialize a placed layout without reassigning surviving diffuser identities."""
    existing = list((cad_room or {}).get("diffusers", []))
    used_existing = set()
    used_tags = {diffuser.get("tag") for diffuser in existing
                 if isinstance(diffuser.get("tag"), str)}
    highest_tag_number = max(
        (int(match.group(1))
         for tag in used_tags
         if (match := re.fullmatch(r"D(\d+)", tag))),
        default=0)
    records = []
    for x_m, y_m in positions_m:
        match_index = next((
            index for index, diffuser in enumerate(existing)
            if index not in used_existing
            and math.dist(
                (float(diffuser.get("x_m", math.inf)),
                 float(diffuser.get("y_m", math.inf))),
                (x_m, y_m)) <= 1e-6
        ), None)
        if match_index is not None:
            old = existing[match_index]
            used_existing.add(match_index)
            tag = old.get("tag")
            diffuser_id = old.get("id") or uuid.uuid4().hex
            if not isinstance(tag, str) or not tag:
                tag = None
        else:
            tag = None
            diffuser_id = uuid.uuid4().hex
        if tag is None:
            while True:
                highest_tag_number += 1
                tag = f"D{highest_tag_number}"
                if tag not in used_tags:
                    break
        used_tags.add(tag)
        records.append({
            "id": diffuser_id,
            "tag": tag,
            "x_m": x_m,
            "y_m": y_m,
            "throw_t50_m": throw_t50_m,
        })
    return records


# ═══════════════════════════════════════════════════════════════
#                         DATABASE SETUP
# ═══════════════════════════════════════════════════════════════

DB = Path(__file__).with_name("hvac_equipment.sqlite")


# ═══════════════════════════════════════════════════════════════
#                         ESP CALCULATOR
# ═══════════════════════════════════════════════════════════════

FRICTION_PER_METER = 0.015

FITTINGS_PRESSURE = {
    "elbow_90": 0.010,
    "elbow_45": 0.005,
    "tee": 0.015,
    "reducer": 0.008,
    "transition": 0.010,
}

ACCESSORIES_PRESSURE = {
    "filter_standard": 0.08,
    "filter_hepa": 0.25,
    "filter_washable": 0.05,
    "cooling_coil": 0.10,
    "heating_coil": 0.06,
    "damper_manual": 0.02,
    "damper_motorized": 0.05,
    "diffuser": 0.03,
    "grille_return": 0.04,
    "fresh_air_intake": 0.05,
    "vav_box": 0.15,
}

ESP_PRESETS = {
    "Split System (Single Room)": {
        "range": (0.05, 0.12),
        "default": 0.08,
        "desc": "Very short ductwork or no ductwork",
    },
    "Residential - Apartment / Villa": {
        "range": (0.10, 0.18),
        "default": 0.14,
        "desc": "Medium ductwork with a limited number of diffusers",
    },
    "Office / Commercial Shop": {
        "range": (0.15, 0.25),
        "default": 0.20,
        "desc": "Medium ductwork + filter + fresh air",
    },
    "Hotel / Hospital": {
        "range": (0.25, 0.35),
        "default": 0.30,
        "desc": "Long ductwork + high-efficiency filter + fresh air",
    },
    "Mall / Large Building": {
        "range": (0.30, 0.45),
        "default": 0.38,
        "desc": "Long ductwork + VAV + filters",
    },
}


@dataclass
class ESPInput:
    supply_duct_length_m: float = 0.0
    return_duct_length_m: float = 0.0
    elbow_90_count: int = 0
    elbow_45_count: int = 0
    tee_count: int = 0
    reducer_count: int = 0
    transition_count: int = 0
    accessories: list[str] = field(default_factory=list)
    custom_additions: float = 0.0


@dataclass
class ESPResult:
    supply_friction: float = 0.0
    return_friction: float = 0.0
    fittings_total: float = 0.0
    accessories_total: float = 0.0
    custom: float = 0.0
    total: float = 0.0
    breakdown: dict = field(default_factory=dict)


def calculate_esp(data: ESPInput) -> ESPResult:
    result = ESPResult()
    result.supply_friction = data.supply_duct_length_m * FRICTION_PER_METER
    result.return_friction = data.return_duct_length_m * FRICTION_PER_METER
    result.fittings_total = (
        data.elbow_90_count * FITTINGS_PRESSURE["elbow_90"]
        + data.elbow_45_count * FITTINGS_PRESSURE["elbow_45"]
        + data.tee_count * FITTINGS_PRESSURE["tee"]
        + data.reducer_count * FITTINGS_PRESSURE["reducer"]
        + data.transition_count * FITTINGS_PRESSURE["transition"]
    )
    result.accessories_total = sum(
        ACCESSORIES_PRESSURE.get(a, 0.0) for a in data.accessories
    )
    result.custom = data.custom_additions
    result.total = (
        result.supply_friction
        + result.return_friction
        + result.fittings_total
        + result.accessories_total
        + result.custom
    )
    result.breakdown = {
        "Supply Duct Friction": result.supply_friction,
        "Return Duct Friction": result.return_friction,
        "Fittings": result.fittings_total,
        "Accessories": result.accessories_total,
        "Custom": result.custom,
    }
    return result


def suggest_esp_from_cfm(
    cfm: float, app_type: str = "Office / Commercial Shop"
) -> float:
    base = ESP_PRESETS.get(app_type, {}).get("default", 0.20)
    if cfm < 200:
        return base * 0.6
    elif cfm < 400:
        return base * 0.8
    elif cfm < 800:
        return base
    elif cfm < 1500:
        return base * 1.2
    else:
        return base * 1.4


# ═══════════════════════════════════════════════════════════════
#                          ESP DIALOG
# ═══════════════════════════════════════════════════════════════


class ESPDialog(QDialog):
    def __init__(self, parent=None, initial_cfm=0.0):
        super().__init__(parent)
        self.setWindowTitle("ESP Calculator - External Static Pressure")
        self.setMinimumSize(700, 700)
        self.result_esp = None
        self.initial_cfm = initial_cfm
        self.setStyleSheet("""
            QDialog { background: #111827; color: #e5e7eb; }
            QLabel { color: #e5e7eb; }
            QGroupBox {
                border: 1px solid #374151; border-radius: 6px;
                margin-top: 10px; padding: 8px;
                color: #93c5fd; font-weight: bold;
            }
            QGroupBox::title { left: 10px; padding: 0 6px; background: #111827; }
            QPushButton {
                background: #2563eb; color: white; border: 0;
                border-radius: 5px; padding: 8px 14px; font-weight: bold;
            }
            QPushButton:hover { background: #3b82f6; }
            QDoubleSpinBox, QSpinBox, QComboBox {
                background: #1f2937; color: #f9fafb;
                border: 1px solid #4b5563; border-radius: 4px; padding: 6px;
            }
            QTextEdit {
                background: #0b1220; color: #86efac;
                border: 1px solid #374151; border-radius: 4px;
            }
            QTabWidget::pane { border: 1px solid #374151; }
            QTabBar::tab { background: #1f2937; color: #9ca3af; padding: 8px 14px; }
            QTabBar::tab:selected { background: #2563eb; color: white; }
            QCheckBox { color: #e5e7eb; }
        """)
        self.build_ui()
        self.apply_preset()
        self.suggest_from_cfm()

    def build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("🌬️ External Static Pressure Calculator")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #60a5fa;")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        hint = QLabel(
            "If HAP does not provide an ESP value, use this calculator to estimate it from the ductwork data."
        )
        hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        root.addWidget(hint)

        tabs = QTabWidget()

        # Presets Tab
        tab_preset = QWidget()
        preset_layout = QVBoxLayout(tab_preset)
        preset_group = QGroupBox("Select Application Type")
        preset_group_layout = QVBoxLayout(preset_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(ESP_PRESETS.keys())
        self.preset_combo.currentTextChanged.connect(self.apply_preset)
        preset_group_layout.addWidget(self.preset_combo)
        self.preset_info = QLabel("")
        self.preset_info.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self.preset_info.setWordWrap(True)
        preset_group_layout.addWidget(self.preset_info)
        preset_layout.addWidget(preset_group)
        preset_layout.addStretch()
        tabs.addTab(tab_preset, "📋 Presets")

        # Detailed Tab
        tab_detail = QWidget()
        detail_layout = QVBoxLayout(tab_detail)

        duct_group = QGroupBox("Ductwork Lengths (meters)")
        duct_layout = QFormLayout(duct_group)
        self.supply_len = QDoubleSpinBox()
        self.supply_len.setRange(0, 100)
        self.supply_len.setSingleStep(0.5)
        self.supply_len.setSuffix(" m")
        duct_layout.addRow("Supply Duct:", self.supply_len)
        self.return_len = QDoubleSpinBox()
        self.return_len.setRange(0, 100)
        self.return_len.setSingleStep(0.5)
        self.return_len.setSuffix(" m")
        duct_layout.addRow("Return Duct:", self.return_len)
        detail_layout.addWidget(duct_group)

        fittings_group = QGroupBox("Fittings")
        fittings_layout = QGridLayout(fittings_group)
        labels = [
            ("Elbow 90°", "elbow90"),
            ("Elbow 45°", "elbow45"),
            ("Tee", "tee"),
            ("Reducer", "reducer"),
            ("Transition", "transition"),
        ]
        for i, (label, attr) in enumerate(labels):
            fittings_layout.addWidget(QLabel(label + ":"), i // 2, (i % 2) * 2)
            spin = QSpinBox()
            spin.setRange(0, 50)
            setattr(self, attr, spin)
            fittings_layout.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        detail_layout.addWidget(fittings_group)

        accessories_group = QGroupBox("Accessories")
        accessories_layout = QVBoxLayout(accessories_group)
        self.acc_checks = {}
        for key, value in ACCESSORIES_PRESSURE.items():
            checkbox = QCheckBox(f"{key.replace('_', ' ').title()} ({value:.2f} in.wg)")
            self.acc_checks[key] = checkbox
            accessories_layout.addWidget(checkbox)
        for key in ["filter_standard", "cooling_coil", "diffuser", "grille_return"]:
            self.acc_checks[key].setChecked(True)
        detail_layout.addWidget(accessories_group)

        custom_group = QGroupBox("Custom Additions")
        custom_layout = QFormLayout(custom_group)
        self.custom = QDoubleSpinBox()
        self.custom.setRange(0, 1.0)
        self.custom.setSingleStep(0.01)
        self.custom.setDecimals(3)
        self.custom.setSuffix(" in.wg")
        custom_layout.addRow("Additional ESP:", self.custom)
        detail_layout.addWidget(custom_group)
        detail_layout.addStretch()
        tabs.addTab(tab_detail, "📐 Detailed Calculation")

        root.addWidget(tabs)

        result_group = QGroupBox("Result")
        result_layout = QVBoxLayout(result_group)
        self.result_label = QLabel("Click Calculate")
        self.result_label.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #34d399; "
            "padding: 12px; background: #0b1220; border-radius: 6px;"
        )
        self.result_label.setAlignment(Qt.AlignCenter)
        result_layout.addWidget(self.result_label)
        self.breakdown_view = QTextEdit()
        self.breakdown_view.setReadOnly(True)
        self.breakdown_view.setMaximumHeight(150)
        result_layout.addWidget(self.breakdown_view)
        root.addWidget(result_group)

        button_row = QHBoxLayout()
        calculate_button = QPushButton("🧮 Calculate")
        calculate_button.clicked.connect(self.calculate)
        button_row.addWidget(calculate_button)
        self.apply_btn = QPushButton("✅ Apply")
        self.apply_btn.clicked.connect(self.accept)
        self.apply_btn.setEnabled(False)
        button_row.addWidget(self.apply_btn)
        cancel_button = QPushButton("❌ Cancel")
        cancel_button.setStyleSheet("background: #6b7280;")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        root.addLayout(button_row)

    def apply_preset(self):
        preset = ESP_PRESETS.get(self.preset_combo.currentText(), {})
        if preset:
            self.preset_info.setText(
                f"Recommended range: {preset['range'][0]} - {preset['range'][1]} in.wg\n{preset['desc']}"
            )

    def suggest_from_cfm(self):
        if self.initial_cfm > 0:
            esp = suggest_esp_from_cfm(self.initial_cfm)
            if esp > 0.15:
                self.supply_len.setValue(8.0)
                self.return_len.setValue(4.0)
            elif esp > 0.10:
                self.supply_len.setValue(5.0)
                self.return_len.setValue(3.0)
            else:
                self.supply_len.setValue(3.0)
                self.return_len.setValue(2.0)

    def calculate(self):
        accessories = [k for k, cb in self.acc_checks.items() if cb.isChecked()]
        data = ESPInput(
            supply_duct_length_m=self.supply_len.value(),
            return_duct_length_m=self.return_len.value(),
            elbow_90_count=self.elbow90.value(),
            elbow_45_count=self.elbow45.value(),
            tee_count=self.tee.value(),
            reducer_count=self.reducer.value(),
            transition_count=self.transition.value(),
            accessories=accessories,
            custom_additions=self.custom.value(),
        )
        result = calculate_esp(data)
        self.result_esp = result.total
        self.result_label.setText(f"ESP = {result.total:.3f} in.wg")

        warnings = []
        if result.total > 2.0:
            warnings.append(
                "⚠️ ESP > 2.0 in.wg — very high static pressure! Check duct sizing."
            )
        elif result.total > 0.40:
            warnings.append(
                "ℹ️ ESP > 0.40 in.wg: Exceeds standard split limits; supported by Carrier Rooftops (up to 2.0 in.wg)."
            )
        elif result.total > 0.32:
            warnings.append("⚠️ High ESP — verify selected unit can handle it.")
        elif result.total < 0.05:
            warnings.append("⚠️ ESP is very low — verify the ductwork data.")

        text = "📊 Calculation Breakdown:\n\n"
        for key, value in result.breakdown.items():
            text += f"  • {key:25s}: {value:.3f} in.wg\n"
        text += f"\n  {'─' * 40}\n  {'Total':25s}: {result.total:.3f} in.wg"
        if warnings:
            text += "\n\n" + "\n".join(warnings)
        self.breakdown_view.setPlainText(text)
        self.apply_btn.setEnabled(True)

    def get_esp(self):
        return self.result_esp


class ExtractorWorker(QThread):
    finished = Signal(str, dict)
    finished_batch = Signal(str)  # raw text for multi-system reports
    error = Signal(str)
    progress = Signal(int)

    def __init__(self, path, extractor, batch=False):
        super().__init__()
        self.path = path
        self.extractor = extractor
        self.batch = batch

    def run(self):
        try:
            self.progress.emit(20)
            text = self.extractor.extract(self.path)
            self.progress.emit(70)
            if self.batch:
                self.progress.emit(100)
                self.finished_batch.emit(text)
                return
            parsed = parse_hap_detailed(text)
            total, sens, cfm, heat, esp = (
                parsed["total_btu"],
                parsed["sensible_btu"],
                parsed["cfm"],
                parsed["heating_btu"],
                parsed["esp"],
            )
            self.progress.emit(100)
            self.finished.emit(
                text,
                {
                    "total_btu": total,
                    "sensible_btu": sens,
                    "cfm": cfm,
                    "heating_btu": heat,
                    "esp": esp,
                    "floor_area": 0,
                    "project_name": "",
                    "warnings": parsed["warnings"],
                    "critical_errors": parsed["critical_errors"],
                },
            )
        except (OSError, ValueError, TypeError, KeyError, RuntimeError,
                ImportError) as e:
            logging.getLogger(__name__).exception("Input processing failed")
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════
#                        MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════


def startup_diagnostics():
    """Return human-readable dependency warnings without blocking startup."""
    warnings = []
    for module in ("PySide6", "sqlite3"):
        try:
            if importlib.util.find_spec(module) is None:
                warnings.append(f"Required dependency {module} is unavailable.")
        except (ImportError, ValueError):
            warnings.append(f"Required dependency {module} is unavailable.")

    checks = (
        ("PyVista/VTK 3D room view", ("pyvista", "vtk"), True),
        ("PyMuPDF PDF extraction", ("pymupdf", "fitz"), False),
        ("pytesseract OCR", ("pytesseract",), True),
    )
    for label, modules, require_all in checks:
        states = []
        for module in modules:
            try:
                states.append(importlib.util.find_spec(module) is not None)
            except (ImportError, ValueError):
                states.append(False)
        available = all(states) if require_all else any(states)
        if not available:
            warnings.append(f"{label} is unavailable; related features are disabled.")
    if shutil.which("tesseract") is None:
        warnings.append(
            "Tesseract executable is unavailable; Tesseract OCR cannot run."
        )
    if not DB.is_file():
        warnings.append(f"Equipment database not found: {DB}")
    for warning in warnings:
        logging.getLogger(__name__).warning("Startup diagnostic: %s", warning)
    return warnings


STYLE = """
QWidget{background:#111827;color:#e5e7eb;font-family:Segoe UI;font-size:13px}
QMainWindow{background:#111827}
QGroupBox{border:1px solid #374151;border-radius:6px;margin-top:10px;padding:8px;color:#93c5fd;font-weight:bold}
QGroupBox::title{left:10px;padding:0 6px;background:#111827}
QPushButton{background:#2563eb;color:white;border:0;border-radius:5px;padding:8px 14px;font-weight:bold}
QPushButton:hover{background:#3b82f6}
QPushButton:disabled{background:#374151;color:#6b7280}
QLineEdit,QDoubleSpinBox,QComboBox{background:#1f2937;color:#f9fafb;border:1px solid #4b5563;border-radius:4px;padding:6px}
QTableWidget{background:#0f172a;color:#e5e7eb;gridline-color:#374151}
QHeaderView::section{background:#1f2937;color:#93c5fd;padding:7px;font-weight:bold}
QTextEdit{background:#0b1220;color:#86efac}
QTabWidget::pane{border:1px solid #374151}
QTabBar::tab{background:#1f2937;color:#9ca3af;padding:8px 14px}
QTabBar::tab:selected{background:#2563eb;color:white}
QCheckBox{color:#e5e7eb}
"""


class App(QMainWindow):
    # Display label -> table-name prefix. '' = the original QDMT tables
    # (equipment_model, cooling_performance, ...) built by build_db.py from
    # hvac_catalog.xlsx. 'miraco_' = the miraco_* tables built by
    # miraco_loader.py from Carrier_Miraco_HVAC_Selection_Database_v3.xlsx.
    CATALOGS = (
        ("All Catalogs", None),
        ("QDMT Duct Split (12–60 kBTU)", ""),
        ("Miraco 50UB/38BB/40AB (55–300 MBH)", "miraco_"),
        ("Carrier Rooftops 50TFF/50TJS/50TJ (5–25 Ton)", "rooftop_"),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Duct DX Equipment Selector - HAP Analyzer")
        self.resize(1500, 900)
        self.setStyleSheet(ui_shell.THEME)

        if not DB.exists():
            print(f"❌ DB not found: {DB}")
            print("Run: python build_db.py")
            sys.exit(1)

        try:
            self.conn = sqlite3.connect(DB)
        except sqlite3.Error as e:
            print(f"❌ DB error: {e}")
            sys.exit(1)

        self.ocr = None
        self.raw = ""
        self.selected_row = -1
        self._speed_cache = {}
        # ── [NEW] Selection Cart ──
        self.cart = {}  # {system_label: {model, catalog, loads...}}
        self._auto_system_n = 0
        self._updating_cart = False
        self._project_path = None
        self._project_dirty = False
        self._project_loading = False
        # ── [NEW] Batch systems state ──
        self.systems = []  # list of dicts from parse_all_systems()
        self._current_system_idx = -1
        self._updating_systems = False
        self.build()
        self.search()
        startup_warnings = startup_diagnostics()
        if startup_warnings:
            self.debug.setPlainText(
                "Startup diagnostics:\n" + "\n".join(startup_warnings)
            )
            self.status.setText(
                "Optional components unavailable: " + "; ".join(startup_warnings)
            )
            self.status.setStyleSheet("color: #fbbf24;")

    def build(self):
        ui_shell.build_shell(self)

    def _refresh_shell(self):
        ui_shell.refresh_shell(self)

    def _project_settings(self):
        return {
            "diffuser_application": self.diff_app_combo.currentText(),
            "diffuser_type": self.diff_type_combo.currentText(),
            "diffuser_max_nc": float(self.diff_max_nc.value()),
            "diffuser_max_dp_inwg": float(self.diff_max_dp.value()),
            "energy_hours_per_year": int(self.hours_per_year.value()),
            "energy_tariff": self.tariff.text(),
            "energy_currency": self.currency_sym.text(),
            "diffuser_room_width_ft": float(self.diff_width.value()),
            "diffuser_room_length_ft": float(self.diff_length.value()),
            "diffuser_ceiling_height_ft": float(self.diff_height.value()),
        }

    def _project_selection_inputs(self):
        return {
            "total_btu": self.btu.text(),
            "sensible_btu": self.sens.text(),
            "cfm": self.cfm.text(),
            "heating_btu": self.heat.text(),
            "esp": self.esp.text(),
            "max_sound": self.max_sound.text(),
            "ceiling_void_mm": self.ceiling_void.text(),
            "ceiling_allowance_mm": self.ceiling_clear.text(),
            "drain_run_m": self.drain_run.text(),
            "catalog_family": self.catalog_family.currentText(),
            "phase": self.phase.currentText(),
            "outdoor_temperature": self.outdoor_temp.currentText(),
            "fan_speed": self.fan_speed.currentText(),
            "ceiling_enforce": self.ceiling_enforce.isChecked(),
            "capacity_margin_percent": float(self.capm.value()),
            "airflow_margin_percent": float(self.afm.value()),
            "sensible_margin_percent": float(self.safm.value()),
            "floor_area_ft2": float(getattr(self, "_current_floor_area", 0.0)),
        }

    def _mark_project_dirty(self):
        if self._project_loading:
            return
        self._project_dirty = True
        path_name = Path(self._project_path).name if self._project_path else "Untitled"
        self.setWindowTitle(f"{path_name} * — Duct DX Equipment Selector")

    def _on_energy_setting_changed(self, *_):
        self._mark_project_dirty()
        self._recompute_cart_costs()

    def _confirm_project_discard(self):
        if not self._project_dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved Project Changes",
            "Save changes to the current project before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save_project()
        return answer == QMessageBox.Discard

    def save_project(self, path=None, *, save_as=False):
        """Save the current equipment cart and room layouts to a project file."""
        target = path or (None if save_as else self._project_path)
        if target is None:
            target, _ = QFileDialog.getSaveFileName(
                self, "Save Duct DX Project", "untitled.ductdx",
                "Duct DX Project (*.ductdx);;JSON Files (*.json)")
            if not target:
                return False
        target = Path(target)
        if not target.suffix:
            target = target.with_suffix(".ductdx")
        if target.exists() and (
                save_as or self._project_path is None
                or target.resolve() != Path(self._project_path).resolve()):
            answer = QMessageBox.question(
                self, "Replace Existing Project?",
                f"The file already exists:\n{target}\n\nReplace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        try:
            document = create_project_document(
                self.cart, self._project_settings(), self.systems,
                project_name=target.stem,
                selection_inputs=self._project_selection_inputs())
            write_project_file(target, document)
        except (ProjectFileError, OSError) as exc:
            QMessageBox.critical(self, "Project Save Failed", str(exc))
            return False
        self._project_path = str(target)
        self._project_dirty = False
        self.setWindowTitle(f"{target.name} — Duct DX Equipment Selector")
        self.status.setText(f"Project saved: {target}")
        self.status.setStyleSheet("color: #34d399;")
        return True

    def open_project(self, path=None):
        """Validate a project fully before replacing the active application state."""
        source = path
        if source is None:
            source, _ = QFileDialog.getOpenFileName(
                self, "Open Duct DX Project", "",
                "Duct DX Project (*.ductdx);;JSON Files (*.json)")
            if not source:
                return False
        try:
            document = read_project_file(source)
            cart, systems, settings, selection_inputs = (
                restore_project_state(document))
        except (ProjectFileError, OSError) as exc:
            QMessageBox.critical(self, "Project Open Failed", str(exc))
            return False
        try:
            self._validate_project_settings(settings)
            self._validate_project_selection_inputs(selection_inputs)
        except ProjectFileError as exc:
            QMessageBox.critical(self, "Project Open Failed", str(exc))
            return False
        if not self._confirm_project_discard():
            return False

        previous = {
            "cart": self.cart,
            "systems": self.systems,
            "raw": self.raw,
            "loaded_path": self._loaded_path,
            "path": self._project_path,
            "dirty": self._project_dirty,
            "auto_system_n": self._auto_system_n,
            "current_system_idx": self._current_system_idx,
            "floor_area_ft2": getattr(self, "_current_floor_area", 0.0),
            "settings": self._project_settings(),
            "selection_inputs": self._project_selection_inputs(),
        }
        self._project_loading = True
        apply_error = None
        rollback_error = None
        try:
            self.cart = cart
            self.systems = systems
            self.raw = ""
            self._loaded_path = None
            self._project_path = str(source)
            self._auto_system_n = 0
            self._current_system_idx = -1
            self._apply_project_settings(settings)
            self._apply_project_selection_inputs(selection_inputs)
            self._project_dirty = False
            self._render_systems_table()
            self._render_cart()
            self._render_diffusers_summary_table()
            self._refresh_shell()
        except (ProjectFileError, OSError, ValueError, TypeError, KeyError,
                RuntimeError, sqlite3.Error) as exc:
            apply_error = exc
            self.cart = previous["cart"]
            self.systems = previous["systems"]
            self.raw = previous["raw"]
            self._loaded_path = previous["loaded_path"]
            self._project_path = previous["path"]
            self._project_dirty = previous["dirty"]
            self._auto_system_n = previous["auto_system_n"]
            self._current_system_idx = previous["current_system_idx"]
            self._current_floor_area = previous["floor_area_ft2"]
            try:
                self._apply_project_settings(previous["settings"])
                self._apply_project_selection_inputs(
                    previous["selection_inputs"])
                self._render_systems_table()
                self._render_cart()
                self._render_diffusers_summary_table()
                self._refresh_shell()
            except (ProjectFileError, OSError, ValueError, TypeError, KeyError,
                    RuntimeError, sqlite3.Error) as restore_exc:
                rollback_error = restore_exc
        finally:
            self._project_loading = False
        if apply_error is not None:
            message = f"Could not apply project: {apply_error}"
            if rollback_error is not None:
                message += (
                    "\nThe previous project could not be fully redrawn: "
                    f"{rollback_error}")
            QMessageBox.critical(self, "Project Open Failed", message)
            return False
        self.setWindowTitle(f"{Path(source).name} — Duct DX Equipment Selector")
        warnings = [
            f"{name}: " + "; ".join(
                item["cad_room_data"].get("layout_validation_errors", []))
            for name, item in cart.items()
            if item.get("cad_room_data", {}).get("layout_validation_errors")
        ]
        if warnings:
            QMessageBox.warning(
                self, "Project Opened with Layout Warnings",
                "Saved diffuser coordinates were preserved and require review:\n\n"
                + "\n".join(warnings))
            self.status.setText("Project opened; one or more diffuser layouts need review.")
            self.status.setStyleSheet("color: #fbbf24;")
        else:
            self.status.setText(f"Project opened: {source}")
            self.status.setStyleSheet("color: #34d399;")
        return True

    def _validate_project_settings(self, settings):
        for key, widget in (
                ("diffuser_application", self.diff_app_combo),
                ("diffuser_type", self.diff_type_combo)):
            if widget.findText(settings[key]) < 0:
                raise ProjectFileError(
                    f"Unsupported project setting {key}: {settings[key]!r}.")
        for key, widget in (
                ("diffuser_max_nc", self.diff_max_nc),
                ("diffuser_max_dp_inwg", self.diff_max_dp),
                ("energy_hours_per_year", self.hours_per_year),
                ("diffuser_room_width_ft", self.diff_width),
                ("diffuser_room_length_ft", self.diff_length),
                ("diffuser_ceiling_height_ft", self.diff_height)):
            if not widget.minimum() <= settings[key] <= widget.maximum():
                raise ProjectFileError(
                    f"Project setting {key} is outside the supported range.")

    def _apply_project_settings(self, settings):
        self._validate_project_settings(settings)
        bindings = (
            ("diffuser_application", self.diff_app_combo, "text"),
            ("diffuser_type", self.diff_type_combo, "text"),
            ("diffuser_max_nc", self.diff_max_nc, "value"),
            ("diffuser_max_dp_inwg", self.diff_max_dp, "value"),
            ("energy_hours_per_year", self.hours_per_year, "value"),
            ("energy_tariff", self.tariff, "text"),
            ("energy_currency", self.currency_sym, "text"),
            ("diffuser_room_width_ft", self.diff_width, "value"),
            ("diffuser_room_length_ft", self.diff_length, "value"),
            ("diffuser_ceiling_height_ft", self.diff_height, "value"),
        )
        for key, widget, setter_kind in bindings:
            if key not in settings:
                continue
            widget.blockSignals(True)
            try:
                if setter_kind == "text":
                    setter = getattr(
                        widget, "setCurrentText", None) or widget.setText
                    setter(str(settings[key]))
                elif setter_kind == "value":
                    widget.setValue(settings[key])
            finally:
                widget.blockSignals(False)

    def _validate_project_selection_inputs(self, inputs):
        for key, widget in (
                ("catalog_family", self.catalog_family),
                ("phase", self.phase),
                ("outdoor_temperature", self.outdoor_temp),
                ("fan_speed", self.fan_speed)):
            if widget.findText(inputs[key]) < 0:
                raise ProjectFileError(
                    f"Unsupported project input {key}: {inputs[key]!r}.")
        for key, widget in (
                ("capacity_margin_percent", self.capm),
                ("airflow_margin_percent", self.afm),
                ("sensible_margin_percent", self.safm)):
            if not widget.minimum() <= inputs[key] <= widget.maximum():
                raise ProjectFileError(
                    f"Project input {key} is outside the supported range.")
    def _apply_project_selection_inputs(self, inputs):
        self._validate_project_selection_inputs(inputs)
        bindings = (
            ("total_btu", self.btu, "text"),
            ("sensible_btu", self.sens, "text"),
            ("cfm", self.cfm, "text"),
            ("heating_btu", self.heat, "text"),
            ("esp", self.esp, "text"),
            ("max_sound", self.max_sound, "text"),
            ("ceiling_void_mm", self.ceiling_void, "text"),
            ("ceiling_allowance_mm", self.ceiling_clear, "text"),
            ("drain_run_m", self.drain_run, "text"),
            ("catalog_family", self.catalog_family, "text"),
            ("phase", self.phase, "text"),
            ("outdoor_temperature", self.outdoor_temp, "text"),
            ("fan_speed", self.fan_speed, "text"),
            ("capacity_margin_percent", self.capm, "value"),
            ("airflow_margin_percent", self.afm, "value"),
            ("sensible_margin_percent", self.safm, "value"),
        )
        for key, widget, setter_kind in bindings:
            widget.blockSignals(True)
            try:
                if setter_kind == "text":
                    setter = getattr(
                        widget, "setCurrentText", None) or widget.setText
                    setter(str(inputs[key]))
                else:
                    widget.setValue(inputs[key])
            finally:
                widget.blockSignals(False)
        self.ceiling_enforce.blockSignals(True)
        try:
            self.ceiling_enforce.setChecked(inputs["ceiling_enforce"])
        finally:
            self.ceiling_enforce.blockSignals(False)
        self._current_floor_area = inputs["floor_area_ft2"]

    def closeEvent(self, event):
        if self._confirm_project_discard():
            event.accept()
        else:
            event.ignore()

    def field(self, layout, label, value=""):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        x = QLineEdit(value)
        x.setPlaceholderText("Enter value")
        row.addWidget(x)
        layout.addLayout(row)
        return x

    def spin(self, layout, label, lo, hi, val):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        x = QDoubleSpinBox()
        x.setRange(lo, hi)
        x.setValue(val)
        x.setSuffix(" %")
        row.addWidget(x)
        layout.addLayout(row)
        return x

    def combo(self, layout, label, items):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        x = QComboBox()
        x.addItems(items)
        row.addWidget(x)
        layout.addLayout(row)
        return x

    def open_esp_calculator(self):
        try:
            cfm_val = float(self.cfm.text().replace(",", "") or 0)
        except (TypeError, ValueError):
            cfm_val = 0.0
        dlg = ESPDialog(self, initial_cfm=cfm_val)
        if dlg.exec():
            esp = dlg.get_esp()
            if esp is not None:
                self.esp.setText(f"{esp:.3f}")
                self.status.setText(f"✅ Calculated ESP: {esp:.3f} in.wg")
                self.status.setStyleSheet("color: #34d399;")
                if esp > 0.40:
                    QMessageBox.warning(
                        self,
                        "High ESP",
                        f"ESP = {esp:.3f} in.wg\n\nMaximum for Duct DX = 0.40 in.wg\n\nConsider an AHU or FCU.",
                    )

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select HAP Report",
            "",
            "Reports (*.rtf *.RTF *.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
            "*.bmp *.tif *.tiff)",
        )
        if path:
            self._start_extraction(path, batch=False)

    def _start_extraction(self, path, batch):
        """Route the file to the right engine by type (RTF/PDF/image)."""
        ext = Path(path).suffix.lower()
        if ext not in (
            ".rtf",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".tif",
            ".tiff",
        ):
            QMessageBox.warning(self, "Unsupported file", ui_shell.unsupported_text())
            return
        preferred = self.extractor_combo.currentText()
        engine_name, extractor = pick_extractor(path, self._extractor_map, preferred)
        if extractor is None:
            QMessageBox.warning(
                self,
                "No engine",
                "No available extraction engine can read this file type.\n\n"
                "Supported formats:\nRTF, PDF, PNG, JPG, JPEG",
            )
            return
        self._loaded_path = path
        self.hap_path.setText(path)
        ui_shell.set_preview(self, path)
        self.upload.setEnabled(False)
        self.batch_upload_btn.setEnabled(False)
        self.manual_btn.setEnabled(False)
        self.status.setText(f"⟳ Parsing HAP report ({engine_name})...")
        self.status.setStyleSheet("")
        self.ocr = ExtractorWorker(path, extractor, batch=batch)
        self.ocr.progress.connect(
            lambda v: self.status.setText(f"⟳ Parsing HAP report... {v}%")
        )
        if batch:
            self.ocr.finished_batch.connect(self._on_batch_extract_done)
            self.ocr.error.connect(self._on_batch_error)
        else:
            self.ocr.finished.connect(self._on_extract_done)
            self.ocr.error.connect(self.ocr_fail)
        self.ocr.start()

    def open_manual_input(self):
        dlg = ManualInputDialog(self)
        if dlg.exec():
            result = dlg.get_result()
            if result:
                self.debug.setPlainText("Manual input / pasted text (no OCR).")
                self.raw = ""
                self._apply_result(result)

    def _on_extract_done(self, text, result):
        self.raw = text
        self.debug.setPlainText(text)
        self._apply_result(result)

    def _apply_result(self, result):
        self._current_floor_area = float(
            result.get("floor_area", 0) or result.get("floor_area_ft2", 0) or 0.0
        )
        b = result.get("total_btu", 0)
        s = result.get("sensible_btu", 0)
        c = result.get("cfm", 0)
        h = result.get("heating_btu", 0)
        e = result.get("esp")

        if b:
            self.btu.setText(f"{b:.0f}")
        if s:
            self.sens.setText(f"{s:.0f}")
        if c:
            self.cfm.setText(f"{c:.0f}")
        if h:
            self.heat.setText(f"{h:.0f}")

        if e is not None and e > 0.001:
            self.esp.setText(f"{e:.3f}")
            esp_note = f"ESP = {e:.3f} in.wg (from HAP)"
        else:
            suggested = suggest_esp_from_cfm(
                c if c else 400, "Office / Commercial Shop"
            )
            self.esp.setText(f"{suggested:.2f}")
            esp_note = (
                f"HAP did not specify ESP (0.00) | "
                f"Suggested {suggested:.2f} in.wg from CFM={c:.0f}"
            )

        warnings = []
        if b > 200000:
            warnings.append(f"Total ({b:,.0f}) very high")
        if c > 5000:
            warnings.append(f"Airflow ({c:,.0f}) very high")
        if s > b and s > 0:
            warnings.append("Sensible > Total")

        msg = f"Loads: {b:,.0f} Btu/hr | {s:,.0f} Sensible | {c:,.0f} CFM\n{esp_note}"
        validation = result.get("warnings", []) + result.get("critical_errors", [])
        if validation:
            msg += "\n" + "\n".join(validation)
        if warnings:
            msg += "\n" + " | ".join(warnings)

        self.upload.setEnabled(True)
        if hasattr(self, "manual_btn"):
            self.manual_btn.setEnabled(True)
        self.status.setText(msg)
        self.status.setStyleSheet(
            "color: #fbbf24;"
            if validation or e is None or e <= 0.001
            else "color: #34d399;"
        )
        self._refresh_shell()

    def ocr_fail(self, msg):
        # Re-enable BOTH action buttons: the previous code only restored
        # `upload`, which left the manual-input button permanently
        # disabled after a single OCR failure.
        self.upload.setEnabled(True)
        if hasattr(self, "manual_btn"):
            self.manual_btn.setEnabled(True)
        self.status.setText("❌ OCR error: " + msg)
        self.status.setStyleSheet("color: #fca5a5;")
        QMessageBox.warning(
            self,
            "Unable to process file",
            ui_shell.friendly_error(msg, getattr(self, "_loaded_path", "")),
        )

    # ── Ceiling void / site specs helpers ─────────────────────
    def _ceiling_params(self):
        def num(w, default=0.0):
            try:
                return float((w.text() or "").replace(",", "").strip() or default)
            except (TypeError, ValueError):
                return default

        p = {
            "void": num(self.ceiling_void, 0.0),
            "clearance": num(self.ceiling_clear, report_builder.DEFAULT_CLEARANCE_MM),
            "drain_run": num(self.drain_run, 0.0),
        }
        report_builder.set_ceiling_params(**p)  # shared with the Word reports
        return p

    def _site_for(self, prefix, model):
        """(spec, ceiling_check) for one model name; handles combos."""
        parts = report_builder.split_combo_model(model) or [(model, 1)]
        spec = report_builder.get_site_specs(self.conn, DB, prefix or "", parts[0][0])
        p = self._ceiling_params()
        ck = report_builder.check_ceiling_void(
            spec["height"], p["void"], p["clearance"], p["drain_run"]
        )
        return spec, ck

    def search(self):
        try:
            b = float(self.btu.text().replace(",", "") or 0)
            s = float(self.sens.text().replace(",", "") or 0)
            c = float(self.cfm.text().replace(",", "") or 0)
            esp = float(self.esp.text().replace(",", "") or 0)
            h = float(self.heat.text().replace(",", "") or 0)
        except (TypeError, ValueError):
            self.status.setText("❌ Enter valid numeric values.")
            return

        if b <= 0 and c <= 0:
            self.table.clearContents()
            self.table.setRowCount(0)
            self.export.setEnabled(False)
            self.summary.setText("Waiting for valid HAP load and airflow values.")
            return

        reqb = b * (1 + self.capm.value() / 100)
        reqs = s * (1 + self.safm.value() / 100) if s > 0 else 0
        reqc = c * (1 + self.afm.value() / 100)
        outdoor = int(self.outdoor_temp.currentText())
        fan_pref = self.fan_speed.currentText()
        phase_filter = self.phase.currentText()
        max_sound = float(self.max_sound.text() or 0)

        db_outdoor = (
            35
            if outdoor <= 35
            else (43 if outdoor <= 43 else (46 if outdoor <= 46 else 52))
        )

        family_label = self.catalog_family.currentText()
        prefixes = next((p for lbl, p in self.CATALOGS if lbl == family_label), None)
        if prefixes is None:  # 'Both Catalogs'
            active = [(lbl, p) for lbl, p in self.CATALOGS if p is not None]
        else:
            active = [(family_label, prefixes)]

        rows = []
        catalog_warnings = []
        for catalog_label, prefix in active:
            try:
                rows.extend(
                    self._search_family(
                        prefix,
                        catalog_label,
                        reqb,
                        reqs,
                        reqc,
                        esp,
                        h,
                        fan_pref,
                        phase_filter,
                        db_outdoor,
                        max_sound,
                    )
                )
            except sqlite3.OperationalError as exc:
                message = f"{catalog_label} catalog unavailable: {exc}"
                logging.getLogger(__name__).warning(message)
                catalog_warnings.append(message)

        rows.sort(key=lambda x: (not x[0], x[1]))
        best_only = self.best_match_only.isChecked()
        self.render(rows, reqb, reqs, reqc, esp, fan_pref, outdoor, best_only)
        if catalog_warnings:
            self.status.setText("; ".join(catalog_warnings))
            self.status.setStyleSheet("color: #fbbf24;")
        if self.cart:  # ceiling inputs may have changed -> refresh cart checks
            self._render_cart()

    def _available_speeds(self, table_name, equipment_model_id):
        """Cache distinct fan speeds per table and equipment model."""
        cache_key = (table_name, equipment_model_id)
        if cache_key not in self._speed_cache:
            self._speed_cache[cache_key] = {
                row[0]
                for row in self.conn.execute(
                    f"""SELECT DISTINCT fan_speed FROM {table_name}
                        WHERE equipment_model_id=? AND fan_speed IS NOT NULL""",
                    (equipment_model_id,),
                )
                if row[0]
            }
        return self._speed_cache[cache_key]

    def _resolve_fan_speed(
        self, airflow_table, cooling_table, equipment_model_id, requested_speed
    ):
        """Resolve the requested fan speed against the published catalog speeds.

        Some catalogs, including Carrier Miraco, publish only ``High``. If the
        user requests ``Medium`` or ``Low`` and that speed is unavailable, use
        ``High`` instead of treating the model as having zero airflow/capacity.
        Returns ``(actual_speed, fallback_reason)``.
        """
        requested = requested_speed if requested_speed != "Any" else "High"
        available = self._available_speeds(
            airflow_table, equipment_model_id
        ) | self._available_speeds(cooling_table, equipment_model_id)

        if not available:
            return None, f"fan speed {requested} unavailable in catalog"
        if requested in available:
            return requested, ""
        if "High" in available:
            return (
                "High",
                f"fan fallback {requested} → High (requested speed not published)",
            )

        for candidate in ("Medium", "Low"):
            if candidate in available:
                return candidate, f"fan fallback {requested} → {candidate}"
        actual = min(available)
        return actual, f"fan fallback {requested} → {actual}"

    def _anomaly_filter(self, table_name):
        """Return a compatible SQL filter for optional is_anomaly columns."""
        try:
            columns = {
                row[1]
                for row in self.conn.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }
        except sqlite3.OperationalError:
            return "", False
        if "is_anomaly" in columns:
            return " AND COALESCE(is_anomaly, 0)=0", True
        return "", False

    def _search_family(
        self,
        prefix,
        catalog_label,
        reqb,
        reqs,
        reqc,
        esp,
        h,
        fan_pref,
        phase_filter,
        db_outdoor,
        max_sound,
    ):
        """Runs the selection query against one table set (prefix ''
        or 'miraco_') and returns rows in the same shape search() used
        to build directly, plus a trailing catalog_label for display
        and Word-report table lookup."""
        t_eq = f"{prefix}equipment_model"
        t_cool = f"{prefix}cooling_performance"
        t_air = f"{prefix}airflow_point"
        t_sound = f"{prefix}sound_level"
        t_heat = f"{prefix}heating_performance"
        cap_margin = 1 + self.capm.value() / 100
        reqh = h * cap_margin if h > 0 else 0
        anomaly_filter, has_anomaly_flag = self._anomaly_filter(t_cool)
        # Conservative lookup: prefer the first catalog point AT OR ABOVE
        # the design value (higher ESP / hotter outdoor air = less
        # capacity and airflow); only fall back to the nearest point below
        # when the catalog has nothing above.
        EPS = 1e-9

        rows = []
        models = self.conn.execute(f"""
            SELECT id, system_model, nominal_class_kbtu, rated_cooling_btuh,
                   max_esp_inwg, power_supply, indoor_model, outdoor_model,
                   indoor_dimensions_mm, eer_btu_wh
            FROM {t_eq} ORDER BY nominal_class_kbtu
        """).fetchall()

        for (
            mid,
            model,
            klass,
            rated_cap,
            maxesp,
            power,
            indoor,
            outdoor_model,
            dims,
            eer,
        ) in models:
            if phase_filter != "Any":
                if "1-Phase" in phase_filter and not (power or "").startswith("220"):
                    continue
                if "3-Phase" in phase_filter and not (power or "").startswith("380"):
                    continue

            fan_to_use, fan_fallback_reason = self._resolve_fan_speed(
                t_air, t_cool, mid, fan_pref
            )
            if fan_to_use is None:
                airflow_point_cfm = (
                    float(flow_row[1])
                    if flow_row and flow_row[1] is not None else None)
                if airflow_point_cfm is not None and airflow_point_cfm <= 0:
                    airflow_point_cfm = None
                airflow_metadata = {
                    "equipment_model_id": mid,
                    "model": model,
                    "catalog": catalog_label,
                    "catalog_prefix": prefix,
                    "fan_speed_requested": fan_pref,
                    "fan_speed_resolved": None,
                    "airflow_cfm": None,
                    "airflow_esp_inwg": None,
                    "requested_esp_inwg": esp,
                    "airflow_source": "airflow point unavailable",
                    "airflow_provenance": "unknown_unverified",
                }
                rows.append(
                    (
                        False,
                        9999.0,
                        model,
                        klass,
                        rated_cap,
                        0,
                        0,
                        power,
                        indoor,
                        outdoor_model,
                        dims,
                        0,
                        "—",
                        0,
                        fan_fallback_reason,
                        catalog_label,
                        eer,
                        airflow_metadata,
                    )
                )
                continue

            cap_row = self.conn.execute(
                f"""
                SELECT total_capacity_btuh, sensible_capacity_btuh, esp_inwg
                FROM {t_cool}
                WHERE equipment_model_id=? AND fan_speed=?
                  AND indoor_db_c=27 AND indoor_wb_c=19
                  {anomaly_filter}
                ORDER BY (outdoor_db_c < ?), ABS(outdoor_db_c - ?),
                         (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
            """,
                (mid, fan_to_use, db_outdoor - EPS, db_outdoor, esp - EPS, esp),
            ).fetchone()
            anomaly_excluded = False
            if not cap_row and fan_to_use != "High":
                cap_row = self.conn.execute(
                    f"""
                    SELECT total_capacity_btuh, sensible_capacity_btuh, esp_inwg
                    FROM {t_cool}
                    WHERE equipment_model_id=? AND fan_speed='High'
                      AND indoor_db_c=27 AND indoor_wb_c=19
                      {anomaly_filter}
                    ORDER BY (outdoor_db_c < ?), ABS(outdoor_db_c - ?),
                             (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
                """,
                    (mid, db_outdoor - EPS, db_outdoor, esp - EPS, esp),
                ).fetchone()
                if cap_row:
                    fan_to_use = "High"

            if not cap_row and has_anomaly_flag:
                anomaly_excluded = (
                    self.conn.execute(
                        f"""
                    SELECT 1 FROM {t_cool}
                    WHERE equipment_model_id=? AND fan_speed=?
                      AND indoor_db_c=27 AND indoor_wb_c=19
                      AND COALESCE(is_anomaly, 0)=1
                    ORDER BY (outdoor_db_c < ?), ABS(outdoor_db_c - ?)
                    LIMIT 1
                """,
                        (mid, fan_to_use, db_outdoor - EPS, db_outdoor),
                    ).fetchone()
                    is not None
                )

            cap = cap_row[0] if cap_row else rated_cap
            sens_cap = cap_row[1] if cap_row else 0

            flow_row = self.conn.execute(
                f"""
                SELECT esp_inwg, airflow_cfm FROM {t_air}
                WHERE equipment_model_id=? AND fan_speed=?
                ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
            """,
                (mid, fan_to_use, esp - EPS, esp),
            ).fetchone()
            airflow_metadata = {
                "equipment_model_id": mid,
                "model": model,
                "catalog": catalog_label,
                "catalog_prefix": prefix,
                "fan_speed_requested": fan_pref,
                "fan_speed_resolved": fan_to_use,
                "airflow_cfm": airflow_point_cfm,
                "airflow_esp_inwg": (
                    float(flow_row[0])
                    if flow_row and flow_row[0] is not None else None),
                "requested_esp_inwg": esp,
                "airflow_source": (
                    f"{catalog_label} airflow-point table"
                    if flow_row else "airflow point unavailable"),
                "airflow_provenance": (
                    "unverified_catalog_point"
                    if airflow_point_cfm is not None
                    else "unknown_unverified"),
            }
            flow = airflow_metadata["airflow_cfm"] or 0

            sound_row = self.conn.execute(
                f"""
                SELECT sound_dba FROM {t_sound}
                WHERE equipment_model_id=? AND fan_speed=?
                ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
            """,
                (mid, fan_to_use, esp - EPS, esp),
            ).fetchone()
            sound = sound_row[0] if sound_row else 0

            try:
                heat_row = self.conn.execute(
                    f"""
                    SELECT total_capacity_btuh FROM {t_heat}
                    WHERE equipment_model_id=? AND fan_speed=? AND outdoor_condition=?
                    ORDER BY (esp_inwg < ?), ABS(esp_inwg - ?) LIMIT 1
                """,
                    (mid, fan_to_use, "2/1°C", esp - EPS, esp),
                ).fetchone()
            except sqlite3.Error:
                heat_row = None
            heat_cap = heat_row[0] if heat_row else 0

            reasons = []
            if fan_fallback_reason:
                reasons.append(fan_fallback_reason)
            if anomaly_excluded:
                reasons.append(
                    "cooling value flagged anomaly and excluded from selection"
                )
            if (
                cap_row
                and esp > 0
                and cap_row[2] is not None
                and cap_row[2] < esp - 0.005
            ):
                reasons.append(
                    f"catalog data only to {cap_row[2]:.2f} in.wg "
                    f"(required {esp:.2f}) - verify"
                )
            ok_cap = (cap >= reqb if reqb else True) and not anomaly_excluded
            ok_sens = sens_cap >= reqs if reqs else True
            ok_flow = flow >= reqc if reqc else True
            # max_esp_inwg is NULL for equipment lines that don't publish an
            # external static pressure rating (e.g. Carrier Miraco 50UB/
            # 38BB/40AB) — treat that as "no ESP limit on record" rather
            # than failing every match.
            ok_esp = (maxesp is None) or (maxesp >= esp)
            ok_sound = (sound <= max_sound) if (max_sound > 0 and sound > 0) else True
            ok_heat = (heat_cap >= reqh) if reqh else True

            if not ok_cap and not anomaly_excluded:
                reasons.append(f"capacity short {reqb - cap:,.0f}")
            if not ok_sens:
                reasons.append(f"sensible short {reqs - sens_cap:,.0f}")
            if not ok_flow:
                reasons.append(f"airflow short {reqc - flow:,.0f}")
            if not ok_esp:
                reasons.append(f"ESP max {maxesp:.2f}")
            if not ok_heat:
                reasons.append(f"heating short {reqh - heat_cap:,.0f}")

            # Ceiling void: always warns; only disqualifies when the box is ticked
            ceil_h = (report_builder.parse_dims(dims) or (0, 0, 0))[1]
            _cp = self._ceiling_params()
            ceil_ck = report_builder.check_ceiling_void(
                ceil_h, _cp["void"], _cp["clearance"], _cp["drain_run"]
            )
            ok_ceiling = True
            if ceil_ck["level"] in ("error", "warn"):
                reasons.append(
                    "ceiling void: unit too tall"
                    if ceil_ck["level"] == "error"
                    else "ceiling void: tight (see Ceiling Void Check)"
                )
                if ceil_ck["level"] == "error" and self.ceiling_enforce.isChecked():
                    ok_ceiling = False

            ok = (
                ok_cap
                and ok_sens
                and ok_flow
                and ok_esp
                and ok_sound
                and ok_heat
                and ok_ceiling
            )
            if ok:
                reasons.append("✅ Meets all requirements")

            # Score = how much this unit exceeds the requirement (closer to 0 = better fit).
            # Ranking uses this so the NEAREST suitable unit is chosen first,
            # instead of always picking the largest available unit.
            score = 0.0
            if reqb:
                score += max(0.0, (cap - reqb) / reqb) * 100
            if reqc:
                score += max(0.0, (flow - reqc) / reqc) * 50
            if reqs and sens_cap:
                score += max(0.0, (sens_cap - reqs) / reqs) * 30

            rows.append(
                (
                    ok,
                    score,
                    model,
                    klass,
                    cap,
                    sens_cap,
                    flow,
                    power,
                    indoor,
                    outdoor_model,
                    dims,
                    sound,
                    fan_to_use,
                    heat_cap,
                    "; ".join(reasons),
                    catalog_label,
                    eer,
                    airflow_metadata,
                )
            )

        return rows

    def render(self, rows, reqb, reqs, reqc, esp, fan_pref, outdoor, best_only=False):
        headers = [
            "Status",
            "Catalog",
            "System Model",
            "Class",
            "Cooling (Btu/hr)",
            "Sensible (Btu/hr)",
            "Heating (Btu/hr)",
            "EER",
            "Airflow (CFM)",
            "Sound dB(A)",
            "Power Supply",
            "Indoor Unit",
            "Outdoor Unit",
            "Indoor Dims",
            "Fan Speed",
            "Decision Reason",
        ] + report_builder.SPEC_HEADERS

        # Total suitable count is computed on the FULL list, before any filtering,
        # so the summary line always reflects reality even in Best-Match mode.
        total_suitable = sum(1 for r in rows if r[0])

        # rows is already sorted: all SUITABLE rows first, nearest-fit first
        # (see the sort key in search()). So rows[0] is the best match, if any.
        best_model_name = rows[0][2] if (rows and rows[0][0]) else None

        display_rows = rows
        if best_only:
            display_rows = [rows[0]] if best_model_name else []

        self.table.setRowCount(len(display_rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        for i, row in enumerate(display_rows):
            (
                ok,
                _score,
                model,
                klass,
                cap,
                sens,
                flow,
                power,
                indoor,
                outdoor_model,
                dims,
                sound,
                fan,
                heat_cap,
                reason,
                catalog_label,
                eer,
            ) = row[:17]
            airflow_metadata = row[17] if len(row) > 17 else None

            is_best = ok and (model == best_model_name)
            status_text = (
                "⭐ BEST MATCH" if is_best else ("SUITABLE" if ok else "NOT SUITABLE")
            )

            vals = [
                status_text,
                catalog_label,
                model,
                f"{klass:g}K",
                f"{cap:,.0f}",
                f"{sens:,.0f}" if sens else "—",
                f"{heat_cap:,.0f}" if heat_cap else "—",
                f"{eer:.2f}" if eer else "—",
                f"{flow:,.0f}" if flow else "—",
                f"{sound:.1f}" if sound else "—",
                power,
                indoor,
                outdoor_model,
                dims,
                fan,
                reason,
            ]
            _pref = (
                next((p for lbl, p in self.CATALOGS if lbl == catalog_label), "") or ""
            )
            _spec, _ck = self._site_for(_pref, model)
            vals += report_builder.spec_cells(_spec, _ck)
            for j, val in enumerate(vals):
                cell = QTableWidgetItem(str(val))
                cell.setTextAlignment(Qt.AlignCenter)
                if j == 0 and airflow_metadata is not None:
                    cell.setData(Qt.UserRole, airflow_metadata)
                if is_best:
                    color = "#fbbf24"  # gold — best/nearest fit
                elif ok:
                    color = "#34d399"  # green — suitable
                else:
                    color = "#fca5a5"  # red — not suitable
                if j == len(vals) - 1:  # Ceiling Void Check column
                    color = {
                        "error": "#f87171",
                        "warn": "#fbbf24",
                        "ok": "#34d399",
                    }.get(_ck["level"], "#9ca3af")
                cell.setForeground(QBrush(QColor(color)))
                if (
                    j >= len(vals) - len(report_builder.SPEC_HEADERS)
                    and j != len(vals) - 1
                    and val == "—"
                ):
                    cell.setForeground(QBrush(QColor("#9ca3af")))
                self.table.setItem(i, j, cell)

        self.table.resizeRowsToContents()
        self.export.setEnabled(bool(display_rows))
        if hasattr(self, "add_best_btn"):
            any_ok = any(r[0] for r in display_rows)
            self.add_best_btn.setEnabled(bool(display_rows) and any_ok)

        mode_note = " | Mode: ⭐ Best Match Only" if best_only else ""
        if best_only and not best_model_name:
            mode_note = " | Mode: ⭐ Best Match Only — no suitable unit found"

        self.summary.setText(
            f"✅ {total_suitable} suitable model(s) | "
            f"Required: {reqb:,.0f} Total | {reqs:,.0f} Sensible | "
            f"{reqc:,.0f} CFM | ESP: {esp:.3f} in.wg | "
            f"Outdoor: {outdoor}°C | Fan: {fan_pref}{mode_note}"
        )
        self._refresh_shell()

    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.selected_row = -1
            self.selected_label.setText(
                "ℹ️  Click a row to select a model for the report"
            )
            self.selected_label.setStyleSheet(
                "color: #9ca3af; padding: 6px; font-style: italic;"
            )
            if hasattr(self, "add_to_cart_btn"):
                self.add_to_cart_btn.setEnabled(False)
            return
        self.selected_row = rows[0].row()
        model = self.table.item(self.selected_row, 2).text()
        status = self.table.item(self.selected_row, 0).text()
        color = (
            "#fbbf24"
            if status == "⭐ BEST MATCH"
            else ("#34d399" if status == "SUITABLE" else "#fca5a5")
        )
        self.selected_label.setText("✅ Selected: " + model + " (" + status + ")")
        self.selected_label.setStyleSheet(
            "color: " + color + "; font-weight: bold; padding: 6px;"
        )
        if hasattr(self, "add_to_cart_btn"):
            self.add_to_cart_btn.setEnabled(True)

    def export_report(self):
        fmt = self.export_format.currentText()

        # ═══ Word: detailed report for SELECTED model ONLY ═══
        if fmt.startswith("Word"):
            if self.selected_row < 0:
                QMessageBox.warning(
                    self,
                    "No Selection",
                    "Click on a row in the table to select a model, "
                    "then click Export again.",
                )
                return
            model_name = self.table.item(self.selected_row, 2).text()
            catalog_label = self.table.item(self.selected_row, 1).text()
            table_prefix = (
                next((p for lbl, p in self.CATALOGS if lbl == catalog_label), "") or ""
            )
            status = self.table.item(self.selected_row, 0).text()
            if status == "NOT SUITABLE":
                reply = QMessageBox.question(
                    self,
                    "Not Suitable",
                    "The selected model is NOT SUITABLE for the load."
                    + chr(10)
                    + "Generate report anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Detailed Report",
                "selection_report_" + model_name + ".docx",
                "Word Files (*.docx)",
            )
            if not path:
                return
            try:

                def _num(s):
                    try:
                        return float((s or "").replace(",", "").strip() or 0)
                    except ValueError:
                        return 0.0

                total = _num(self.btu.text())
                sens = _num(self.sens.text())
                cfm = _num(self.cfm.text())
                esp = _num(self.esp.text())
                heat = _num(self.heat.text())
                outdoor = int(self.outdoor_temp.currentText())
                fan = self.fan_speed.currentText()
                if fan == "Any":
                    fan = "High"
                dc = {
                    "total_btu": total,
                    "sensible_btu": sens,
                    "cfm": cfm,
                    "required_cfm": cfm,
                    "design_cfm": cfm,
                    "available_unit_cfm": (
                        (self.table.item(self.selected_row, 0).data(Qt.UserRole)
                         or {}).get("airflow_cfm")
                        if self.table.item(self.selected_row, 0) else None),
                    "airflow_provenance": (
                        (self.table.item(self.selected_row, 0).data(Qt.UserRole)
                         or {}).get("airflow_provenance", "unknown_unverified")
                        if self.table.item(self.selected_row, 0)
                        else "unknown_unverified"),
                    "available_unit_cfm_status": (
                        (self.table.item(self.selected_row, 0).data(Qt.UserRole)
                         or {}).get("airflow_provenance", "unknown_unverified")
                        if self.table.item(self.selected_row, 0)
                        else "unknown_unverified"),
                    "airflow_source": (
                        (self.table.item(self.selected_row, 0).data(Qt.UserRole)
                         or {}).get("airflow_source", "not recorded")
                        if self.table.item(self.selected_row, 0)
                        else "not recorded"),
                    "airflow_esp_inwg": (
                        (self.table.item(self.selected_row, 0).data(Qt.UserRole)
                         or {}).get("airflow_esp_inwg")
                        if self.table.item(self.selected_row, 0) else None),
                    "esp": esp,
                    "heating_btu": heat,
                    "outdoor_db": outdoor,
                    "fan_speed": fan,
                }
                ok, msg = generate_detailed_report(
                    DB,
                    model_name,
                    dc,
                    path,
                    project_name="HVAC Selection",
                    prepared_by="Engineering Dept.",
                    table_prefix=table_prefix,
                )
                if ok:
                    self.status.setText("✅ " + msg)
                    self.status.setStyleSheet("color: #34d399;")
                else:
                    QMessageBox.critical(self, "Error", msg)
            except (OSError, ValueError, TypeError, KeyError, IndexError,
                    RuntimeError, sqlite3.Error, ImportError) as exc:
                logging.getLogger(__name__).exception("Detailed report generation failed")
                QMessageBox.critical(self, "Export Error", str(exc))
            return

        # ═══ All other formats: every row of the results table ═══
        ext, filt = FORMATS.get(fmt, ("csv", "All Files (*.*)"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "equipment_selection." + ext, filt
        )
        if not path:
            return

        headers = [
            self.table.horizontalHeaderItem(i).text()
            for i in range(self.table.columnCount())
        ]
        rows = []
        for r in range(self.table.rowCount()):
            rows.append(
                [
                    self.table.item(r, c).text() if self.table.item(r, c) else ""
                    for c in range(self.table.columnCount())
                ]
            )
        summary = self.summary.text()
        try:
            export_table(path, ext, headers, rows, summary)
            self.status.setText("Exported: " + path)
            self.status.setStyleSheet("color: #34d399;")
        except ImportError:
            QMessageBox.critical(
                self, "Missing Library", "Install: pip install openpyxl"
            )
        except (OSError, ValueError, TypeError, KeyError, csv.Error,
                RuntimeError) as exc:
            logging.getLogger(__name__).exception("Table export failed")
            QMessageBox.critical(self, "Export Error", str(exc))

    # ═══════════════════════════════════════════════════════════
    #  [NEW]  Selection Cart  —  purely additive
    # ═══════════════════════════════════════════════════════════

    def _cart_next_name(self):
        """Suggest a default name: current batch system, else 'System NN'."""
        if self.systems and 0 <= self._current_system_idx < len(self.systems):
            return self.systems[self._current_system_idx]["name"]
        self._auto_system_n += 1
        return f"System {self._auto_system_n:02d}"

    def _add_to_selection(self):
        """Snapshot the currently selected row + current design conditions."""
        if self.selected_row < 0:
            QMessageBox.information(
                self,
                "No Selection",
                "Click a row in the Results table first, then click "
                "“Add to Selection”.",
            )
            return

        def _num(widget):
            try:
                return float((widget.text() or "").replace(",", "").strip() or 0)
            except (TypeError, ValueError):
                return 0.0

        def _cell(col, default=""):
            it = self.table.item(self.selected_row, col)
            return it.text() if it else default

        # Column indexes match the updated results table:
        # 0 Status | 1 Catalog | 2 Model | 3 Class | 4 Cooling | 5 Sens
        # 6 Heating | 7 EER | 8 Airflow | 9 Sound | 10 Power Supply
        # 11 Indoor Unit | 12 Outdoor Unit | 13 Indoor Dims | 14 Fan
        # 15 Decision Reason
        model_name = _cell(2)
        catalog_label = _cell(1)
        status = _cell(0)
        klass_text = _cell(3)
        indoor_model = _cell(11)
        outdoor_model = _cell(12)
        indoor_dims = _cell(13)
        power_supply = _cell(10)
        reason = _cell(15)

        try:
            rated_cap = float(_cell(4).replace(",", "") or 0)
        except (TypeError, ValueError):
            rated_cap = 0.0
        try:
            heating_cap = float(_cell(6).replace(",", "").replace("—", "0") or 0)
        except (TypeError, ValueError):
            heating_cap = 0.0
        try:
            sound_dba = float(_cell(9).replace("—", "0") or 0)
        except (TypeError, ValueError):
            sound_dba = 0.0
        try:
            eer = float(_cell(7).replace("—", "0") or 0)
        except (TypeError, ValueError):
            eer = 0.0

        table_prefix = (
            next((p for lbl, p in self.CATALOGS if lbl == catalog_label), "") or ""
        )
        airflow_metadata = (
            self.table.item(self.selected_row, 0).data(Qt.UserRole)
            if self.table.item(self.selected_row, 0) else None)
        airflow_metadata = dict(airflow_metadata or {})
        available_unit_cfm = airflow_metadata.get("airflow_cfm")
        airflow_status = (
            airflow_metadata.get("airflow_provenance")
            if available_unit_cfm is not None else "unknown_unverified")
        airflow_status = airflow_status or "unknown_unverified"

        try:
            klass = float(klass_text.replace("K", "").strip())
        except (TypeError, ValueError):
            klass = 0.0

        # ── [NEW] Warn before adding a NOT SUITABLE row ──
        if status == "NOT SUITABLE":
            reply = QMessageBox.question(
                self,
                "Not Suitable",
                f"The model “{model_name}” is NOT SUITABLE for this load.\n\n"
                f"Reason: {reason or '(no details)'}\n\n"
                f"Add it to the cart anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Ask for the system name
        default_name = self._cart_next_name()
        name, ok = QInputDialog.getText(
            self, "Add to Selection", "System / Zone name:", text=default_name
        )
        if not ok or not name.strip():
            # Only roll back the auto-counter when we actually incremented it
            if not (self.systems and 0 <= self._current_system_idx < len(self.systems)):
                self._auto_system_n = max(0, self._auto_system_n - 1)
            return
        name = name.strip()

        replaced = name in self.cart

        self.cart[name] = {
            "system": name,
            "model": model_name,
            "catalog": catalog_label,
            "table_prefix": table_prefix,
            "class_kbtu": klass,
            "rated_capacity": rated_cap,
            "indoor_model": indoor_model,
            "outdoor_model": outdoor_model,
            "indoor_dims": indoor_dims,
            "power_supply": power_supply,
            "heating_btu": heating_cap,
            "sound_dba": sound_dba,
            "eer": eer,
            "total_btu": _num(self.btu),
            "sensible_btu": _num(self.sens),
            "cfm": _num(self.cfm),
            "required_cfm": _num(self.cfm),
            "design_cfm": _num(self.cfm),
            "available_unit_cfm": available_unit_cfm,
            "available_unit_cfm_status": airflow_status,
            "airflow_source": airflow_metadata.get(
                "airflow_source", "airflow point provenance unavailable"),
            "airflow_provenance": airflow_metadata.get(
                "airflow_provenance", "unknown_unverified"),
            "airflow_esp_inwg": airflow_metadata.get("airflow_esp_inwg"),
            "requested_esp_inwg": airflow_metadata.get(
                "requested_esp_inwg", _num(self.esp)),
            "fan_speed_resolved": airflow_metadata.get(
                "fan_speed_resolved"),
            "selected_units": [{
                **airflow_metadata,
                "qty": 1,
            }] if airflow_metadata else [],
            "esp": _num(self.esp),
            "outdoor": int(self.outdoor_temp.currentText()),
            "fan": self.fan_speed.currentText(),
            "status": status,
            "floor_area_ft2": (
                self.systems[self._current_system_idx].get("floor_area_ft2", 0.0)
                if (self.systems and 0 <= self._current_system_idx < len(self.systems))
                else getattr(self, "_current_floor_area", 0.0)
            ),
        }
        self._mark_project_dirty()

        self._render_cart()
        verb = "Updated" if replaced else "Added"
        self.status.setText(
            f"🛒  {verb}: {model_name} → {name}  ({len(self.cart)} item(s) in cart)"
        )
        self.status.setStyleSheet("color: #a78bfa;")

        # ── Batch mode: mark this system as Added, maybe auto-advance ──
        if self.systems and 0 <= self._current_system_idx < len(self.systems):
            cur = self.systems[self._current_system_idx]
            cur["status"] = "Added"
            cur["model"] = model_name
            self._render_systems_table()
            if hasattr(self, "auto_advance_cb") and self.auto_advance_cb.isChecked():
                self._go_to_next_pending()

    # ═══════════════════════════════════════════════════════════
    #  Combine several units (parallel) to cover one system load
    # ═══════════════════════════════════════════════════════════
    def _unit_from_row(self, r):
        def cell(c):
            it = self.table.item(r, c)
            return it.text() if it else ""

        n = ui_shell._num
        catalog = cell(1)
        airflow_metadata = (
            self.table.item(r, 0).data(Qt.UserRole)
            if self.table.item(r, 0) else None)
        airflow_metadata = dict(airflow_metadata or {})
        airflow_cfm = airflow_metadata.get("airflow_cfm")
        return {
            "status": cell(0),
            "catalog": catalog,
            "model": cell(2),
            "klass": n(cell(3).replace("K", "")),
            "cap": n(cell(4)),
            "sens": n(cell(5)),
            "heat": n(cell(6)),
            "eer": n(cell(7)),
            "flow": float(airflow_cfm) if airflow_cfm is not None else 0.0,
            "sound": n(cell(9)),
            "power": cell(10),
            "indoor": cell(11),
            "outdoor_unit": cell(12),
            "dims": cell(13),
            "table_prefix": next((p for lbl, p in self.CATALOGS if lbl == catalog), "")
            or "",
            **airflow_metadata,
        }

    def _combine_units(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self,
                "Combine Units",
                "Select one row (for 2× the same model) or Ctrl+click "
                "several rows, then press “Combine Units…”.",
            )
            return
        units = [self._unit_from_row(r) for r in rows]
        req = {
            "total": ui_shell._num(self.btu.text()) * (1 + self.capm.value() / 100),
            "sens": ui_shell._num(self.sens.text()) * (1 + self.safm.value() / 100),
            "cfm": ui_shell._num(self.cfm.text()) * (1 + self.afm.value() / 100),
        }
        dlg = ui_shell.CombineUnitsDialog(self, units, req)
        if dlg.exec():
            self._add_combo_to_selection(dlg.result_units(), dlg.totals(), dlg.all_ok)

    def _add_combo_to_selection(self, units, tot, covers):
        import math
        from airflow_utils import combine_selected_unit_airflow

        def _num(widget):
            try:
                return float((widget.text() or "").replace(",", "").strip() or 0)
            except (TypeError, ValueError):
                return 0.0

        def uniq(seq):
            return list(dict.fromkeys(x for x in seq if x))

        def label(key):
            return " + ".join(
                (f"{u['qty']}× " if u["qty"] > 1 else "") + u[key] for u in units
            )

        default_name = self._cart_next_name()
        name, ok = QInputDialog.getText(
            self, "Add to Selection", "System / Zone name:", text=default_name
        )
        if not ok or not name.strip():
            if not (self.systems and 0 <= self._current_system_idx < len(self.systems)):
                self._auto_system_n = max(0, self._auto_system_n - 1)
            return
        name = name.strip()

        # Effective EER so the existing energy formula (cap / EER) stays valid
        power_kw = sum(
            u["qty"] * u["cap"] / u["eer"] / 1000.0 for u in units if u["eer"] > 0
        )
        eer_eff = (tot["cap"] / (power_kw * 1000.0)) if power_kw > 0 else 0.0
        # Sound: energetic (logarithmic) sum of all units
        sound_lin = sum(
            u["qty"] * 10 ** (u["sound"] / 10.0) for u in units if u["sound"] > 0
        )
        sound = 10 * math.log10(sound_lin) if sound_lin > 0 else 0.0

        replaced = name in self.cart
        model_label = label("model")
        combined_airflow = combine_selected_unit_airflow(units)
        self.cart[name] = {
            "system": name,
            "model": model_label,
            "catalog": " + ".join(uniq(u["catalog"] for u in units)),
            "table_prefix": units[0]["table_prefix"],
            "class_kbtu": sum(u["qty"] * u["klass"] for u in units),
            "rated_capacity": tot["cap"],
            "indoor_model": label("indoor"),
            "outdoor_model": label("outdoor_unit"),
            "indoor_dims": " | ".join(uniq(u["dims"] for u in units)),
            "power_supply": " / ".join(uniq(u["power"] for u in units)),
            "heating_btu": sum(u["qty"] * u["heat"] for u in units),
            "sound_dba": sound,
            "eer": eer_eff,
            "total_btu": _num(self.btu),
            "sensible_btu": _num(self.sens),
            "cfm": _num(self.cfm),
            "required_cfm": _num(self.cfm),
            "design_cfm": _num(self.cfm),
            "available_unit_cfm": combined_airflow["available_unit_cfm"],
            "available_unit_cfm_status": combined_airflow["status"],
            "airflow_source": "combined selected catalog airflow points",
            "airflow_provenance": combined_airflow["status"],
            "airflow_esp_inwg": (
                units[0].get("airflow_esp_inwg") if units else None),
            "requested_esp_inwg": _num(self.esp),
            "fan_speed_resolved": (
                units[0].get("fan_speed_resolved") if units else None),
            "esp": _num(self.esp),
            "outdoor": int(self.outdoor_temp.currentText()),
            "fan": self.fan_speed.currentText(),
            "status": "SUITABLE (combined)" if covers else "NOT SUITABLE (combined)",
            "combined_sens": tot["sens"],
            "combined_cfm": tot["flow"],
            "units": [dict(u) for u in units],
            "floor_area_ft2": (
                self.systems[self._current_system_idx].get("floor_area_ft2", 0.0)
                if (self.systems and 0 <= self._current_system_idx < len(self.systems))
                else getattr(self, "_current_floor_area", 0.0)
            ),
        }
        self._mark_project_dirty()

        self._render_cart()
        self.status.setText(
            f"🛒  {'Updated' if replaced else 'Added'} combination: {model_label} → {name}  "
            f"({len(self.cart)} item(s) in cart)"
        )
        self.status.setStyleSheet("color: #a78bfa;")

        if self.systems and 0 <= self._current_system_idx < len(self.systems):
            cur = self.systems[self._current_system_idx]
            cur["status"] = "Added"
            cur["model"] = model_label
            self._render_systems_table()
            if hasattr(self, "auto_advance_cb") and self.auto_advance_cb.isChecked():
                self._go_to_next_pending()

    def _add_best_match(self):
        """Pick the first SUITABLE row automatically and add it to the cart."""
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "No Results", "Run a search first.")
            return

        target = -1
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() in ("⭐ BEST MATCH", "SUITABLE"):
                target = r
                break

        if target < 0:
            QMessageBox.information(
                self,
                "No Suitable Model",
                "The current results contain no SUITABLE model.\n"
                "Relax the margins or change the design conditions.",
            )
            return

        # Select the target row, then reuse the normal add path
        self.table.clearSelection()
        self.table.selectRow(target)
        # Ensure selected_row is set even if the signal didn't fire yet
        self.selected_row = target
        self._add_to_selection()

    def _cart_site_cells(self, item):
        """8 site-spec cells (SPEC_HEADERS order) for a cart item.
        Combined items list each distinct unit, separated by ' | '."""
        units = item.get("units")
        if units:
            pairs = [(u["model"], u.get("table_prefix", ""), u["qty"]) for u in units]
        else:
            pairs = [(item["model"], item.get("table_prefix", ""), 1)]
        cols = [[] for _ in report_builder.SPEC_HEADERS]
        for model, pref, _qty in pairs:
            spec, ck = self._site_for(pref, model)
            for i, c in enumerate(report_builder.spec_cells(spec, ck)):
                cols[i].append(c)
        out = []
        for col in cols:
            uniq = list(dict.fromkeys(col))
            out.append(" | ".join(uniq))
        return out

    def _render_cart(self):
        """Rebuild the cart table from self.cart (with energy columns)."""
        if not hasattr(self, "cart_table"):
            return
        hours, tariff, currency = self._energy_params()

        headers = [
            "System",
            "Catalog",
            "Model",
            "Rated (kBTU)",
            "Indoor Unit",
            "Outdoor Unit",
            "Required Total (Btu/hr)",
            "Required Sensible (Btu/hr)",
            "Required CFM",
            "ESP (in.wg)",
            "Outdoor (°C)",
            "Fan",
            "Sound dB(A)",
            "Power Supply",
            "EER",
            "Power (kW)",
            f"Annual ({currency})",
            "Status",
        ] + report_builder.SPEC_HEADERS
        self._updating_cart = True
        try:
            self.cart_table.setColumnCount(len(headers))
            self.cart_table.setHorizontalHeaderLabels(headers)
            self.cart_table.setRowCount(len(self.cart))
            total_annual = 0.0
            for r, item in enumerate(self.cart.values()):
                power_kw, annual = self._compute_energy_for(item, hours, tariff)
                total_annual += annual
                eer_val = item.get("eer", 0)
                cells = [
                    item["system"],
                    item["catalog"],
                    item["model"],
                    f"{item['class_kbtu']:g}",
                    item.get("indoor_model", "") or "—",
                    item.get("outdoor_model", "") or "—",
                    f"{item['total_btu']:,.0f}",
                    f"{item['sensible_btu']:,.0f}",
                    f"{item['cfm']:,.0f}",
                    f"{item['esp']:.3f}",
                    str(item["outdoor"]),
                    item["fan"],
                    f"{item.get('sound_dba', 0):.1f}" if item.get("sound_dba") else "—",
                    item.get("power_supply", "") or "—",
                    f"{eer_val:.2f}" if eer_val else "—",
                    f"{power_kw:.2f}" if power_kw > 0 else "—",
                    f"{annual:,.0f}" if annual > 0 else "—",
                    item["status"],
                ] + self._cart_site_cells(item)
                for c, text in enumerate(cells):
                    cell = QTableWidgetItem(str(text))
                    cell.setTextAlignment(Qt.AlignCenter)
                    self.cart_table.setItem(r, c, cell)
            self.cart_table.resizeRowsToContents()
        finally:
            self._updating_cart = False

        n = len(self.cart)
        has = n > 0
        self.cart_remove_btn.setEnabled(False)
        self.cart_clear_btn.setEnabled(has)
        self.cart_export_btn.setEnabled(has)
        if hasattr(self, "cart_dxf_btn"):
            self.cart_dxf_btn.setEnabled(has)
        if n:
            self.cart_grp.setTitle(
                f"🛒 Selection Cart — {n} system(s) | "
                f"{total_annual:,.0f} {currency}/year"
            )
            self.lbl_cart_hint.setText(
                f"🛒  {n} item(s) in cart — click “Export Cart” to save "
                f"them all in one file."
            )
        else:
            self.cart_grp.setTitle("🛒 Selection Cart")
            self.lbl_cart_hint.setText(
                "Add one model per system from the Results table, "
                "then export them all in ONE file."
            )
        self._refresh_shell()

    # ── [NEW] Energy helpers ──────────────────────────────────
    def _energy_params(self):
        """Return (hours, tariff, currency) safely — with defaults."""
        try:
            hours = int(self.hours_per_year.value())
        except (TypeError, ValueError):
            hours = 2000
        self._tariff_defaulted = False
        try:
            tariff = float((self.tariff.text() or "").replace(",", ".").strip())
            if tariff <= 0:
                raise ValueError
        except (TypeError, ValueError):
            tariff = 2.0  # explicit default, flagged in UI
            self._tariff_defaulted = True
        currency = "EGP"
        if self.currency_sym.text().strip():
            currency = self.currency_sym.text().strip()
        return hours, tariff, currency

    def _compute_energy_for(self, item, hours, tariff):
        """Return (power_kw, annual_cost) for one cart entry."""
        eer = float(item.get("eer", 0) or 0)
        cap = float(item.get("rated_capacity", 0) or 0)
        if eer <= 0 or cap <= 0:
            return 0.0, 0.0
        power_kw = cap / eer / 1000.0
        annual = power_kw * float(hours) * float(tariff)
        return power_kw, annual

    def _recompute_cart_costs(self, *_):
        """Slot: re-render the cart when hours / tariff / currency change."""
        if hasattr(self, "cart_table"):
            self._render_cart()

        n = len(self.cart)
        has = n > 0
        self.cart_remove_btn.setEnabled(False)
        self.cart_clear_btn.setEnabled(has)
        self.cart_export_btn.setEnabled(has)
        if hasattr(self, "cart_dxf_btn"):
            self.cart_dxf_btn.setEnabled(has)
        if n:
            self.lbl_cart_hint.setText(
                f"🛒  {n} item(s) in cart — click “Export Cart” to save "
                f"them all in one file."
            )
        else:
            self.lbl_cart_hint.setText(
                "Add one model per system from the Results table, "
                "then export them all in ONE file."
            )

    def _on_cart_selection(self):
        if getattr(self, "_updating_cart", False):
            return
        rows = {it.row() for it in self.cart_table.selectedItems()}
        self.cart_remove_btn.setEnabled(bool(rows))

    def _remove_from_cart(self):
        rows = {it.row() for it in self.cart_table.selectedItems()}
        if not rows:
            return
        keys = list(self.cart.keys())
        removed = 0
        for r in sorted(rows, reverse=True):
            if 0 <= r < len(keys):
                self.cart.pop(keys[r], None)
                removed += 1
        if removed:
            self._mark_project_dirty()
        self._render_cart()
        self.status.setText(f"🗑  Removed {removed} item(s) from cart.")
        self.status.setStyleSheet("color: #9ca3af;")

    def _clear_cart(self):
        if not self.cart:
            return
        reply = QMessageBox.question(
            self,
            "Clear Selection Cart?",
            f"This will remove all {len(self.cart)} selected systems.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.cart.clear()
        self._mark_project_dirty()
        self._render_cart()
        self.status.setText("Cart cleared.")
        self.status.setStyleSheet("color: #9ca3af;")

    def _prepare_cart_rows(self):
        hours, tariff, currency = self._energy_params()
        from airflow_utils import airflow_summary_text
        headers = [
            "System",
            "Catalog",
            "System Model",
            "Rated (kBTU)",
            "Indoor Unit",
            "Outdoor Unit",
            "Rated Capacity (Btu/hr)",
            "EER",
            "Power (kW)",
            f"Annual Cost ({currency}/year)",
            "Sound (dB(A))",
            "Power Supply",
            "Required Total Load (Btu/hr)",
            "Required Sensible Load (Btu/hr)",
            "Required CFM",
            "Available Unit CFM",
            "Diffuser Design CFM",
            "Selected Airflow-Point ESP (in.wg)",
            "Airflow Reconciliation / Provenance",
            "ESP (in.wg)",
            "Outdoor (°C)",
            "Fan Speed",
            "Selection Status",
            "Diffuser Status",
            "T50/L Screening Estimate",
            "Diffuser ΔT (°F)",
            "Diffuser Warnings / Errors",
        ] + report_builder.SPEC_HEADERS
        rows = []
        from diffuser_report import _solve_system_diffuser
        for item in self.cart.values():
            power_kw, annual = self._compute_energy_for(item, hours, tariff)
            diffuser = _solve_system_diffuser(item)
            diffuser_status = (
                "Feasible" if "error" not in diffuser else "Not feasible")
            screening = (
                "Within band" if diffuser.get("adpi_ok")
                else "Review estimate"
            ) if "error" not in diffuser else "Unverified"
            diffuser_warnings = diffuser.get("warnings", [])
            if diffuser.get("error"):
                diffuser_warnings = [diffuser["error"]]
            rows.append(
                [
                    item["system"],
                    item["catalog"],
                    item["model"],
                    int(item["class_kbtu"]) if item["class_kbtu"] else 0,
                    item.get("indoor_model", "") or "—",
                    item.get("outdoor_model", "") or "—",
                    int(item.get("rated_capacity", 0) or 0),
                    round(item.get("eer", 0) or 0, 2),
                    round(power_kw, 2),
                    round(annual, 0),
                    round(item.get("sound_dba", 0) or 0, 1),
                    item.get("power_supply", "") or "—",
                    int(item["total_btu"]),
                    int(item["sensible_btu"]),
                    float(item.get("required_cfm", item["cfm"])),
                    (item["available_unit_cfm"]
                     if item.get("available_unit_cfm") is not None
                     else "Unknown / unverified"),
                    float(item.get("design_cfm", item["cfm"])),
                    (item["airflow_esp_inwg"]
                     if item.get("airflow_esp_inwg") is not None
                     else "Unknown"),
                    airflow_summary_text(item),
                    round(item["esp"], 3),
                    item["outdoor"],
                    item["fan"],
                    item["status"],
                    diffuser_status,
                    screening,
                    (f"{diffuser['delta_t_f']:.1f}"
                     if diffuser.get("delta_t_f") is not None else "Unverified"),
                    "; ".join(diffuser_warnings),
                ]
                + self._cart_site_cells(item)
            )
        return headers, rows

    def _export_cart(self, fmt=None):
        """Save every cart item into ONE file (Excel/CSV/Word/PDF/HTML/JSON/MD)."""
        if not self.cart:
            QMessageBox.warning(self, "Empty Cart", "Add at least one model first.")
            return

        if not isinstance(fmt, str):
            fmt = self.export_format.currentText()
        ext, filt = FORMATS.get(fmt, ("xlsx", "Excel Files (*.xlsx)"))

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Cart", f"cart_selection.{ext}", filt
        )
        if not path:
            return

        try:
            if ext == "docx":
                self._build_cart_word_report(path)
            elif ext == "dxf":
                headers, rows, sec_tables = self._prepare_schedule_dxf_rows()
                from exporters import export_dxf

                export_dxf(
                    path,
                    headers,
                    rows,
                    title="DUCT DX EQUIPMENT SCHEDULE",
                    secondary_tables=sec_tables,
                )
            elif ext == "xlsx":
                # Excel: main summary sheet + second "Diffusers" sheet
                from diffuser_report import add_diffuser_sheet_to_excel
                from exporters import export_excel

                headers, rows = self._prepare_cart_rows()
                summary = (
                    f"Selection Cart — {len(self.cart)} system(s). "
                    f"One row per system; loads shown are the values "
                    f"at the moment each model was added."
                )
                cart_items = list(self.cart.values())

                def _builder(wb):
                    add_diffuser_sheet_to_excel(
                        wb, cart_items, application="office_priv"
                    )

                export_excel(path, headers, rows, summary, extra_sheet_builder=_builder)
            else:
                headers, rows = self._prepare_cart_rows()
                summary = (
                    f"Selection Cart — {len(self.cart)} system(s). "
                    f"One row per system; loads shown are the values "
                    f"at the moment each model was added."
                )
                export_table(path, ext, headers, rows, summary)
            self.status.setText(f"✅  Cart exported → {path}")
            self.status.setStyleSheet("color: #34d399;")
            QMessageBox.information(
                self, "Export Successful", f"{len(self.cart)} item(s) saved to:\n{path}"
            )
        except (OSError, ValueError, TypeError, KeyError, IndexError,
                RuntimeError, sqlite3.Error, ImportError) as exc:
            logging.getLogger(__name__).exception("Cart export failed")
            QMessageBox.critical(self, "Export Error", str(exc))

    def _build_cart_word_report(self, path):
        """Generate a Word report with one block per system in the cart."""
        from datetime import datetime as _dt

        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Pt, RGBColor

        from report_builder import load_model_data, render_system_block

        doc = Document()
        for section in doc.sections:
            section.top_margin = Cm(1.5)
            section.bottom_margin = Cm(1.5)
            section.left_margin = Cm(2.0)
            section.right_margin = Cm(2.0)

        # ── Cover page ────────────────────────────────────────
        title = doc.add_heading("HVAC Equipment Selection Report", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = sub.add_run(f"{len(self.cart)} system(s) selected")
        run.bold = True
        run.font.size = Pt(12)

        meta = doc.add_paragraph()
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = meta.add_run(
            f"Generated: {_dt.now().astimezone().strftime('%Y-%m-%d %H:%M')}")
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
        doc.add_paragraph()

        # ── Summary table ─────────────────────────────────────
        doc.add_heading("Summary of Selected Units", level=1)

        hours, tariff, currency = self._energy_params()

        summary_cols = [
            "System",
            "Model",
            "Rated (kBTU)",
            "Required Total (Btu/hr)",
            "Required CFM",
            "EER",
            "Power (kW)",
            f"Annual ({currency}/yr)",
            "Selection Status",
        ]
        stbl = doc.add_table(rows=1, cols=len(summary_cols))
        stbl.style = "Light Grid Accent 1"
        hdr = stbl.rows[0].cells
        for i, name in enumerate(summary_cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(9)

        total_annual = 0.0
        for item in self.cart.values():
            power_kw, annual = self._compute_energy_for(item, hours, tariff)
            total_annual += annual
            cells = stbl.add_row().cells
            cells[0].text = item["system"]
            cells[1].text = item["model"]
            cells[2].text = f"{item['class_kbtu']:g}"
            cells[3].text = f"{item['total_btu']:,.0f}"
            cells[4].text = (
                f"{item.get('required_cfm', item['cfm']):,.0f}")
            cells[5].text = f"{item.get('eer', 0):.2f}" if item.get("eer") else "—"
            cells[6].text = f"{power_kw:.2f}" if power_kw > 0 else "—"
            cells[7].text = f"{annual:,.0f}" if annual > 0 else "—"
            cells[8].text = item["status"]
            for c in cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)

        if total_annual > 0:
            tot = doc.add_paragraph()
            tot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = tot.add_run(
                f"Total estimated annual cost: "
                f"{total_annual:,.0f} {currency}/year "
                f"({hours:,} h/yr @ {tariff:.3f} {currency}/kWh)"
            )
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x05, 0x96, 0x69)

        # ── Electrical & site summary (one row per system) ────
        doc.add_heading("Electrical & Site Summary", level=1)
        site_cols = ["System", "Model"] + list(report_builder.SPEC_HEADERS)
        etbl = doc.add_table(rows=1, cols=len(site_cols))
        etbl.style = "Light Grid Accent 1"
        for i, name in enumerate(site_cols):
            etbl.rows[0].cells[i].text = name
            for p in etbl.rows[0].cells[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(8)
        for item in self.cart.values():
            cells = etbl.add_row().cells
            vals = [item["system"], item["model"]] + self._cart_site_cells(item)
            for i, v in enumerate(vals):
                cells[i].text = str(v)
                for p in cells[i].paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(8)
        note = doc.add_paragraph(
            "MCA/MOP/cable marked Estimated are indicative only (MCA = 1.25 × "
            "running current); confirm against the unit nameplate and "
            'installation manual. "—" = not published in the catalog.'
        )
        for r in note.runs:
            r.italic = True
            r.font.size = Pt(8)

        # ── Per-system blocks ─────────────────────────────────
        for item in self.cart.values():
            dc = {
                "total_btu": item["total_btu"],
                "sensible_btu": item["sensible_btu"],
                "cfm": item["cfm"],
                "required_cfm": item.get("required_cfm", item["cfm"]),
                "design_cfm": item.get("design_cfm", item["cfm"]),
                "available_unit_cfm": item.get("available_unit_cfm"),
                "airflow_provenance": item.get(
                    "airflow_provenance", "unknown_unverified"),
                "airflow_source": item.get("airflow_source", "not recorded"),
                "available_unit_cfm_status": item.get(
                    "available_unit_cfm_status", "unknown_unverified"),
                "airflow_esp_inwg": item.get("airflow_esp_inwg"),
                "esp": item["esp"],
                "heating_btu": item.get("heating_btu", 0),
                "outdoor_db": item["outdoor"],
                "fan_speed": item["fan"],
            }
            if item.get("units"):
                available_text = (
                    f"{item['available_unit_cfm']:,.0f} CFM "
                    f"({item.get('airflow_provenance', 'unverified')})"
                    if item.get("available_unit_cfm") is not None else
                    "Unknown / incompatible or unverified operating points")
                doc.add_page_break()
                doc.add_heading(
                    f"{item['system']} — Combined: {item['model']}", level=1
                )
                doc.add_paragraph(
                    f"Required: {item['total_btu']:,.0f} Btu/hr total · "
                    f"{item['sensible_btu']:,.0f} sensible · "
                    f"{item.get('required_cfm', item['cfm']):,.0f} CFM "
                    f"required; diffuser design uses "
                    f"{item.get('design_cfm', item['cfm']):,.0f} CFM.  "
                    f"Combined rated: {item['rated_capacity']:,.0f} Btu/hr · "
                    f"{item.get('combined_sens', 0):,.0f} sensible. "
                    f"Selected-unit catalog-point airflow: {available_text}. "
                    f"The units run in parallel; each block below shows one model "
                    f"against its proportional calculated load share, not a "
                    f"verified delivered-airflow allocation."
                )
                tot_cap = item["rated_capacity"] or 1.0
                for u in item["units"]:
                    share = u["cap"] / tot_cap  # per physical unit
                    ucd = dict(dc)
                    for k in ("total_btu", "sensible_btu", "cfm", "heating_btu"):
                        ucd[k] = dc.get(k, 0) * share
                    ucd["required_cfm"] = ucd["cfm"]
                    ucd["design_cfm"] = ucd["cfm"]
                    ucd["available_unit_cfm"] = u.get("airflow_cfm")
                    ucd["airflow_provenance"] = u.get(
                        "airflow_provenance", "unknown_unverified")
                    ucd["airflow_source"] = u.get(
                        "airflow_source", "not recorded")
                    ucd["available_unit_cfm_status"] = u.get(
                        "airflow_provenance", "unknown_unverified")
                    ucd["airflow_esp_inwg"] = u.get("airflow_esp_inwg")
                    udata = load_model_data(
                        DB, u["model"], ucd, u.get("table_prefix", "")
                    )
                    if udata is None:
                        doc.add_paragraph(
                            f"⚠ Could not load {u['model']} from the database."
                        )
                        continue
                    doc.add_page_break()
                    render_system_block(
                        doc,
                        f"{item['system']} — {u['qty']}× {u['model']}",
                        udata,
                        ucd,
                        heading_level=2,
                        hours_per_year=hours,
                        tariff=tariff,
                        currency=currency,
                    )
                continue
            data = load_model_data(DB, item["model"], dc, item.get("table_prefix", ""))
            doc.add_page_break()
            if data is None:
                doc.add_heading(f"{item['system']} — {item['model']}", level=1)
                p = doc.add_paragraph(
                    "⚠ Could not load this model from the database. "
                    "The row is preserved in the summary above."
                )
                for r in p.runs:
                    r.italic = True
                    r.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)
                continue
            render_system_block(
                doc,
                item["system"],
                data,
                dc,
                heading_level=1,
                hours_per_year=hours,
                tariff=tariff,
                currency=currency,
            )

        # ── Diffuser Selection & Layout (all systems) ────────
        try:
            from diffuser_report import add_diffuser_summary

            add_diffuser_summary(
                doc, list(self.cart.values()), application="office_priv"
            )
        except (OSError, ValueError, TypeError, KeyError, IndexError,
                RuntimeError, sqlite3.Error, ImportError) as exc:
            logging.getLogger(__name__).exception("Diffuser report generation failed")
            doc.add_page_break()
            doc.add_heading("Diffuser Selection & Layout", level=1)
            _wp = doc.add_paragraph(
                "The diffuser section could not be generated automatically."
                f"\nReason: {exc}\n"
                "Run diffuser_cli.py --batch to produce it separately."
            )
            for r in _wp.runs:
                r.italic = True
                r.font.size = Pt(9)
                r.font.color.rgb = RGBColor(0xB4, 0x53, 0x09)

        # ── Footer (LAST so it stays on the final page) ──────
        doc.add_paragraph()
        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            "Generated by Duct DX Equipment Selector — "
            "Carrier / Miraco Satellite Series"
        )
        run.font.size = Pt(8)
        run.italic = True
        run.font.color.rgb = RGBColor(0x9C, 0xA3, 0xAF)

        doc.save(str(path))

    # ═══════════════════════════════════════════════════════════
    #  [NEW]  Batch HAP systems — upload / navigate / auto-search
    # ═══════════════════════════════════════════════════════════

    def open_batch_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select HAP Systems Design Report",
            "",
            "Reports (*.rtf *.RTF *.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
            "*.bmp *.tif *.tiff)",
        )
        if path:
            self._start_extraction(path, batch=True)

    def _on_batch_error(self, msg):
        self.upload.setEnabled(True)
        self.batch_upload_btn.setEnabled(True)
        if hasattr(self, "manual_btn"):
            self.manual_btn.setEnabled(True)
        self.status.setText("❌ Batch OCR error: " + msg)
        self.status.setStyleSheet("color: #fca5a5;")
        QMessageBox.warning(
            self,
            "Unable to process file",
            ui_shell.friendly_error(msg, getattr(self, "_loaded_path", "")),
        )

    def _on_batch_extract_done(self, text):
        self.raw = text
        self.debug.setPlainText(text)
        self.upload.setEnabled(True)
        self.batch_upload_btn.setEnabled(True)
        if hasattr(self, "manual_btn"):
            self.manual_btn.setEnabled(True)

        self.systems = parse_all_systems(text)
        self._mark_project_dirty()

        if not self.systems:
            # No system headers — fall back to single-system mode
            total, sens, cfm, heat, esp = parse_hap(text)
            self._apply_result(
                {
                    "total_btu": total,
                    "sensible_btu": sens,
                    "cfm": cfm,
                    "heating_btu": heat,
                    "esp": esp,
                    "floor_area": 0,
                    "project_name": "",
                }
            )
            QMessageBox.information(
                self,
                "Single System Only",
                'No "Air System Information" headers were found '
                "in this report.\n\n"
                "The file was loaded as a single system instead.",
            )
            return

        self._current_system_idx = -1
        self._render_systems_table()
        self.systems_grp.setVisible(True)
        self.systems_grp.setTitle(
            f"📚 HAP Systems (Batch) — {len(self.systems)} loaded"
        )

        # Auto-select the first row
        self._select_system_row(0)

        self.status.setText(
            f"📚 {len(self.systems)} system(s) loaded. "
            f"Click a row or use Prev/Next to browse."
        )
        self.status.setStyleSheet("color: #22d3ee;")
        ui_shell.go_page(self, ui_shell.P_SYS)

    def _render_systems_table(self):
        """Rebuild the Systems table from self.systems."""
        if not hasattr(self, "systems_table"):
            return

        headers = [
            "System",
            "Load (Btu/hr)",
            "Sensible (Btu/hr)",
            "CFM",
            "ESP (in.wg)",
            "Status",
            "Model",
        ]
        self._updating_systems = True
        try:
            self.systems_table.setColumnCount(len(headers))
            self.systems_table.setHorizontalHeaderLabels(headers)
            self.systems_table.setRowCount(len(self.systems))
            for r, s in enumerate(self.systems):
                esp_txt = f"{s['esp']:.3f}" if s.get("esp") else "—"
                cells = [
                    s["name"],
                    f"{s['total_btu']:,.0f}" if s["total_btu"] else "—",
                    f"{s['sensible_btu']:,.0f}" if s["sensible_btu"] else "—",
                    f"{s['cfm']:,.0f}" if s["cfm"] else "—",
                    esp_txt,
                    s["status"],
                    s["model"] or "—",
                ]
                for c, text in enumerate(cells):
                    cell = QTableWidgetItem(str(text))
                    cell.setTextAlignment(Qt.AlignCenter)
                    if s["status"] == "Added":
                        cell.setForeground(QBrush(QColor("#34d399")))
                    elif s["status"] == "Searched":
                        cell.setForeground(QBrush(QColor("#fbbf24")))
                    self.systems_table.setItem(r, c, cell)
            self.systems_table.resizeRowsToContents()
        finally:
            self._updating_systems = False

        self._update_systems_nav()

    def _update_systems_nav(self):
        if not hasattr(self, "lbl_nav_pos"):
            return
        self._refresh_shell()
        n = len(self.systems)
        if n == 0:
            self.lbl_nav_pos.setText("—")
            return
        i = self._current_system_idx
        if 0 <= i < n:
            done = sum(1 for s in self.systems if s["status"] == "Added")
            self.lbl_nav_pos.setText(f"{i + 1}/{n}  •  {done} done")
        else:
            self.lbl_nav_pos.setText(f"—/{n}")

    def _select_system_row(self, idx):
        """Select row idx in the systems table + load its data."""
        if not (0 <= idx < len(self.systems)):
            return
        self._updating_systems = True
        try:
            self.systems_table.clearSelection()
            self.systems_table.selectRow(idx)
        finally:
            self._updating_systems = False
        self._load_system_into_fields(idx)

    def _load_system_into_fields(self, idx):
        """Fill the input fields from system idx and run a search."""
        if not (0 <= idx < len(self.systems)):
            return
        s = self.systems[idx]
        self._current_system_idx = idx

        if s["total_btu"] > 0:
            self.btu.setText(f"{s['total_btu']:.0f}")
        if s["sensible_btu"] > 0:
            self.sens.setText(f"{s['sensible_btu']:.0f}")
        if s["cfm"] > 0:
            self.cfm.setText(f"{s['cfm']:.0f}")
        if s["heating_btu"] > 0:
            self.heat.setText(f"{s['heating_btu']:.0f}")

        if s.get("esp") and s["esp"] > 0.001:
            self.esp.setText(f"{s['esp']:.3f}")
        else:
            suggested = suggest_esp_from_cfm(
                s["cfm"] or 400, "Office / Commercial Shop"
            )
            self.esp.setText(f"{suggested:.2f}")

        # Mark as Searched (if not already Added)
        if s["status"] == "Pending":
            s["status"] = "Searched"
            self._render_systems_table()

        self.search()
        self._update_systems_nav()

        self.status.setText(
            f"📚 {s['name']}: {s['total_btu']:,.0f} Btu/hr  •  "
            f"{s['cfm']:,.0f} CFM  —  click ⭐ Add Best Match"
        )
        self.status.setStyleSheet("color: #22d3ee;")

    def _on_system_selected(self):
        if getattr(self, "_updating_systems", False):
            return
        rows = self.systems_table.selectionModel().selectedRows()
        if not rows:
            return
        self._load_system_into_fields(rows[0].row())

    def _navigate_system(self, delta):
        n = len(self.systems)
        if n == 0:
            return
        i = self._current_system_idx
        if i < 0:
            i = 0 if delta > 0 else n - 1
        else:
            i = (i + delta) % n
        self._select_system_row(i)

    def _go_to_next_pending(self):
        """Advance to the next Pending system, else stay."""
        n = len(self.systems)
        if n == 0:
            return
        start = max(self._current_system_idx, 0)
        for step in range(1, n + 1):
            idx = (start + step) % n
            if self.systems[idx]["status"] != "Added":
                self._select_system_row(idx)
                return
        # All done
        self.status.setText("✅ All systems have been added to the cart!")
        self.status.setStyleSheet("color: #34d399;")

    # ═══════════════════════════════════════════════════════════
    #  [NEW]  Diffuser Selection  —  per-system Al-Andalosia pick
    # ═══════════════════════════════════════════════════════════

    def _populate_diffuser_candidate_choices(self, options, selected_index=0):
        """Display the ranked candidate list and keep the explicit selection in sync."""
        combo = getattr(self, "diff_candidate_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            if not options:
                combo.setEnabled(False)
                return
            for idx, option in enumerate(options[:8]):
                combo.addItem(
                    f"{idx + 1}. {option.model} {option.size_label} - "
                    f"{option.qty} × {option.cfm_per_unit:,.0f} CFM",
                    idx,
                )
            safe_index = max(0, min(int(selected_index or 0), combo.count() - 1))
            combo.setEnabled(combo.count() > 1)
            combo.setCurrentIndex(safe_index)
        finally:
            combo.blockSignals(False)

    def _on_diffuser_candidate_changed(self):
        """Persist an explicitly selected candidate and re-run diffuser selection."""
        sys_name = self.diff_sys_combo.currentText()
        if not sys_name or sys_name not in self.cart:
            return
        combo = getattr(self, "diff_candidate_combo", None)
        if combo is None:
            return
        index = combo.currentIndex()
        item = self.cart[sys_name]
        item.setdefault("diffuser_selection", {})["selected_option_index"] = index
        self._mark_project_dirty()
        self._calculate_current_diffuser()

    def _on_diffuser_sys_changed(self, *, recalculate=True):
        """Loads room and airflow parameters when selecting a system
        in the Diffusers page. Blocks every input widget while the
        values are being populated so the per-widget ``valueChanged``
        slots do not trigger eight back-to-back recalculations."""
        sys_name = self.diff_sys_combo.currentText()
        if not sys_name or sys_name not in self.cart:
            return
        item = self.cart[sys_name]
        from airflow_utils import airflow_summary_text, design_airflow_cfm
        if hasattr(self, "diff_airflow_lbl"):
            self.diff_airflow_lbl.setText(airflow_summary_text(item))

        ds = item.get("diffuser_selection") or {}
        if not recalculate:
            cad_room = item.get("cad_room_data")
            geometry = None
            positions = list(ds.get("positions", []) or [])
            width_ft = float(ds.get("room_w_ft", self.diff_width.value()))
            length_ft = float(ds.get("room_l_ft", self.diff_length.value()))
            if cad_room is not None:
                from diffuser_calculator import m_to_ft
                from room_geometry import RoomGeometry
                try:
                    geometry = RoomGeometry.from_dict(cad_room)
                except (KeyError, TypeError, ValueError, IndexError):
                    geometry = None
                if geometry is not None and geometry.is_valid:
                    min_x, max_x, min_y, max_y = geometry.bounding_box
                    width_ft = m_to_ft(max_x - min_x)
                    length_ft = m_to_ft(max_y - min_y)
                    positions = []
                    for diffuser in cad_room.get("diffusers", []):
                        try:
                            point = (
                                m_to_ft(float(diffuser["x_m"])),
                                m_to_ft(float(diffuser["y_m"])),
                            )
                        except (KeyError, TypeError, ValueError):
                            continue
                        if all(math.isfinite(value) for value in point):
                            positions.append(point)
            self.diff_canvas.set_data(
                width_ft, length_ft, positions,
                ds.get("t50_ft", 0.0),
                ds.get("model", ds.get("type_label", "CCD")),
                cad_room=cad_room, geometry=geometry)
            for widget in (self.diff_width, self.diff_length, self.diff_height):
                widget.setEnabled(cad_room is None)
                if cad_room is not None:
                    widget.setToolTip(
                        "Controlled by the current Room Designer geometry.")
            return

        area = float(item.get("floor_area_ft2", 0) or 0)
        cad_room = item.get("cad_room_data")
        if cad_room is not None:
            from diffuser_calculator import m_to_ft
            from room_geometry import RoomGeometry
            try:
                geometry = RoomGeometry.from_dict(cad_room)
            except (KeyError, TypeError, ValueError, IndexError):
                geometry = None
            if geometry is not None and geometry.is_valid:
                min_x, max_x, min_y, max_y = geometry.bounding_box
                default_w = m_to_ft(max_x - min_x)
                default_l = m_to_ft(max_y - min_y)
                default_h = m_to_ft(geometry.height_m)
            else:
                default_w = float(ds.get("room_w_ft", 20.0))
                default_l = float(ds.get("room_l_ft", 20.0))
                default_h = float(ds.get("room_h_ft", 10.0))
        else:
            side = round(math.sqrt(area), 1) if area > 0 else 20.0
            default_w = default_l = side
            default_h = 10.0

        # Block every widget that has a valueChanged / currentIndexChanged
        # handler connected to _calculate_current_diffuser.
        widgets = [
            self.diff_width,
            self.diff_length,
            self.diff_height,
            self.diff_max_nc,
            self.diff_max_dp,
            self.diff_return_cfm,
            self.diff_vcd_width_mm,
            self.diff_vcd_height_mm,
            self.diff_rvcd_diameter_in,
            self.diff_app_combo,
            self.diff_type_combo,
            self.diff_rvcd_pressure_class,
        ]
        for w in widgets:
            w.blockSignals(True)

        self.diff_width.setValue(float(
            default_w if cad_room is not None else ds.get("room_w_ft", default_w)))
        self.diff_length.setValue(float(
            default_l if cad_room is not None else ds.get("room_l_ft", default_l)))
        self.diff_height.setValue(float(
            default_h if cad_room is not None else ds.get("room_h_ft", default_h)))
        self.diff_return_cfm.setValue(float(
            item.get("return_cfm", design_airflow_cfm(item)) or 0))
        self.diff_vcd_width_mm.setValue(float(
            item.get("vcd_duct_width_mm", 0) or 0))
        self.diff_vcd_height_mm.setValue(float(
            item.get("vcd_duct_height_mm", 0) or 0))
        self.diff_rvcd_diameter_in.setValue(float(
            item.get("rvcd_diameter_in", 0) or 0))
        self.diff_rvcd_pressure_class.setCurrentText(
            str(item.get("rvcd_pressure_class", "LP")))

        if ds.get("app_key"):
            back = {
                "office_priv": "Office (Private)",
                "office_open": "Office (Open Plan)",
                "conference": "Conference / Meeting",
                "classroom": "Classroom",
                "hospital": "Hospital / Clinic",
                "residential": "Residential",
                "retail": "Retail / Showroom",
                "industrial": "Industrial",
                "server_room": "Server Room",
            }.get(ds["app_key"], "Office (Private)")
            self.diff_app_combo.setCurrentText(back)

        if ds.get("type_label"):
            self.diff_type_combo.setCurrentText(ds["type_label"])
        if ds.get("max_nc") is not None:
            self.diff_max_nc.setValue(float(ds["max_nc"]))
        if ds.get("max_dp") is not None:
            self.diff_max_dp.setValue(float(ds["max_dp"]))

        for w in widgets:
            w.blockSignals(False)

        # Immediately display the custom CAD room when one is available.
        if "cad_room_data" in item:
            self.diff_canvas.cad_room = item["cad_room_data"]
            if item.get("diffuser_selection", {}).get("positions"):
                self.diff_canvas.positions = item["diffuser_selection"]["positions"]
            for widget in (self.diff_width, self.diff_length, self.diff_height):
                widget.setEnabled(False)
                widget.setToolTip(
                    "Controlled by the current Room Designer geometry.")
        else:
            self.diff_canvas.cad_room = None
            for widget in (self.diff_width, self.diff_length, self.diff_height):
                widget.setEnabled(True)

        self._calculate_current_diffuser()

    def _update_vcd_branch_result(self, item, cfm_each, diffuser_dp_inwg):
        """Show the estimated branch VCD loss without screening diffuser ΔP."""
        width_mm = float(self.diff_vcd_width_mm.value())
        height_mm = float(self.diff_vcd_height_mm.value())
        if width_mm == 0 and height_mm == 0:
            item.pop("vcd_selection", None)
            self.diff_vcd_result_lbl.clear()
            return
        item["vcd_duct_width_mm"] = width_mm
        item["vcd_duct_height_mm"] = height_mm
        try:
            from diffuser_calculator import (
                vcd_face_velocity_fpm,
                vcd_pressure_drop,
                vcd_select,
            )

            selected = vcd_select(width_mm, height_mm)
            velocity = vcd_face_velocity_fpm(cfm_each, width_mm, height_mm)
            damper_dp = vcd_pressure_drop(velocity)
            branch_dp = diffuser_dp_inwg + damper_dp
        except ValueError as exc:
            item["vcd_selection"] = {
                "success": False,
                "error": str(exc),
                "estimated": True,
            }
            self.diff_vcd_result_lbl.setText(
                f"<b>VCD:</b> {exc}")
            return
        result = {
            "success": True,
            "estimated": True,
            "quantity": selected.damper_count,
            "velocity_fpm": velocity,
            "dp_inwg": damper_dp,
            "branch_dp_inwg": branch_dp,
            "diffuser_dp_inwg": diffuser_dp_inwg,
            "nominal_width_mm": selected.nominal_width_mm,
            "nominal_height_mm": selected.nominal_height_mm,
            "casing_width_mm": selected.casing_width_mm,
            "casing_height_mm": selected.casing_height_mm,
            "blade_count": selected.blade_count,
            "drive_arm_x_mm": selected.drive_arm_x_mm,
            "warnings": list(selected.warnings),
        }
        item["vcd_selection"] = result
        warning = (
            "<br><b>Review:</b> " + "; ".join(selected.warnings)
            if selected.warnings else "")
        self.diff_vcd_result_lbl.setText(
            f"<b>Estimated VCD ΔP:</b> {damper_dp:.3f} in.wg at "
            f"{velocity:,.0f} fpm; diffuser ΔP {diffuser_dp_inwg:.3f}; "
            f"branch total {branch_dp:.3f} in.wg (not screened against "
            f"the diffuser max-ΔP limit)."
            + warning)

    def _on_vcd_dimensions_changed(self):
        sys_name = self.diff_sys_combo.currentText()
        if sys_name in self.cart:
            self.cart[sys_name]["vcd_duct_width_mm"] = (
                self.diff_vcd_width_mm.value())
            self.cart[sys_name]["vcd_duct_height_mm"] = (
                self.diff_vcd_height_mm.value())
            self._mark_project_dirty()
        self._calculate_current_diffuser()

    def _on_rvcd_inputs_changed(self):
        sys_name = self.diff_sys_combo.currentText()
        if sys_name in self.cart:
            item = self.cart[sys_name]
            item["rvcd_diameter_in"] = (
                self.diff_rvcd_diameter_in.value())
            item["rvcd_pressure_class"] = (
                self.diff_rvcd_pressure_class.currentText())
            self._mark_project_dirty()
        self._calculate_current_diffuser()

    def _update_return_accessories(self, item, return_cfm):
        from diffuser_calculator import access_door_select, rvcd_select

        try:
            door = access_door_select(return_cfm)
        except ValueError as exc:
            item["access_door_selection"] = {
                "success": False, "cfm": return_cfm, "error": str(exc)}
            self.diff_access_door_result_lbl.setText(
                f"<b>Access/transfer grille:</b> {exc}")
        else:
            door_data = {
                "success": True,
                "cfm": return_cfm,
                "width_in": door.width_in,
                "height_in": door.height_in,
                "size": door.size_label,
                "quantity": 1,
                "min_cfm": door.min_cfm,
                "max_cfm": door.max_cfm,
                "area_in2": door.area_in2,
                "blade_angle_deg": 45,
                "blade_pitch_mm": 19,
                "filter_optional_depth_in": 1,
                "notes": list(door.notes),
            }
            item["access_door_selection"] = door_data
            notes = (
                "<br><b>Review:</b> " + "; ".join(door.notes)
                if door.notes else "")
            self.diff_access_door_result_lbl.setText(
                f"<b>Access/transfer grille option:</b> "
                f"{door.size_label}&quot;, 1 × at {return_cfm:,.0f} CFM "
                f"(catalogue range {door.min_cfm}–{door.max_cfm} CFM); "
                "45° blades, 19 mm pitch, optional 1&quot; filter."
                + notes)

        diameter = float(self.diff_rvcd_diameter_in.value())
        pressure_class = self.diff_rvcd_pressure_class.currentText()
        item["rvcd_diameter_in"] = diameter
        item["rvcd_pressure_class"] = pressure_class
        if diameter <= 0:
            item.pop("rvcd_selection", None)
            self.diff_rvcd_result_lbl.clear()
            return
        try:
            selected = rvcd_select(diameter, return_cfm, pressure_class)
        except ValueError as exc:
            item["rvcd_selection"] = {
                "success": False, "error": str(exc)}
            self.diff_rvcd_result_lbl.setText(f"<b>RVCD:</b> {exc}")
            return
        rvcd_data = {
            "success": True,
            "pressure_class": selected.pressure_class,
            "diameter_in": selected.diameter_in,
            "frame_allowance_in": selected.frame_allowance_in,
            "overall_length_in": selected.overall_length_in,
            "velocity_fpm": selected.velocity_fpm,
            "dp_inwg": selected.pressure_drop_inwg,
            "leakage_cfm_at_1_inwg": selected.leakage_cfm_at_1_inwg,
            "estimated": True,
            "warning": selected.warning,
        }
        item["rvcd_selection"] = rvcd_data
        frame_text = (
            f"{selected.overall_length_in:.3f} in overall length"
            if selected.overall_length_in is not None
            else "frame allowance unavailable")
        warning = (
            "<br><b>Review:</b> " + selected.warning
            if selected.warning else "")
        self.diff_rvcd_result_lbl.setText(
            f"<b>Estimated {selected.pressure_class} RVCD:</b> "
            f'{selected.diameter_in:g}&quot;, {frame_text}; '
            f"{selected.velocity_fpm:,.0f} fpm, "
            f"ΔP≈{selected.pressure_drop_inwg:.3f} in.wg; "
            f"closed leakage≈{selected.leakage_cfm_at_1_inwg:.2f} CFM "
            "at 1 in.wg."
            + warning)

    def _calculate_current_diffuser(self, *, replace_invalid_saved_layout=False):
        """Select, place, and evaluate diffusers against the current geometry."""
        sys_name = self.diff_sys_combo.currentText()
        if not sys_name or sys_name not in self.cart:
            if hasattr(self, "diff_apply_fallback_btn"):
                self.diff_apply_fallback_btn.setEnabled(False)
            return
        item = self.cart[sys_name]
        previous_selection = item.get("diffuser_selection") or {}
        from airflow_utils import (
            design_airflow_cfm,
            normalize_airflow_fields,
        )
        normalize_airflow_fields(item)
        cfm = design_airflow_cfm(item)
        sensible = float(item.get("sensible_btu") or 0)
        sensible_load_btuh = sensible if sensible > 0 else None
        from diffuser_calculator import (
            ADPI_BANDS,
            APPLICATION_NC,
            ach,
            diffuser_face_half_extent_m,
            ft_to_m,
            get_catalog,
            m_to_ft,
            rank_options,
            room_layout_from_geometry,
            DiffuserLayoutResult,
            evaluate_layout,
            select_layout_for_geometry,
            select_return_diffusers,
            selection_input_signature,
            selection_signatures_match,
        )
        from room_geometry import RoomGeometry

        app_map = {
            "Office (Private)": "office_priv", "Office (Open Plan)": "office_open",
            "Conference / Meeting": "conference", "Classroom": "classroom",
            "Hospital / Clinic": "hospital", "Residential": "residential",
            "Retail / Showroom": "retail", "Industrial": "industrial",
            "Server Room": "server_room",
        }
        app_key = app_map.get(self.diff_app_combo.currentText(), "office_priv")
        type_label = self.diff_type_combo.currentText()
        catalog = get_catalog(type_label)
        max_nc_val = self.diff_max_nc.value()
        max_nc = max_nc_val if max_nc_val > 0 else None
        max_dp = self.diff_max_dp.value()
        return_cfm = self.diff_return_cfm.value()
        item["return_cfm"] = return_cfm

        cad_room = item.get("cad_room_data")
        for widget in (self.diff_width, self.diff_length, self.diff_height):
            widget.setEnabled(cad_room is None)
        geometry_error = None
        if cad_room is not None:
            try:
                geometry = RoomGeometry.from_dict(cad_room)
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                geometry = RoomGeometry.from_vertices([], 0, closed=False)
                geometry_error = str(exc)
            self.diff_width.setEnabled(False)
            self.diff_length.setEnabled(False)
            if geometry.is_valid:
                min_x, max_x, min_y, max_y = geometry.bounding_box
                w_ft, l_ft = m_to_ft(max_x - min_x), m_to_ft(max_y - min_y)
                h_ft = m_to_ft(geometry.height_m)
            else:
                w_ft = l_ft = h_ft = 0.0
        else:
            self.diff_width.setEnabled(True)
            self.diff_length.setEnabled(True)
            w_ft, l_ft, h_ft = (
                self.diff_width.value(), self.diff_length.value(),
                self.diff_height.value())
            geometry = RoomGeometry.from_vertices(
                [(0, 0), (ft_to_m(w_ft), 0),
                 (ft_to_m(w_ft), ft_to_m(l_ft)), (0, ft_to_m(l_ft))],
                ft_to_m(h_ft))

        try:
            return_selection = select_return_diffusers(
                return_cfm, application=app_key, max_nc=max_nc,
                max_dp=max_dp, ceiling_height_ft=h_ft)
        except ValueError as exc:
            item["return_diffuser_selection"] = {
                "success": False, "error": str(exc), "cfm": return_cfm}
            self.diff_return_result_lbl.setText(
                f"<b>Return diffuser:</b> No compliant selection — {exc}")
        else:
            return_data = {
                "success": True,
                "model": f"RETURN-CCD-{return_selection.size_in}",
                "quantity": return_selection.quantity,
                "size": f'{return_selection.size_in}"',
                "cfm_each": return_selection.cfm_each,
                "velocity_fpm": return_selection.velocity_fpm,
                "dp_inwg": return_selection.pressure_drop_inwg,
                "nc": return_selection.nc,
                "nc_label": return_selection.nc_label,
                "cfm": return_cfm,
                "warnings": list(return_selection.warnings),
            }
            item["return_diffuser_selection"] = return_data
            return_warning = (
                "<br><b>Review:</b> "
                + "<br>".join(return_selection.warnings)
                if return_selection.warnings else "")
            self.diff_return_result_lbl.setText(
                f"<b>Return CCD:</b> {return_selection.quantity} × "
                f'{return_selection.size_in}" — '
                f"{return_selection.cfm_each:,.0f} CFM each, "
                f"{return_selection.velocity_fpm:,.0f} fpm, "
                f"ΔP {return_selection.pressure_drop_inwg:.3f} in.wg, "
                f"NC {return_selection.nc_label} (no throw)"
                + return_warning)
        if hasattr(self, "diff_access_door_result_lbl"):
            self._update_return_accessories(item, return_cfm)

        selected_option_index = int(
            previous_selection.get("selected_option_index", 0) or 0)
        saved_layout_errors = list(
            (cad_room or {}).get("layout_validation_errors", []))
        ranked_candidates = []
        if geometry.is_valid and cfm > 0:
            ranked_candidates = rank_options(
                cfm, application=app_key, catalog=catalog,
                max_nc=max_nc, max_dp_inwg=max_dp,
                room=room_layout_from_geometry(geometry),
                sensible_load_btuh=sensible_load_btuh, top=8)
            if ranked_candidates:
                selected_option_index = max(
                    0, min(selected_option_index, len(ranked_candidates) - 1))
                self._populate_diffuser_candidate_choices(
                    ranked_candidates, selected_option_index)
            else:
                self._populate_diffuser_candidate_choices([], 0)
        else:
            self._populate_diffuser_candidate_choices([], 0)
        if cad_room is not None:
            saved_diffusers = cad_room.get("diffusers", []) or []
            face_half_extent_m = None
            if saved_diffusers and geometry.is_valid:
                selected_candidate = (
                    ranked_candidates[selected_option_index]
                    if ranked_candidates and 0 <= selected_option_index < len(ranked_candidates)
                    else (ranked_candidates[0] if ranked_candidates else None))
                if selected_candidate is not None:
                    face_half_extent_m = diffuser_face_half_extent_m(
                        selected_candidate)

            if saved_diffusers:
                detected_layout_errors = []
                if not geometry.is_valid:
                    detected_layout_errors.append(
                        ("Saved diffuser layout cannot be verified because "
                         "room geometry is invalid.")
                    )
                else:
                    from placement_engine import (
                        DEFAULT_MIN_SPACING_M,
                        EPSILON,
                        validate_diffuser_layout,
                    )

                    saved_positions = []
                    for index, diffuser in enumerate(saved_diffusers):
                        tag = f"D{index + 1}"
                        try:
                            tag = str(diffuser.get("tag") or tag)
                            position = (
                                float(diffuser["x_m"]),
                                float(diffuser["y_m"]),
                            )
                        except (AttributeError, KeyError, TypeError, ValueError):
                            detected_layout_errors.append(
                                f"{tag}: saved diffuser has malformed coordinates.")
                            continue
                        saved_positions.append((tag, position))
                        validation = validate_diffuser_layout(
                            geometry, [position],
                            face_half_extent_m=face_half_extent_m)
                        if not validation.valid:
                            detected_layout_errors.append(
                                f"{tag}: {validation.reason}")
                        detected_layout_errors.extend(validation.limitations)

                    for index, (tag_a, position_a) in enumerate(saved_positions):
                        for tag_b, position_b in saved_positions[index + 1:]:
                            distance = math.hypot(
                                position_a[0] - position_b[0],
                                position_a[1] - position_b[1])
                            if distance < DEFAULT_MIN_SPACING_M - EPSILON:
                                detected_layout_errors.append(
                                    f"{tag_a} and {tag_b} are closer than the "
                                    f"{DEFAULT_MIN_SPACING_M:.3f} m minimum "
                                    "center spacing.")

                saved_layout_errors = list(dict.fromkeys(
                    saved_layout_errors + detected_layout_errors))
                if saved_layout_errors:
                    cad_room["layout_validation_errors"] = list(
                        saved_layout_errors)
                    cad_room["layout_stale"] = True
        if cad_room is not None and saved_layout_errors:
            cad_room["layout_validation_errors"] = list(saved_layout_errors)
            cad_room["layout_stale"] = True
            if not geometry.is_valid or not replace_invalid_saved_layout:
                retained_positions = []
                if geometry.is_valid:
                    for diffuser in cad_room.get("diffusers", []):
                        try:
                            x_m = float(diffuser["x_m"])
                            y_m = float(diffuser["y_m"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if math.isfinite(x_m) and math.isfinite(y_m):
                            retained_positions.append(
                                (m_to_ft(x_m), m_to_ft(y_m)))
                self.diff_model_name.setText("Saved diffuser layout needs review")
                self.diff_metrics_lbl.setText(
                    "<b>Saved diffuser coordinates were not replaced.</b><br>"
                    + "<br>".join(saved_layout_errors))
                self.diff_apply_fallback_btn.setEnabled(False)
                if hasattr(self, "diff_recalc_btn"):
                    self.diff_recalc_btn.setText("Re-place diffusers")
                    self.diff_recalc_btn.setEnabled(geometry.is_valid)
                self.diff_canvas.set_data(
                    max(w_ft, 1.0), max(l_ft, 1.0), retained_positions,
                    0.0, "", cad_room=cad_room,
                    geometry=geometry if geometry.is_valid else None)
                self.status.setText(
                    f"Diffuser layout for {sys_name} needs review.")
                selection_metadata = {
                    key: previous_selection[key]
                    for key in (
                        "app_key", "type_label", "max_nc", "max_dp",
                        "room_w_ft", "room_l_ft", "room_h_ft",
                    )
                    if key in previous_selection
                }
                item["diffuser_selection"] = {
                    **selection_metadata,
                    "success": False,
                    "status": "invalid_saved_layout",
                    "layout_stale": True,
                    "required_cfm": cfm,
                    "errors": saved_layout_errors,
                }
                self._mark_project_dirty()
                self._render_diffusers_summary_table()
                return

        if hasattr(self, "diff_recalc_btn"):
            self.diff_recalc_btn.setText("🔄 Recalculate")
            self.diff_recalc_btn.setEnabled(True)
        geometry_signature = {
            "vertices": [list(point) for point in geometry.vertices],
            "height_m": geometry.height_m,
        }
        input_signature = selection_input_signature(
            cfm, sensible_load_btuh, geometry_signature, type_label,
            app_key, max_nc, max_dp, selected_option_index)
        self.diff_apply_fallback_btn.setEnabled(False)
        cached_layout = None
        saved_diffusers = (cad_room.get("diffusers", []) or []
                           if cad_room is not None else [])
        previous_signature = previous_selection.get("input_signature")
        same_selection_inputs = selection_signatures_match(
            previous_signature, input_signature)
        if (cad_room is not None and saved_diffusers
                and not saved_layout_errors
                and not replace_invalid_saved_layout
                and same_selection_inputs):
            saved_positions_m = [
                (float(diffuser["x_m"]), float(diffuser["y_m"]))
                for diffuser in saved_diffusers
            ]
            saved_positions_ft = [
                (m_to_ft(x), m_to_ft(y)) for x, y in saved_positions_m]
            evaluated, _ = evaluate_layout(
                cfm, positions_ft=saved_positions_ft,
                room=room_layout_from_geometry(geometry),
                application=app_key, catalog=catalog, max_nc=max_nc,
                max_dp_inwg=max_dp, sensible_load_btuh=sensible_load_btuh)
            configured_nc = (
                max_nc if max_nc is not None and max_nc > 0
                else APPLICATION_NC.get(app_key, 35))
            compliant = (
                evaluated.adpi_ok
                and evaluated.nc_estimated <= configured_nc
                and not any(
                    "CFM/diffuser exceeds Table 4" in warning
                    for warning in evaluated.warnings)
                and not any(
                    warning.startswith("Neck velocity ")
                    for warning in evaluated.warnings))
            cached_layout = DiffuserLayoutResult(
                success=compliant, geometry=geometry,
                requested_quantity=len(saved_positions_ft),
                recommended_quantity=len(saved_positions_ft),
                placed_quantity=len(saved_positions_ft),
                required_cfm=cfm, actual_total_cfm=cfm,
                selected_model=evaluated, positions_m=saved_positions_m,
                positions_ft=saved_positions_ft, performance=evaluated,
                wall_clearance_ok=True, spacing_ok=True,
                failure_kind=None if compliant else "no_fully_compliant_option",
                fallback_available=not compliant,
                warnings=list(evaluated.warnings),
                errors=[] if compliant else [
                    "Saved diffuser positions require review against current selection criteria."])
        if not geometry.is_valid:
            layout = None
            failure_kind = "invalid_geometry"
            errors = geometry.validate()
            if geometry_error:
                errors.insert(0, geometry_error)
        elif cfm <= 0:
            layout = None
            failure_kind = "airflow_infeasible"
            errors = ["Required airflow must be greater than zero."]
        elif cached_layout is not None:
            layout = cached_layout
            failure_kind = layout.failure_kind
            errors = list(layout.errors)
        else:
            layout = select_layout_for_geometry(
                geometry, cfm, application=app_key, catalog=catalog,
                max_nc=max_nc, max_dp_inwg=max_dp,
                sensible_load_btuh=sensible_load_btuh,
                selected_option_index=selected_option_index)
            failure_kind = layout.failure_kind
            errors = list(layout.errors)

        if layout is None or not layout.success:
            fallback = bool(
                layout is not None and layout.fallback_available
                and layout.performance is not None)
            fallback_selection = layout.performance if fallback else None
            fallback_positions_ft = (
                list(layout.positions_ft) if fallback else [])
            fallback_positions_m = (
                list(layout.positions_m) if fallback else [])
            fallback_warnings = list(layout.warnings) if fallback else []
            fallback_data = {}
            if fallback_selection is not None:
                configured_nc = (
                    max_nc if max_nc is not None and max_nc > 0
                    else APPLICATION_NC.get(app_key, 35))
                table4_ok = not any(
                    "CFM/diffuser exceeds Table 4" in warning or warning.startswith(("Supply ΔT ", "Neck velocity "))
                    for warning in fallback_warnings)
                fallback_data = {
                    "model": fallback_selection.model,
                    "size": fallback_selection.size_label,
                    "qty": layout.placed_quantity,
                    "cfm_each": fallback_selection.cfm_per_unit,
                    "v_fpm": fallback_selection.neck_velocity_fpm,
                    "dp_inwg": fallback_selection.pressure_drop_inwg,
                    "nc": fallback_selection.nc_estimated,
                    "t50_ft": fallback_selection.throw_t50_ft,
                    "t100_ft": fallback_selection.throw_t100_ft,
                    "ach": ach(cfm, room_layout_from_geometry(geometry)),
                    "adpi_pass": fallback_selection.adpi_ok,
                    "adpi_val": fallback_selection.adpi_ratio,
                    "adpi_val_min": fallback_selection.adpi_ratio_min,
                    "delta_t_f": fallback_selection.delta_t_f,
                    "delta_t_limit_f": fallback_selection.delta_t_limit_f,
                    "rationale": dict(fallback_selection.rationale),
                    "positions": fallback_positions_ft,
                    "positions_m": fallback_positions_m,
                    "fallback_available": True,
                    "fallback_criteria": {
                        "catalog_ok": fallback_selection.rationale.get(
                            "catalog_valid", not any(
                                warning.startswith("Neck velocity ")
                                for warning in fallback_warnings)),
                        "t50_l_ok": fallback_selection.adpi_ok,
                        "table4_ok": fallback_selection.rationale.get(
                            "table4_airflow_compliant", table4_ok),
                        "nc_ok": fallback_selection.rationale.get(
                            "noise_compliant",
                            fallback_selection.nc_estimated <= configured_nc),
                    },
                }
            selection_data = {
                "success": False, "status": failure_kind or "infeasible",
                "fallback_failure_kind": failure_kind if fallback else None,
                "requested_quantity": (
                    layout.requested_quantity if layout else 0),
                "recommended_quantity": (
                    layout.recommended_quantity if layout else 0),
                "required_cfm": cfm,
                "actual_total_cfm": layout.actual_total_cfm if fallback else 0.0,
                "placed_quantity": layout.placed_quantity if fallback else 0,
                "errors": errors,
                "warnings": fallback_warnings,
                **fallback_data,
                "geometry_signature": geometry_signature,
                "input_signature": input_signature,
                "layout_stale": cad_room is not None,
                "room_w_ft": w_ft, "room_l_ft": l_ft, "room_h_ft": h_ft,
                "app_key": app_key, "type_label": type_label,
                "max_nc": max_nc_val, "max_dp": max_dp,
                "is_cad": cad_room is not None,
            }
            preserve_acceptance = (
                fallback
                and failure_kind == "no_fully_compliant_option"
                and previous_selection.get("fallback_accepted")
                and previous_selection.get("input_signature") == input_signature)
            if preserve_acceptance:
                selection_data.update({
                    "success": True,
                    "status": "fallback_accepted",
                    "fallback_accepted": True,
                    "layout_stale": False,
                })
                accepted_warning = (
                    "Fallback layout accepted by user despite failed screening "
                    "criteria; engineering review remains required.")
                selection_data["warnings"].append(accepted_warning)
            item["diffuser_selection"] = selection_data
            if (cad_room is not None and not fallback
                    and not saved_layout_errors):
                cad_room["diffusers"] = []
                cad_room["layout_stale"] = True
            elif cad_room is not None and fallback and not preserve_acceptance:
                cad_room["layout_stale"] = True
            self._mark_project_dirty()
            if fallback:
                self._update_vcd_branch_result(
                    item, fallback_selection.cfm_per_unit,
                    fallback_selection.pressure_drop_inwg)
                self.diff_model_name.setText(
                    f"{'Fallback Accepted' if preserve_acceptance else 'Fallback review'}: "
                    f"{layout.placed_quantity} × "
                    f"{fallback_selection.size_label} ({fallback_selection.model})")
                self.diff_metrics_lbl.setText(
                    ui_shell.format_fallback_status(selection_data))
                ui_shell.set_diffuser_rationale(
                    self.diff_rationale_lbl, fallback_selection.rationale)
                self.diff_apply_fallback_btn.setEnabled(
                    failure_kind == "no_fully_compliant_option"
                    and not preserve_acceptance)
                throw_t50_ft = fallback_selection.throw_t50_ft
                canvas_positions = fallback_positions_ft
                canvas_model = fallback_selection.model
            else:
                item.pop("vcd_selection", None)
                self.diff_vcd_result_lbl.clear()
                self.diff_model_name.setText("No feasible diffuser layout")
                self.diff_metrics_lbl.setText(
                    "<b>Placement failed:</b> " + "<br>".join(errors))
                ui_shell.set_diffuser_rationale(
                    self.diff_rationale_lbl,
                    {"summary": failure_kind.replace("_", " ").title()})
                throw_t50_ft = 0
                canvas_positions = []
                canvas_model = ""
            self.diff_canvas.set_data(
                max(w_ft, 1.0), max(l_ft, 1.0), canvas_positions,
                throw_t50_ft, canvas_model,
                cad_room=cad_room if geometry.is_valid else None,
                geometry=geometry if geometry.is_valid else None)
            detail = "; ".join(errors)
            self.status.setText(
                f"{'Review required' if fallback else 'Calculation failed'} "
                f"for {sys_name}: {detail or failure_kind or 'No valid layout'}.")
            self._render_diffusers_summary_table()
            return

        sel = layout.performance
        self._update_vcd_branch_result(
            item, sel.cfm_per_unit, sel.pressure_drop_inwg)
        room = room_layout_from_geometry(geometry)
        room_ach = ach(cfm, room)
        positions = layout.positions_ft
        adpi_pass = sel.adpi_ok
        adpi_val = sel.adpi_ratio
        adpi_val_min = sel.adpi_ratio_min
        adpi_lo, adpi_hi = ADPI_BANDS.get(sel.kind, (0.7, 1.2))
        delta_t_f = sel.delta_t_f
        delta_t_limit = sel.delta_t_limit_f
        if cad_room is not None:
            diffuser_positions = _cad_diffuser_records(
                cad_room, layout.positions_m, ft_to_m(sel.throw_t50_ft))
            cad_room["diffusers"] = diffuser_positions
            cad_room["layout_stale"] = False
            cad_room["layout_validation_errors"] = []

        item["diffuser_selection"] = {
            "success": True, "status": "valid",
            "model": sel.model, "size": sel.size_label,
            "requested_quantity": layout.requested_quantity,
            "recommended_quantity": layout.recommended_quantity,
            "placed_quantity": layout.placed_quantity,
            "required_cfm": layout.required_cfm,
            "actual_total_cfm": layout.actual_total_cfm,
            "qty": layout.placed_quantity, "cfm_each": sel.cfm_per_unit,
            "v_fpm": sel.neck_velocity_fpm,
            "dp_inwg": sel.pressure_drop_inwg, "nc": sel.nc_estimated,
            "t50_ft": sel.throw_t50_ft, "t100_ft": sel.throw_t100_ft,
            "spacing_max_ft": sel.spacing_max_ft, "ach": room_ach,
            "adpi_pass": adpi_pass, "adpi_val": adpi_val,
            "adpi_val_min": adpi_val_min,
            "adpi_window": f"{adpi_lo}-{adpi_hi}",
            "delta_t_f": delta_t_f,
            "delta_t_limit_f": delta_t_limit,
            "rationale": dict(sel.rationale),
            "input_signature": input_signature,
            "positions": positions, "positions_m": layout.positions_m,
            "room_w_ft": w_ft, "room_l_ft": l_ft, "room_h_ft": h_ft,
            "app_key": app_key, "type_label": type_label,
            "max_nc": max_nc_val, "max_dp": max_dp,
            "warnings": list(layout.warnings), "errors": [],
            "geometry_signature": geometry_signature,
            "wall_clearance_ok": layout.wall_clearance_ok,
            "spacing_ok": layout.spacing_ok,
            "layout_stale": False,
            "is_cad": cad_room is not None,
        }
        self._mark_project_dirty()

        self.diff_model_name.setText(
            f"{sel.qty} × {sel.model} ({sel.size_label})"
            + (" [CAD]" if cad_room is not None else ""))
        ui_shell.set_diffuser_rationale(self.diff_rationale_lbl, sel.rationale)
        if delta_t_f is None:
            dt_html = "Unverified (sensible load unavailable)"
        else:
            dt_over = (
                delta_t_limit is not None and delta_t_f > delta_t_limit)
            dt_color = "#F59E0B" if dt_over else "#10B981"
            dt_html = (
                f"<span style='color:{dt_color};font-weight:700'>"
                f"{delta_t_f:.1f}°F</span>")
            if delta_t_limit is not None:
                dt_html += f" (Table 4 max {delta_t_limit:.0f}°F)"
        warn_html = ""
        if layout.warnings:
            items = "".join(f"<li>{w}</li>" for w in layout.warnings)
            warn_html = (
                "<div style='margin-top:8px;padding:8px 10px;"
                "border:1px solid #B45309;border-radius:6px;"
                "background:#3F2A14'>"
                "<b style='color:#FBBF24'>Engineering review</b>"
                f"<ul style='margin:6px 0 0 18px;color:#FDE68A'>"
                f"{items}</ul></div>"
            )
        self.diff_metrics_lbl.setText(
            f"<b>CFM / unit:</b> {sel.cfm_per_unit:,.0f} CFM &nbsp;|&nbsp; "
            f"<b>Neck Velocity:</b> {sel.neck_velocity_fpm:,.0f} FPM &nbsp;|&nbsp; "
            f"<b>ΔP:</b> {sel.pressure_drop_inwg:.4f} in.wg &nbsp;|&nbsp; "
            f"<b>Sound:</b> NC {sel.nc_estimated:.0f} dB<br>"
            f"<b>Throw (T100-T50):</b> {sel.throw_t100_ft:.1f} – "
            f"{sel.throw_t50_ft:.1f} ft &nbsp;|&nbsp; "
            f"<b>ACH:</b> {room_ach:.1f} &nbsp;|&nbsp; "
            f"<b>ΔT:</b> {dt_html}<br>"
            f"{ui_shell.format_t50_screening(adpi_pass, adpi_val_min, adpi_val, adpi_lo, adpi_hi)}"
            + warn_html)
        self.diff_canvas.set_data(
            max(w_ft, 1.0), max(l_ft, 1.0), positions,
            sel.throw_t50_ft, sel.model, cad_room=cad_room,
            geometry=geometry)
        self._render_diffusers_summary_table()
        self.status.setText(
            f"Diffuser layout calculated for {sys_name}: "
            f"{layout.placed_quantity} × {sel.size_label} ({sel.model})"
            + ("; engineering review required." if layout.warnings else "."))
        self._mark_project_dirty()

    def _on_return_cfm_changed(self, value):
        """Persist the editable return airflow for the current system."""
        sys_name = self.diff_sys_combo.currentText()
        if sys_name in self.cart:
            self.cart[sys_name]["return_cfm"] = float(value)
            self._mark_project_dirty()
        self._calculate_current_diffuser()

    def _apply_diffuser_fallback(self):
        """Accept the current review candidate while preserving its warning status."""
        sys_name = self.diff_sys_combo.currentText()
        item = self.cart.get(sys_name)
        if not item:
            return
        selection = item.get("diffuser_selection") or {}
        if (selection.get("status") != "no_fully_compliant_option"
                or not selection.get("fallback_available")
                or not selection.get("positions")
                or selection.get("fallback_accepted")):
            self.diff_apply_fallback_btn.setEnabled(False)
            return

        selection["success"] = True
        selection["status"] = "fallback_accepted"
        selection["fallback_accepted"] = True
        selection["layout_stale"] = False
        selection["warnings"] = list(selection.get("warnings", []))
        acceptance_warning = (
            "Fallback layout accepted by user despite failed screening criteria; "
            "engineering review remains required.")
        if acceptance_warning not in selection["warnings"]:
            selection["warnings"].append(acceptance_warning)

        cad_room = item.get("cad_room_data")
        if cad_room is not None:
            from diffuser_calculator import ft_to_m
            positions_m = selection.get("positions_m", [])
            t50_m = ft_to_m(float(selection.get("t50_ft", 0) or 0))
            cad_room["diffusers"] = _cad_diffuser_records(
                cad_room, positions_m, t50_m)
            cad_room["layout_stale"] = False
            cad_room["layout_validation_errors"] = []
            self.diff_canvas.cad_room = cad_room
        self._mark_project_dirty()

        self.diff_model_name.setText(
            f"Fallback Accepted: {selection['qty']} × "
            f"{selection['size']} ({selection['model']})")
        self.diff_metrics_lbl.setText(ui_shell.format_fallback_status(selection))
        self.diff_canvas.set_data(
            max(float(selection.get("room_w_ft", 1) or 1), 1),
            max(float(selection.get("room_l_ft", 1) or 1), 1),
            selection.get("positions", []),
            float(selection.get("t50_ft", 0) or 0),
            selection.get("model", ""),
            cad_room=cad_room,
        )
        ui_shell.set_diffuser_rationale(
            self.diff_rationale_lbl, selection.get("rationale", {}))
        self.diff_apply_fallback_btn.setEnabled(False)
        self._render_diffusers_summary_table()
        self._refresh_shell()
        self.status.setText(
            f"Fallback diffuser layout accepted for {sys_name}; engineering "
            "review is still required.")
        self.status.setStyleSheet("color: #fbbf24;")

    def _auto_select_all_diffusers(self):
        """Iterates through all cart items and solves optimal diffusers efficiently."""
        if not self.cart:
            QMessageBox.information(
                self, "Empty Cart", "No systems in the cart to process."
            )
            return

        self.diff_sys_combo.blockSignals(True)
        failures = []
        try:
            for sys_name in list(self.cart.keys()):
                self.diff_sys_combo.setCurrentText(sys_name)
                try:
                    self._calculate_current_diffuser()
                    result = self.cart[sys_name].get("diffuser_selection", {})
                    if not result.get("success", False):
                        detail = "; ".join(result.get("errors", []))
                        failures.append(
                            f"{sys_name}: {detail or result.get('status', 'selection failed')}")
                except (ValueError, TypeError, KeyError, IndexError,
                        RuntimeError) as exc:
                    logging.getLogger(__name__).exception(
                        "Diffuser selection failed for %s", sys_name)
                    self.cart[sys_name]["diffuser_selection"] = {
                        "success": False,
                        "status": "calculation_error",
                        "errors": [str(exc)],
                    }
                    failures.append(f"{sys_name}: {exc}")
        finally:
            self.diff_sys_combo.blockSignals(False)

        self._on_diffuser_sys_changed()
        if failures:
            QMessageBox.warning(
                self, "Diffuser selection completed with failures",
                "Selections were attempted for every system. Review these failures:\n\n"
                + "\n".join(failures))
        else:
            QMessageBox.information(
                self, "Completed",
                "Optimized diffuser selection completed for all systems.")

    def open_room_designer_for_current_system(self):
        """Open the Room Designer with bidirectional project synchronization."""
        sys_name = self.diff_sys_combo.currentText()
        if not sys_name or sys_name not in self.cart:
            QMessageBox.information(
                self, "No System Selected",
                "Please select a system from the cart first.")
            return

        item = self.cart[sys_name]
        from airflow_utils import design_airflow_cfm
        cfm = design_airflow_cfm(item)
        existing_room = item.get("cad_room_data")
        selection = item.get("diffuser_selection") or {}

        try:
            from diffuser_calculator import (
                ft_to_m,
                get_catalog,
                m_to_ft,
                rank_options,
            )
            from room_designer import RoomDesignerDialog
        except (ImportError, SyntaxError) as e:
            QMessageBox.critical(self, "Import Error",
                                 f"Cannot import room_designer: {e}")
            return

        # Build a rectangular room from the input dimensions if no design exists.
        if not existing_room:
            w_m = round(ft_to_m(self.diff_width.value()), 2)
            l_m = round(ft_to_m(self.diff_length.value()), 2)
            h_m = round(ft_to_m(self.diff_height.value()), 2)
            walls = [
                {"length": w_m, "angle": 0.0},
                {"length": l_m, "angle": 90.0},
                {"length": w_m, "angle": 180.0},
                {"length": l_m, "angle": 270.0},
            ]
            ds = item.get("diffuser_selection") or {}
            pos = ds.get("positions", [])
            t50_m = ft_to_m(float(ds.get("t50_ft", 8.0) or 8.0))
            existing_room = {
                "name": sys_name,
                "height": h_m,
                "wall_thickness": 0.20,
                "walls": walls,
                "openings": [],
                "diffusers": [
                    {"tag": f"D{i+1}",
                     "x_m": ft_to_m(px), "y_m": ft_to_m(py),
                     "throw_t50_m": t50_m}
                    for i, (px, py) in enumerate(pos)
                ],
            }

        app_key = selection.get("app_key", "office_priv")
        type_label = selection.get("type_label", "Circular")
        max_nc = selection.get("max_nc") or None
        max_dp = float(selection.get("max_dp", 0.20) or 0.20)
        sensible_load = float(item.get("sensible_btu", 0) or 0)
        sensible_load_btuh = sensible_load if sensible_load > 0 else None

        def recommended_diffuser_count(room_layout):
            if cfm <= 0:
                return None
            options = rank_options(
                cfm, application=app_key, catalog=get_catalog(type_label),
                max_nc=max_nc, max_dp_inwg=max_dp, room=room_layout,
                sensible_load_btuh=sensible_load_btuh, top=1)
            return options[0] if options else None

        dlg = RoomDesignerDialog(
            parent=self, room_data=existing_room,
            system_cfm=cfm, system_name=sys_name,
            n_target_provider=recommended_diffuser_count)

        if dlg.exec():
            res = dlg.get_result_data()
            item["cad_room_data"] = res["room_dict"]
            item["cad_room_data"]["layout_stale"] = bool(res["layout_stale"])
            item["cad_room_data"]["layout_validation_errors"] = list(
                res.get("layout_validation_errors", []))
            self._mark_project_dirty()
            item["floor_area_ft2"] = res["area_sqft"]

            room_w_ft = res["room_w_ft"]
            room_l_ft = res["room_l_ft"]
            self.diff_width.blockSignals(True)
            self.diff_length.blockSignals(True)
            self.diff_height.blockSignals(True)
            self.diff_width.setValue(round(room_w_ft, 1))
            self.diff_length.setValue(round(room_l_ft, 1))
            self.diff_height.setValue(round(res["height_ft"], 1))
            self.diff_width.blockSignals(False)
            self.diff_length.blockSignals(False)
            self.diff_height.blockSignals(False)

            self.diff_canvas.cad_room = res["room_dict"]

            diff_list = res.get("diffusers") or res["room_dict"].get("diffusers", [])
            layout_validation_errors = list(
                res.get("layout_validation_errors", []))
            if diff_list and not res["layout_stale"]:
                positions_ft = [(m_to_ft(d["x_m"]), m_to_ft(d["y_m"]))
                                for d in diff_list]
                self.diff_canvas.positions = positions_ft
                ds = item.setdefault("diffuser_selection", {})
                ds["positions"] = positions_ft
                ds["positions_m"] = [
                    (d["x_m"], d["y_m"]) for d in diff_list]
                ds["qty"] = len(positions_ft)
                ds["cfm_each"] = cfm / max(1, len(positions_ft))
                ds["layout_stale"] = False
            elif layout_validation_errors:
                positions_ft = []
                for diffuser in diff_list:
                    try:
                        x_m = float(diffuser["x_m"])
                        y_m = float(diffuser["y_m"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if math.isfinite(x_m) and math.isfinite(y_m):
                        positions_ft.append((m_to_ft(x_m), m_to_ft(y_m)))
                self.diff_canvas.positions = positions_ft
                ds = item.setdefault("diffuser_selection", {})
                ds["positions"] = positions_ft
                ds["positions_m"] = [
                    (d["x_m"], d["y_m"]) for d in diff_list]
                ds["qty"] = len(positions_ft)
                ds["cfm_each"] = cfm / max(1, len(positions_ft))
                ds["layout_stale"] = True
                ds["success"] = False
                ds["status"] = "invalid_saved_layout"
                ds["errors"] = layout_validation_errors
            else:
                self.diff_canvas.positions = []
                ds = item.setdefault("diffuser_selection", {})
                ds["positions"] = []
                ds["positions_m"] = []
                ds["layout_stale"] = True

            if layout_validation_errors:
                self.status.setText(
                    f"⚠ CAD Room applied to {sys_name}, but its saved diffuser "
                    f"layout needs review: {'; '.join(layout_validation_errors)}")
                self.status.setStyleSheet("color: #fbbf24;")
                self._refresh_shell()
                return
            self.status.setText(
                f"📐 CAD Room applied to {sys_name}: "
                f"{res['area_m2']:.2f} m² ({res['area_sqft']:,.0f} ft²)")
            self.status.setStyleSheet("color: #34d399;")
            self._calculate_current_diffuser()
            self._refresh_shell()

    def _render_diffusers_summary_table(self):
        """Renders the overview table for all systems."""
        headers = [
            "System",
            "Model",
            "Size",
            "Qty",
            "CFM/ea",
            "V (fpm)",
            "\u0394P (in.wg)",
            "NC",
            "T50 (ft)",
            "ACH",
            "ΔT (°F)",
            "T50/L Screen (not ADPI)",
            "Status",
        ]
        self.diff_summary_table.setColumnCount(len(headers))
        self.diff_summary_table.setHorizontalHeaderLabels(headers)
        self.diff_summary_table.setRowCount(len(self.cart))

        for r, (name, it) in enumerate(self.cart.items()):
            ds = it.get("diffuser_selection")
            vals = ui_shell.diffuser_summary_values(name, ds)
            for c, val in enumerate(vals):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignCenter)
                self.diff_summary_table.setItem(r, c, cell)
        self.diff_summary_table.resizeColumnsToContents()

    def _prepare_schedule_dxf_rows(self):
        """Prepare complete consulting-grade schedule for AutoCAD DXF (Equipment + Diffusers)."""
        from airflow_utils import airflow_summary_text
        headers = [
            "TAG",
            "CATALOG",
            "SYSTEM MODEL",
            "INDOOR UNIT",
            "OUTDOOR UNIT",
            "COOL MBH",
            "SENS MBH",
            "REQUIRED CFM",
            "AVAILABLE UNIT CFM",
            "DESIGN CFM",
            "AIRFLOW POINT ESP (in.wg)",
            "AIRFLOW STATUS / PROVENANCE",
            "ESP in.wg",
            "HEAT MBH",
            "EER",
            "POWER SUPPLY",
            "MCA (A)",
            "MOP (A)",
            "LIQ. (in)",
            "GAS (in)",
            "WEIGHT (kg)",
            "SOUND dBA",
        ]
        rows = []
        for item in self.cart.values():
            pref = item.get("table_prefix", "")
            spec = report_builder.get_site_specs(self.conn, DB, pref, item["model"])

            cool_mbh = f"{(float(item.get('rated_capacity', 0) or 0) / 1000.0):.1f}"
            sens_val = item.get("sensible_btu", 0)
            sens_mbh = f"{(float(sens_val) / 1000.0):.1f}" if sens_val else "---"
            heat_val = item.get("heating_btu", 0)
            heat_mbh = f"{(float(heat_val) / 1000.0):.1f}" if heat_val else "---"

            mca_str = f"{spec.get('mca'):.1f}" if spec.get("mca") is not None else "---"
            mop_str = f"{spec.get('mop'):.0f}" if spec.get("mop") is not None else "---"
            wt_str = f"{spec.get('weight', 0):.0f}" if spec.get("weight") else "---"

            rows.append(
                [
                    str(item["system"]),
                    str(item["catalog"]),
                    str(item["model"]),
                    str(item.get("indoor_model", "") or "---"),
                    str(item.get("outdoor_model", "") or "---"),
                    cool_mbh,
                    sens_mbh,
                    f"{float(item.get('required_cfm', item.get('cfm', 0)) or 0):.0f}",
                    (f"{float(item['available_unit_cfm']):.0f}"
                     if item.get("available_unit_cfm") is not None
                     else "Unknown / unverified"),
                    f"{float(item.get('design_cfm', item.get('cfm', 0)) or 0):.0f}",
                    (f"{float(item['airflow_esp_inwg']):.3f}"
                     if item.get("airflow_esp_inwg") is not None else "Unknown"),
                    airflow_summary_text(item),
                    f"{float(item.get('esp', 0.20) or 0.20):.2f}",
                    heat_mbh,
                    f"{float(item.get('eer', 0) or 0):.2f}"
                    if item.get("eer")
                    else "---",
                    str(item.get("power_supply", "") or "---"),
                    mca_str,
                    mop_str,
                    spec.get("liquid", "---"),
                    spec.get("gas", "---"),
                    wt_str,
                    f"{float(item.get('sound_dba', 0) or 0):.1f}"
                    if item.get("sound_dba")
                    else "---",
                ]
            )

        # Prepare Diffuser Schedule Table for CAD
        diff_headers = [
            "TAG",
            "ROOM AREA",
            "DIFFUSER MODEL",
            "SIZE",
            "QTY",
            "CFM / EA",
            "NECK VEL (FPM)",
            "dP (in.wg)",
            "NOISE",
            "THROW T50 (ft)",
            "ACH",
            "T50/L SCREENING (ADPI NOT VERIFIED)",
            "ΔT (°F)",
            "TABLE 4 MAX ΔT (°F)",
            "STATUS / WARNINGS",
            "SELECTION RATIONALE",
        ]
        diff_rows = []
        from diffuser_report import _solve_system_diffuser
        for item in self.cart.values():
            ds = _solve_system_diffuser(item)
            area_str = (
                f"{item.get('floor_area_ft2', 0):.1f} SQ.FT"
                if item.get("floor_area_ft2") else "---")
            is_valid = "error" not in ds
            warnings = ds.get("warnings", [])
            if ds.get("error"):
                warnings = [ds["error"]]
            rationale = ds.get("rationale", {}).get("summary", "")
            diff_rows.append(
                [
                    str(item["system"]),
                    area_str,
                    str(ds.get("model", "---")),
                    str(ds.get("size", "---")),
                    str(ds.get("qty", "---")),
                    f"{ds.get('cfm_each', 0):,.0f}" if is_valid else "---",
                    f"{ds.get('v_fpm', 0):,.0f}" if is_valid else "---",
                    f"{ds.get('dp_inwg', 0):.4f}" if is_valid else "---",
                    f"NC {ds.get('nc', 0):.0f}" if is_valid else "---",
                    f"{ds.get('t50_ft', 0):.1f}" if is_valid else "---",
                    f"{ds.get('ach', 0):.1f}" if is_valid else "---",
                    ("Within screening band" if ds.get("adpi_ok") else
                     "Review screening estimate") if is_valid else "Unverified",
                    (f"{ds['delta_t_f']:.1f}" if ds.get("delta_t_f") is not None
                     else "Unverified"),
                    (f"{ds['delta_t_limit_f']:.1f}"
                     if ds.get("delta_t_limit_f") is not None else "---"),
                    "; ".join(warnings),
                    str(rationale),
                ])

        secondary_tables = []
        if diff_rows:
            secondary_tables.append(
                {
                    "title": "AIR OUTLET & DIFFUSER SCHEDULE (AL-ANDALOSIA)",
                    "headers": diff_headers,
                    "rows": diff_rows,
                }
            )

        return headers, rows, secondary_tables

    def export_cad_schedule(self):
        """Export the selection cart directly to an AutoCAD DXF Schedule table."""
        if not self.cart:
            QMessageBox.warning(
                self,
                "Empty Cart",
                "Add at least one model to the Selection Cart first.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export AutoCAD Schedule",
            "hvac_equipment_schedule.dxf",
            "AutoCAD DXF Files (*.dxf)",
        )
        if not path:
            return
        try:
            headers, rows, sec_tables = self._prepare_schedule_dxf_rows()
            from exporters import export_dxf

            export_dxf(
                path,
                headers,
                rows,
                title="DUCT DX EQUIPMENT SCHEDULE",
                secondary_tables=sec_tables,
            )
        except (OSError, ValueError, TypeError, IndexError,
                RuntimeError, ImportError) as exc:
            logging.getLogger(__name__).exception("CAD schedule export failed")
            QMessageBox.critical(
                self, "CAD Export Failed",
                f"Could not generate the AutoCAD schedule:\n{exc}")
            return
        self.status.setText(f"✅ AutoCAD Schedule exported → {path}")
        self.status.setStyleSheet("color: #34d399;")
        QMessageBox.information(
            self,
            "AutoCAD Export Successful",
            f"Equipment Schedule successfully generated for AutoCAD:\n{path}\n\n"
            "You can open or INSERT this DXF file directly in AutoCAD / CAD.",
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = App()
    win.show()
    sys.exit(app.exec())
