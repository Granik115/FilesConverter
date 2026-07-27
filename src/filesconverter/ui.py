"""PySide6 user interface."""

from __future__ import annotations

import sys
import time
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


def filename_for_format(filename: str, output_format: str) -> str:
    """Keep the entered base name and enforce only a supported output extension."""

    clean_name = Path(filename.strip() or "Объединённая-книга").name
    path = Path(clean_name)
    if path.suffix.lower() in {".txt", ".fb2"}:
        clean_name = path.stem
    return f"{clean_name}.{output_format.lower()}"


def format_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} мс"
    return f"{seconds:.1f} с"


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

        options_group = QGroupBox("Итоговый файл")
        form = QFormLayout(options_group)
        self.output_format = QComboBox()
        self.output_format.addItems(["FB2", "TXT"])
        form.addRow("Формат результата:", self.output_format)

        saved_output = Path(
            str(
                settings.value(
                    "merge/output",
                    Path.home() / "Documents" / "Объединённая-книга.txt",
                )
            )
        )
        directory_row = QWidget()
        directory_layout = QHBoxLayout(directory_row)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        self.output_directory = QLineEdit(str(saved_output.parent))
        self.output_directory.setPlaceholderText("Папка для итогового файла")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose_output)
        directory_layout.addWidget(self.output_directory, 1)
        directory_layout.addWidget(browse)
        form.addRow("Сохранить в папку:", directory_row)

        self.file_name = QLineEdit(saved_output.name)
        self.file_name.setPlaceholderText("Например: Барьер-Ориона-все.txt")
        form.addRow("Имя итогового файла:", self.file_name)

        self.book_title_label = QLabel("Название книги:")
        self.book_title = QLineEdit(str(settings.value("merge/title", "")))
        self.book_title.setPlaceholderText("Если пусто — используется имя файла")
        form.addRow(self.book_title_label, self.book_title)
        self.author_label = QLabel("Автор:")
        self.author = QLineEdit(str(settings.value("merge/author", "")))
        self.author.setPlaceholderText("Необязательно")
        form.addRow(self.author_label, self.author)
        self.source_titles = QCheckBox("Добавлять заголовок перед каждым исходным файлом")
        self.source_titles.setChecked(
            str(settings.value("merge/source_titles", "true")).lower() != "false"
        )
        form.addRow("", self.source_titles)
        layout.addWidget(options_group)

        actions = QHBoxLayout()
        self.format_note = QLabel()
        self.format_note.setObjectName("hint")
        actions.addWidget(self.format_note)
        actions.addStretch(1)
        self.start = QPushButton("Объединить")
        self.start.setObjectName("primaryButton")
        self.start.clicked.connect(self._request)
        actions.addWidget(self.start)
        layout.addLayout(actions)

        self.output_format.currentTextChanged.connect(self._format_changed)
        saved_format = str(settings.value("merge/format", "TXT")).upper()
        self.output_format.setCurrentText(saved_format if saved_format in {"TXT", "FB2"} else "TXT")
        self._format_changed(self.output_format.currentText())

    def _format_changed(self, value: str) -> None:
        output_format = value.lower()
        self.file_name.setText(filename_for_format(self.file_name.text(), output_format))
        is_fb2 = value == "FB2"
        self.book_title_label.setVisible(is_fb2)
        self.book_title.setVisible(is_fb2)
        self.author_label.setVisible(is_fb2)
        self.author.setVisible(is_fb2)
        self.format_note.setText(
            "FB2 будет создан заново из нормализованного текста."
            if is_fb2
            else "TXT-файлы объединяются напрямую; обычно это занимает меньше секунды."
        )

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Папка для итогового файла",
            self.output_directory.text(),
        )
        if folder:
            self.output_directory.setText(folder)

    def _request(self) -> None:
        paths = self.files.list.paths()
        if not paths:
            QMessageBox.information(self, "Нет файлов", "Добавьте хотя бы один TXT или FB2.")
            return
        directory = self.output_directory.text().strip()
        if not directory:
            QMessageBox.information(self, "Нет папки", "Выберите папку для итогового файла.")
            return
        output_format = self.output_format.currentText().lower()
        file_name = filename_for_format(self.file_name.text(), output_format)
        self.file_name.setText(file_name)
        output = str(Path(directory) / file_name)
        metadata = BookMetadata(
            title=self.book_title.text() if output_format == "fb2" else "",
            author=self.author.text() if output_format == "fb2" else "",
        )
        self.settings.setValue("merge/output", output)
        self.settings.setValue("merge/format", output_format.upper())
        self.settings.setValue("merge/title", self.book_title.text())
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


