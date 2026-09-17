"""Off-thread ffprobe.

Results carry the path they describe plus a generation token, so a slow probe
for a file the user has already navigated away from can be discarded instead of
overwriting the current inspection panel.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ..media.probe import ProbeError, probe
from ..models.media_info import MediaInfo

log = logging.getLogger(__name__)


class ProbeSignals(QObject):
    finished = Signal(Path, object, int)  # path, MediaInfo, generation
    failed = Signal(Path, str, int)  # path, message, generation


class ProbeWorker(QRunnable):
    """Probes one file on the shared thread pool."""

    def __init__(self, path: Path, generation: int = 0) -> None:
        super().__init__()
        self.path = Path(path)
        self.generation = generation
        self.signals = ProbeSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            info: MediaInfo = probe(self.path)
        except ProbeError as exc:
            log.warning("probe failed for %s: %s", self.path, exc)
            self.signals.failed.emit(self.path, str(exc), self.generation)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected probe error for %s", self.path)
            self.signals.failed.emit(self.path, f"Unexpected error: {exc}", self.generation)
        else:
            self.signals.finished.emit(self.path, info, self.generation)
