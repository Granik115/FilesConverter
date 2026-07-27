import ctypes
import os
import sys

import pytest

if sys.platform.startswith("linux"):
    try:
        ctypes.CDLL("libEGL.so.1")
    except OSError:
        pytest.skip("The Linux validation container has no libEGL", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from filesconverter.ui import MainWindow


def test_main_window_can_be_created() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle().startswith("FilesConverter")
    assert window.conversion.start.text() == "Конвертировать"
    assert window.merge.start.text() == "Объединить"
    window.close()
    app.processEvents()