class WorkerCallbacks(QObject):
    """Deliver worker results to callbacks owned by the GUI thread."""

    handled = Signal()

    def __init__(
        self,
        completed: Callable[[object], None],
        failed: Callable[[str], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._completed = completed
        self._failed = failed

    @Slot(object)
    def handle_completed(self, value: object) -> None:
        try:
            self._completed(value)
        finally:
            self.handled.emit()

    @Slot(str)
    def handle_failed(self, message: str) -> None:
        try:
            self._failed(message)
        finally:
            self.handled.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self._callbacks: list[WorkerCallbacks] = []
        self._message_boxes: list[QMessageBox] = []
        self._busy = False
        self._busy_started_at = 0.0
        self._busy_message = ""
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(250)
        self._elapsed_timer.timeout.connect(self._update_elapsed_status)
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
            self._busy_started_at = time.perf_counter()
            self._busy_message = message or "Выполняется…"
            self.progress.setRange(0, 0)
            self._elapsed_timer.start()
        else:
            self._elapsed_timer.stop()
        self.status_label.setText(message or ("Выполняется…" if busy else "Готово"))

    def _elapsed_seconds(self) -> float:
        if not self._busy_started_at:
            return 0.0
        return time.perf_counter() - self._busy_started_at

    def _update_elapsed_status(self) -> None:
        if self._busy:
            self.status_label.setText(
                f"{self._busy_message} · {format_elapsed(self._elapsed_seconds())}"
            )

    def _run_worker(
        self,
        worker: QObject,
        *,
        completed: Callable[[object], None],
        failed: Callable[[str], None],
    ) -> None:
        thread = QThread(self)
        callbacks = WorkerCallbacks(completed, failed, self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(
            callbacks.handle_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.failed.connect(
            callbacks.handle_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._threads.append(thread)
        self._workers.append(worker)
        self._callbacks.append(callbacks)

        state = {"thread_finished": False, "callback_finished": False}

        def release_if_finished() -> None:
            if not all(state.values()):
                return
            if thread in self._threads:
                self._threads.remove(thread)
            if worker in self._workers:
                self._workers.remove(worker)
            if callbacks in self._callbacks:
                self._callbacks.remove(callbacks)
            callbacks.deleteLater()
            thread.deleteLater()

        def thread_finished() -> None:
            state["thread_finished"] = True
            release_if_finished()

        def callback_finished() -> None:
            state["callback_finished"] = True
            release_if_finished()

        thread.finished.connect(thread_finished)
        callbacks.handled.connect(callback_finished)
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
        message = f"{message} · за {format_elapsed(self._elapsed_seconds())}"
        self._set_busy(False, message)
        box = QMessageBox(self)
        box.setWindowTitle("Готово")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(message)
        open_button = box.addButton("Открыть папку", QMessageBox.ButtonRole.ActionRole)
        ok_button = box.addButton(QMessageBox.StandardButton.Ok)
        box.setDefaultButton(ok_button)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        self._message_boxes.append(box)

        def finished(_result: int) -> None:
            if box.clickedButton() is open_button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            if box in self._message_boxes:
                self._message_boxes.remove(box)
            box.deleteLater()

        box.finished.connect(finished)
        box.open()

    def _operation_failed(self, message: str) -> None:
        self._set_busy(False, f"Ошибка · через {format_elapsed(self._elapsed_seconds())}")
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
