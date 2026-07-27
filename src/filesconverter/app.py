"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from filesconverter.theme import stylesheet
from filesconverter.ui import MainWindow, resource_path


def main() -> int:
    QCoreApplication.setOrganizationName("Granik115")
    QCoreApplication.setApplicationName("FilesConverter")
    QApplication.setApplicationDisplayName("FilesConverter")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    icon = resource_path("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
