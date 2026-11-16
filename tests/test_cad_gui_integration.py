import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_cad_page_is_registered_in_real_navigation(qapp):
    import main
    import ui_shell

    window = main.App()
    assert "CAD Import" in ui_shell.PAGES
    assert hasattr(window, "cad_page")
    assert window.stack.count() == len(ui_shell.PAGES)
    ui_shell.go_page(window, ui_shell.P_CAD)
    assert window.stack.currentWidget() is window.cad_page
    assert window.cad_page.layers.count() == 0
