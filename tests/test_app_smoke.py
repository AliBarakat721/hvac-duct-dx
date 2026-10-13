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


# ── Equipment page features ───────────────────────────────────────────
def _searched_window(qapp):
    import main
    window = main.App()
    window.show()
    _load_g01(window)
    for _ in range(100):
        if window.table.rowCount():
            break
        QTest.qWait(100)
    assert window.table.rowCount() > 0
    return window


@pytest.mark.skipif(not (ROOT / "G01.RTF").exists(), reason="G01.RTF missing")
def test_results_sort_by_eer_and_reset(qapp):
    window = _searched_window(qapp)
    default = [window.table.item(r, 2).text()
               for r in range(window.table.rowCount())]
    window._on_result_header_clicked(7)  # EER, high to low
    eer = [window.table.item(r, 7).text() for r in range(window.table.rowCount())]
    values = [float(v) for v in eer if v != "—"]
    assert values == sorted(values, reverse=True)
    assert all(v == "—" for v in eer[len(values):])  # missing values last
    window._on_result_header_clicked(7)
    window._on_result_header_clicked(7)  # third click restores best-first
    assert [window.table.item(r, 2).text()
            for r in range(window.table.rowCount())] == default


@pytest.mark.skipif(not (ROOT / "G01.RTF").exists(), reason="G01.RTF missing")
def test_stale_banner_follows_inputs(qapp):
    window = _searched_window(qapp)
    assert window.stale_banner.isHidden()
    original = window.btu.text()
    window.btu.setText("99999")
    assert not window.stale_banner.isHidden()
    window.btu.setText(original)
    assert window.stale_banner.isHidden()
    window.cfm.setText("1234")
    assert not window.stale_banner.isHidden()
    window.search()
    assert window.stale_banner.isHidden()


@pytest.mark.skipif(not (ROOT / "G01.RTF").exists(), reason="G01.RTF missing")
def test_results_context_menu_and_copy(qapp):
    import ui_shell
    window = _searched_window(qapp)
    table = window.table
    pos = table.visualRect(table.model().index(2, 2)).center()
    menu = ui_shell._build_table_menu(window, table, pos)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert any("Add to cart" in t for t in labels)
    assert window.selected_row == 2  # right-click selected the row
    next(a for a in menu.actions() if a.text() == "Copy row").trigger()
    copied = QApplication.clipboard().text()
    assert table.item(2, 2).text() in copied
    assert len(copied.split("\t")) == table.columnCount()


def test_drop_filters_and_window_accepts_drops(qapp):
    from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent
    import main
    window = main.App()
    assert window.acceptDrops()
    assert not window.debug.acceptDrops()  # text box must not swallow drops

    def accepted(path):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = QDragEnterEvent(
            QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.dragEnterEvent(event)
        return event.isAccepted()

    assert accepted(ROOT / "G01.RTF")
    assert accepted(ROOT / "project.ductdx")
    assert not accepted(ROOT / "requirements.txt")
