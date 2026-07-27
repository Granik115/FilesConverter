"""GitHub Release update helpers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from filesconverter import __version__

GITHUB_REPOSITORY = "Granik115/FilesConverter"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    page_url: str
    notes: str
    installer_url: str
    installer_name: str
    installer_digest: str = ""


def version_tuple(value: str) -> tuple[int, ...]:
    clean = value.strip().lower().lstrip("v")
    pieces: list[int] = []
    for piece in clean.split("."):
        number = ""
        for character in piece:
            if character.isdigit():
                number += character
            else:
                break
        pieces.append(int(number or 0))
    return tuple(pieces)


def fetch_latest_release(timeout: float = 15) -> ReleaseInfo:
    request = urllib.request.Request(
        API_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"FilesConverter-Updater/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось проверить обновления: {exc}") from exc

    installer = next(
        (
            asset
            for asset in payload.get("assets", [])
            if str(asset.get("name", "")).lower().endswith("-setup.exe")
        ),
        None,
    )
    return ReleaseInfo(
        version=str(payload.get("tag_name", "")).lstrip("v"),
        page_url=str(payload.get("html_url", "")),
        notes=str(payload.get("body") or ""),
        installer_url=str(installer.get("browser_download_url", "")) if installer else "",
        installer_name=str(installer.get("name", "")) if installer else "",
        installer_digest=str(installer.get("digest", "")) if installer else "",
    )


def has_update(release: ReleaseInfo) -> bool:
    return version_tuple(release.version) > version_tuple(__version__)


def download_installer(release: ReleaseInfo, timeout: float = 60) -> Path:
    if not release.installer_url:
        raise RuntimeError("В релизе нет EXE-установщика.")
    target = Path(tempfile.gettempdir()) / (
        release.installer_name or f"FilesConverter-{release.version}-setup.exe"
    )
    request = urllib.request.Request(
        release.installer_url,
        headers={"User-Agent": f"FilesConverter-Updater/{__version__}"},
    )
    temporary = target.with_suffix(".download")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if temporary.stat().st_size < 100_000:
            raise RuntimeError("Скачанный установщик имеет некорректный размер.")
        if release.installer_digest.startswith("sha256:"):
            expected = release.installer_digest.partition(":")[2].lower()
            sha256 = hashlib.sha256()
            with temporary.open("rb") as downloaded:
                while chunk := downloaded.read(1024 * 1024):
                    sha256.update(chunk)
            if sha256.hexdigest().lower() != expected:
                raise RuntimeError("Контрольная сумма обновления не совпала.")
        os.replace(temporary, target)
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Не удалось скачать обновление: {exc}") from exc
    return target


def launch_installer(installer: Path) -> None:
    subprocess.Popen(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ],
        close_fds=True,
    )


class UpdateCheckWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(fetch_latest_release())
        except Exception as exc:  # noqa: BLE001 - forwarded to the UI
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class DownloadWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, release: ReleaseInfo) -> None:
        super().__init__()
        self.release = release

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(download_installer(self.release))
        except Exception as exc:  # noqa: BLE001 - forwarded to the UI
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
