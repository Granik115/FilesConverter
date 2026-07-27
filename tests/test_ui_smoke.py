import ctypes
import os
import sys
from pathlib import Path

import pytest

if sys.platform.startswith("linux"):
    try:
        ctypes.CDLL("libEGL.so.1")
    except OSError:
        pytest.skip("The Linux validation container has no libEGL", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTimer
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
    callback_on_gui_thread = []
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(
        lambda: (
            loop.quit()
            if result
            and not window._threads
            and not window._workers
            and not window._callbacks
            else None
        )
    )
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)

    def completed(value: object) -> None:
        result.append(value)
        callback_on_gui_thread.append(QThread.currentThread() == window.thread())

    window._run_worker(
        OperationWorker(lambda: "готово"),
        completed=completed,
        failed=errors.append,
    )
    poll.start()
    timeout.start(5_000)
    loop.exec()

    assert result == ["готово"]
    assert errors == []
    assert callback_on_gui_thread == [True]
    assert window._threads == []
    assert window._workers == []
    assert window._callbacks == []
    window.close()
    app.processEvents()


def test_completion_dialog_opens_without_blocking() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._set_busy(True, "Проверка…")

    window._operation_done("Готово: test.fb2", Path.cwd())

    assert not window._busy
    assert len(window._message_boxes) == 1
    box = window._message_boxes[0]
    assert "Готово: test.fb2" in box.text()
    box.accept()
    app.processEvents()
    assert window._message_boxes == []
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
