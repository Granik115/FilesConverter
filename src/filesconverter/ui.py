"""PySide6 user interface."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from filesconverter import __version__
from filesconverter.core import BookMetadata, ConversionError, convert_many, merge_files
from filesconverter.theme import ACCENT_GLOW, TEXT_MUTED
from filesconverter.updater import (
    DownloadWorker,
    ReleaseInfo,
    UpdateCheckWorker,
    has_update,
    launch_installer,
)

PATH_ROLE = int(Qt.ItemDataRole.UserRole)
FILE_FILTER = "Книги (*.txt *.fb2);;TXT (*.txt);;FictionBook 2 (*.fb2)"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


class FileListWidget(QListWidget):
    files_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(230)
        self.setToolTip("Перетащите сюда TXT и FB2. Строки можно менять местами мышью.")
        self.model().rowsMoved.connect(lambda *_: self.files_changed.emit())

    def paths(self) -> list[Path]:
        return [Path(self.item(row).data(PATH_ROLE)) for row in range(self.count())]

    def add_paths(self, paths: list[str | Path]) -> int:
        known = {str(path.resolve()).casefold() for path in self.paths()}
        added = 0
        for raw in paths:
            path = Path(raw)
            if path.suffix.lower() not in {".txt", ".fb2"} or not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved.casefold() in known:
                continue
            item = QListWidgetItem(
                f"{path.name}    ·    {human_size(path.stat().st_size)}    ·    "
                f"{path.suffix.upper().lstrip('.')}"
            )
            item.setData(PATH_ROLE, resolved)
            item.setToolTip(resolved)
            self.addItem(item)
            known.add(resolved.casefold())
            added += 1
        if added:
            self.files_changed.emit()
        return added

    def remove_selected(self) -> None:
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.files_changed.emit()

    def clear_files(self) -> None:
        self.clear()
        self.files_changed.emit()

    def move_selected(self, offset: int) -> None:
        row = self.currentRow()
        if row < 0:
            return
        target = max(0, min(self.count() - 1, row + offset))
        if target == row:
            return
        item = self.takeItem(row)
        self.insertItem(target, item)
        self.setCurrentRow(target)
        self.files_changed.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls() and event.source() is not self:
            self.add_paths(
                [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            )
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class FilePicker(QWidget):
    def __init__(self, *, order_hint: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = FileListWidget()
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        add_button = QPushButton("Добавить файлы")
        add_button.clicked.connect(self._choose_files)
        remove_button = QPushButton("Удалить выбранные")
        remove_button.clicked.connect(self.list.remove_selected)
        clear_button = QPushButton("Очистить")
        clear_button.clicked.connect(self.list.clear_files)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addWidget(clear_button)
        if order_hint:
            buttons.addStretch(1)
            up_button = QPushButton("↑ Выше")
            down_button = QPushButton("↓ Ниже")
            up_button.clicked.connect(lambda: self.list.move_selected(-1))
            down_button.clicked.connect(lambda: self.list.move_selected(1))
            buttons.addWidget(up_button)
            buttons.addWidget(down_button)
        layout.addLayout(buttons)

    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите TXT или FB2", "", FILE_FILTER)
        self.list.add_paths(files)


class ConversionPanel(QWidget):
    requested = Signal(list, str, bool)

    def __init__(self, settings: QSettings) -> None:
        super().__init__()
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Пакетная конвертация")
        title.setObjectName("sectionTitle")
        hint = QLabel(
            "Можно добавить сразу несколько TXT и FB2: каждый файл будет преобразован "
            "в противоположный формат."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        self.files = FilePicker()
        layout.addWidget(self.files, 1)

        output_group = QGroupBox("Куда сохранить")
        output_layout = QHBoxLayout(output_group)
        self.output = QLineEdit(
            str(settings.value("conversion/output", str(Path.home() / "Documents")))
        )
        self.output.setPlaceholderText("Папка для готовых файлов")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(self.output, 1)
        output_layout.addWidget(browse)
        layout.addWidget(output_group)

        options = QHBoxLayout()
        self.overwrite = QCheckBox("Перезаписывать существующие файлы")
        self.overwrite.setToolTip(
            "Если выключено, к совпадающему имени автоматически добавится номер."
        )
        options.addWidget(self.overwrite)
        options.addStretch(1)
        self.start = QPushButton("Конвертировать")
        self.start.setObjectName("primaryButton")
        self.start.clicked.connect(self._request)
        options.addWidget(self.start)
        layout.addLayout(options)

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Папка для результата", self.output.text()
        )
        if folder:
            self.output.setText(folder)

    def _request(self) -> None:
        paths = self.files.list.paths()
        if not paths:
            QMessageBox.information(self, "Нет файлов", "Добавьте хотя бы один TXT или FB2.")
            return
        output = self.output.text().strip()
        if not output:
            QMessageBox.information(self, "Нет папки", "Выберите папку для результата.")
            return
        self.settings.setValue("conversion/output", output)
        self.requested.emit(
            [str(path) for path in paths],
            output,
            self.overwrite.isChecked(),
        )


class MergePanel(QWidget):
    requested = Signal(list, str, str, object, bool)

    def __init__(self, settings: QSettings) -> None:
        super().__init__()
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Объединение файлов")
        title.setObjectName("sectionTitle")
        hint = QLabel(
            "Порядок сверху вниз станет порядком книг в результате. Можно смешивать TXT и FB2."
        )
        hint.setObjectName("hint")
        layout.addWidget(title)
        layout.addWidget(hint)
        self.files = FilePicker(order_hint=True)
        layout.addWidget(self.files, 1)

        options_group = QGroupBox("Параметры результата")
        form = QFormLayout(options_group)
        self.output_format = QComboBox()
        self.output_format.addItems(["FB2", "TXT"])
        self.output_format.currentTextChanged.connect(self._format_changed)
        form.addRow("Формат:", self.output_format)

        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.output = QLineEdit(
            str(settings.value("merge/output", Path.home() / "Documents" / "merged.fb2"))
        )
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(self.output, 1)
        output_layout.addWidget(browse)
        form.addRow("Файл:", output_row)

        self.title = QLineEdit(str(settings.value("merge/title", "Объединённая книга")))
        self.title.setPlaceholderText("Название объединённой книги")
        form.addRow("Название:", self.title)
        self.author = QLineEdit(str(settings.value("merge/author", "")))
        self.author.setPlaceholderText("Необязательно")
        form.addRow("Автор:", self.author)
        self.source_titles = QCheckBox("Добавлять название каждого исходного файла")
        self.source_titles.setChecked(
            str(settings.value("merge/source_titles", "true")).lower() != "false"
        )
        form.addRow("", self.source_titles)
        layout.addWidget(options_group)

        actions = QHBoxLayout()
        note = QLabel("FB2 объединяется через нормализованный текст.")
        note.setObjectName("hint")
        actions.addWidget(note)
        actions.addStretch(1)
        self.start = QPushButton("Объединить")
        self.start.setObjectName("primaryButton")
        self.start.clicked.connect(self._request)
        actions.addWidget(self.start)
        layout.addLayout(actions)

    def _format_changed(self, value: str) -> None:
        extension = f".{value.lower()}"
        current = Path(self.output.text().strip() or "merged")
        self.output.setText(str(current.with_suffix(extension)))
        is_fb2 = value == "FB2"
        self.title.setEnabled(is_fb2)
        self.author.setEnabled(is_fb2)

    def _choose_output(self) -> None:
        extension = self.output_format.currentText().lower()
        selected_filter = (
            "FictionBook 2 (*.fb2)" if extension == "fb2" else "Текстовый файл (*.txt)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Имя объединённого файла",
            self.output.text(),
            selected_filter,
        )
        if path:
            self.output.setText(str(Path(path).with_suffix(f".{extension}")))

    def _request(self) -> None:
        paths = self.files.list.paths()
        if not paths:
            QMessageBox.information(self, "Нет файлов", "Добавьте хотя бы один TXT или FB2.")
            return
        output = self.output.text().strip()
        if not output:
            QMessageBox.information(self, "Нет имени", "Выберите имя итогового файла.")
            return
        output_format = self.output_format.currentText().lower()
        output = str(Path(output).with_suffix(f".{output_format}"))
        metadata = BookMetadata(title=self.title.text(), author=self.author.text())
        self.settings.setValue("merge/output", output)
        self.settings.setValue("merge/title", self.title.text())
        self.settings.setValue("merge/author", self.author.text())
        self.settings.setValue("merge/source_titles", self.source_titles.isChecked())
        self.requested.emit(
            [str(path) for path in paths],
            output,
            output_format,
            metadata,
            self.source_titles.isChecked(),
        )


class OperationWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.operation())
        except (ConversionError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - do not crash the GUI worker
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self._threads: list[QThread] = []
        self._busy = False
        self.setWindowTitle(f"FilesConverter {__version__}")
        self.setMinimumSize(920, 680)
        self.resize(1080, 760)
        self.setAcceptDrops(True)
        self._build_menu()
        self._build_ui()
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        QTimer.singleShot(5000, lambda: self.check_updates(manual=False))

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        help_menu = self.menuBar().addMenu("Справка")
        update_action = QAction("Проверить обновления", self)
        update_action.triggered.connect(lambda: self.check_updates(manual=True))
        about_action = QAction("О программе", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(update_action)
        help_menu.addAction(about_action)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 22)
        app_title = QLabel(
            f"Files<span style='color:{ACCENT_GLOW}'>Converter</span>"
        )
        app_title.setObjectName("appTitle")
        subtitle = QLabel("TXT ⇄ FB2")
        subtitle.setObjectName("appSubtitle")
        side.addWidget(app_title)
        side.addWidget(subtitle)
        side.addSpacing(24)
        description = QLabel(
            "Пакетная конвертация и объединение электронных книг."
        )
        description.setObjectName("hint")
        description.setWordWrap(True)
        side.addWidget(description)
        side.addItem(
            QSpacerItem(10, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )
        update = QPushButton("↻  Обновления")
        update.clicked.connect(lambda: self.check_updates(manual=True))
        side.addWidget(update)
        version = QLabel(f"Версия {__version__}")
        version.setStyleSheet(f"color: {TEXT_MUTED};")
        side.addWidget(version)
        root.addWidget(sidebar)

        self.tabs = QTabWidget()
        self.conversion = ConversionPanel(self.settings)
        self.merge = MergePanel(self.settings)
        self.conversion.requested.connect(self.run_conversion)
        self.merge.requested.connect(self.run_merge)
        self.tabs.addTab(self.conversion, "Конвертация")
        self.tabs.addTab(self.merge, "Объединение")
        root.addWidget(self.tabs, 1)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Готово")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.hide()
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.progress)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.conversion.start.setEnabled(not busy)
        self.merge.start.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        self.status_label.setText(message or ("Выполняется…" if busy else "Готово"))

    def _run_worker(
        self,
        worker: QObject,
        *,
        completed: Callable[[object], None],
        failed: Callable[[str], None],
    ) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda current=thread: self._threads.remove(current)
            if current in self._threads
            else None
        )
        self._threads.append(thread)
        thread.start()

    @Slot(list, str, bool)
    def run_conversion(self, paths: list[str], output: str, overwrite: bool) -> None:
        if self._busy:
            return
        self._set_busy(True, f"Конвертация: {len(paths)} файл(ов)…")
        worker = OperationWorker(
            lambda: convert_many(paths, output, overwrite=overwrite)
        )
        self._run_worker(
            worker,
            completed=lambda result: self._operation_done(
                f"Готово: создано файлов — {len(result)}", Path(output)
            ),
            failed=self._operation_failed,
        )

    @Slot(list, str, str, object, bool)
    def run_merge(
        self,
        paths: list[str],
        output: str,
        output_format: str,
        metadata: BookMetadata,
        source_titles: bool,
    ) -> None:
        if self._busy:
            return
        self._set_busy(True, f"Объединение: {len(paths)} файл(ов)…")
        worker = OperationWorker(
            lambda: merge_files(
                paths,
                output,
                output_format=output_format,
                metadata=metadata,
                add_source_titles=source_titles,
            )
        )
        self._run_worker(
            worker,
            completed=lambda result: self._operation_done(
                f"Готово: {Path(result).name}", Path(result).parent
            ),
            failed=self._operation_failed,
        )

    def _operation_done(self, message: str, folder: Path) -> None:
        self._set_busy(False, message)
        box = QMessageBox(self)
        box.setWindowTitle("Готово")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(message)
        open_button = box.addButton("Открыть папку", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _operation_failed(self, message: str) -> None:
        self._set_busy(False, "Ошибка")
        QMessageBox.critical(self, "Ошибка", message)

    def check_updates(self, *, manual: bool) -> None:
        worker = UpdateCheckWorker()
        if manual:
            self.status_label.setText("Проверка обновлений…")

        def completed(value: object) -> None:
            release = value
            if not isinstance(release, ReleaseInfo):
                return
            self.status_label.setText("Готово")
            if has_update(release):
                self._offer_update(release)
            elif manual:
                QMessageBox.information(
                    self,
                    "Обновления",
                    f"Установлена последняя версия — {__version__}.",
                )

        def failed(message: str) -> None:
            self.status_label.setText("Не удалось проверить обновления")
            if manual:
                QMessageBox.warning(self, "Обновления", message)

        self._run_worker(worker, completed=completed, failed=failed)

    def _offer_update(self, release: ReleaseInfo) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Доступно обновление")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"Доступна версия {release.version}. Установлена версия {__version__}."
        )
        if release.notes:
            box.setDetailedText(release.notes)
        install_button = box.addButton(
            "Скачать и установить", QMessageBox.ButtonRole.AcceptRole
        )
        page_button = box.addButton("Открыть релиз", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is install_button:
            if release.installer_url:
                self._download_update(release)
            else:
                QMessageBox.warning(
                    self, "Обновление", "В релизе пока нет EXE-установщика."
                )
        elif box.clickedButton() is page_button and release.page_url:
            QDesktopServices.openUrl(QUrl(release.page_url))

    def _download_update(self, release: ReleaseInfo) -> None:
        progress = QProgressDialog(
            f"Скачивается FilesConverter {release.version}…",
            "",
            0,
            0,
            self,
        )
        progress.setWindowTitle("Обновление")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        worker = DownloadWorker(release)

        def completed(value: object) -> None:
            progress.close()
            try:
                launch_installer(Path(value))
            except OSError as exc:
                QMessageBox.critical(
                    self, "Обновление", f"Не удалось запустить установщик: {exc}"
                )
                return
            QApplication.quit()

        def failed(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Обновление", message)

        self._run_worker(worker, completed=completed, failed=failed)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "О FilesConverter",
            f"<b>FilesConverter {__version__}</b><br><br>"
            "Конвертер и объединитель TXT/FB2.<br>"
            "Проект: Granik115/FilesConverter",
        )

    def closeEvent(self, event) -> None:
        if self._busy:
            answer = QMessageBox.question(
                self,
                "Операция выполняется",
                "Закрыть программу до завершения операции?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.settings.setValue("window/geometry", self.saveGeometry())
        event.accept()
