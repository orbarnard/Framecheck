"""Export panel: where the file goes, how far it got, and what came out.

A pure renderer plus a few controls. It emits intent; the window decides what
to do about it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.export_job import ExportJob, ExportResult, ExportState
from ..models.validation_result import ValidationReport
from ..utils.paths import shorten_path
from .status_chip import StatusChip
from .theme import Color, Metrics

_RUNNING_STATES = (
    ExportState.QUEUED,
    ExportState.ANALYZING,
    ExportState.ENCODING,
    ExportState.VERIFYING,
)

_STATE_LABELS = {
    ExportState.QUEUED: "Queued",
    ExportState.ANALYZING: "Measuring loudness",
    ExportState.ENCODING: "Encoding",
    ExportState.VERIFYING: "Verifying output",
    ExportState.DONE: "Complete",
    ExportState.FAILED: "Failed",
    ExportState.CANCELLED: "Cancelled",
}


def _wrap_height(label: QLabel) -> None:
    """Make a word-wrapped label report its true height to the layout.

    A wrapping QLabel in a QVBoxLayout reports the height it would need at its
    natural width, not the width it actually gets, so a two-line warning is
    allotted one line and the widget above it overlaps the text.
    """
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setMinimumHeight(label.fontMetrics().height())


class ExportPanel(QWidget):
    """Right-hand EXPORT panel. States: empty, idle, running, finished."""

    export_requested = Signal()
    cancel_requested = Signal()
    output_folder_change_requested = Signal()
    filename_edited = Signal(str)
    open_folder_requested = Signal()
    open_file_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExportPanel")

        self._job: ExportJob | None = None
        # Empty until an export starts: no state is not a running state.
        self._state: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER_SM)

        # The inspector tab bar names this panel; an in-panel header repeats it.
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_empty_page())
        self._stack.addWidget(self._build_content_page())

        self.set_result(None)
        self.show_empty()

    # -- construction ----------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addStretch(1)

        title = QLabel("Nothing to export")
        title.setObjectName("MetaValueMuted")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Load a file and choose a destination.")
        hint.setObjectName("MetaKey")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)
        return page

    def _build_content_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ExportPanel")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER)

        column.addLayout(self._build_destination())
        column.addWidget(self._build_actions())
        column.addWidget(self._build_progress())

        self._result_box = QWidget()
        column.addWidget(self._result_box)
        self._result_parent = column

        column.addStretch(1)
        return page

    def _build_destination(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER_XS)

        label = QLabel("OUTPUT")
        label.setObjectName("SectionLabel")
        column.addWidget(label)

        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedHeight(1)
        column.addWidget(separator)

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

        self._filename = QLineEdit()
        self._filename.setPlaceholderText("Output filename")
        self._filename.setFixedHeight(Metrics.CONTROL_HEIGHT)
        # textEdited, not textChanged: programmatic fills must not echo back.
        self._filename.textEdited.connect(self.filename_edited)
        column.addWidget(self._filename)

        self._collision = QLabel("A file with this name already exists")
        self._collision.setWordWrap(True)
        self._collision.setStyleSheet(f"color: {Color.WARNING}; font-size: 11px;")
        self._collision.setVisible(False)
        _wrap_height(self._collision)
        column.addWidget(self._collision)

        self._collision_hint = QLabel("Rename it, or export again to overwrite deliberately.")
        self._collision_hint.setObjectName("MetaKey")
        self._collision_hint.setWordWrap(True)
        self._collision_hint.setVisible(False)
        _wrap_height(self._collision_hint)
        column.addWidget(self._collision_hint)
        return column

    def _build_actions(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        # Doubled ampersand: Qt reads a single "&" as a mnemonic marker and the
        # button would render as "CONFORM _EXPORT".
        self._export_button = QPushButton("CONFORM && EXPORT")
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

    def _build_progress(self) -> QWidget:
        self._progress_box = QWidget()
        layout = QVBoxLayout(self._progress_box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._progress_detail = QLabel("")
        self._progress_detail.setObjectName("MetaKey")
        self._progress_detail.setWordWrap(True)
        layout.addWidget(self._progress_detail)

        self._progress_box.setVisible(False)
        return self._progress_box

    # -- api -------------------------------------------------------------

    def set_job(self, job: ExportJob | None) -> None:
        self._job = job
        self._export_button.setEnabled(job is not None and self._state not in _RUNNING_STATES)
        if job is not None:
            self._stack.setCurrentIndex(1)

    def set_output(self, folder: Path, filename: str, collision: bool) -> None:
        text = str(folder) if folder is not None else ""
        self._folder_label.setText(shorten_path(text, 40) if text else "No folder chosen")
        self._folder_label.setObjectName("MetaValue" if text else "MetaValueMuted")
        self._folder_label.setToolTip(text)
        # Object name drives the stylesheet rule; Qt needs a repolish to notice.
        self._folder_label.style().unpolish(self._folder_label)
        self._folder_label.style().polish(self._folder_label)

        # Never rewrite the field while the user is typing in it. Each keystroke
        # rebuilds the export job, which calls back here; a setText() at that
        # moment resets the cursor to the end and makes the field impossible to
        # edit anywhere but the tail.
        wanted = filename or ""
        if not self._filename.hasFocus() and self._filename.text() != wanted:
            self._filename.setText(wanted)

        self._collision.setVisible(bool(collision))
        self._collision_hint.setVisible(bool(collision))
        self._filename.setStyleSheet(
            f"border: 1px solid {Color.WARNING};" if collision else ""
        )

    def set_state(self, state: str, progress: float = 0.0, detail: str = "") -> None:
        self._state = state or ExportState.QUEUED
        running = self._state in _RUNNING_STATES

        self._export_button.setVisible(not running)
        self._export_button.setEnabled(self._job is not None and not running)
        self._cancel_button.setVisible(running)
        self._progress_box.setVisible(running)

        if not running:
            return

        # Accept either a 0-1 fraction or an already-scaled percentage.
        percent = progress * 100.0 if progress <= 1.0 else progress
        percent = max(0.0, min(100.0, percent))
        self._progress.setValue(int(round(percent)))

        parts = [_STATE_LABELS.get(self._state, self._state), f"{percent:.0f}%"]
        if detail:
            parts.append(detail)
        self._progress_detail.setText("  ·  ".join(parts))

    def set_result(self, result: ExportResult | None) -> None:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        if result is not None:
            self._fill_result(layout, result)

        self._result_parent.replaceWidget(self._result_box, box)
        self._result_box.deleteLater()
        self._result_box = box
        box.setVisible(result is not None)

    def show_empty(self) -> None:
        self._stack.setCurrentIndex(0)

    # -- result ----------------------------------------------------------

    def _fill_result(self, layout: QVBoxLayout, result: ExportResult) -> None:
        succeeded = result.succeeded
        if succeeded:
            heading, color = "EXPORT COMPLETE", Color.PASS
        elif result.state == ExportState.CANCELLED:
            heading, color = "EXPORT CANCELLED", Color.NEUTRAL
        else:
            heading, color = "EXPORT FAILED", Color.FAIL

        title = QLabel(heading)
        title.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 600; letter-spacing: 0.7px;")
        layout.addWidget(title)

        if result.output_path is not None:
            path_label = QLabel(shorten_path(result.output_path.name, 40))
            path_label.setObjectName("MetaValueMono")
            path_label.setToolTip(str(result.output_path))
            path_label.setWordWrap(True)
            layout.addWidget(path_label)

        if result.error:
            error = QLabel(result.error)
            error.setWordWrap(True)
            error.setTextInteractionFlags(Qt.TextSelectableByMouse)
            error.setStyleSheet(f"color: {Color.FAIL}; font-size: 11px;")
            layout.addWidget(error)

        if succeeded and result.reports:
            caption = QLabel("Verified against the exported file")
            caption.setObjectName("MetaKey")
            caption.setWordWrap(True)
            layout.addWidget(caption)
            for report in result.reports:
                layout.addWidget(self._build_verification(report))

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, Metrics.GUTTER_XS, 0, 0)
        buttons.setSpacing(Metrics.GUTTER_SM)

        open_folder = QPushButton("Open Output Folder")
        open_folder.clicked.connect(self.open_folder_requested)
        buttons.addWidget(open_folder, 1)

        open_file = QPushButton("Open Output File")
        open_file.setEnabled(result.output_path is not None)
        open_file.clicked.connect(self.open_file_requested)
        buttons.addWidget(open_file, 1)
        layout.addLayout(buttons)

    def _build_verification(self, report: ValidationReport) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        layout.addWidget(StatusChip(report.status, compact=True), 0, Qt.AlignTop)

        name = QLabel(report.profile_name or report.profile_id or "Destination")
        name.setObjectName("MetaValue")
        name.setWordWrap(True)
        layout.addWidget(name, 1)

        summary = QLabel(report.summary_line())
        summary.setObjectName("MetaKey")
        summary.setAlignment(Qt.AlignRight | Qt.AlignTop)
        layout.addWidget(summary, 0)
        return row
