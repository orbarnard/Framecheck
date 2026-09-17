"""Sequential batch export.

Encodes run **one at a time**, deliberately. x264 already saturates every core,
so running four files concurrently finishes no sooner, makes progress
meaningless, and turns the machine unusable while it happens. Sequential also
means a cancel stops the whole batch immediately rather than leaving three
half-written files behind.

Each item reuses the ordinary single-file TranscodeWorker, so batch exports get
the same frame-accurate trim handling and the same post-export re-probe and
re-validation as a single export. There is no second, lesser code path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from ..media.conform import build_job
from ..models.batch import BatchItemState, BatchPlan
from ..models.export_job import ExportResult, ExportState
from ..utils.paths import OutputDestination
from .transcode_worker import TranscodeWorker

log = logging.getLogger(__name__)


class BatchRunner(QObject):
    """Drives a BatchPlan to completion, one export at a time."""

    item_started = Signal(str)  # MediaFile.key
    item_progress = Signal(str, float)
    item_finished = Signal(str)
    finished = Signal()

    def __init__(self, pool: QThreadPool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._plan: BatchPlan | None = None
        self._destination = OutputDestination.same_as_source()
        self._queue: list[str] = []
        self._current: TranscodeWorker | None = None
        self._current_key: str | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._current is not None

    @property
    def plan(self) -> BatchPlan | None:
        return self._plan

    def start(self, plan: BatchPlan, destination: OutputDestination) -> None:
        if self.running:
            return
        self._plan = plan
        self._destination = destination
        self._cancelled = False
        # Only items with usable probe data can be encoded; the rest are
        # reported as skipped rather than silently dropped.
        self._queue = []
        for item in plan.items:
            if item.media_file.info is None:
                item.state = BatchItemState.SKIPPED
                item.error = "Not inspected — could not be read"
                self.item_finished.emit(item.media_file.key)
            else:
                item.state = BatchItemState.PENDING
                self._queue.append(item.media_file.key)

        log.info(
            "batch start: %d file(s) -> %s",
            len(self._queue),
            plan.profile.id if plan.profile else "no profile",
        )
        self._advance()

    def cancel(self) -> None:
        """Stop after cancelling the running encode; skip everything queued."""
        self._cancelled = True
        if self._current is not None:
            self._current.cancel()
        if self._plan is not None:
            for key in self._queue:
                item = self._plan.item_for(key)
                if item is not None:
                    item.state = BatchItemState.CANCELLED
                    self.item_finished.emit(key)
        self._queue.clear()
        log.info("batch cancelled")

    # -- internals --------------------------------------------------------

    def _advance(self) -> None:
        if self._plan is None:
            return
        if self._cancelled or not self._queue:
            self._current = None
            self._current_key = None
            self.finished.emit()
            log.info(
                "batch finished: %d done, %d failed",
                self._plan.completed,
                self._plan.failed,
            )
            return

        key = self._queue.pop(0)
        item = self._plan.item_for(key)
        if item is None:
            self._advance()
            return

        info = item.media_file.info
        profile = self._plan.profile
        if info is None or profile is None:
            item.state = BatchItemState.SKIPPED
            item.error = "No destination profile selected"
            self.item_finished.emit(key)
            self._advance()
            return

        # Batch never trims: durations differ per file, and applying one trim
        # across a folder would cut creative the user never reviewed.
        job = build_job(
            info,
            profile,
            destination=self._destination,
            trim=None,
            loudness=None,
            normalize=self._plan.normalize_loudness,
            overwrite=True,
        )
        item.job = job
        item.state = BatchItemState.RUNNING
        item.progress = 0.0
        self._current_key = key
        self.item_started.emit(key)

        worker = TranscodeWorker(job)
        worker.signals.progress.connect(lambda f, _s, k=key: self._on_progress(k, f))
        worker.signals.finished.connect(lambda result, k=key: self._on_finished(k, result))
        worker.signals.failed.connect(lambda message, k=key: self._on_failed(k, message))
        self._current = worker
        self._pool.start(worker)

    def _on_progress(self, key: str, fraction: float) -> None:
        if self._plan is None:
            return
        item = self._plan.item_for(key)
        if item is not None:
            item.progress = max(0.0, min(1.0, fraction))
        self.item_progress.emit(key, fraction)

    def _on_finished(self, key: str, result: ExportResult) -> None:
        if self._plan is None:
            return
        item = self._plan.item_for(key)
        if item is not None:
            item.result = result
            item.progress = 1.0
            if result.state == ExportState.DONE:
                item.state = BatchItemState.DONE
            elif result.state == ExportState.CANCELLED:
                item.state = BatchItemState.CANCELLED
            else:
                item.state = BatchItemState.FAILED
                item.error = result.error
            log.info("batch item %s: %s", Path(key).name, item.state.value)
        self.item_finished.emit(key)
        self._current = None
        self._advance()

    def _on_failed(self, key: str, message: str) -> None:
        if self._plan is None:
            return
        item = self._plan.item_for(key)
        if item is not None:
            item.state = BatchItemState.FAILED
            item.error = message
            item.progress = 1.0
        log.warning("batch item %s failed: %s", Path(key).name, message)
        self.item_finished.emit(key)
        self._current = None
        self._advance()
