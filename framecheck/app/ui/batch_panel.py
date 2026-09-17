"""Batch panel: ten files, one destination, one progress bar.

Sibling of `export_panel.py` and built to the same pattern -- a pure renderer
that emits intent and lets the window decide. The one structural difference is
the file list: rows are built once per plan and refreshed in place, because a
rebuild on every progress tick across fifty files stutters visibly.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.batch import BatchItem, BatchItemState, BatchPlan
from ..models.profile import CheckStatus
from ..utils.paths import shorten_path
from .status_chip import StatusChip
from .theme import Color, Metrics

# Only the four status colours are allowed in this panel; a running file gets
# amber because it is in flight, not because anything is wrong with it.
_STATE_COLORS = {
    BatchItemState.PENDING: Color.NEUTRAL,
    BatchItemState.RUNNING: Color.WARNING,
    BatchItemState.DONE: Color.PASS,
    BatchItemState.FAILED: Color.FAIL,
    BatchItemState.SKIPPED: Color.NEUTRAL,
    BatchItemState.CANCELLED: Color.NEUTRAL,
}

# MANUAL_REVIEW takes amber rather than its own blue: within this panel the
# question is only "can this go out?", and an unreviewed file cannot.
_STATUS_COLORS = {
    CheckStatus.PASS: Color.PASS,
    CheckStatus.WARNING: Color.WARNING,
    CheckStatus.FAIL: Color.FAIL,
    CheckStatus.MANUAL_REVIEW: Color.WARNING,
    CheckStatus.NOT_APPLICABLE: Color.NEUTRAL,
}

_STATUS_WORDS = {
    CheckStatus.PASS: "pass",
    CheckStatus.WARNING: "warning",
    CheckStatus.FAIL: "fail",
    CheckStatus.MANUAL_REVIEW: "manual review",
    CheckStatus.NOT_APPLICABLE: "not applicable",
}

_NO_TRIM_NOTE = (
    "Batch export conforms each file in full. "
    "Trim applies to single-file export only."
)

_DOT = 7


def _wrap_height(label: QLabel) -> None:
    """Make a word-wrapped label report its true height to the layout.

    Same fix as in `export_panel.py`: a wrapping QLabel in a QVBoxLayout reports
    the height it would need at its natural width, not the width it actually
    gets, so a two-line error is allotted one line and overlaps its neighbour.
    """
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setMinimumHeight(label.fontMetrics().height())


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(1)
    return line


class _ItemRow(QWidget):
    """One file in the list. Refreshed in place, never rebuilt."""

    def __init__(self, item: BatchItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_SM)

        self._dot = QFrame()
        self._dot.setFixedSize(_DOT, _DOT)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)

        self._name = QLabel(shorten_path(item.media_file.name, 34))
        self._name.setObjectName("MetaValue")
        self._name.setToolTip(str(item.media_file.path))
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self._name, 1)

        # Right-hand slot: the chip when there is a verified verdict, otherwise
        # the plain-text state. Both exist; one is visible.
        self._detail = QLabel("")
        self._detail.setObjectName("MetaKey")
        self._detail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._detail, 0)

        self._chip = StatusChip(CheckStatus.NOT_APPLICABLE, compact=True)
        self._chip.setVisible(False)
        row.addWidget(self._chip, 0, Qt.AlignVCenter)
        column.addLayout(row)

        self._error = QLabel("")
        self._error.setObjectName("MetaKey")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        self._error.setContentsMargins(_DOT + Metrics.GUTTER_SM, 0, 0, 0)
        _wrap_height(self._error)
        column.addWidget(self._error)

        self.refresh()

    def refresh(self) -> None:
        item = self._item
        status = item.status
        # Once the output has been verified the verdict owns the colour: a green
        # dot beside a WARNING chip would be two answers to the same question.
        color = (
            _STATUS_COLORS[status]
            if status is not None and item.state is BatchItemState.DONE
            else _STATE_COLORS.get(item.state, Color.NEUTRAL)
        )
        self._dot.setStyleSheet(
            f"background-color: {color}; border-radius: {_DOT // 2}px;"
        )

        if item.state is BatchItemState.RUNNING:
            percent = int(round(max(0.0, min(1.0, item.progress)) * 100))
            self._detail.setText(f"{percent}%")
            self._detail.setVisible(True)
            self._chip.setVisible(False)
        elif status is not None:
            self._chip.set_status(status)
            self._chip.setVisible(True)
            self._detail.setVisible(False)
        else:
            self._detail.setText(item.summary())
            self._detail.setVisible(True)
            self._chip.setVisible(False)

        failed = item.state is BatchItemState.FAILED
        self._error.setText((item.error or "Export failed") if failed else "")
        self._error.setStyleSheet(
            f"color: {Color.FAIL}; font-size: 11px;" if failed else ""
        )
        self._error.setVisible(failed)


class BatchPanel(QWidget):
    """Batch export panel. States: empty, idle, running, finished."""

    export_requested = Signal()
    cancel_requested = Signal()
    output_folder_change_requested = Signal()
    open_folder_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BatchPanel")
        # theme.py styles the sibling panels by object name; this one is new, so
        # it carries its own surface rule rather than editing the shared sheet.
        # Background comes from the shared #BatchPanel rule in theme.py.

        self._plan: BatchPlan | None = None
        self._rows: dict[str, _ItemRow] = {}
        self._running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER_SM)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_empty_page())
        self._stack.addWidget(self._build_content_page())

        self.show_empty()

    # -- construction ----------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addStretch(1)

        title = QLabel("Nothing queued")
        title.setObjectName("MetaValueMuted")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Add files to the workspace to export them together.")
        hint.setObjectName("MetaKey")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        _wrap_height(hint)
        layout.addWidget(hint)

        layout.addStretch(1)
        return page

    def _build_content_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        _wrap_height(self._summary)
        column.addWidget(self._summary)

        column.addLayout(self._build_destination())
        column.addWidget(self._build_list(), 1)
        column.addWidget(self._build_progress())
        column.addWidget(self._build_actions())
        column.addWidget(self._build_completion())
        return page

    def _build_destination(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER_XS)

        label = QLabel("OUTPUT FOLDER")
        label.setObjectName("SectionLabel")
        column.addWidget(label)
        column.addWidget(_separator())

        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, Metrics.GUTTER_XS, 0, 0)
        folder_row.setSpacing(Metrics.GUTTER_SM)

        self._folder_label = QLabel("No folder chosen")
        self._folder_label.setObjectName("MetaValueMuted")
        self._folder_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        folder_row.addWidget(self._folder_label, 1)

        change = QPushButton("Change")
        change.setObjectName("Ghost")
        change.clicked.connect(self.output_folder_change_requested)
        folder_row.addWidget(change, 0)
        column.addLayout(folder_row)

        note = QLabel(_NO_TRIM_NOTE)
        note.setObjectName("MetaKey")
        note.setWordWrap(True)
        _wrap_height(note)
        column.addWidget(note)
        return column

    def _build_list(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        label = QLabel("FILES")
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        layout.addWidget(_separator())

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, Metrics.GUTTER_XS, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._list_host)
        layout.addWidget(scroll, 1)
        return box

    def _build_progress(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._progress_detail = QLabel("")
        self._progress_detail.setObjectName("MetaKey")
        self._progress_detail.setWordWrap(True)
        _wrap_height(self._progress_detail)
        layout.addWidget(self._progress_detail)
        return box

    def _build_actions(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        self._export_button = QPushButton("EXPORT")
        self._export_button.setObjectName("Primary")
        self._export_button.setFixedHeight(Metrics.CONTROL_HEIGHT + 4)
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self.export_requested)
        layout.addWidget(self._export_button)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setFixedHeight(Metrics.CONTROL_HEIGHT + 4)
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self.cancel_requested)
        layout.addWidget(self._cancel_button)
        return box

    def _build_completion(self) -> QWidget:
        self._completion = QWidget()
        layout = QVBoxLayout(self._completion)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        self._completion_title = QLabel("BATCH COMPLETE")
        layout.addWidget(self._completion_title)

        self._completion_failures = QLabel("")
        self._completion_failures.setWordWrap(True)
        self._completion_failures.setStyleSheet(f"color: {Color.FAIL}; font-size: 11px;")
        self._completion_failures.setVisible(False)
        _wrap_height(self._completion_failures)
        layout.addWidget(self._completion_failures)

        self._completion_counts = QLabel("")
        self._completion_counts.setObjectName("MetaKey")
        self._completion_counts.setWordWrap(True)
        _wrap_height(self._completion_counts)
        layout.addWidget(self._completion_counts)

        open_folder = QPushButton("Open Output Folder")
        open_folder.clicked.connect(self.open_folder_requested)
        layout.addWidget(open_folder)

        self._completion.setVisible(False)
        return self._completion

    # -- api -------------------------------------------------------------

    def set_plan(self, plan: BatchPlan | None) -> None:
        """Adopt a plan and build its rows. The only call that rebuilds."""
        self._plan = plan
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        if plan is None or not plan.items:
            self._refresh()
            self.show_empty()
            return

        for index, item in enumerate(plan.items):
            row = _ItemRow(item)
            self._rows[item.key] = row
            # Keep the trailing stretch last so a short list hugs the top.
            self._list_layout.insertWidget(index, row)

        self._stack.setCurrentIndex(1)
        self._refresh()

    def update_item(self, key: str) -> None:
        """Repaint one row after its state changed, plus the aggregates."""
        row = self._rows.get(key)
        if row is not None:
            row.refresh()
        self._refresh()

    def set_output_folder(self, folder: Path | None) -> None:
        text = str(folder) if folder is not None else ""
        self._folder_label.setText(shorten_path(text, 40) if text else "No folder chosen")
        self._folder_label.setToolTip(text)
        # Object name drives the stylesheet rule; Qt needs a repolish to notice.
        self._folder_label.setObjectName("MetaValue" if text else "MetaValueMuted")
        self._folder_label.style().unpolish(self._folder_label)
        self._folder_label.style().polish(self._folder_label)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._refresh()

    def show_empty(self) -> None:
        self._stack.setCurrentIndex(0)

    # -- rendering -------------------------------------------------------

    def _refresh(self) -> None:
        plan = self._plan
        total = plan.total if plan is not None else 0

        self._summary.setText(self._summary_html(plan, total))

        percent = (plan.overall_progress * 100.0) if plan is not None else 0.0
        self._progress.setValue(int(round(max(0.0, min(100.0, percent)))))

        self._progress_detail.setText(self._progress_text(plan, total))

        self._export_button.setText(
            "EXPORT 1 FILE" if total == 1 else f"EXPORT {total} FILES"
        )
        has_profile = plan is not None and plan.profile is not None
        self._export_button.setVisible(not self._running)
        self._export_button.setEnabled(bool(total) and has_profile and not self._running)
        self._cancel_button.setVisible(self._running)

        self._fill_completion(plan)

    def _summary_html(self, plan: BatchPlan | None, total: int) -> str:
        if plan is None or plan.profile is None:
            return (
                f'<span style="color: {Color.WARNING};">'
                "Select a destination profile in VALIDATE first."
                "</span>"
            )
        noun = "file" if total == 1 else "files"
        name = html.escape(plan.profile.name or plan.profile.id)
        return (
            f'<span style="color: {Color.TEXT_TERTIARY};">{total} {noun} &#8594;</span> '
            f'<span style="color: {Color.TEXT};">{name}</span>'
        )

    def _progress_text(self, plan: BatchPlan | None, total: int) -> str:
        if plan is None or not total:
            return ""
        parts = [f"{plan.completed} of {total} complete"]
        if plan.failed:
            parts.append(f"{plan.failed} failed")
        return "  ·  ".join(parts)

    def _fill_completion(self, plan: BatchPlan | None) -> None:
        finished = plan is not None and plan.is_finished and not self._running
        self._completion.setVisible(finished)
        if not finished or plan is None:
            return

        failed = plan.failed
        color = Color.FAIL if failed else Color.PASS
        self._completion_title.setStyleSheet(
            f"color: {color}; font-size: 10px; font-weight: 600; letter-spacing: 0.7px;"
        )
        self._completion_title.setText("BATCH COMPLETE")

        self._completion_failures.setVisible(bool(failed))
        if failed:
            noun = "file" if failed == 1 else "files"
            self._completion_failures.setText(
                f"{failed} of {plan.total} {noun} did not export. "
                "Nothing was delivered for those."
            )

        self._completion_counts.setText(self._counts_text(plan))

    def _counts_text(self, plan: BatchPlan) -> str:
        counts = plan.status_counts()
        parts = [
            f"{counts[status]} {_STATUS_WORDS[status]}"
            for status in (
                CheckStatus.PASS,
                CheckStatus.MANUAL_REVIEW,
                CheckStatus.WARNING,
                CheckStatus.FAIL,
                CheckStatus.NOT_APPLICABLE,
            )
            if counts.get(status)
        ]
        unverified = plan.total - sum(counts.values())
        if unverified:
            parts.append(f"{unverified} not verified")
        return "  ·  ".join(parts) if parts else "No files were verified"
