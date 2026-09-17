"""Batch export: a list of files heading for one destination profile.

Pure data, no Qt. The panel renders a BatchPlan and the runner mutates its
items; keeping the rollups here means the progress bar, the counts line and the
completion block cannot disagree with each other.

The one rule worth stating: a batch conforms each source *in full*. Trim is a
single-file decision -- ten sources have ten different durations, and applying
one in/out pair across them would silently destroy nine pieces of creative. So
there is no trim field on this model, deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .export_job import ExportJob, ExportResult
from .media_file import MediaFile
from .profile import CheckStatus, Profile


class BatchItemState(Enum):
    """Where one file is in the batch run."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # Deliberately not attempted: unreadable source, missing file, user choice.
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


# Anything that will not move again without a new run.
FINISHED_STATES: frozenset[BatchItemState] = frozenset(
    {
        BatchItemState.DONE,
        BatchItemState.FAILED,
        BatchItemState.SKIPPED,
        BatchItemState.CANCELLED,
    }
)


@dataclass
class BatchItem:
    """One source file and its outcome. Mutable: the runner fills it in."""

    media_file: MediaFile
    state: BatchItemState = BatchItemState.PENDING
    job: ExportJob | None = None
    result: ExportResult | None = None
    error: str | None = None
    progress: float = 0.0  # 0.0-1.0 within this file's encode

    @property
    def key(self) -> str:
        return self.media_file.key

    @property
    def is_finished(self) -> bool:
        return self.state in FINISHED_STATES

    @property
    def status(self) -> CheckStatus | None:
        """Worst validation status across the exported file's reports.

        None until the output has actually been verified -- an unverified file
        has no status, and guessing PASS from a zero exit code is exactly the
        lie this tool exists to prevent.
        """
        if self.result is None or not self.result.reports:
            return None
        return max((r.status for r in self.result.reports), key=lambda s: s.rank)

    def summary(self) -> str:
        """One-line outcome for the row."""
        if self.state is BatchItemState.PENDING:
            return "Queued"
        if self.state is BatchItemState.RUNNING:
            percent = int(round(max(0.0, min(1.0, self.progress)) * 100))
            return f"Exporting {percent}%" if percent else "Exporting"
        if self.state is BatchItemState.SKIPPED:
            return "Skipped"
        if self.state is BatchItemState.CANCELLED:
            return "Cancelled"
        if self.state is BatchItemState.FAILED:
            return self.error or "Export failed"
        reports = self.result.reports if self.result is not None else ()
        if not reports:
            return "Exported"
        worst = max(reports, key=lambda r: r.status.rank)
        return worst.summary_line()


@dataclass
class BatchPlan:
    """Every file in one batch run, plus the settings shared across them."""

    items: list[BatchItem] = field(default_factory=list)
    profile: Profile | None = None
    normalize_loudness: bool = False

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def completed(self) -> int:
        return sum(1 for i in self.items if i.state is BatchItemState.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.state is BatchItemState.FAILED)

    @property
    def remaining(self) -> int:
        """Files that have not settled yet."""
        return sum(1 for i in self.items if not i.is_finished)

    @property
    def is_finished(self) -> bool:
        """True only when there is work and all of it has settled.

        An empty plan is not "finished": nothing ran, so the completion block
        must not claim a batch completed.
        """
        return bool(self.items) and self.remaining == 0

    @property
    def overall_progress(self) -> float:
        """0.0-1.0 across the whole batch, including the file mid-encode.

        Counting only completed files freezes the bar for the length of every
        encode, which reads as a hang on a ten-minute master.
        """
        if not self.items:
            return 0.0
        done = sum(
            1.0 if i.is_finished else max(0.0, min(1.0, i.progress)) for i in self.items
        )
        return done / len(self.items)

    def item_for(self, key: str) -> BatchItem | None:
        for item in self.items:
            if item.key == key:
                return item
        return None

    def status_counts(self) -> dict[CheckStatus, int]:
        """Verified statuses across finished items, worst-first order preserved
        by the caller. Items without a verdict are absent, not counted as PASS."""
        counts: dict[CheckStatus, int] = {}
        for item in self.items:
            status = item.status
            if status is not None:
                counts[status] = counts.get(status, 0) + 1
        return counts

    def reset(self) -> None:
        """Return every item to PENDING, discarding the previous run's outcome.

        `job` survives: it is the plan for the file, not a result of running it.
        """
        for item in self.items:
            item.state = BatchItemState.PENDING
            item.result = None
            item.error = None
            item.progress = 0.0
