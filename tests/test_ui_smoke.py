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

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from filesconverter.ui import MainWindow, OperationWorker, filename_for_format


def test_main_window_can_be_created() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle().startswith("FilesConverter")
    assert window.conversion.start.text() == "Конвертировать"
    assert window.merge.start.text() == "Объединить"
    window.close()
    app.processEvents()


def test_background_worker_finishes_and_releases_references() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    result = []
    errors = []
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(
        lambda: loop.quit() if result and not window._threads and not window._workers else None
    )
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)

    window._run_worker(
        OperationWorker(lambda: "готово"),
        completed=result.append,
        failed=errors.append,
    )
    poll.start()
    timeout.start(5_000)
    loop.exec()

    assert result == ["готово"]
    assert errors == []
    assert window._threads == []
    assert window._workers == []
    window.close()
    app.processEvents()


def test_merge_fields_follow_selected_format() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window.merge.output_format.setCurrentText("TXT")
    assert window.merge.book_title.isHidden()
    assert window.merge.author.isHidden()
    assert filename_for_format("Моя.книга.fb2", "txt") == "Моя.книга.txt"

    window.merge.output_format.setCurrentText("FB2")
    assert not window.merge.book_title.isHidden()
    assert not window.merge.author.isHidden()
    assert window.merge.file_name.text().endswith(".fb2")
    window.close()
    app.processEvents()
