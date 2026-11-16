import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_duct_design_tab_calculates_and_renders_default_segment(qapp):
    from duct_design.qt_page import DuctDesignTab

    page = DuctDesignTab()
    assert page.segment_table.rowCount() == 1
    assert page.calculate() is True
    assert page.results_table.rowCount() == 1
    assert page.results_table.item(0, 0).text() == "S1"
    assert "independent segment" in page.status_label.text()
    page.max_velocity.setValue(page.max_velocity.value() - 100)
    assert page.results_table.rowCount() == 0
    assert "Inputs changed" in page.status_label.text()


def test_unexpected_engine_failure_is_reported_without_crashing(qapp, monkeypatch):
    import duct_design.qt_page as qt_page

    page = qt_page.DuctDesignTab()

    def fail_calculation(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(qt_page, "run_design", fail_calculation)
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *args: QMessageBox.Ok)
    )
    assert page.calculate() is False
    assert "failed unexpectedly" in page.status_label.text()
    assert page.results_table.rowCount() == 0


def test_main_window_registers_duct_design_page(qapp):
    import main
    import ui_shell

    window = main.App()
    assert ui_shell.P_DUCT == 6
    assert window.stack.count() == len(ui_shell.PAGES) == 9
    assert window.stack.widget(ui_shell.P_DUCT) is window.duct_design_page
    assert ui_shell.PAGES[ui_shell.P_DUCT] == "Duct Design"
