"""Validation panel: destinations to check against, and what each one said.

A pure renderer. It receives Profiles, ValidationReports and a
CompatibilityReport and draws them; it never decides what passes.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.profile import CheckStatus, Profile
from ..models.validation_result import CheckResult, CompatibilityReport, ValidationReport
from .status_chip import StatusChip
from .theme import Color, Metrics

_DASH = "--"


@lru_cache(maxsize=1)
def _chip_width() -> int:
    """Width of the status column: the widest compact chip, measured.

    Hardcoding this clipped "WARNING" to "WARNIN(" -- the real width depends on
    the font the theme resolved and on letter spacing, neither of which is
    knowable at import time. Requires a live QApplication, hence the lazy call.
    """
    widest = max(
        StatusChip(status, compact=True).sizeHint().width() for status in CheckStatus
    )
    return widest + 2


def _indent() -> int:
    return _chip_width() + Metrics.GUTTER_SM


_LIST_ROW_HEIGHT = 20

# Problems first. Nobody scrolls past twenty green rows to find the red one.
_SORT_ORDER = {
    CheckStatus.FAIL: 0,
    CheckStatus.WARNING: 1,
    CheckStatus.MANUAL_REVIEW: 2,
    CheckStatus.PASS: 3,
    CheckStatus.NOT_APPLICABLE: 4,
}


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("MetaValueMuted")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    return label


class ValidationPanel(QWidget):
    """Right-hand VALIDATE panel. States: empty, pending, reports."""

    fix_requested = Signal()
    profile_selection_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ValidationPanel")

        self._reports: list[ValidationReport] = []
        self._compatibility: CompatibilityReport | None = None
        # Suppresses profile_selection_changed while set_profiles repopulates.
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER_SM)

        # The inspector tab bar names this panel; an in-panel header repeats it.
        outer.addWidget(self._build_destinations())

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_message_page("No file loaded", "Open a file to validate it."))
        self._stack.addWidget(
            self._build_message_page("Not validated yet", "Run validation to check this file.")
        )

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._stack.addWidget(self._scroll)

        self._rebuild()
        self.show_empty()

    # -- construction ----------------------------------------------------

    def _build_destinations(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        label = QLabel("DESTINATIONS")
        label.setObjectName("SectionLabel")
        layout.addWidget(label)

        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        self._profile_list = QListWidget()
        self._profile_list.setFrameShape(QFrame.NoFrame)
        self._profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._profile_list.setSelectionMode(QListWidget.NoSelection)
        self._profile_list.setFocusPolicy(Qt.NoFocus)
        # Every destination is visible at once. There are nine of them and the
        # choice drives everything below it -- a scroll bar here hides options
        # the user does not know to look for.
        self._profile_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._profile_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._profile_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._profile_list)
        return box

    def _build_message_page(self, message: str, hint: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addStretch(1)

        title = QLabel(message)
        title.setObjectName("MetaValueMuted")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(hint)
        subtitle.setObjectName("MetaKey")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addStretch(1)
        return page

    # -- api -------------------------------------------------------------

    def set_profiles(self, profiles: list[Profile], selected_ids: list[str]) -> None:
        selected = set(selected_ids or ())
        self._loading = True
        self._profile_list.clear()
        for profile in profiles or ():
            item = QListWidgetItem(profile.name or profile.id)
            item.setData(Qt.UserRole, profile.id)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if profile.id in selected else Qt.Unchecked)
            item.setSizeHint(QSize(0, _LIST_ROW_HEIGHT))
            if profile.description:
                item.setToolTip(profile.description)
            self._profile_list.addItem(item)
        self._loading = False
        self._fit_profile_list()

    def _fit_profile_list(self) -> None:
        """Size the list to its contents so nothing is hidden behind a scroll."""
        rows = self._profile_list.count()
        frame = 2 * self._profile_list.frameWidth()
        self._profile_list.setFixedHeight(rows * _LIST_ROW_HEIGHT + frame)

    def selected_profile_ids(self) -> list[str]:
        return [
            self._profile_list.item(i).data(Qt.UserRole)
            for i in range(self._profile_list.count())
            if self._profile_list.item(i).checkState() == Qt.Checked
        ]

    def set_reports(self, reports: list[ValidationReport]) -> None:
        self._reports = list(reports or ())
        self._rebuild()
        self._stack.setCurrentIndex(2)

    def set_compatibility(self, report: CompatibilityReport | None) -> None:
        self._compatibility = report
        self._rebuild()

    def show_empty(self) -> None:
        self._stack.setCurrentIndex(0)

    def show_pending(self) -> None:
        self._stack.setCurrentIndex(1)

    # -- rendering -------------------------------------------------------

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        self.profile_selection_changed.emit(self.selected_profile_ids())

    def _rebuild(self) -> None:
        """Rebuild the whole report body.

        Cheaper to reason about than clearing layouts in place, and setWidget
        deletes the previous body for us.
        """
        body = QWidget()
        body.setObjectName("ValidationPanel")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, Metrics.GUTTER_SM, 0)
        column.setSpacing(Metrics.GUTTER)

        compat = self._compatibility
        if compat is not None and not compat.single_master_possible:
            column.addWidget(self._build_compatibility(compat))

        for report in self._reports:
            column.addWidget(self._build_report(report))

        if not self._reports:
            column.addWidget(_muted("No results."))

        column.addStretch(1)
        self._scroll.setWidget(body)

    def _build_compatibility(self, report: CompatibilityReport) -> QWidget:
        frame = QFrame()
        frame.setObjectName("CompatNotice")
        frame.setStyleSheet(
            f"#CompatNotice {{ border: 1px solid {Color.WARNING};"
            f" border-radius: {Metrics.RADIUS_SM}px; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(Metrics.GUTTER_SM, Metrics.GUTTER_SM, Metrics.GUTTER_SM, Metrics.GUTTER_SM)
        layout.setSpacing(Metrics.GUTTER_XS)

        title = QLabel("ONE UNIVERSAL OUTPUT IS NOT RECOMMENDED")
        title.setWordWrap(True)
        title.setStyleSheet(
            f"color: {Color.WARNING}; font-size: 10px; font-weight: 600; letter-spacing: 0.7px;"
        )
        layout.addWidget(title)

        reason = report.reason or "; ".join(report.conflicts)
        if reason:
            layout.addWidget(_muted(reason))

        for group in report.groups:
            names = ", ".join(p.name or p.id for p in group.profiles)
            text = f"{group.label} — {names}" if names else group.label
            line = QLabel(text)
            line.setObjectName("MetaValue")
            line.setWordWrap(True)
            if group.reason:
                line.setToolTip(group.reason)
            layout.addWidget(line)

        return frame

    def _build_report(self, report: ValidationReport) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(Metrics.GUTTER_SM)
        name = QLabel(report.profile_name or report.profile_id or "Destination")
        name.setObjectName("SectionLabel")
        name.setWordWrap(True)
        head.addWidget(name, 1)
        head.addWidget(StatusChip(report.status), 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(head)

        if report.unavailable_reason:
            layout.addWidget(_muted(report.unavailable_reason))
            return box

        summary = QLabel(report.summary_line())
        summary.setObjectName("MetaKey")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        checks = sorted(report.checks, key=lambda c: _SORT_ORDER.get(c.status, 5))
        quiet = (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE)
        passed = [c for c in checks if c.status in quiet]
        problems = [c for c in checks if c.status not in quiet]

        for check in problems:
            layout.addWidget(self._build_check(check))

        if passed:
            layout.addWidget(self._build_passed_group(passed))
        elif not problems:
            layout.addWidget(_muted("No checks in this profile."))

        return box

    def _build_passed_group(self, checks: list[CheckResult]) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)

        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(Metrics.GUTTER_XS)
        for check in checks:
            rows_layout.addWidget(self._build_check(check))
        rows.setVisible(False)

        toggle = QPushButton(f"{len(checks)} checks passed" if len(checks) != 1 else "1 check passed")
        toggle.setObjectName("Ghost")
        toggle.setCheckable(True)
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.toggled.connect(rows.setVisible)

        layout.addWidget(toggle, 0, Qt.AlignLeft)
        layout.addWidget(rows)
        return box

    def _build_check(self, check: CheckResult) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(Metrics.GUTTER_SM)

        chip = StatusChip(check.status, compact=True)
        chip.setFixedWidth(_chip_width())
        top.addWidget(chip, 0, Qt.AlignTop)

        label = QLabel(check.label or "Check")
        label.setObjectName("MetaValue")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        top.addWidget(label, 1)

        actual = QLabel(check.actual if check.actual else _DASH)
        actual.setObjectName("MetaValueMono" if check.actual else "MetaValueMuted")
        actual.setAlignment(Qt.AlignRight | Qt.AlignTop)
        actual.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(actual, 0)

        layout.addLayout(top)

        if check.status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE):
            return row

        parts = [f"Expected: {check.expected}"] if check.expected else []
        if check.guidance:
            parts.append(check.guidance)
        if parts:
            layout.addLayout(self._indented(_muted("  ·  ".join(parts))))

        if check.fixable:
            button = QPushButton(check.fix_description or "Fix on export")
            button.setObjectName("Ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(f"color: {Color.MANUAL};")
            button.clicked.connect(self.fix_requested)
            layout.addLayout(self._indented(button, stretch=0))

        return row

    def _indented(self, widget: QWidget, stretch: int = 1) -> QHBoxLayout:
        """Align a secondary line with the check label, past the chip column."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addSpacing(_indent())
        layout.addWidget(widget, stretch)
        if not stretch:
            layout.addStretch(1)
        return layout
