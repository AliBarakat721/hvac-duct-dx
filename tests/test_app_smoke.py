"""GUI smoke tests. Skipped automatically when PySide6 is unavailable (CI)."""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))


def _load_g01(window):
    window._start_extraction(str(ROOT / "G01.RTF"), True)
    for _ in range(300):
        QTest.qWait(100)
        if getattr(window, "systems", None):
            break


@pytest.mark.skipif(not (ROOT / "G01.RTF").exists(), reason="G01.RTF missing")
def test_g01_loads_and_search_does_not_raise(qapp):
    import main
    window = main.App()
    _load_g01(window)
    assert window.systems
    window.search()
    assert window.table.rowCount() > 0


def test_open_project_in_fresh_session(qapp, tmp_path):
    import main
    path = tmp_path / "project.ductdx"
    saver = main.App()
    assert saver.save_project(str(path)) is not False
    assert path.exists()
    opener = main.App()  # nothing loaded yet: _loaded_path must exist
    assert opener.open_project(str(path)) is not False
