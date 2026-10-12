"""
ui_shell.py
===========
Professional UI shell for the Duct DX Equipment Selector.

This module ONLY builds widgets / layouts / styling. All backend logic
(parse_hap, search, _search_family, cart, energy, exports ...) stays in
main.py and is reached through the very same attribute names the old UI
used (self.btu, self.sens, self.table, self.cart_table, self.status ...).

Public API
----------
THEME                    -> application stylesheet
build_shell(app)         -> builds the whole window onto the App instance
refresh_shell(app)       -> re-syncs header / workflow / sidebar / cards
set_preview(app, path)   -> safe preview for RTF / PDF / images
friendly_error(msg, path)-> human-readable error text
"""
import logging
from html import escape
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

SHOW_PHASE2_DUCT_OPTIONS = False

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


def set_diffuser_rationale(label, rationale):
    """Render the structured selection rationale in the diffuser result panel."""
    summary = rationale.get("summary") if isinstance(rationale, dict) else None
    label.setText("Selection Rationale: " + str(summary or "—"))


def format_t50_screening(adpi_ok, ratio_min, ratio_max, lower, upper):
    """Format the geometric T50/L estimate without claiming verified ADPI."""
    status = "Within screening band" if adpi_ok else "Outside screening band"
    return (
        f"<b>T50/L Screening Estimate:</b> {status} "
        f"(T50/L {ratio_min:.2f}–{ratio_max:.2f}, screening band "
        f"{lower}–{upper}; ADPI not verified)"
    )


def format_fallback_status(selection):
    """Render a failed-compliance candidate as review data, not a valid layout."""
    model = escape(str(selection.get("model") or "Unknown model"))
    size = escape(str(selection.get("size") or "Unknown size"))
    qty = int(selection.get("qty") or 0)
    failure_kind = selection.get("fallback_failure_kind")
    headline = "Fallback Accepted — Review Required" if selection.get(
        "fallback_accepted") else {
            "no_acoustically_compliant_option":
                "No Acoustically Compliant Option Found",
            "no_fully_compliant_option":
                "No Fully Compliant Option Found",
        }.get(failure_kind, "No Fully Compliant Option Found")
    status = [
        f"<b>Status:</b> {headline}",
        f"<b>Best Fallback:</b> {qty} × {size} ({model})",
    ]
    criteria = selection.get("fallback_criteria") or {}
    failed = []
    passed = []
    if criteria.get("catalog_ok"):
        passed.append("Catalog neck-velocity range met")
    else:
        failed.append("Neck velocity outside catalog validity range")
    if criteria.get("t50_l_ok"):
        passed.append("T50/L screening band met")
    else:
        failed.append("T50/L Screening out of range")
    if criteria.get("table4_ok"):
        passed.append("Table 4 CFM limit met")
    else:
        failed.append("Table 4 airflow limit not met")
    if criteria.get("nc_ok"):
        passed.append("NC meets configured limit")
    else:
        failed.append("NC exceeds configured limit")
    reason = "; ".join(failed) or "No fully compliant candidate"
    passed_text = "; ".join(passed) or "None"
    status.extend((
        f"<b>Failure Reason:</b> {escape(reason)}",
        f"<b>Passed Criteria:</b> {escape(passed_text)}",
    ))
    return "<br>".join(status)


def diffuser_summary_values(name, selection):
    """Return the summary-table cells, including reviewable fallback metrics."""
    selection = selection or {}
    if selection.get("status") == "invalid_saved_layout":
        return [
            name, "—", "—", "—", "—", "—", "—",
            "—", "—", "—", "—", "—", "Needs review",
        ]
    candidate_available = bool(
        selection.get("fallback_available") or selection.get("model"))
    success = bool(selection.get("success", bool(selection.get("model"))))
    if not success and not candidate_available:
        return [
            name, "—", "—", "—", "—", "—", "—",
            "—", "—", "—", "—", "—",
            f"No feasible layout ({selection.get('status', 'unknown')})",
        ]
    delta_t = selection.get("delta_t_f")
    screening = (
        "Within band" if selection.get("adpi_pass") else "Review")
    if selection.get("adpi_val") is not None:
        screening += f" ({selection['adpi_val']:.2f})"
    status = (
        "Fallback Accepted" if selection.get("fallback_accepted")
        else "Fallback - Review Required" if not success
        else "Valid")
    return [
        name,
        selection.get("model", "—"),
        selection.get("size", "—"),
        str(selection.get("qty", "—")),
        f"{selection.get('cfm_each', 0):,.0f}",
        f"{selection.get('v_fpm', 0):,.0f}",
        f"{selection.get('dp_inwg', 0):.4f}",
        f"{selection.get('nc', 0):.0f}",
        f"{selection.get('t50_ft', 0):.1f}",
        f"{selection.get('ach', 0):.1f}",
        f"{delta_t:.1f}" if delta_t is not None else "Unverified",
        screening,
        status,
    ]


# ═══════════════════════════════════════════════════════════════
#  THEME
# ═══════════════════════════════════════════════════════════════
C_BG, C_BG2, C_CARD, C_BORDER = "#0B1220", "#111827", "#172033", "#26344D"
C_PRIMARY, C_OK, C_WARN, C_DANGER = "#2563EB", "#10B981", "#F59E0B", "#EF4444"
C_TEXT, C_MUTED = "#E5E7EB", "#94A3B8"

THEME = f"""
* {{ font-family: "Inter","Noto Sans","DejaVu Sans","Segoe UI",sans-serif; }}
QWidget {{ background:{C_BG2}; color:{C_TEXT}; font-size:12px; }}
QMainWindow, QStackedWidget, QSplitter {{ background:{C_BG}; }}
QLabel {{ background:transparent; }}
QWidget#plain {{ background:transparent; }}
QWidget#page {{ background:{C_BG}; }}
QToolTip {{ background:{C_CARD}; color:{C_TEXT}; border:1px solid {C_BORDER}; padding:4px; }}

QFrame#header {{ background:{C_BG}; border-bottom:1px solid {C_BORDER}; }}
QFrame#header QLabel {{ background:transparent; }}
QLabel#appTitle {{ font-size:20px; font-weight:700; color:#60A5FA; }}
QLabel#appSub {{ color:{C_MUTED}; font-size:11px; }}
QLabel#pageTitle {{ font-size:18px; font-weight:700; color:{C_TEXT}; background:transparent; }}
QLabel#pageSub {{ color:{C_MUTED}; font-size:12px; background:transparent; }}
QLabel#muted {{ color:{C_MUTED}; background:transparent; }}
QLabel#section {{ color:#93C5FD; font-size:11px; font-weight:700; letter-spacing:1px; background:transparent; }}
QLabel#hint {{ color:{C_MUTED}; font-size:11px; background:transparent; }}
QLabel#warn {{ color:{C_WARN}; font-size:11px; background:transparent; }}

QFrame#workflow {{ background:{C_BG2}; border-bottom:1px solid {C_BORDER}; }}
QPushButton#step {{ background:transparent; color:{C_MUTED}; border:0; border-bottom:3px solid transparent;
    border-radius:0; padding:9px 16px; font-weight:600; }}
QPushButton#step:hover {{ color:{C_TEXT}; }}
QPushButton#step[state="done"] {{ color:{C_OK}; }}
QPushButton#step[state="error"] {{ color:{C_DANGER}; }}
QPushButton#step[current="true"] {{ color:white; border-bottom:3px solid {C_PRIMARY}; background:{C_CARD}; }}
QLabel#arrow {{ color:{C_BORDER}; background:transparent; }}

QFrame#sidebar {{ background:{C_BG2}; border-right:1px solid {C_BORDER}; }}
QFrame#card, QFrame#bestCard {{ background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:8px; }}
QFrame#card QLabel, QFrame#bestCard QLabel {{ background:transparent; }}
QFrame#bestCard {{ border:1px solid {C_WARN}; }}
QLabel#statValue {{ font-size:22px; font-weight:700; color:white; }}
QLabel#statCap {{ font-size:11px; color:{C_MUTED}; }}

QGroupBox {{ background:transparent; border:1px solid {C_BORDER}; border-radius:8px; margin-top:14px;
    padding:12px 10px 8px 10px; color:#93C5FD; font-weight:700; }}
QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px; background:{C_BG2}; }}

QPushButton {{ background:{C_PRIMARY}; color:white; border:0; border-radius:5px; padding:7px 14px; font-weight:600; }}
QPushButton:hover {{ background:#3B82F6; }}
QPushButton:disabled {{ background:#1F2937; color:#6B7280; }}
QPushButton#secondary {{ background:#1F2937; color:{C_TEXT}; border:1px solid {C_BORDER}; }}
QPushButton#secondary:hover {{ background:#273449; }}
QPushButton#success {{ background:#059669; }}
QPushButton#success:hover {{ background:{C_OK}; }}
QPushButton#danger {{ background:#B91C1C; }}
QPushButton#danger:hover {{ background:{C_DANGER}; }}
QPushButton#warning {{ background:#B45309; }}
QPushButton#warning:hover {{ background:{C_WARN}; }}

QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{ background:#1F2937; color:#F9FAFB; border:1px solid #3B4A63;
    border-radius:4px; padding:5px 8px; min-height:18px; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border:1px solid {C_PRIMARY}; }}
QComboBox QAbstractItemView {{ background:#1F2937; selection-background-color:{C_PRIMARY}; }}

QTableWidget {{ background:{C_BG}; alternate-background-color:#0F1A2E; color:{C_TEXT};
    gridline-color:{C_BORDER}; border:1px solid {C_BORDER}; font-size:11px; selection-background-color:#1D4ED8; }}
QHeaderView::section {{ background:#1A2740; color:#93C5FD; padding:6px; border:0;
    border-right:1px solid {C_BORDER}; font-weight:700; font-size:11px; }}
QTextEdit {{ background:{C_BG}; color:#86EFAC; border:1px solid {C_BORDER}; font-family:"DejaVu Sans Mono",monospace; }}
QListWidget {{ background:transparent; border:0; outline:0; }}
QListWidget::item {{ padding:7px 8px; border-radius:5px; margin:1px 0; }}
QListWidget::item:selected {{ background:{C_PRIMARY}; color:white; }}
QListWidget::item:hover:!selected {{ background:#1F2937; }}

QTabWidget::pane {{ border:1px solid {C_BORDER}; border-radius:4px; }}
QTabBar::tab {{ background:#1F2937; color:{C_MUTED}; padding:7px 14px; border-top-left-radius:4px; border-top-right-radius:4px; }}
QTabBar::tab:selected {{ background:{C_PRIMARY}; color:white; }}
QCheckBox, QRadioButton {{ background:transparent; color:{C_TEXT}; }}
QStatusBar {{ background:{C_BG}; border-top:1px solid {C_BORDER}; color:{C_MUTED}; }}
QStatusBar QLabel {{ background:transparent; }}
QToolButton#collapse {{ background:transparent; color:#93C5FD; border:0; font-weight:700; }}
QSplitter::handle {{ background:{C_BORDER}; }}
"""

