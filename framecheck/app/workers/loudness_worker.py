"""Off-thread loudness analysis.

The analysis pass decodes the whole (trimmed) audio, so it must never run on the
UI thread. Results carry the path plus a generation token so a measurement for a
file the user has navigated away from is discarded rather than applied to
whatever is selected now.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ..media.loudness import LoudnessError, analyze_loudness
from ..models.export_job import LoudnessResult
from ..models.profile import AudioTarget
from ..models.trim import TrimRange

log = logging.getLogger(__name__)


class LoudnessSignals(QObject):
    finished = Signal(Path, object, int)  # path, LoudnessResult, generation
    failed = Signal(Path, str, int)  # path, message, generation


class LoudnessWorker(QRunnable):
    """Measures one file's loudness on the shared thread pool."""

    def __init__(
        self,
        path: Path,
        trim: TrimRange | None = None,
        target: AudioTarget | None = None,
        generation: int = 0,
        max_seconds: float | None = None,
        source_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.trim = trim
        self.target = target
        self.generation = generation
        self.max_seconds = max_seconds
        self.source_seconds = source_seconds
        self.signals = LoudnessSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            result: LoudnessResult = analyze_loudness(
                self.path,
                self.trim,
                target=self.target,
                max_seconds=self.max_seconds,
                source_seconds=self.source_seconds,
            )
        except LoudnessError as exc:
            log.warning("loudness analysis failed for %s: %s", self.path, exc)
            self.signals.failed.emit(self.path, str(exc), self.generation)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected loudness error for %s", self.path)
            self.signals.failed.emit(self.path, f"Unexpected error: {exc}", self.generation)
        else:
            self.signals.finished.emit(self.path, result, self.generation)
