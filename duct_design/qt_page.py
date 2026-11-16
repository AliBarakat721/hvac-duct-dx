"""Qt tab for preliminary rectangular duct sizing."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .engine import (
    DuctDesignCalculationError,
    DuctDesignOptions,
    DuctSegmentInput,
    run_design,
)

logger = logging.getLogger(__name__)


class DuctDesignTab(QWidget):
    """Main-window page for independent constant-flow rectangular duct sections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(12)

        title = QLabel("Rectangular Duct Design")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel(
            "Preliminary sizing for independent constant-flow sections using an "
            "equal-friction round equivalent and Darcy–Weisbach losses."
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        notice = QLabel(
            "Engineering boundary: this tool does not solve a connected duct network, "
            "balance terminals, select fittings, determine fan static pressure, or verify "
            "SMACNA construction compliance. Enter a justified total K for each segment. "
            "Final dimensions and pressure losses require engineer review."
        )
        notice.setObjectName("warn")
        notice.setWordWrap(True)
        notice.setFrameShape(QFrame.StyledPanel)
        notice.setContentsMargins(10, 8, 10, 8)
        root.addWidget(notice)

        content = QHBoxLayout()
        content.setSpacing(14)
        root.addLayout(content, 1)

        left = QVBoxLayout()
        left.setSpacing(10)
        content.addLayout(left, 3)

        segment_group = QGroupBox("Duct segments — one row per independent section")
        segment_layout = QVBoxLayout(segment_group)
        self.segment_table = QTableWidget(0, 4)
        self.segment_table.setObjectName("ductSegmentTable")
        self.segment_table.setHorizontalHeaderLabels(
            ["Tag", "Airflow (CFM)", "Length (ft)", "ΣK"]
        )
        self.segment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, 4):
            self.segment_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.Stretch
            )
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.setMinimumHeight(140)
        segment_layout.addWidget(self.segment_table)
        row_actions = QHBoxLayout()
        self.add_segment_button = QPushButton("Add segment")
        self.add_segment_button.setObjectName("secondary")
        self.add_segment_button.clicked.connect(self._add_segment_row)
        self.remove_segment_button = QPushButton("Remove selected")
        self.remove_segment_button.setObjectName("secondary")
        self.remove_segment_button.clicked.connect(self._remove_selected_rows)
        row_actions.addWidget(self.add_segment_button)
        row_actions.addWidget(self.remove_segment_button)
        row_actions.addStretch(1)
        segment_layout.addLayout(row_actions)
        left.addWidget(segment_group)
        self._add_segment_row(tag="S1", airflow=1000.0, length=50.0, fitting_k=0.0)

        criteria_group = QGroupBox("Sizing criteria and air properties")
        criteria = QGridLayout(criteria_group)
        criteria.setHorizontalSpacing(12)
        criteria.setVerticalSpacing(7)
        self.max_velocity = self._number(100, 8000, 0, 1500, " fpm")
        self.max_friction = self._number(0.001, 10, 3, 0.08, " in.w.g./100 ft")
        self.min_height = self._number(2, 100, 1, 8, " in")
        self.max_height = self._number(2, 100, 1, 24, " in")
        self.min_width = self._number(2, 200, 1, 8, " in")
        self.max_width = self._number(2, 200, 1, 48, " in")
        self.size_step = self._number(0.25, 12, 2, 1, " in")
        self.max_aspect = self._number(1, 20, 2, 4, " : 1")
        self.temperature = self._number(10, 60, 1, 20, " °C")
        self.pressure = self._number(50, 120, 3, 101.325, " kPa abs")
        self.roughness = self._number(0, 5, 3, 0.09, " mm")
        fields = [
            ("Maximum velocity", self.max_velocity),
            ("Maximum friction rate", self.max_friction),
            ("Minimum duct height", self.min_height),
            ("Maximum duct height", self.max_height),
            ("Minimum duct width", self.min_width),
            ("Maximum duct width", self.max_width),
            ("Size grid increment", self.size_step),
            ("Maximum aspect ratio", self.max_aspect),
            ("Dry-bulb air temperature", self.temperature),
            ("Absolute air pressure", self.pressure),
            ("Absolute roughness", self.roughness),
        ]
        for index, (label, widget) in enumerate(fields):
            row, pair = divmod(index, 2)
            criteria.addWidget(QLabel(label), row, pair * 2)
            criteria.addWidget(widget, row, pair * 2 + 1)
        self.max_velocity.setToolTip("Screening limit on actual mean velocity in the rectangular duct.")
        self.max_friction.setToolTip("Straight-duct friction criterion; fitting losses are reported separately.")
        self.max_aspect.setToolTip("Project screening cap; not a universal code limit.")
        self.roughness.setToolTip("SMACNA clean galvanized reference is 0.09 mm; override only with a documented basis.")
        left.addWidget(criteria_group)

        actions = QHBoxLayout()
        self.calculate_button = QPushButton("Calculate duct sizes")
        self.calculate_button.setObjectName("success")
        self.calculate_button.clicked.connect(self.calculate)
        actions.addWidget(self.calculate_button)
        self.status_label = QLabel("Ready. Results are preliminary engineering estimates.")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        actions.addWidget(self.status_label, 1)
        left.addLayout(actions)

        results_group = QGroupBox("Calculated results")
        results_layout = QVBoxLayout(results_group)
        self.results_table = QTableWidget(0, 11)
        self.results_table.setObjectName("ductResultsTable")
        self.results_table.setHorizontalHeaderLabels([
            "Tag", "W × H (in)", "Velocity (fpm)", "Dₑq (in)", "Re", "Darcy f",
            "Friction (in.w.g./100 ft)", "Straight loss (in.w.g.)",
            "K loss (in.w.g.)", "Total modelled loss (in.w.g.)", "Status",
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        results_layout.addWidget(self.results_table)
        root.addWidget(results_group, 1)
        self.segment_table.itemChanged.connect(self._mark_stale)
        for widget in (
            self.max_velocity, self.max_friction, self.min_height, self.max_height,
            self.min_width, self.max_width, self.size_step, self.max_aspect,
            self.temperature, self.pressure, self.roughness,
        ):
            widget.valueChanged.connect(self._mark_stale)

    @staticmethod
    def _number(minimum: float, maximum: float, decimals: int, value: float, suffix: str) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        widget.setMinimumWidth(125)
        return widget

    def _add_segment_row(
        self,
        *,
        tag: str | None = None,
        airflow: float = 500.0,
        length: float = 25.0,
        fitting_k: float = 0.0,
    ) -> None:
        row = self.segment_table.rowCount()
        self.segment_table.insertRow(row)
        values = [tag or f"S{row + 1}", f"{airflow:g}", f"{length:g}", f"{fitting_k:g}"]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column > 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.segment_table.setItem(row, column, item)
        if hasattr(self, "status_label"):
            self._mark_stale()

    def _remove_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.segment_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.segment_table.removeRow(row)
        if rows:
            self._mark_stale()

    def _mark_stale(self, *_args) -> None:
        if self.results_table.rowCount():
            self.results_table.setRowCount(0)
            self.status_label.setText(
                "Inputs changed; prior results were cleared. Calculate again."
            )

    def _segments_from_table(self) -> tuple[DuctSegmentInput, ...]:
        segments = []
        for row in range(self.segment_table.rowCount()):
            values = [self.segment_table.item(row, column) for column in range(4)]
            if any(item is None for item in values):
                raise DuctDesignCalculationError(f"Segment row {row + 1} has a blank cell.")
            try:
                tag = values[0].text().strip() or f"Segment {row + 1}"
                segments.append(DuctSegmentInput(
                    tag=tag,
                    airflow_cfm=float(values[1].text().replace(",", "")),
                    length_ft=float(values[2].text().replace(",", "")),
                    fitting_k=float(values[3].text().replace(",", "")),
                ))
            except ValueError as exc:
                raise DuctDesignCalculationError(
                    f"Segment row {row + 1} contains a non-numeric value."
                ) from exc
        return tuple(segments)

    def _options(self) -> DuctDesignOptions:
        return DuctDesignOptions(
            max_velocity_fpm=self.max_velocity.value(),
            max_friction_inwg_100ft=self.max_friction.value(),
            min_height_in=self.min_height.value(),
            max_height_in=self.max_height.value(),
            min_width_in=self.min_width.value(),
            max_width_in=self.max_width.value(),
            size_step_in=self.size_step.value(),
            max_aspect_ratio=self.max_aspect.value(),
            air_temperature_c=self.temperature.value(),
            absolute_pressure_kpa=self.pressure.value(),
            roughness_mm=self.roughness.value(),
        )

    def calculate(self) -> bool:
        """Read the rows, call the pure calculation engine, and render results."""
        try:
            results = run_design(self._segments_from_table(), self._options())
        except DuctDesignCalculationError as exc:
            self.results_table.setRowCount(0)
            self.status_label.setText(f"Calculation not completed: {exc}")
            QMessageBox.warning(self, "Duct design input / sizing error", str(exc))
            return False
        except Exception:  # UI boundary: an engine defect must not close the application.
            logger.exception("Unexpected failure while calculating duct design")
            self.results_table.setRowCount(0)
            self.status_label.setText("Calculation failed unexpectedly; see the application log.")
            QMessageBox.critical(
                self,
                "Duct design calculation failed",
                "An unexpected error prevented the calculation. See the application log.",
            )
            return False
        self.results_table.setRowCount(len(results))
        for row, result in enumerate(results):
            status = "PASS — screening only"
            if result.warnings:
                status = "REVIEW — transitional Re"
            values = [
                result.tag,
                f"{result.width_in:g} × {result.height_in:g}",
                f"{result.velocity_fpm:,.0f}",
                f"{result.equivalent_diameter_in:.2f}",
                f"{result.reynolds_number:,.0f}",
                f"{result.darcy_friction_factor:.5f}",
                f"{result.friction_rate_inwg_100ft:.4f}",
                f"{result.straight_friction_loss_inwg:.4f}",
                f"{result.fitting_loss_inwg:.4f}",
                f"{result.total_modeled_loss_inwg:.4f}",
                status,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.results_table.setItem(row, column, item)
        self.status_label.setText(
            f"Sized {len(results)} independent segment(s). Values include only straight friction "
            "and user-entered ΣK losses; they are not a connected-system pressure calculation."
        )
        return True


def build_duct_design_page(parent: QWidget | None = None) -> DuctDesignTab:
    """Factory used by the main application's UI shell."""
    return DuctDesignTab(parent)
