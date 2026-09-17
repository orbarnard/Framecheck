"""Off-thread folder scanning.

Scanning is bounded and cancellable: a user who drops a network root onto the
window should get a partial answer and a warning, not a frozen application.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ..models.media_file import SUPPORTED_EXTENSIONS

log = logging.getLogger(__name__)

# Guard rails. A creative folder has tens of files; anything past these limits
# is a user pointing at the wrong directory.
MAX_FILES = 2000
MAX_DIRECTORIES = 500


class FolderScanSignals(QObject):
    finished = Signal(Path, list, bool)  # folder, list[Path], hit_limit
    failed = Signal(Path, str)


class FolderScanWorker(QRunnable):
    def __init__(self, folder: Path, recursive: bool = False) -> None:
        super().__init__()
        self.folder = Path(folder)
        self.recursive = recursive
        self.signals = FolderScanSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            files, hit_limit = self._scan()
        except OSError as exc:
            log.warning("folder scan failed for %s: %s", self.folder, exc)
            self.signals.failed.emit(self.folder, f"Could not read folder: {exc}")
            return
        if self._cancelled:
            return
        # Case-insensitive sort matches how Explorer presents the same folder.
        files.sort(key=lambda p: p.name.lower())
        log.info("scanned %s: %d media files (recursive=%s)", self.folder, len(files), self.recursive)
        self.signals.finished.emit(self.folder, files, hit_limit)

    def _scan(self) -> tuple[list[Path], bool]:
        found: list[Path] = []
        directories_seen = 0
        stack: list[Path] = [self.folder]

        while stack:
            if self._cancelled:
                return found, False
            current = stack.pop()
            directories_seen += 1
            if directories_seen > MAX_DIRECTORIES:
                return found, True

            try:
                entries = list(os.scandir(current))
            except OSError as exc:
                if current == self.folder:
                    raise
                log.debug("skipping unreadable directory %s: %s", current, exc)
                continue

            for entry in entries:
                if self._cancelled:
                    return found, False
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if self.recursive and not entry.name.startswith("."):
                            stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                            found.append(path)
                            if len(found) >= MAX_FILES:
                                return found, True
                except OSError:
                    continue

        return found, False
