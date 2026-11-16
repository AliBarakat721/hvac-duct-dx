"""Optional PySide6 adapter for the pure-Python CAD pipeline."""
from __future__ import annotations

from pathlib import Path

from .io import geometry_diagnostics, import_dxf

try:
    from PySide6.QtWidgets import QFileDialog, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover
    QWidget = object  # type: ignore[misc, assignment]


class CadIntegrationPage(QWidget):
    """Small hostable page for import, layer inspection, and validation summary."""

    def __init__(self, parent=None):
        if QWidget is object:
            raise RuntimeError("CAD GUI requires PySide6")
        super().__init__(parent)
        self._cad = None
        self.status = QLabel("No DXF loaded")
        self.layers = QListWidget()
        button = QPushButton("Import DXF")
        button.clicked.connect(self.import_file)
        layout = QVBoxLayout(self)
        layout.addWidget(button)
        layout.addWidget(self.status)
        layout.addWidget(self.layers)

    def import_file(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import DXF", "", "DXF Files (*.dxf)")
        if not path:
            return
        try:
            self._cad = import_dxf(Path(path))
            diagnostics = geometry_diagnostics(self._cad)
            self.layers.clear()
            self.layers.addItems(list(self._cad.layers))
            errors = sum(item.severity == "ERROR" for item in diagnostics)
            self.status.setText(f"DXF {self._cad.version}: {len(self._cad.entities)} entities; {errors} geometry errors")
        except (OSError, ValueError, RuntimeError) as exc:
            self._cad = None
            self.status.setText(f"Import failed: {exc}")

    @property
    def cad_import(self):
        return self._cad
