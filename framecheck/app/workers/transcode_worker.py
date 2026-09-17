"""Off-thread export.

Same shape as ProbeWorker: a QRunnable that emits results back to the UI thread,
carrying a generation token so a result for a job the user has already replaced
can be discarded rather than driving a stale progress bar.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, Signal

from ..media.conform import plan_conform
from ..media.loudness import LoudnessError, analyze_loudness
from ..media.probe import ProbeError, probe
from ..media.transcode import TranscodeError, run_export
from ..models.export_job import ExportJob, ExportResult, ExportState
from ..profiles.validator import validate

log = logging.getLogger(__name__)


class TranscodeSignals(QObject):
    progress = Signal(float, str)  # 0.0-1.0, status text
    finished = Signal(object)  # ExportResult
    failed = Signal(str)


class TranscodeWorker(QRunnable):
    """Runs one export on the shared thread pool."""

    def __init__(self, job: ExportJob, generation: int = 0) -> None:
        super().__init__()
        self.job = job
        self.generation = generation
        self.signals = TranscodeSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        """Ask the encode to stop. Safe to call from any thread."""
        self._cancelled = True

    def _should_cancel(self) -> bool:
        return self._cancelled

    def _on_progress(self, fraction: float, status: str) -> None:
        self.signals.progress.emit(fraction, status)

    def _ensure_exact_loudness(self) -> None:
        """Re-measure in full when normalising from a sampled reading.

        The panel may be showing an estimate taken from a window of a long
        source. That is fine to look at, but a two-pass normalisation fed
        approximate measurements produces an approximate result, so the exact
        pass happens here before any encoding starts.
        """
        if not self.job.normalize_loudness:
            return
        measured = self.job.source_loudness
        if measured is not None and not measured.estimated:
            return

        self.signals.progress.emit(0.0, "Measuring loudness")
        try:
            exact = analyze_loudness(self.job.source_path, self.job.trim)
        except LoudnessError as exc:
            log.warning("exact loudness pass failed for %s: %s", self.job.source_path, exc)
            return
        self.job = replace(
            self.job,
            source_loudness=exact,
            actions=plan_conform(
                self.job.source_info,
                self.job.target,
                self.job.trim,
                exact,
                True,
            ),
        )

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            self._ensure_exact_loudness()
            result: ExportResult = run_export(
                self.job, self._on_progress, self._should_cancel
            )
        except TranscodeError as exc:
            log.warning("export failed for %s: %s", self.job.output_path, exc)
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected export error for %s", self.job.output_path)
            self.signals.failed.emit(f"Unexpected error: {exc}")
        else:
            self.signals.finished.emit(self._verify(result))

    def _verify(self, result: ExportResult) -> ExportResult:
        """Re-probe the written file and validate it against the job's profiles.

        FFmpeg exiting zero is not evidence of compliance -- it is evidence that
        FFmpeg ran. The only trustworthy check is to read back what actually
        landed on disk, so this re-probes the output and re-runs the same
        validator against it.
        """
        if not result.succeeded or result.output_path is None:
            return result

        self.signals.progress.emit(1.0, "Verifying output")
        try:
            output_info = probe(result.output_path)
        except ProbeError as exc:
            log.warning("could not verify %s: %s", result.output_path, exc)
            return replace(
                result,
                state=ExportState.FAILED,
                error=f"Export finished but the output could not be read back: {exc}",
            )

        reports = tuple(
            validate(output_info, profile, None) for profile in self.job.profiles
        )
        for report in reports:
            log.info(
                "output validation %s: %s (%s)",
                report.profile_id,
                report.status.value,
                report.summary_line(),
            )
        return replace(result, output_info=output_info, reports=reports)
