"""
manual_input.py
===============
Fallback dialog when OCR fails:
  Tab 1 → type the numbers
  Tab 2 → paste text copied from the HAP PDF

The "Project" fields (name, floor area) live OUTSIDE the QTabWidget so they
are always visible and shared between the two tabs without any QWidget
reparenting. Apply uses whichever tab is currently active — switching tabs
does not silently discard the other one's input.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hap_parser import parse_hap, parse_hap_detailed

UNIT_TONS, UNIT_MBH, UNIT_BTU = "Tons", "MBH", "Btu/hr"
UNIT_TO_BTU = {UNIT_TONS: 12000, UNIT_MBH: 1000, UNIT_BTU: 1}

TAB_FORM = 0
TAB_PASTE = 1


# ═══════════════════════════════════════════════════════════
#  Test-mode-safe message boxes
# ═══════════════════════════════════════════════════════════


def _offscreen() -> bool:
    """True when running in Qt offscreen mode (CI / headless)."""
    return os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def _safe_warning(parent, title, text):
    if _offscreen():
        print(f"[TEST MODE] Warning suppressed: {title} — {text}")
        return
    QMessageBox.warning(parent, title, text)


def _safe_critical(parent, title, text):
    if _offscreen():
        print(f"[TEST MODE] Critical suppressed: {title} — {text}")
        return
    QMessageBox.critical(parent, title, text)


def _safe_question(parent, title, text):
    """Yes/No prompt. Returns True only on Yes. In offscreen/CI mode it
    never blocks and answers No (the safe default)."""
    if _offscreen():
        print(f"[TEST MODE] Question suppressed (default No): {title} — {text}")
        return False
    return (
        QMessageBox.question(
            parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        == QMessageBox.Yes
    )


def _get_parser():
    """Return the standalone parser without re-importing the application."""
    return parse_hap


# ═══════════════════════════════════════════════════════════
#  Dialog
# ═══════════════════════════════════════════════════════════


class ManualInputDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Input / Paste HAP Text")
        self.setMinimumSize(680, 720)
        self.result_data = None
        self._paste_parsed = False  # True once the paste tab has parsed text
        self._paste_result = None  # dict from the last successful parse
        self.setStyleSheet(self._style())
        self._build()

    # ── styling ────────────────────────────────────────────────
    def _style(self):
        return """
            QDialog { background: #111827; color: #e5e7eb; }
            QLabel { color: #e5e7eb; }
            QGroupBox { border: 1px solid #374151; border-radius: 6px;
                margin-top: 10px; padding: 10px; color: #93c5fd; font-weight: bold; }
            QGroupBox::title { left: 10px; padding: 0 6px; background: #111827; }
            QPushButton { background: #2563eb; color: white; border: 0;
                border-radius: 5px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #3b82f6; }
            QPushButton#cancel { background: #6b7280; }
            QPushButton#parse { background: #059669; }
            QLineEdit, QDoubleSpinBox, QComboBox { background: #1f2937; color: #f9fafb;
                border: 1px solid #4b5563; border-radius: 4px; padding: 6px; min-width: 120px; }
            QTextEdit { background: #0b1220; color: #86efac;
                border: 1px solid #374151; border-radius: 4px; font-family: monospace; }
            QTabWidget::pane { border: 1px solid #374151; }
            QTabBar::tab { background: #1f2937; color: #9ca3af; padding: 8px 16px; }
            QTabBar::tab:selected { background: #2563eb; color: white; }
        """

    # ── UI construction ────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)

        title = QLabel("✏️ Manual Input / Paste HAP Text")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #60a5fa;")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        hint = QLabel(
            "Use this when OCR reads wrong values. "
            "Either fill the form or paste text copied from the HAP PDF — "
            "then press Apply on the tab you used."
        )
        hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ═══════════════════════════════════════════════════════════
        # Project fields — OUTSIDE the tabs so they are always visible
        # and share a single parent (no QWidget reparenting).
        # ═══════════════════════════════════════════════════════════
        proj_grp = QGroupBox("Project")
        pf = QFormLayout(proj_grp)

        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("(optional)")
        pf.addRow("Project Name:", self.project_name)

        self.floor_area = QDoubleSpinBox()
        self.floor_area.setRange(0, 1_000_000)
        self.floor_area.setDecimals(1)
        self.floor_area.setSuffix(" ft²")
        pf.addRow("Floor Area:", self.floor_area)

        root.addWidget(proj_grp)

        # ═══════════════════════════════════════════════════════════
        # Tabs — now contain ONLY the load fields / paste box
        # ═══════════════════════════════════════════════════════════
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_form_tab(), "📝 Fill the Form")
        self.tabs.addTab(self._build_paste_tab(), "📋 Paste Text")
        root.addWidget(self.tabs, 1)

        # ── Buttons ─────────────────────────────────────────────
        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("❌ Cancel")
        cancel.setObjectName("cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        ok = QPushButton("✅ Apply")
        ok.clicked.connect(self._on_apply)
        buttons.addWidget(ok)

        root.addLayout(buttons)

    # ── form tab ───────────────────────────────────────────────
    def _build_form_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        cool_grp = QGroupBox("Central Cooling Coil")
        cf = QFormLayout(cool_grp)

        self.total_val = QDoubleSpinBox()
        self.total_val.setRange(0, 1e7)
        self.total_val.setDecimals(2)
        self.total_unit = QComboBox()
        self.total_unit.addItems([UNIT_TONS, UNIT_MBH, UNIT_BTU])
        cf.addRow("Total Load:", self._pair(self.total_val, self.total_unit))

        self.sens_val = QDoubleSpinBox()
        self.sens_val.setRange(0, 1e7)
        self.sens_val.setDecimals(2)
        self.sens_unit = QComboBox()
        self.sens_unit.addItems([UNIT_MBH, UNIT_TONS, UNIT_BTU])
        cf.addRow("Sensible Load:", self._pair(self.sens_val, self.sens_unit))

        self.shr_val = QDoubleSpinBox()
        self.shr_val.setRange(0, 1.0)
        self.shr_val.setDecimals(3)
        self.shr_val.setSingleStep(0.01)
        cf.addRow("SHR:", self.shr_val)
        _hint = QLabel("Used only when Sensible is left blank.")
        _hint.setStyleSheet("color: #9ca3af; font-size: 10px;")
        cf.addRow("", _hint)

        self.cfm_val = QDoubleSpinBox()
        self.cfm_val.setRange(0, 100000)
        self.cfm_val.setDecimals(0)
        self.cfm_val.setSuffix(" CFM")
        cf.addRow("Coil CFM:", self.cfm_val)

        self.esp_val = QDoubleSpinBox()
        self.esp_val.setRange(0, 5.0)
        self.esp_val.setDecimals(3)
        self.esp_val.setSingleStep(0.01)
        self.esp_val.setSuffix(" in.wg")
        cf.addRow("ESP (Fan Static):", self.esp_val)

        v.addWidget(cool_grp)

        heat_grp = QGroupBox("Central Heating Coil")
        hf = QFormLayout(heat_grp)

        self.heat_val = QDoubleSpinBox()
        self.heat_val.setRange(0, 1e7)
        self.heat_val.setDecimals(2)
        self.heat_unit = QComboBox()
        self.heat_unit.addItems([UNIT_MBH, UNIT_BTU])
        hf.addRow("Heating Load:", self._pair(self.heat_val, self.heat_unit))

        v.addWidget(heat_grp)
        v.addStretch()
        return w

    def _pair(self, num, unit):
        c = QWidget()
        h = QHBoxLayout(c)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(num, 2)
        h.addWidget(unit, 1)
        return c

    # ── paste tab ──────────────────────────────────────────────
    def _build_paste_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)

        lbl = QLabel(
            "Paste text copied from HAP PDF (Ctrl+C → Ctrl+V).\n"
            "Parser extracts Total, Sensible, CFM, Heating, ESP."
        )
        lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        self.paste_box = QTextEdit()
        self.paste_box.setPlaceholderText(
            "Example:\n"
            "  Total coil load ............ 1.1 Tons\n"
            "  Total coil load ............ 12.7 MBH\n"
            "  Sensible coil load ......... 11.0 MBH\n"
            "  Coil CFM at Jul 1300 ....... 421 CFM\n"
            "  Max coil load .............. 3.8 MBH\n"
            "  Fan static ................. 0.00 in.wg"
        )
        v.addWidget(self.paste_box, 1)

        self.paste_status = QLabel("")
        self.paste_status.setStyleSheet("color: #fbbf24; font-size: 11px;")
        self.paste_status.setWordWrap(True)
        v.addWidget(self.paste_status)
        # Editing the text invalidates the last parse result.
        self.paste_box.textChanged.connect(self._on_paste_text_changed)

        btn = QPushButton("🔍 Parse Text")
        btn.setObjectName("parse")
        btn.clicked.connect(self._parse_pasted_text)
        v.addWidget(btn)
        return w

    # ── handlers ───────────────────────────────────────────────
    def _on_paste_text_changed(self):
        if self._paste_parsed:
            self._paste_parsed = False
            self._paste_result = None
            self.paste_status.setText("Text changed — click Parse Text again.")
            self.paste_status.setStyleSheet("color: #fbbf24; font-size: 11px;")

    def _parse_pasted_text(self):
        text = self.paste_box.toPlainText().strip()
        if not text:
            _safe_warning(self, "Empty", "Paste some text first.")
            return

        try:
            parsed = parse_hap_detailed(text)
            total, sens, cfm, heat, esp = (
                parsed["total_btu"],
                parsed["sensible_btu"],
                parsed["cfm"],
                parsed["heating_btu"],
                parsed["esp"],
            )
        except (KeyError, TypeError, ValueError) as e:
            _safe_critical(self, "Parse Error", str(e))
            return

        extras = []
        if self.floor_area.value() > 0:
            extras.append(f"   • Floor Area: {self.floor_area.value():,.0f} ft²")
        if self.project_name.text().strip():
            extras.append(f"   • Project: {self.project_name.text().strip()}")

        status = (
            f"✅ Parsed:\n"
            f"   • Total:    {total:,.0f} Btu/hr\n"
            f"   • Sensible: {sens:,.0f} Btu/hr\n"
            f"   • CFM:      {cfm:,.0f}\n"
            f"   • Heating:  {heat:,.0f} Btu/hr\n"
            f"   • ESP:      {esp if esp is not None else '(not found)'}"
        )
        if extras:
            status += "\n" + "\n".join(extras)
        validation = parsed["warnings"] + parsed["critical_errors"]
        if validation:
            status += "\n" + "\n".join(validation)

        self.paste_status.setText(status)
        self.paste_status.setStyleSheet("color: #34d399; font-size: 12px;")

        self._paste_parsed = True
        self._paste_result = {
            "total_btu": total,
            "sensible_btu": sens,
            "cfm": cfm,
            "heating_btu": heat,
            "esp": esp,
            "warnings": parsed["warnings"],
            "critical_errors": parsed["critical_errors"],
        }

    def _on_apply(self):
        """Apply the currently visible tab. Project fields are shared."""
        current_tab = self.tabs.currentIndex()

        if current_tab == TAB_PASTE:
            if not self._paste_parsed or not self._paste_result:
                _safe_warning(
                    self,
                    "Nothing parsed",
                    "Click 'Parse Text' on the Paste tab first, "
                    "or switch to the Form tab.",
                )
                return
            base = dict(self._paste_result)
        else:  # TAB_FORM
            total = self.total_val.value() * UNIT_TO_BTU[self.total_unit.currentText()]
            sens = self.sens_val.value() * UNIT_TO_BTU[self.sens_unit.currentText()]
            heat = self.heat_val.value() * UNIT_TO_BTU[self.heat_unit.currentText()]

            # HAP prints both, but a user transcribing by hand may only
            # have SHR: derive Sensible from it when Sensible is blank.
            if sens <= 0 and total > 0 and self.shr_val.value() > 0:
                sens = total * self.shr_val.value()

            if total <= 0 and self.cfm_val.value() <= 0:
                _safe_warning(
                    self,
                    "Missing Data",
                    "Enter Total Load or CFM — or paste text in the other tab.",
                )
                return

            base = {
                "total_btu": total,
                "sensible_btu": sens,
                "cfm": self.cfm_val.value(),
                "heating_btu": heat,
                "esp": self.esp_val.value() if self.esp_val.value() > 0 else None,
            }

        # Sanity check (form or paste): sensible can never exceed total.
        _t, _s = base.get("total_btu", 0) or 0, base.get("sensible_btu", 0) or 0
        if _t > 0 and _s > _t and not _safe_question(
            self,
            "Sensible > Total",
            f"Sensible load ({_s:,.0f} Btu/hr) exceeds Total "
            f"({_t:,.0f} Btu/hr).\n\nApply anyway?",
        ):
            return

        # Overlay the shared project fields on either path.
        self.result_data = {
            **base,
            "floor_area": self.floor_area.value(),
            "project_name": self.project_name.text().strip(),
        }
        self.accept()

    def get_result(self):
        return self.result_data