CATALOG_SHORT = {"QDMT": "QDMT Duct Split", "Miraco": "Miraco 50UB/38BB", "Rooftop": "Carrier Rooftop RTU"}


# ═══════════════════════════════════════════════════════════════
#  Small helpers / reusable widgets
# ═══════════════════════════════════════════════════════════════
def _num(text, default=0.0):
    try:
        return float(str(text).replace(",", "").replace("—", "").strip() or default)
    except (TypeError, ValueError):
        return default


def _btn(text, name=None, slot=None, tip=None):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def _label(text, name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def _row(label, widget, tip=None):
    """Label + widget on one line (used in the requirement forms)."""
    w = QWidget()
    w.setObjectName("plain")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    lab = QLabel(label)
    lab.setStyleSheet("background:transparent;")
    lab.setMinimumWidth(150)
    if tip:
        lab.setToolTip(tip)
        widget.setToolTip(tip)
    h.addWidget(lab)
    h.addWidget(widget, 1)
    return w


def _line(app, label, value="", tip=None, placeholder="Enter value"):
    x = QLineEdit(value)
    x.setPlaceholderText(placeholder)
    return x


class StatusLabel(QLabel):
    """Status-bar label. Collapses the multi-line messages the backend
    writes into one compact line (full text stays in the tooltip)."""

    def setText(self, text):
        full = str(text)
        super().setText(" │ ".join(p.strip() for p in full.splitlines() if p.strip()))
        self.setToolTip(full)


class StatCard(QFrame):
    def __init__(self, caption, value="—", tip=None):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(2)
        self.value = _label(value, "statValue")
        self.cap = _label(caption, "statCap")
        v.addWidget(self.value)
        v.addWidget(self.cap)
        if tip:
            self.setToolTip(tip)

    def set(self, value):
        self.value.setText(str(value))


def _cards_row(*cards):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    for c in cards:
        h.addWidget(c)
    return w


def _page(title, subtitle):
    """Create an empty page with a title block. Returns (page, body_layout)."""
    page = QWidget()
    page.setObjectName("page")
    v = QVBoxLayout(page)
    v.setContentsMargins(20, 16, 20, 12)
    v.setSpacing(12)
    v.addWidget(_label(title, "pageTitle"))
    v.addWidget(_label(subtitle, "pageSub"))
    return page, v


def _configure_table(t, stretch_last=False):
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(stretch_last)
    t.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
    return t


# ═══════════════════════════════════════════════════════════════
#  Safe preview  (Bug 2 — no QPixmap on RTF / unsupported files)
# ═══════════════════════════════════════════════════════════════
_IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def set_preview(app, path):
    lab = app.preview
    lab.setPixmap(QPixmap())
    ext = Path(path).suffix.lower()
    pix = None
    try:
        if ext == ".pdf" and fitz:
            doc = fitz.open(path)
            try:
                p = doc[0].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
                q = QImage(p.samples, p.width, p.height, p.stride, QImage.Format_RGB888)
                pix = QPixmap.fromImage(q.copy())
            finally:
                doc.close()
        elif ext in _IMG_EXT:
            q = QImage(path)
            if not q.isNull():
                pix = QPixmap.fromImage(q)
    except (OSError, ValueError, RuntimeError):
        logging.getLogger(__name__).exception("Could not load preview for %s", path)
        pix = None

    if pix is not None and not pix.isNull():
        lab._pix = pix
        lab.setText("")
        _fit_preview(lab)
        return
    lab._pix = None
    if ext == ".rtf":
        lab.setText("📄 HAP Report (RTF)\n\nText-based report — read with the RTF text parser.\n"
                    "No image preview is needed.")
    else:
        lab.setText("Preview unavailable for this file type.")


def _fit_preview(lab):
    pix = getattr(lab, "_pix", None)
    if pix is not None and not pix.isNull() and lab.width() > 10 and lab.height() > 10:
        lab.setPixmap(pix.scaled(lab.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class PreviewLabel(QLabel):
    def resizeEvent(self, e):
        super().resizeEvent(e)
        _fit_preview(self)


# ═══════════════════════════════════════════════════════════════
#  Human-readable errors (section 29)
# ═══════════════════════════════════════════════════════════════
SUPPORTED_TEXT = "RTF, PDF, PNG, JPG, JPEG"


def friendly_error(msg, path=""):
    ext = Path(path).suffix.upper().lstrip(".") if path else ""
    low = str(msg).lower()
    if "cannot identify image" in low or (ext == "RTF" and "image" in low):
        return ("Unable to process the selected file.\n\n"
                f"File type: {ext or 'unknown'}\n\n"
                "Reason: this is a text-based HAP report and should not be read with image OCR.\n"
                "Use automatic extraction instead.")
    if "tesseract" in low:
        return ("The OCR engine (Tesseract) is not available.\n\n"
                "Install Tesseract, or load an RTF / PDF report which does not need OCR.")
    return ("Unable to process the selected file.\n\n"
            f"Details: {msg}\n\nSupported formats: {SUPPORTED_TEXT}")


def unsupported_text():
    return f"Unsupported file type.\n\nSupported formats:\n{SUPPORTED_TEXT}"


# ═══════════════════════════════════════════════════════════════
#  Best Match card
# ═══════════════════════════════════════════════════════════════
class BestMatchCard(QFrame):
    def __init__(self, add_slot):
        super().__init__()
        self.setObjectName("bestCard")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(16, 12, 16, 12)
        self.v.setSpacing(6)

        self.title = _label("⭐ BEST MATCH", "section")
        self.title.setStyleSheet(f"color:{C_WARN};font-weight:700;letter-spacing:1px;")
        self.model = _label("—")
        self.model.setStyleSheet("font-size:20px;font-weight:700;color:white;")
        self.catalog = _label("", "muted")
        self.v.addWidget(self.title)
        self.v.addWidget(self.model)
        self.v.addWidget(self.catalog)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(24)
        self.v.addWidget(self.grid_host)

        self.checks = _label("", wrap=True)
        self.checks.setTextFormat(Qt.RichText)
        self.v.addWidget(self.checks)

        self.add_btn = _btn("＋ Add to Selection", "success", add_slot,
                            "Add this unit to the Selection Cart for the current system.")
        self.v.addWidget(self.add_btn)

    def _set_grid(self, items):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (cap, val, tip) in enumerate(items):
            box = QWidget()
            box.setObjectName("plain")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(0)
            a = _label(cap, "statCap")
            b = _label(val)
            b.setStyleSheet("font-size:14px;font-weight:700;")
            if tip:
                a.setToolTip(tip)
                b.setToolTip(tip)
            bl.addWidget(a)
            bl.addWidget(b)
            self.grid.addWidget(box, i // 3, i % 3)

    def show_none(self, hint):
        self.model.setText("No suitable equipment found")
        self.catalog.setText("")
        self._set_grid([])
        self.checks.setText(hint)
        self.add_btn.setEnabled(False)

    def show_empty(self):
        self.model.setText("—")
        self.catalog.setText("Run a search to see the best match.")
        self._set_grid([])
        self.checks.setText("")
        self.add_btn.setEnabled(False)

    def show_row(self, d, req):
        self.model.setText(d["model"])
        self.catalog.setText(d["catalog"])
        self._set_grid([
            ("Cooling Capacity", f"{d['cap']} Btu/hr", None),
            ("Sensible Capacity", f"{d['sens']} Btu/hr", "Sensible cooling capacity at the design condition."),
            ("Airflow", f"{d['flow']} CFM", "Airflow at the selected fan speed."),
            ("ESP", f"{req['esp']:.3f} in.w.g.", "External Static Pressure in inches of water gauge."),
            ("EER", d["eer"], "Energy Efficiency Ratio. Cooling capacity divided by electrical input."),
            ("Sound", f"{d['sound']} dB(A)", None),
        ])

        def line(ok, name, need, have):
            icon, col = ("✓", C_OK) if ok else ("⚠", C_WARN)
            return (f"<span style='color:{col};font-weight:700'>{icon}</span> "
                    f"{name}: required {need} · available {have}")

        rows = []
        cap, req_cap = _num(d["cap"]), req["total"]
        if req_cap > 0:
            rows.append(line(cap >= req_cap, "Cooling capacity", f"{req_cap:,.0f}", f"{cap:,.0f}"))
        sens, req_sens = _num(d["sens"]), req["sens"]
        if req_sens > 0 and sens > 0:
            rows.append(line(sens >= req_sens, "Sensible capacity", f"{req_sens:,.0f}", f"{sens:,.0f}"))
        flow, req_flow = _num(d["flow"]), req["cfm"]
        if req_flow > 0 and flow > 0:
            rows.append(line(flow >= req_flow, "Airflow", f"{req_flow:,.0f}", f"{flow:,.0f}"))
        rows.append(f"<span style='color:{C_OK};font-weight:700'>✓</span> "
                    f"ESP, sound and heating limits applied by the selection engine")
        self.checks.setText("<br>".join(rows))
        self.add_btn.setEnabled(True)


# ═══════════════════════════════════════════════════════════════
#  Equipment details dialog (section 17)
# ═══════════════════════════════════════════════════════════════
class EquipmentDetailsDialog(QDialog):
    def __init__(self, parent, headers, values):
        super().__init__(parent)
        self.setWindowTitle("Equipment Details")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        data = dict(zip(headers, values, strict=True))
        v.addWidget(_label("EQUIPMENT DETAILS", "section"))
        t = _label(data.get("System Model", ""))
        t.setStyleSheet("font-size:20px;font-weight:700;color:white;")
        v.addWidget(t)
        v.addWidget(_label(data.get("Catalog", ""), "muted"))

        groups = [
            ("COOLING PERFORMANCE", ["Cooling (Btu/hr)", "Sensible (Btu/hr)", "EER", "Fan Speed"]),
            ("AIRFLOW", ["Airflow (CFM)"]),
            ("HEATING", ["Heating (Btu/hr)"]),
            ("SOUND", ["Sound dB(A)"]),
            ("UNIT", ["Class", "Power Supply", "Indoor Unit", "Outdoor Unit", "Indoor Dims"]),
            ("ELECTRICAL & SITE", ["Liquid Line", "Gas/Suction Line", "MCA (A)", "MOP (A)",
                                   "Min Cable (mm²)", "Elec. Source", "Unit Height (mm)",
                                   "Ceiling Void Check"]),
            ("DECISION", ["Status", "Decision Reason"]),
        ]
        for name, keys in groups:
            g = QGroupBox(name)
            f = QFormLayout(g)
            for k in keys:
                if k in data:
                    val = QLabel(str(data[k]))
                    val.setWordWrap(True)
                    val.setStyleSheet("background:transparent;font-weight:600;")
                    f.addRow(k + ":", val)
            v.addWidget(g)
        close = _btn("Close", "secondary", self.accept)
        v.addWidget(close, 0, Qt.AlignRight)



# ═══════════════════════════════════════════════════════════════
#  Combine Units dialog — several units together cover one load
# ═══════════════════════════════════════════════════════════════
class CombineUnitsDialog(QDialog):
    """Pick quantities of the highlighted units and see, live, whether the
    SUM of their capacities / airflow covers the system requirement."""

    def __init__(self, parent, units, req):
        super().__init__(parent)
        self.setWindowTitle("Combine Units")
        self.setMinimumWidth(820)
        self.units, self.req = units, req
        v = QVBoxLayout(self)
        v.addWidget(_label("COMBINE UNITS", "section"))
        v.addWidget(_label(
            "Units run in parallel on the same system: capacities and airflows are added. "
            "Set the quantity of each unit — use 2 or more of the same model if needed.",
            "hint", wrap=True))

        heads = ["Model", "Catalog", "Qty", "Cooling (Btu/hr)", "Sensible (Btu/hr)",
                 "Airflow (CFM)", "EER", "Status"]
        self.tbl = _configure_table(QTableWidget(len(units), len(heads)), stretch_last=True)
        self.tbl.setHorizontalHeaderLabels(heads)
        self.tbl.setSelectionMode(QTableWidget.NoSelection)
        self.spins = []
        default_qty = 2 if len(units) == 1 else 1
        for r, u in enumerate(units):
            vals = [u["model"], u["catalog"], None, f"{u['cap']:,.0f}",
                    f"{u['sens']:,.0f}" if u["sens"] else "—",
                    f"{u['flow']:,.0f}" if u["flow"] else "—",
                    f"{u['eer']:.2f}" if u["eer"] else "—", u["status"]]
            for c, val in enumerate(vals):
                if val is None:
                    sp = QSpinBox()
                    sp.setRange(0, 20)
                    sp.setValue(default_qty)
                    sp.valueChanged.connect(self._update)
                    self.tbl.setCellWidget(r, c, sp)
                    self.spins.append(sp)
                else:
                    it = QTableWidgetItem(str(val))
                    it.setTextAlignment(Qt.AlignCenter)
                    self.tbl.setItem(r, c, it)
        self.tbl.resizeColumnsToContents()
        self.tbl.setMinimumHeight(min(60 + 34 * len(units), 320))
        v.addWidget(self.tbl)

        self.result_lbl = QLabel()
        self.result_lbl.setTextFormat(Qt.RichText)
        self.result_lbl.setWordWrap(True)
        v.addWidget(self.result_lbl)

        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(_btn("Cancel", "secondary", self.reject))
        self.ok = _btn("Add Combination to Selection", "success", self._accept)
        bar.addWidget(self.ok)
        v.addLayout(bar)
        self._update()

    def result_units(self):
        out = []
        for u, sp in zip(self.units, self.spins, strict=True):
            if sp.value() > 0:
                d = dict(u)
                d["qty"] = sp.value()
                out.append(d)
        return out

    def totals(self):
        us = self.result_units()
        return {
            "cap": sum(u["qty"] * u["cap"] for u in us),
            "sens": sum(u["qty"] * u["sens"] for u in us),
            "flow": sum(u["qty"] * u["flow"] for u in us),
            "n": sum(u["qty"] for u in us),
        }

    def _check(self, name, need, have, enforce=True):
        if need <= 0:
            return ""
        ok = have >= need
        icon, col = ("✓", C_OK) if ok else ("⚠", C_WARN)
        return (f"<span style='color:{col};font-weight:700'>{icon}</span> {name}: "
                f"required {need:,.0f} · combined {have:,.0f}")

    def _update(self):
        t = self.totals()
        lines = [self._check("Cooling capacity", self.req["total"], t["cap"]),
                 self._check("Sensible capacity", self.req["sens"], t["sens"]),
                 self._check("Airflow", self.req["cfm"], t["flow"])]
        self.all_ok = (t["cap"] >= self.req["total"]
                       and (self.req["sens"] <= 0 or t["sens"] >= self.req["sens"])
                       and (self.req["cfm"] <= 0 or t["flow"] >= self.req["cfm"]))
        head = (f"<b>{t['n']} unit(s) combined</b> — "
                + (f"<span style='color:{C_OK};font-weight:700'>covers the load</span>"
                   if self.all_ok and t["n"] else
                   f"<span style='color:{C_WARN};font-weight:700'>does not cover the load yet</span>"))
        self.result_lbl.setText(head + "<br>" + "<br>".join(l for l in lines if l))
        self.ok.setEnabled(t["n"] > 0)

    def _accept(self):
        if not self.all_ok:
            r = QMessageBox.question(
                self, "Combination does not cover the load",
                "The combined units do NOT fully cover the requirement.\n\nAdd anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self.accept()

# ═══════════════════════════════════════════════════════════════
#  Collapsible section (Advanced Diagnostics)
# ═══════════════════════════════════════════════════════════════
class Collapsible(QWidget):
    def __init__(self, title, body):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.btn = QToolButton()
        self.btn.setObjectName("collapse")
        self.btn.setText("▶  " + title)
        self.btn.setCheckable(True)
        self.btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.title = title
        self.body = body
        body.setVisible(False)
        self.btn.toggled.connect(self._toggle)
        v.addWidget(self.btn)
        v.addWidget(body)

    def _toggle(self, on):
        self.btn.setText(("▼  " if on else "▶  ") + self.title)
        self.body.setVisible(on)


# ═══════════════════════════════════════════════════════════════
#  BUILD
# ═══════════════════════════════════════════════════════════════
PAGES = ["HAP Input", "Systems", "Equipment Selection", "Selection Cart",
         "Energy & Cost", "Diffuser Selection", "Reports"]
P_HAP, P_SYS, P_EQ, P_CART, P_ENERGY, P_DIFF, P_REPORT = range(7)


def go_page(app, idx):
    app.stack.setCurrentIndex(idx)
    refresh_shell(app)


def build_shell(app):
    app.setWindowTitle("Duct DX Equipment Selector — HAP Analyzer")
    app.setMinimumSize(1400, 850)
    app.resize(1600, 950)

    # status label must exist before pages (backend writes to it)
    app.status = StatusLabel("✓ Ready")

    central = QWidget()
    root = QVBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    root.addWidget(_build_header(app))
    root.addWidget(_build_workflow(app))

    body = QSplitter(Qt.Horizontal)
    body.setChildrenCollapsible(True)
    body.addWidget(_build_sidebar(app))

    app.stack = QStackedWidget()
    app.stack.addWidget(_build_hap_page(app))
    app.stack.addWidget(_build_systems_page(app))
    app.stack.addWidget(_build_equipment_page(app))
    app.stack.addWidget(_build_cart_page(app))
    app.stack.addWidget(_build_energy_page(app))
    app.stack.addWidget(_build_diffuser_page(app))
    app.stack.addWidget(_build_reports_page(app))
    body.addWidget(app.stack)
    body.setStretchFactor(0, 0)
    body.setStretchFactor(1, 1)
    body.setSizes([300, 1300])
    root.addWidget(body, 1)

    app.setCentralWidget(central)

    project_menu = app.menuBar().addMenu("&File")
    project_menu.addAction(
        QAction("Open Project…", app, shortcut=QKeySequence("Ctrl+Shift+O"),
                triggered=lambda _checked=False: app.open_project()))
    project_menu.addAction(
        QAction("Save Project", app, shortcut=QKeySequence("Ctrl+Shift+S"),
                triggered=lambda _checked=False: app.save_project()))
    project_menu.addAction(
        QAction("Save Project As…", app,
                shortcut=QKeySequence("Ctrl+Alt+Shift+S"),
                triggered=lambda: app.save_project(save_as=True)))

    # status bar
    sb = QStatusBar()
    sb.setSizeGripEnabled(True)
    sb.addWidget(app.status, 1)
    app.sb_counts = QLabel("")
    sb.addPermanentWidget(app.sb_counts)
    app.setStatusBar(sb)

    # shortcuts
    QShortcut(QKeySequence("Ctrl+O"), app, activated=app.open_batch_file)
    QShortcut(QKeySequence("Ctrl+F"), app, activated=lambda: _focus_filter(app))
    QShortcut(QKeySequence("Ctrl+S"), app, activated=lambda: _generate_report(app))

    # energy parameter signals (same slots the old UI used)
    app.hours_per_year.valueChanged.connect(app._on_energy_setting_changed)
    app.tariff.textChanged.connect(app._on_energy_setting_changed)
    app.currency_sym.textChanged.connect(app._on_energy_setting_changed)
    for w in (
            app.btu, app.sens, app.cfm, app.heat, app.esp, app.max_sound,
            app.ceiling_void, app.ceiling_clear, app.drain_run):
        w.textChanged.connect(lambda *_: app._mark_project_dirty())
    for w in (
            app.catalog_family, app.phase, app.outdoor_temp, app.fan_speed):
        w.currentTextChanged.connect(lambda *_: app._mark_project_dirty())
    for w in (app.capm, app.afm, app.safm):
        w.valueChanged.connect(lambda *_: app._mark_project_dirty())
    app.ceiling_enforce.stateChanged.connect(
        lambda *_: app._mark_project_dirty())

    go_page(app, P_HAP)


# ── header ────────────────────────────────────────────────────
def _build_header(app):
    f = QFrame()
    f.setObjectName("header")
    h = QHBoxLayout(f)
    h.setContentsMargins(20, 8, 20, 8)
    left = QVBoxLayout()
    left.setSpacing(0)
    left.addWidget(_label("Duct DX Equipment Selector  ·  HAP Analyzer", "appTitle"))
    left.addWidget(_label("HVAC DX Equipment Selection & Engineering Analysis", "appSub"))
    h.addLayout(left)
    h.addStretch()
    app.hdr_project = _label("Project: —")
    app.hdr_stats = _label("Systems: 0   Matched: 0/0   Cart: 0")
    app.hdr_stats.setStyleSheet(f"color:{C_MUTED};")
    right = QVBoxLayout()
    right.setSpacing(0)
    right.addWidget(app.hdr_project, 0, Qt.AlignRight)
    right.addWidget(app.hdr_stats, 0, Qt.AlignRight)
    h.addLayout(right)
    return f


# ── workflow bar ──────────────────────────────────────────────
def _build_workflow(app):
    f = QFrame()
    f.setObjectName("workflow")
    h = QHBoxLayout(f)
    h.setContentsMargins(16, 0, 16, 0)
    h.setSpacing(0)
    app.step_btns = []
    for i, name in enumerate(PAGES):
        b = QPushButton(name)
        b.setObjectName("step")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, i=i: go_page(app, i))
        app.step_btns.append(b)
        h.addWidget(b)
        if i < len(PAGES) - 1:
            h.addWidget(_label("›", "arrow"))
    h.addStretch()
    return f


# ── sidebar ───────────────────────────────────────────────────
def _build_sidebar(app):
    f = QFrame()
    f.setObjectName("sidebar")
    f.setMinimumWidth(240)
    f.setMaximumWidth(340)
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 14, 14, 10)
    v.setSpacing(8)

    v.addWidget(_label("PROJECT", "section"))
    app.side_file = _label("No HAP report loaded", wrap=True)
    app.side_file.setStyleSheet("font-weight:600;")
    v.addWidget(app.side_file)
    app.side_loaded = _label("", "hint", wrap=True)
    v.addWidget(app.side_loaded)
    v.addWidget(_btn("Load HAP Report", None, app.open_batch_file,
                     "Load a HAP Systems Design Report (Ctrl+O). RTF / PDF / images."))

    v.addSpacing(8)
    v.addWidget(_label("SYSTEMS", "section"))
    app.side_list = QListWidget()
    app.side_list.setUniformItemSizes(True)
    app.side_list.itemClicked.connect(lambda it: _on_side_clicked(app, it))
    v.addWidget(app.side_list, 1)

    app.side_stats = _label("0 Systems · 0 Matched · 0 Unmatched", "hint", wrap=True)
    v.addWidget(app.side_stats)
    return f


def _on_side_clicked(app, item):
    idx = app.side_list.row(item)
    if 0 <= idx < len(app.systems):
        app._select_system_row(idx)
        if app.stack.currentIndex() in (P_HAP, P_SYS):
            go_page(app, P_EQ)


# ── HAP input page ────────────────────────────────────────────
def _build_hap_page(app):
    page, v = _page("HAP Input", "Load and analyze the HAP systems design report")

    # engine / file group
    grp = QGroupBox("Report file")
    g = QVBoxLayout(grp)
    app.hap_path = QLineEdit()
    app.hap_path.setReadOnly(True)
    app.hap_path.setPlaceholderText("No file selected — use Load HAP Report")
    g.addWidget(app.hap_path)

    btns = QHBoxLayout()
    app.batch_upload_btn = _btn("📚  Load HAP Systems Report", None, app.open_batch_file,
                                "Multi-system report (G01, G02 …). Each system is parsed separately.")
    app.upload = _btn("📄  Load Single Report", "secondary", app.open_file,
                      "Single-system report (one coil).")
    app.manual_btn = _btn("✏️  Enter / Paste Manually", "secondary", app.open_manual_input,
                          "Type values or paste text copied from the HAP PDF.")
    for b in (app.batch_upload_btn, app.upload, app.manual_btn):
        btns.addWidget(b)
    btns.addStretch()
    g.addLayout(btns)

    eng = QHBoxLayout()
    eng.addWidget(_label("Engine:"))
    app.extractor_combo = QComboBox()
    app.extractor_combo.addItem("Automatic")
    app._extractor_map = {}
    from extractors import get_available_extractors
    for name, inst in get_available_extractors():
        app.extractor_combo.addItem(name)
        app._extractor_map[name] = inst
    app.extractor_combo.setToolTip("Automatic picks the right engine from the file type.")
    eng.addWidget(app.extractor_combo)
    eng.addStretch()
    g.addLayout(eng)
    g.addWidget(_label("Automatic routing:   RTF → text / RTF parser   ·   PDF → PDF text extraction   ·   "
                       "PNG / JPG → OCR", "hint", wrap=True))
    v.addWidget(grp)

    # summary cards
    app.card_sys = StatCard("Systems")
    app.card_load = StatCard("Cooling load (kBtu/h)", tip="Sum of all system total coil loads.")
    app.card_cfm = StatCard("Total CFM", tip="Sum of required airflow across systems.")
    app.card_match = StatCard("Matched", tip="Systems with a model added to the cart.")
    v.addWidget(_cards_row(app.card_sys, app.card_load, app.card_cfm, app.card_match))

    # preview + info
    split = QHBoxLayout()
    app.preview = PreviewLabel("No report loaded")
    app.preview.setAlignment(Qt.AlignCenter)
    app.preview.setWordWrap(True)
    app.preview.setMinimumHeight(240)
    app.preview.setStyleSheet(f"border:1px dashed {C_BORDER};color:{C_MUTED};background:{C_BG2};")
    app.preview._pix = None
    split.addWidget(app.preview, 1)
    v.addLayout(split, 1)

    # advanced diagnostics (collapsed)
    box = QWidget()
    bl = QVBoxLayout(box)
    bl.setContentsMargins(0, 0, 0, 0)
    app.debug = QTextEdit()
    app.debug.setReadOnly(True)
    app.debug.setMaximumHeight(180)
    bl.addWidget(app.debug)
    bl.addWidget(_btn("Copy Debug Text", "secondary",
                      lambda: QApplication.clipboard().setText(app.debug.toPlainText())), 0, Qt.AlignLeft)
    v.addWidget(Collapsible("Advanced Diagnostics — OCR / extracted text", box))
    return page


# ── systems page ──────────────────────────────────────────────
def _build_systems_page(app):
    page, v = _page("Systems", "All air systems parsed from the HAP report — double-click a system to select equipment")

    app.sys_summary = _label("", "hint")
    v.addWidget(app.sys_summary)

    top = QHBoxLayout()
    app.sys_filter = QLineEdit()
    app.sys_filter.setPlaceholderText("Filter systems…  (Ctrl+F)")
    app.sys_filter.textChanged.connect(lambda t: _filter_systems(app, t))
    app.sys_filter.setMaximumWidth(320)
    top.addWidget(app.sys_filter)
    top.addStretch()
    v.addLayout(top)

    app.sys_empty = _label("No systems loaded yet.\nUse “Load HAP Systems Report” on the HAP Input page.", "muted")
    app.sys_empty.setAlignment(Qt.AlignCenter)
    v.addWidget(app.sys_empty, 1)

    # --- same widgets/names the backend expects -----------------
    app.systems_grp = QGroupBox("HAP Systems — 0 loaded")
    sl = QVBoxLayout(app.systems_grp)

    nav = QHBoxLayout()
    app.btn_prev_sys = _btn("⬅ Prev", "secondary", lambda: app._navigate_system(-1))
    app.lbl_nav_pos = QLabel("—")
    app.lbl_nav_pos.setAlignment(Qt.AlignCenter)
    app.lbl_nav_pos.setMinimumWidth(110)
    app.lbl_nav_pos.setStyleSheet("color:#93C5FD;font-weight:700;background:transparent;")
    app.btn_next_sys = _btn("Next ➡", "secondary", lambda: app._navigate_system(+1))
    app.auto_advance_cb = QCheckBox("Auto-advance after Add")
    app.auto_advance_cb.setChecked(True)
    for w in (app.btn_prev_sys, app.lbl_nav_pos, app.btn_next_sys):
        nav.addWidget(w)
    nav.addSpacing(12)
    nav.addWidget(app.auto_advance_cb)
    nav.addStretch()
    nav.addWidget(_btn("Open Equipment Selection  →", None, lambda: go_page(app, P_EQ)))
    sl.addLayout(nav)

    app.systems_table = _configure_table(QTableWidget(), stretch_last=True)
    app.systems_table.setSelectionMode(QTableWidget.SingleSelection)
    app.systems_table.itemSelectionChanged.connect(app._on_system_selected)
    app.systems_table.itemDoubleClicked.connect(lambda *_: go_page(app, P_EQ))
    sl.addWidget(app.systems_table)
    app.systems_grp.setVisible(False)
    v.addWidget(app.systems_grp, 1)
    return page


def _filter_systems(app, text):
    t = text.strip().lower()
    for r in range(app.systems_table.rowCount()):
        hit = not t or any(
            (app.systems_table.item(r, c) and t in app.systems_table.item(r, c).text().lower())
            for c in range(app.systems_table.columnCount()))
        app.systems_table.setRowHidden(r, not hit)


def _focus_filter(app):
    go_page(app, P_SYS)
    app.sys_filter.setFocus()
    app.sys_filter.selectAll()


# ── equipment selection page ──────────────────────────────────
def _build_equipment_page(app):
    page, v = _page("Equipment Selection", "Set the requirements, search the catalogs, review the best match")
    app.eq_title = _label("", "section")
    v.addWidget(app.eq_title)

    split = QSplitter(Qt.Horizontal)

    # ----- left: requirements & filters (all backend field names kept)
    left = QWidget()
    left.setObjectName("plain")
    lv = QVBoxLayout(left)
    lv.setContentsMargins(0, 0, 8, 0)

    tabs = QTabWidget()

    t1 = QWidget()
    t1l = QVBoxLayout(t1)
    app.btu = _line(app, "")
    app.sens = _line(app, "")
    app.cfm = _line(app, "")
    app.heat = _line(app, "")
    t1l.addWidget(_row("Total coil load (Btu/hr)", app.btu, "Total cooling coil load from HAP."))
    t1l.addWidget(_row("Sensible load (Btu/hr)", app.sens, "Sensible cooling load from the HAP report."))
    t1l.addWidget(_row("Required airflow (CFM)", app.cfm, "Required airflow in cubic feet per minute."))
    app.esp = QLineEdit("0")
    app.esp.setPlaceholderText("0.10 – 0.40")
    app.esp_calc_btn = _btn("Calculator", "secondary", app.open_esp_calculator,
                            "Open the ESP calculator (ductwork, fittings, accessories).")
    esp_w = QWidget()
    eh = QHBoxLayout(esp_w)
    eh.setContentsMargins(0, 0, 0, 0)
    eh.addWidget(app.esp, 1)
    eh.addWidget(app.esp_calc_btn)
    t1l.addWidget(_row("ESP (in.w.g.)", esp_w, "External Static Pressure in inches of water gauge."))
    t1l.addWidget(_label("HAP often shows 0.00 when no ductwork is entered — use the calculator.",
                         "hint", wrap=True))
    t1l.addWidget(_row("Heating load (Btu/hr)", app.heat, "Optional heating coil load."))
    t1l.addStretch()
    tabs.addTab(t1, "Requirements")

    t2 = QWidget()
    t2l = QVBoxLayout(t2)
    app.catalog_family = QComboBox()
    app.catalog_family.addItems([lbl for lbl, _ in app.CATALOGS])
    app.phase = QComboBox()
    app.phase.addItems(["Any", "1-Phase (220-240V)", "3-Phase (380-420V)"])
    app.outdoor_temp = QComboBox()
    app.outdoor_temp.addItems(["35", "43", "46", "52"])
    app.outdoor_temp.setCurrentText("43")
    app.fan_speed = QComboBox()
    app.fan_speed.addItems(["High", "Medium", "Low", "Any"])
    app.max_sound = QLineEdit("0")
    t2l.addWidget(_row("Catalog", app.catalog_family))
    t2l.addWidget(_row("Power supply", app.phase))
    t2l.addWidget(_row("Outdoor DB (°C)", app.outdoor_temp,
                       "Outdoor dry-bulb temperature used for equipment performance lookup."))
    t2l.addWidget(_row("Fan speed", app.fan_speed, "Preferred fan speed. 'Any' accepts every speed."))
    t2l.addWidget(_row("Max sound dB(A)", app.max_sound, "Optional. 0 = no limit."))

    # ── Ceiling void / clearance check ──
    t2l.addWidget(_label("CEILING VOID CHECK", "section"))
    app.ceiling_void = QLineEdit("0")
    app.ceiling_clear = QLineEdit("100")
    app.drain_run = QLineEdit("0")
    app.ceiling_enforce = QCheckBox("Mark units that do not fit as NOT SUITABLE")
    t2l.addWidget(_row("Ceiling void depth (mm)", app.ceiling_void,
                       "Max available depth of the suspended-ceiling void. 0 = check off."))
    t2l.addWidget(_row("Hanger / ceiling allowance (mm)", app.ceiling_clear,
                       "Extra room needed above/below the unit: hanger rods, slab, ceiling-tile build-up."))
    t2l.addWidget(_row("Condensate drain run (m)", app.drain_run,
                       "Gravity drain length. Adds 10 mm of fall per metre (1:100) to the required void."))
    t2l.addWidget(app.ceiling_enforce)
    for w in (app.ceiling_void, app.ceiling_clear, app.drain_run):
        w.editingFinished.connect(app.search)
    app.ceiling_enforce.stateChanged.connect(app.search)
    t2l.addStretch()
    tabs.addTab(t2, "Filters")

    t3 = QWidget()
    t3l = QVBoxLayout(t3)

    def _spin(val):
        x = QDoubleSpinBox()
        x.setRange(0, 50)
        x.setValue(val)
        x.setSuffix(" %")
        return x
    app.capm, app.afm, app.safm = _spin(10), _spin(10), _spin(10)
    t3l.addWidget(_row("Capacity margin", app.capm))
    t3l.addWidget(_row("Airflow margin", app.afm))
    t3l.addWidget(_row("Sensible margin", app.safm))
    t3l.addStretch()
    tabs.addTab(t3, "Margins")
    lv.addWidget(tabs)

    app.searchbtn = _btn("🔍  Search Suitable Equipment", None, app.search)
    lv.addWidget(app.searchbtn)
    app.best_match_only = QCheckBox("Show best match only (nearest fit)")
    app.best_match_only.stateChanged.connect(app.search)
    lv.addWidget(app.best_match_only)

    nav = QHBoxLayout()
    nav.addWidget(_btn("⬅ Prev system", "secondary", lambda: app._navigate_system(-1)))
    nav.addWidget(_btn("Next system ➡", "secondary", lambda: app._navigate_system(+1)))
    lv.addLayout(nav)
    lv.addStretch()
    split.addWidget(left)

    # ----- right: best match + results
    right = QWidget()
    right.setObjectName("plain")
    rv = QVBoxLayout(right)
    rv.setContentsMargins(8, 0, 0, 0)

    app.best_card = BestMatchCard(app._add_best_match)
    rv.addWidget(app.best_card)

    app.summary = QLabel("")
    app.summary.setWordWrap(True)
    app.summary.setStyleSheet(f"color:{C_OK};font-weight:600;background:transparent;")
    rv.addWidget(app.summary)

    rv.addWidget(_label("OTHER SUITABLE UNITS", "section"))
    app.table = _configure_table(QTableWidget())
    app.table.setWordWrap(False)
    app.table.itemSelectionChanged.connect(app._on_row_selected)
    app.table.itemDoubleClicked.connect(lambda *_: _show_details(app))
    rv.addWidget(app.table, 1)

    app.selected_label = QLabel("Click a row to select a model — Ctrl+click several rows to combine units — double-click for details")
    app.selected_label.setStyleSheet(f"color:{C_MUTED};font-style:italic;background:transparent;")
    app.selected_label.setWordWrap(True)
    rv.addWidget(app.selected_label)

    bar = QHBoxLayout()
    app.add_to_cart_btn = _btn("＋ Add Selected Row", "success", app._add_to_selection,
                               "Add the highlighted row to the Selection Cart.")
    app.add_to_cart_btn.setEnabled(False)
    app.add_best_btn = _btn("⭐ Add Best Match", "warning", app._add_best_match,
                            "Pick the best suitable row automatically and add it.")
    app.add_best_btn.setEnabled(False)
    app.combine_btn = _btn("⊕ Combine Units…", "secondary", app._combine_units,
                           "Ctrl+click several rows (or one row for 2× the same model), then combine them to cover the load together.")
    app.export = _btn("💾 Export Results", "secondary", app.export_report,
                      "Export the current results table (format chosen on the Reports page).")
    app.export.setEnabled(False)
    bar.addWidget(app.add_to_cart_btn)
    bar.addWidget(app.add_best_btn)
    bar.addWidget(app.combine_btn)
    bar.addStretch()
    bar.addWidget(app.export)
    rv.addLayout(bar)

    split.addWidget(right)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([430, 1000])
    v.addWidget(split, 1)
    return page


def _show_details(app):
    r = app.selected_row
    if r < 0 or r >= app.table.rowCount():
        return
    headers = [app.table.horizontalHeaderItem(c).text() for c in range(app.table.columnCount())]
    values = [(app.table.item(r, c).text() if app.table.item(r, c) else "")
              for c in range(app.table.columnCount())]
    EquipmentDetailsDialog(app, headers, values).exec()


# ── cart page ─────────────────────────────────────────────────
def _build_cart_page(app):
    page, v = _page("Selection Cart", "One selected model per system — review before exporting")

    app.card_cart_n = StatCard("Selected systems")
    app.card_cart_cap = StatCard("Total rated capacity (Btu/hr)")
    app.card_cart_cost = StatCard("Estimated annual cost")
    v.addWidget(_cards_row(app.card_cart_n, app.card_cart_cap, app.card_cart_cost))

    app.cart_grp = QGroupBox("Selection Cart")
    cl = QVBoxLayout(app.cart_grp)
    app.lbl_cart_hint = _label("", "hint", wrap=True)
    cl.addWidget(app.lbl_cart_hint)
    app.cart_table = _configure_table(QTableWidget(), stretch_last=True)
    app.cart_table.setSelectionMode(QTableWidget.ExtendedSelection)
    app.cart_table.itemSelectionChanged.connect(app._on_cart_selection)
    cl.addWidget(app.cart_table, 1)

    row = QHBoxLayout()
    app.cart_remove_btn = _btn("Remove Selected", "warning", app._remove_from_cart)
    app.cart_remove_btn.setEnabled(False)
    app.cart_clear_btn = _btn("Clear Cart", "danger", app._clear_cart)
    app.cart_clear_btn.setEnabled(False)
    app.cart_export_btn = _btn("Export Cart  ▾", "success", None,
                               "Choose a format: Excel, Word, PDF, CSV, HTML, JSON, Markdown.")
    from PySide6.QtWidgets import QMenu
    menu = QMenu(app.cart_export_btn)
    from exporters import FORMATS as _F
    for label in _F:
        menu.addAction(label, lambda l=label: app._export_cart(l))
    app.cart_export_btn.setMenu(menu)
    app.cart_export_btn.setEnabled(False)
    app.cart_dxf_btn = _btn("📐 Export CAD DXF", "secondary", app.export_cad_schedule,
                            "Generate an AutoCAD DXF schedule table ready to drop onto CAD drawing sheets.")
    row.addWidget(app.cart_remove_btn)
    row.addWidget(app.cart_clear_btn)
    row.addStretch()
    row.addWidget(app.cart_dxf_btn)
    row.addWidget(app.cart_export_btn)
    cl.addLayout(row)
    v.addWidget(app.cart_grp, 1)
    return page


# ── energy page ───────────────────────────────────────────────
def _build_energy_page(app):
    page, v = _page("Energy & Cost", "Estimated electrical consumption and annual operating cost of the selected units")

    grp = QGroupBox("Energy assumptions")
    f = QHBoxLayout(grp)
    app.hours_per_year = QSpinBox()
    app.hours_per_year.setRange(100, 8760)
    app.hours_per_year.setValue(2000)
    app.hours_per_year.setSingleStep(100)
    app.hours_per_year.setSuffix(" h/year")
    app.tariff = QLineEdit("2.0")
    app.tariff.setPlaceholderText("currency per kWh")
    app.tariff.setMaximumWidth(120)
    app.currency_sym = QLineEdit("EGP")
    app.currency_sym.setMaxLength(8)
    app.currency_sym.setMaximumWidth(80)
    f.addWidget(_label("Operating hours:"))
    f.addWidget(app.hours_per_year)
    f.addSpacing(16)
    f.addWidget(_label("Electricity tariff (per kWh):"))
    f.addWidget(app.tariff)
    f.addSpacing(16)
    f.addWidget(_label("Currency:"))
    f.addWidget(app.currency_sym)
    f.addStretch()
    v.addWidget(grp)

    app.tariff_warn = _label("", "warn", wrap=True)
    v.addWidget(app.tariff_warn)
    v.addWidget(_label("Annual cost = (Capacity ÷ EER ÷ 1000) × Hours × Tariff", "hint"))

    app.card_e_cost = StatCard("Annual cost")
    app.card_e_kwh = StatCard("Energy (kWh/year)")
    app.card_e_n = StatCard("Systems")
    app.card_e_avg = StatCard("Average cost / system")
    v.addWidget(_cards_row(app.card_e_cost, app.card_e_kwh, app.card_e_n, app.card_e_avg))

    v.addWidget(_label("ENERGY BREAKDOWN", "section"))
    app.energy_table = _configure_table(QTableWidget(), stretch_last=True)
    app.energy_table.setSortingEnabled(True)
    v.addWidget(app.energy_table, 1)
    return page


# ── diffuser selection page ───────────────────────────────────

class DiffuserCanvas(QWidget):
    """Professional 2D floor-plan preview with dimensions,
    T50 throw circles, and diffuser coordinate labels."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self.setStyleSheet(
            f"background:{C_BG};border:1px solid {C_BORDER};border-radius:6px;")
        self.room_w = 20.0
        self.room_l = 20.0
        self.positions = []
        self.throw_t50 = 8.0
        self.model_label = "CCD"
        self.cad_room = None
        self.geometry = None

    def set_data(self, width_ft, length_ft, positions,
                 throw_t50, model_label="CCD", cad_room=None, geometry=None):
        self.room_w = max(1.0, float(width_ft or 0))
        self.room_l = max(1.0, float(length_ft or 0))
        self.positions = list(positions or [])
        self.throw_t50 = max(0.5, float(throw_t50 or 0))
        self.model_label = str(model_label or "CCD")
        self.cad_room = cad_room
        self.geometry = geometry
        self.update()

    def _display_tags(self):
        diffusers = (self.cad_room or {}).get("diffusers", [])
        tags = [diffuser.get("tag") for diffuser in diffusers]
        if (len(tags) == len(self.positions)
                and all(isinstance(tag, str) and tag for tag in tags)):
            return tags
        return [f"D{index + 1}" for index in range(len(self.positions))]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        H = self.height()
        margin = 35.0

        if (self.room_w <= 0 or self.room_l <= 0
                or (not self.positions and self.geometry is None
                    and not self.cad_room)):
            p.setPen(QColor(C_MUTED))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Select a system and press \U0001f504 Recalculate")
            return

        avail_w = max(10.0, W - 2 * margin)
        avail_h = max(10.0, H - 2 * margin)
        scale = min(avail_w / self.room_w, avail_h / self.room_l)
        draw_w = self.room_w * scale
        draw_h = self.room_l * scale
        ox = (W - draw_w) / 2.0
        oy = (H - draw_h) / 2.0

        # Draw the actual polygon for a CAD-designed room.
        cad_room = getattr(self, "cad_room", None)
        geometry = getattr(self, "geometry", None)
        if geometry is None and cad_room:
            try:
                from room_geometry import RoomGeometry
                geometry = RoomGeometry.from_dict(cad_room)
                if not geometry.is_valid:
                    geometry = None
            except (KeyError, TypeError, ValueError, IndexError):
                geometry = None
        if geometry is not None:
            from diffuser_calculator import m_to_ft
            pts_ft = [(m_to_ft(px), m_to_ft(py))
                      for px, py in geometry.vertices]
            # Include diffuser centers AND their T50 throw radius so
            # the framing never clips a throw circle at the edges.
            _t50 = float(getattr(self, "throw_t50", 0.0) or 0.0)
            all_xs = [pt[0] for pt in pts_ft] + [pos[0] for pos in self.positions]
            all_ys = [pt[1] for pt in pts_ft] + [pos[1] for pos in self.positions]
            min_x, max_x = min(all_xs) - _t50, max(all_xs) + _t50
            min_y, max_y = min(all_ys) - _t50, max(all_ys) + _t50
            rw = max(1.0, max_x - min_x)
            rl = max(1.0, max_y - min_y)

            scale = min((W - 2 * margin) / rw, (H - 2 * margin) / rl)
            ox = (W - rw * scale) / 2.0
            oy = (H - rl * scale) / 2.0

            qpoly = QPolygonF([QPointF(ox + (px - min_x) * scale, oy + (max_y - py) * scale) for px, py in pts_ft])
            p.setBrush(QBrush(QColor("#131D2F")))
            p.setPen(QPen(QColor("#60A5FA"), 2.5))
            p.drawPolygon(qpoly)

            # Draw the calculated diffusers.
            for tag, (px, py) in zip(
                    self._display_tags(), self.positions, strict=True):
                # Convert diffuser coordinates.
                cx = ox + (px - min_x) * scale if rw > 0 else ox
                cy = oy + (max_y - py) * scale if rl > 0 else oy

                # Draw the T50 throw circle.
                r = self.throw_t50 * scale
                p.setPen(QPen(QColor(37, 99, 235, 110), 1.0, Qt.DashLine))
                p.setBrush(QBrush(QColor(37, 99, 235, 35)))
                p.drawEllipse(QPointF(cx, cy), r, r)

                # Draw the diffuser marker.
                p.setPen(QPen(QColor("#FFFFFF"), 1.5))
                p.setBrush(QBrush(QColor("#F59E0B")))
                p.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
                p.setFont(QFont("Inter", 8, QFont.Bold))
                p.setPen(QColor("#FFFFFF"))
                p.drawText(QPointF(cx + 8, cy - 4), tag)

            if not self.positions:
                p.setPen(QColor(C_MUTED))
                p.drawText(self.rect(), Qt.AlignCenter,
                           "Room geometry valid — diffuser layout requires recalculation")
            p.end()
            return

        # ── Room outline ──
        p.fillRect(QRectF(ox, oy, draw_w, draw_h), QColor("#131D2F"))
        p.setPen(QPen(QColor("#60A5FA"), 2.0))
        p.drawRect(QRectF(ox, oy, draw_w, draw_h))

        # ── Dimension labels ──
        p.setFont(QFont("Inter", 8))
        p.setPen(QColor(C_MUTED))
        p.drawText(QRectF(ox, oy - 20, draw_w, 18), Qt.AlignCenter,
                   f"Width: {self.room_w:.1f} ft")
        p.save()
        p.translate(ox - 10, oy + draw_h / 2.0)
        p.rotate(-90)
        p.drawText(QRectF(-draw_h / 2.0, -10, draw_h, 20), Qt.AlignCenter,
                   f"Length: {self.room_l:.1f} ft")
        p.restore()

        # ── Throw circles (T50) ──
        p.setPen(QPen(QColor(37, 99, 235, 90), 1.0, Qt.DashLine))
        p.setBrush(QBrush(QColor(37, 99, 235, 30)))
        for (px, py) in self.positions:
            cx = ox + px * scale
            cy = oy + py * scale
            r = self.throw_t50 * scale
            p.drawEllipse(QPointF(cx, cy), r, r)

        # ── Diffuser dots + coordinate labels ──
        for tag, (px, py) in zip(
                self._display_tags(), self.positions, strict=True):
            cx = ox + px * scale
            cy = oy + py * scale
            p.setPen(QPen(QColor("#F59E0B"), 2.0))
            p.setBrush(QBrush(QColor("#FBBF24")))
            p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(QFont("Inter", 8, QFont.Bold))
            p.drawText(QPointF(cx + 7, cy - 6),
                       f"{tag} ({px:.1f}, {py:.1f})")

        p.end()


def _refresh_diffuser_summary(app):
    """Compat shim: refresh_shell() calls this, but the actual
    rendering lives on the App class (added by
    apply_diffuser_functions.sh). Delegate to it when present."""
    method = getattr(app, "_render_diffusers_summary_table", None)
    if callable(method):
        try:
            method()
        except (AttributeError, RuntimeError):
            logging.getLogger(__name__).exception(
                "Could not refresh diffuser summary")


def _diffuser_selection_matches_inputs(app, item):
    """Compare persisted selection inputs without launching placement search."""
    selection = item.get("diffuser_selection") or {}
    saved_signature = selection.get("input_signature")
    if not isinstance(saved_signature, dict):
        return False

    from diffuser_calculator import (
        ft_to_m,
        selection_input_signature,
        selection_signatures_match,
    )
    from room_geometry import RoomGeometry

    cad_room = item.get("cad_room_data")
    if cad_room is not None:
        try:
            geometry = RoomGeometry.from_dict(cad_room)
        except (KeyError, TypeError, ValueError, IndexError):
            return False
    else:
        width = float(selection.get("room_w_ft", 0) or 0)
        length = float(selection.get("room_l_ft", 0) or 0)
        height = float(selection.get("room_h_ft", 0) or 0)
        if min(width, length, height) <= 0:
            return False
        geometry = RoomGeometry.from_vertices(
            [(0.0, 0.0), (ft_to_m(width), 0.0),
             (ft_to_m(width), ft_to_m(length)), (0.0, ft_to_m(length))],
            ft_to_m(height))

    geometry_signature = {
        "vertices": [list(point) for point in geometry.vertices],
        "height_m": geometry.height_m,
    }
    sensible_load = float(item.get("sensible_btu", 0) or 0)
    from airflow_utils import design_airflow_cfm
    expected_signature = selection_input_signature(
        design_airflow_cfm(item),
        sensible_load if sensible_load > 0 else None,
        geometry_signature,
        selection.get("type_label", ""),
        selection.get("app_key", "office_priv"),
        (float(selection.get("max_nc", 0) or 0)
         if float(selection.get("max_nc", 0) or 0) > 0 else None),
        float(selection.get("max_dp", 0.20) or 0.20))
    return selection_signatures_match(saved_signature, expected_signature)


def _build_diffuser_page(app):
    page, v = _page(
        "Diffuser Selection",
        "Choose the air diffuser for every system \u2014 size, throw, layout and noise")

    # System selector strip
    top = QHBoxLayout()
    app.diff_sys_combo = QComboBox()
    app.diff_sys_combo.setMinimumWidth(260)
    app.diff_sys_combo.currentTextChanged.connect(
        lambda _: app._on_diffuser_sys_changed())
    app.diff_prev_btn = _btn("\u2b05 Prev", "secondary",
                             lambda: _diffuser_step(app, -1))
    app.diff_next_btn = _btn("Next \u27a1", "secondary",
                             lambda: _diffuser_step(app, +1))
    app.diff_progress = QLabel("\u2014")
    app.diff_progress.setStyleSheet(
        "color:#93C5FD;font-weight:700;background:transparent;")
    top.addWidget(_label("System:"))
    top.addWidget(app.diff_sys_combo)
    top.addWidget(app.diff_prev_btn)
    top.addWidget(app.diff_next_btn)
    top.addSpacing(16)
    top.addWidget(app.diff_progress)
    top.addStretch()
    v.addLayout(top)

    split = QSplitter(Qt.Horizontal)

    # ── LEFT: inputs ──────────────────────────────────────
    left = QWidget()
    left.setObjectName("plain")
    lv = QVBoxLayout(left)
    lv.setContentsMargins(0, 0, 8, 0)

    grp_room = QGroupBox("Room Dimensions")
    rl = QVBoxLayout(grp_room)
    app.diff_width = QDoubleSpinBox()
    app.diff_width.setRange(1, 500); app.diff_width.setValue(20)
    app.diff_width.setSuffix(" ft"); app.diff_width.setDecimals(1)
    app.diff_length = QDoubleSpinBox()
    app.diff_length.setRange(1, 500); app.diff_length.setValue(20)
    app.diff_length.setSuffix(" ft"); app.diff_length.setDecimals(1)
    app.diff_height = QDoubleSpinBox()
    app.diff_height.setRange(6, 40); app.diff_height.setValue(10)
    app.diff_height.setSuffix(" ft"); app.diff_height.setDecimals(1)
    app.diff_return_cfm = QDoubleSpinBox()
    app.diff_return_cfm.setRange(0, 100_000)
    app.diff_return_cfm.setValue(0)
    app.diff_return_cfm.setDecimals(0)
    app.diff_return_cfm.setSuffix(" CFM")
    app.diff_return_cfm.setToolTip(
        "Return airflow defaults to the system supply airflow and can be edited.")
    rl.addWidget(_row("Width", app.diff_width))
    rl.addWidget(_row("Length", app.diff_length))
    rl.addWidget(_row("Ceiling Height", app.diff_height,
                      "Floor to diffuser face."))
    rl.addWidget(_row("Return Airflow", app.diff_return_cfm,
                      "Defaults to supply airflow."))
    # ── Room Designer CAD Integration Button ──
    app.btn_open_room_designer = _btn("📐 Open CAD Room Designer (2D / 3D)", "success",
                                      app.open_room_designer_for_current_system,
                                      "Draw real polygon room, place doors, windows and view in 3D.")
    rl.addWidget(app.btn_open_room_designer)

    lv.addWidget(grp_room)

    grp_app = QGroupBox("Application & Diffuser Type")
    al = QVBoxLayout(grp_app)
    app.diff_app_combo = QComboBox()
    app.diff_app_combo.addItems([
        "Office (Private)", "Office (Open Plan)", "Conference / Meeting", "Classroom",
        "Hospital / Clinic", "Residential", "Retail / Showroom",
        "Industrial", "Server Room"])
    app.diff_type_combo = QComboBox()
    app.diff_type_combo.addItems(["Circular (CCD)", "Square 4-way (4SCD)", "Square 3-way (3SCD)"])
    al.addWidget(_row("Application", app.diff_app_combo))
    al.addWidget(_row("Diffuser Type", app.diff_type_combo))
    lv.addWidget(grp_app)

    grp_lim = QGroupBox("Limits")
    ll = QVBoxLayout(grp_lim)
    app.diff_max_nc = QDoubleSpinBox()
    app.diff_max_nc.setRange(0, 60); app.diff_max_nc.setValue(0)
    app.diff_max_nc.setSuffix(" dB")
    app.diff_max_nc.setSpecialValueText("auto")
    app.diff_max_dp = QDoubleSpinBox()
    app.diff_max_dp.setRange(0.01, 1.0); app.diff_max_dp.setValue(0.20)
    app.diff_max_dp.setSingleStep(0.01); app.diff_max_dp.setDecimals(2)
    app.diff_max_dp.setSuffix(" in.wg")
    ll.addWidget(_row("Max NC", app.diff_max_nc,
                      "0 = derive from application."))
    ll.addWidget(_row("Max \u0394P", app.diff_max_dp))
    lv.addWidget(grp_lim)

    grp_vcd = QGroupBox("Optional Supply-Branch VCD")
    vl = QVBoxLayout(grp_vcd)
    app.diff_vcd_width_mm = QDoubleSpinBox()
    app.diff_vcd_width_mm.setRange(0, 10_000)
    app.diff_vcd_width_mm.setDecimals(0)
    app.diff_vcd_width_mm.setSpecialValueText("disabled")
    app.diff_vcd_width_mm.setSuffix(" mm")
    app.diff_vcd_height_mm = QDoubleSpinBox()
    app.diff_vcd_height_mm.setRange(0, 10_000)
    app.diff_vcd_height_mm.setDecimals(0)
    app.diff_vcd_height_mm.setSpecialValueText("disabled")
    app.diff_vcd_height_mm.setSuffix(" mm")
    vl.addWidget(_row("Branch Duct Width", app.diff_vcd_width_mm))
    vl.addWidget(_row("Branch Duct Height", app.diff_vcd_height_mm))
    lv.addWidget(grp_vcd)
    grp_vcd.setVisible(SHOW_PHASE2_DUCT_OPTIONS)

    grp_rvcd = QGroupBox("Optional Return RVCD")
    rvl = QVBoxLayout(grp_rvcd)
    app.diff_rvcd_diameter_in = QDoubleSpinBox()
    app.diff_rvcd_diameter_in.setRange(0, 100)
    app.diff_rvcd_diameter_in.setDecimals(3)
    app.diff_rvcd_diameter_in.setSpecialValueText("disabled")
    app.diff_rvcd_diameter_in.setSuffix(" in")
    app.diff_rvcd_pressure_class = QComboBox()
    app.diff_rvcd_pressure_class.addItems(["LP", "HP"])
    rvl.addWidget(_row("Round Damper Diameter", app.diff_rvcd_diameter_in))
    rvl.addWidget(_row("Pressure Class", app.diff_rvcd_pressure_class))
    lv.addWidget(grp_rvcd)
    grp_rvcd.setVisible(SHOW_PHASE2_DUCT_OPTIONS)

    # Auto-recalculate on any change
    for w in (app.diff_width, app.diff_length, app.diff_height,
              app.diff_max_nc, app.diff_max_dp):
        w.valueChanged.connect(lambda *_: app._mark_project_dirty())
        w.valueChanged.connect(lambda _: app._calculate_current_diffuser())
    app.diff_app_combo.currentTextChanged.connect(
        lambda *_: app._mark_project_dirty())
    app.diff_type_combo.currentTextChanged.connect(
        lambda *_: app._mark_project_dirty())
    app.diff_app_combo.currentTextChanged.connect(
        lambda _: app._calculate_current_diffuser())
    app.diff_type_combo.currentTextChanged.connect(
        lambda _: app._calculate_current_diffuser())
    app.diff_return_cfm.valueChanged.connect(app._on_return_cfm_changed)
    app.diff_vcd_width_mm.valueChanged.connect(
        lambda *_: app._on_vcd_dimensions_changed())
    app.diff_vcd_height_mm.valueChanged.connect(
        lambda *_: app._on_vcd_dimensions_changed())
    app.diff_rvcd_diameter_in.valueChanged.connect(
        lambda *_: app._on_rvcd_inputs_changed())
    app.diff_rvcd_pressure_class.currentTextChanged.connect(
        lambda *_: app._on_rvcd_inputs_changed())

    recalc_row = QHBoxLayout()
    app.diff_recalc_btn = _btn("\U0001f504 Recalculate", None,
                               lambda: app._calculate_current_diffuser(
                                   replace_invalid_saved_layout=True))
    app.diff_apply_fallback_btn = _btn(
        "Apply Fallback Layout", "warning",
        lambda: app._apply_diffuser_fallback(),
        "Accept the best available layout despite failed screening criteria.")
    app.diff_apply_fallback_btn.setEnabled(False)
    recalc_row.addWidget(app.diff_recalc_btn)
    recalc_row.addWidget(app.diff_apply_fallback_btn)
    lv.addLayout(recalc_row)

    app.diff_auto_btn = _btn("\u2699\ufe0f  Auto-Select All Systems", "success",
                             lambda: app._auto_select_all_diffusers())
    lv.addWidget(app.diff_auto_btn)
    lv.addStretch()
    split.addWidget(left)

    # ── RIGHT: result + canvas + summary ──────────────────
    right = QWidget()
    right.setObjectName("plain")
    rv = QVBoxLayout(right)
    rv.setContentsMargins(8, 0, 0, 0)

    app.diff_model_name = _label("\u2014")
    app.diff_model_name.setStyleSheet(
        "font-size:18px;font-weight:700;color:white;background:transparent;")
    rv.addWidget(app.diff_model_name)

    app.diff_airflow_lbl = QLabel("")
    app.diff_airflow_lbl.setWordWrap(True)
    app.diff_airflow_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px;color:{C_TEXT};")
    rv.addWidget(app.diff_airflow_lbl)

    app.diff_metrics_lbl = QLabel("")
    app.diff_metrics_lbl.setTextFormat(Qt.RichText)
    app.diff_metrics_lbl.setWordWrap(True)
    app.diff_metrics_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:10px;color:{C_TEXT};")
    rv.addWidget(app.diff_metrics_lbl)

    app.diff_return_result_lbl = QLabel("")
    app.diff_return_result_lbl.setWordWrap(True)
    app.diff_return_result_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px;color:{C_TEXT};")
    rv.addWidget(app.diff_return_result_lbl)

    app.diff_vcd_result_lbl = QLabel("")
    app.diff_vcd_result_lbl.setWordWrap(True)
    app.diff_vcd_result_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px;color:{C_TEXT};")
    rv.addWidget(app.diff_vcd_result_lbl)
    app.diff_vcd_result_lbl.setVisible(SHOW_PHASE2_DUCT_OPTIONS)

    app.diff_rvcd_result_lbl = QLabel("")
    app.diff_rvcd_result_lbl.setWordWrap(True)
    app.diff_rvcd_result_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px;color:{C_TEXT};")
    rv.addWidget(app.diff_rvcd_result_lbl)
    app.diff_rvcd_result_lbl.setVisible(SHOW_PHASE2_DUCT_OPTIONS)

    app.diff_access_door_result_lbl = QLabel("")
    app.diff_access_door_result_lbl.setWordWrap(True)
    app.diff_access_door_result_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px;color:{C_TEXT};")
    rv.addWidget(app.diff_access_door_result_lbl)
    app.diff_access_door_result_lbl.setVisible(SHOW_PHASE2_DUCT_OPTIONS)

    app.diff_rationale_lbl = QLabel("Selection Rationale: —")
    app.diff_rationale_lbl.setWordWrap(True)
    app.diff_rationale_lbl.setStyleSheet(
        f"background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:6px;padding:8px 10px;color:{C_TEXT};")
    rv.addWidget(app.diff_rationale_lbl)

    rv.addWidget(_label("FLOOR-PLAN LAYOUT (T50 coverage shown)", "section"))
    app.diff_canvas = DiffuserCanvas()
    rv.addWidget(app.diff_canvas, 1)

    rv.addWidget(_label("ALL SYSTEMS \u2014 SUMMARY", "section"))
    app.diff_summary_table = _configure_table(QTableWidget(), stretch_last=True)
    rv.addWidget(app.diff_summary_table)

    split.addWidget(right)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([380, 1000])
    v.addWidget(split, 1)
    return page


def _diffuser_step(app, delta):
    n = app.diff_sys_combo.count()
    if n == 0:
        return
    app.diff_sys_combo.setCurrentIndex(
        (app.diff_sys_combo.currentIndex() + delta) % n)


# ── reports page ──────────────────────────────────────────────
def _build_reports_page(app):
    page, v = _page("Reports", "Export the selection as an engineering report")

    grp = QGroupBox("Export scope")
    g = QVBoxLayout(grp)
    app.rep_scope_cart = QRadioButton("Complete selection (Selection Cart)")
    app.rep_scope_cur = QRadioButton(
        "Current results table (all rows shown on Equipment Selection; "
        "Word = highlighted row)")
    app.rep_scope_cart.setChecked(True)
    bg = QButtonGroup(page)
    bg.addButton(app.rep_scope_cart)
    bg.addButton(app.rep_scope_cur)
    g.addWidget(app.rep_scope_cart)
    g.addWidget(app.rep_scope_cur)
    v.addWidget(grp)

    grp2 = QGroupBox("Format")
    g2 = QHBoxLayout(grp2)
    app.export_format = QComboBox()
    from exporters import FORMATS
    app.export_format.addItems(list(FORMATS))
    app.export_format.setCurrentText("Excel (.xlsx)")
    app.export_format.setMaximumWidth(220)
    g2.addWidget(app.export_format)
    g2.addWidget(_label("Word = detailed engineering report · others = tabular summary (one row per system)", "hint"))
    g2.addStretch()
    v.addWidget(grp2)

    app.rep_status = _label("", "hint", wrap=True)
    v.addWidget(app.rep_status)
    v.addWidget(_btn("Generate Report   (Ctrl+S)", "success", lambda: _generate_report(app)), 0, Qt.AlignLeft)
    v.addStretch()
    return page


def _generate_report(app):
    if app.rep_scope_cur.isChecked():
        if app.table.rowCount() == 0:
            QMessageBox.information(app, "Nothing to export", "Run a search on the Equipment Selection page first.")
            return
        app.export_report()
    else:
        if not app.cart:
            QMessageBox.information(app, "Empty cart",
                                    "The Selection Cart is empty. Add at least one model first.")
            return
        app._export_cart()


# ═══════════════════════════════════════════════════════════════
#  REFRESH — keeps all derived UI in sync with backend state
# ═══════════════════════════════════════════════════════════════
def refresh_shell(app):
    if not hasattr(app, "stack"):
        return  # shell not built yet (backend called early)

    n = len(app.systems)
    cart_n = len(app.cart)
    matched = sum(1 for s in app.systems if s.get("status") == "Added")
    hours, tariff, currency = app._energy_params()

    # header
    names = {Path(getattr(app, "_loaded_path", "") or "").name} - {""}
    app.hdr_project.setText("Project: " + (", ".join(names) if names else "—"))
    app.hdr_stats.setText(f"Systems: {n}   Matched: {matched}/{n}   Cart: {cart_n}")

    # workflow states
    has_input = n > 0 or _num(app.btu.text()) > 0
    done = {P_HAP: has_input, P_SYS: n > 0, P_EQ: cart_n > 0,
            P_CART: cart_n > 0, P_ENERGY: cart_n > 0,
            P_DIFF: bool(cart_n) and all(
                it.get("diffuser_selection") for it in app.cart.values()),
            P_REPORT: False}
    cur = app.stack.currentIndex()
    for i, b in enumerate(app.step_btns):
        st = "done" if done[i] and i != cur else "pending"
        mark = "✓ " if done[i] else ("● " if i == cur else "○ ")
        b.setText((mark + PAGES[i]).replace("&", "&&"))
        b.setProperty("state", st)
        b.setProperty("current", "true" if i == cur else "false")
        b.style().unpolish(b)
        b.style().polish(b)

    # sidebar
    path = getattr(app, "_loaded_path", "")
    app.side_file.setText(Path(path).name if path else "No HAP report loaded")
    app.side_loaded.setText(f"✓ Report loaded — {n} systems detected" if n else "")
    app.side_stats.setText(f"{n} Systems · {matched} Matched · {n - matched} Unmatched")
    _sync_side_list(app)

    # HAP cards
    tot = sum(s.get("total_btu", 0) or 0 for s in app.systems)
    cfm = sum(s.get("cfm", 0) or 0 for s in app.systems)
    app.card_sys.set(n)
    app.card_load.set(f"{tot / 1000:,.1f}")
    app.card_cfm.set(f"{cfm:,.0f}")
    app.card_match.set(f"{matched}/{n}")

    # systems page
    app.sys_summary.setText(f"{n} Systems · {n} Loaded · {matched} Matched · {n - matched} Unmatched")
    app.sys_empty.setVisible(n == 0)
    app.systems_grp.setVisible(n > 0)

    # equipment page
    if 0 <= app._current_system_idx < n:
        s = app.systems[app._current_system_idx]
        app.eq_title.setText(f"{s['name']} — EQUIPMENT SELECTION")
    else:
        app.eq_title.setText("MANUAL REQUIREMENTS")
    _refresh_best(app)

    # diffuser page — keep combo + summary in sync with cart
    if hasattr(app, "diff_sys_combo"):
        prev = app.diff_sys_combo.currentIndex()
        app.diff_sys_combo.blockSignals(True)
        app.diff_sys_combo.clear()
        for k in app.cart:
            app.diff_sys_combo.addItem(k)
        if 0 <= prev < app.diff_sys_combo.count():
            app.diff_sys_combo.setCurrentIndex(prev)
        elif app.diff_sys_combo.count() > 0:
            app.diff_sys_combo.setCurrentIndex(0)
        app.diff_sys_combo.blockSignals(False)

        if (app.diff_sys_combo.count() > 0
                and hasattr(app, "_on_diffuser_sys_changed")):
            project_loading = getattr(app, "_project_loading", False)
            app._on_diffuser_sys_changed(recalculate=False)
            if not project_loading:
                current_name = app.diff_sys_combo.currentText()
                item = app.cart.get(current_name, {})
                selection = item.get("diffuser_selection") or {}
                if (not selection
                        or not _diffuser_selection_matches_inputs(app, item)):
                    app._on_diffuser_sys_changed(recalculate=True)

        done_d = sum(1 for it in app.cart.values()
                     if it.get("diffuser_selection"))
        if hasattr(app, "diff_progress"):
            app.diff_progress.setText(
                f"{done_d}/{cart_n} selected" if cart_n else "—")
        _refresh_diffuser_summary(app)

    # cart + energy
    total_cap = total_cost = total_kwh = 0.0
    rows = []
    for it in app.cart.values():
        kw, annual = app._compute_energy_for(it, hours, tariff)
        kwh = kw * hours
        total_cap += float(it.get("rated_capacity", 0) or 0)
        total_kwh += kwh
        total_cost += annual
        rows.append((it, kw, kwh, annual))
    app.card_cart_n.set(f"{cart_n}/{n}" if n else cart_n)
    app.card_cart_cap.set(f"{total_cap:,.0f}")
    app.card_cart_cost.set(f"{total_cost:,.0f} {currency}/yr")
    app.card_e_cost.set(f"{total_cost:,.0f} {currency}")
    app.card_e_kwh.set(f"{total_kwh:,.0f}")
    app.card_e_n.set(cart_n)
    app.card_e_avg.set(f"{total_cost / cart_n:,.0f} {currency}" if cart_n else "—")
    app.tariff_warn.setText(
        "⚠ Tariff is empty or invalid — using the default 2.00 per kWh. Enter a positive number."
        if getattr(app, "_tariff_defaulted", False) else "")
    _fill_energy_table(app, rows, hours, currency)

    # status-bar counters
    app.sb_counts.setText(f"{n} Systems Loaded  ·  {matched}/{n} Matched  ·  Cart: {cart_n}   ")


def _sync_side_list(app):
    lst = app.side_list
    n = len(app.systems)
    lst.blockSignals(True)
    if lst.count() != n:
        lst.clear()
        for _ in range(n):
            lst.addItem(QListWidgetItem(""))
    for i, s in enumerate(app.systems):
        it = lst.item(i)
        icon = {"Added": "✓", "Searched": "●"}.get(s.get("status"), "○")
        load = f"{(s.get('total_btu') or 0) / 1000:5.1f} kBtu/h" if s.get("total_btu") else "   —"
        flow = f"{(s.get('cfm') or 0):,.0f} CFM" if s.get("cfm") else "—"
        it.setText(f"{icon}  {s['name']:<5} {load:>13}  {flow:>9}")
        col = {"Added": C_OK, "Searched": C_WARN}.get(s.get("status"), C_MUTED)
        it.setForeground(QBrush(QColor(col)))
    if 0 <= app._current_system_idx < n:
        lst.setCurrentRow(app._current_system_idx)
    lst.blockSignals(False)


def _refresh_best(app):
    card = app.best_card
    t = app.table
    t.resizeColumnsToContents()
    t.setColumnWidth(15, min(t.columnWidth(15), 420)) if t.columnCount() > 15 else None
    if t.rowCount() == 0:
        card.show_empty()
        return
    first = t.item(0, 0).text() if t.item(0, 0) else ""
    if first not in ("⭐ BEST MATCH", "SUITABLE"):
        card.show_none("No single unit covers this load. Ctrl+click two or more rows below and press <b>Combine Units…</b> to cover it with units working together.<br>"
                      "Otherwise review: cooling load · sensible load · airflow · ESP · fan speed · outdoor condition")
        return

    def cell(c):
        it = t.item(0, c)
        return it.text() if it else ""
    d = {"catalog": cell(1), "model": cell(2), "cap": cell(4), "sens": cell(5),
         "eer": cell(7), "flow": cell(8), "sound": cell(9)}
    req = {
        "total": _num(app.btu.text()) * (1 + app.capm.value() / 100),
        "sens": _num(app.sens.text()) * (1 + app.safm.value() / 100),
        "cfm": _num(app.cfm.text()) * (1 + app.afm.value() / 100),
        "esp": _num(app.esp.text()),
    }
    card.show_row(d, req)


def _fill_energy_table(app, rows, hours, currency):
    t = app.energy_table
    t.setSortingEnabled(False)
    headers = ["System", "Model", "Capacity (Btu/hr)", "EER", "kW", "Hours",
               "Annual kWh", f"Annual cost ({currency})"]
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setRowCount(len(rows))

    class _N(QTableWidgetItem):          # numeric-aware sorting
        def __lt__(self, o):
            try:
                return float(self.data(Qt.UserRole)) < float(o.data(Qt.UserRole))
            except (TypeError, ValueError):
                return super().__lt__(o)

    for r, (it, kw, kwh, annual) in enumerate(rows):
        vals = [(it["system"], None), (it["model"], None),
                (f"{float(it.get('rated_capacity', 0) or 0):,.0f}", it.get("rated_capacity", 0)),
                (f"{it.get('eer', 0) or 0:.2f}", it.get("eer", 0)),
                (f"{kw:.2f}", kw), (f"{hours:,}", hours),
                (f"{kwh:,.0f}", kwh), (f"{annual:,.0f}", annual)]
        for c, (txt, key) in enumerate(vals):
            cell = _N(str(txt))
            cell.setTextAlignment(Qt.AlignCenter)
            if key is not None:
                cell.setData(Qt.UserRole, float(key or 0))
            t.setItem(r, c, cell)
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True)
