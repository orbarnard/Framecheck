"""Numeric trim controls: IN/OUT/DURATION readouts, duration presets, and the
frame-alignment truth.

The readouts are editable because a delivery spec arrives as a number, not as a
gesture. Every field accepts the formats a user might paste -- clock, timecode,
or bare seconds -- and reverts rather than guessing when it cannot parse one.

The alignment note under the presets is the point of the whole panel: at 29.97
a ":30" is 900 frames and 30.030 s, and 30.000 s is not a frame at all. That
gets stated, never rounded away.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from collections.abc import Callable
from fractions import Fraction

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models.media_time import FrameRate, MediaTime, Rounding
from ..models.trim import (
    STANDARD_TARGET_SECONDS,
    Fit,
    TargetDuration,
    TargetMode,
    TrimRange,
)
from .theme import Color, Metrics, mono_font_family

_FIELD_WIDTH = 108

# HH:MM:SS:FF or HH:MM:SS;FF -- the trailing field is frames, not fractions.
_TIMECODE_RE = re.compile(r"^(\d{1,3}):([0-5]?\d):([0-5]?\d)[:;](\d{1,3})$")
# [HH:]MM:SS[.mmm]
_CLOCK_RE = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")
_SECONDS_RE = re.compile(r"^\d+(?:\.\d+)?$")


def parse_time_input(text: str, rate: FrameRate) -> MediaTime | None:
    """Parse a user-typed time at `rate`. None means "unparseable, reject it".

    Accepts HH:MM:SS.mmm, MM:SS, bare seconds, and HH:MM:SS:FF timecode
    (drop-frame aware at 29.97/59.94, where a ';' or ':' separator reads the
    same -- dropped frame *numbers* are what makes the arithmetic differ, not
    the punctuation).
    """
    text = text.strip()
    if not text:
        return None

    match = _TIMECODE_RE.match(text)
    if match:
        hours, minutes, seconds, frames = (int(g) for g in match.groups())
        fps = rate.nominal
        if frames >= fps:
            return None
        counted = ((hours * 60 + minutes) * 60 + seconds) * fps + frames
        if rate.is_drop_frame:
            # Undo the frame numbers SMPTE skips: 2 (or 4) per minute, except
            # every tenth minute.
            drop = 2 if fps == 30 else 4
            total_minutes = hours * 60 + minutes
            counted -= drop * (total_minutes - total_minutes // 10)
        return MediaTime(max(0, counted), rate)

    match = _CLOCK_RE.match(text)
    if match:
        hours = int(match.group(1) or 0)
        minutes, seconds = int(match.group(2)), Fraction(match.group(3))
        if minutes > 59 or seconds >= 60:
            return None
        total = Fraction((hours * 60 + minutes) * 60) + seconds
        return MediaTime.from_seconds(total, rate, Rounding.NEAREST)

    if _SECONDS_RE.match(text):
        return MediaTime.from_seconds(Fraction(text), rate, Rounding.NEAREST)
    return None


def _nearest_cuts(target: TargetDuration, rate: FrameRate) -> tuple[int, int]:
    """Frame counts either side of a target that lands between frames."""
    exact = target.seconds * rate.value
    return math.floor(exact), math.ceil(exact)


class TrimPanel(QWidget):
    """The numeric half of trimming. Owns no playback, only the range."""

    trim_changed = Signal(object)  # TrimRange
    preview_cut_requested = Signal()
    loop_cut_toggled = Signal(bool)
    set_in_requested = Signal()
    set_out_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrimPanel")
        self._rate = FrameRate(Fraction(25, 1))
        self._duration: MediaTime | None = None
        self._trim: TrimRange | None = None
        self._target: TargetDuration | None = None

        mono = QFont(mono_font_family(), 10)
        mono.setStyleHint(QFont.Monospace)

        outer = QVBoxLayout(self)
        # Match the transport bar's gutters so the trim controls line up with
        # the scrubber above them instead of running into the panel edge.
        outer.setContentsMargins(
            Metrics.GUTTER, Metrics.GUTTER_SM, Metrics.GUTTER, Metrics.GUTTER_SM
        )
        outer.setSpacing(Metrics.GUTTER_SM)

        fields = QGridLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setHorizontalSpacing(Metrics.GUTTER_SM)
        fields.setVerticalSpacing(2)
        self.in_edit = self._add_field(fields, 0, "IN", mono, self._on_in_edited)
        self.out_edit = self._add_field(fields, 1, "OUT", mono, self._on_out_edited)
        self.duration_edit = self._add_field(
            fields, 2, "DURATION", mono, self._on_duration_edited
        )
        fields.setColumnStretch(3, 1)
        outer.addLayout(fields)

        outer.addLayout(self._build_actions())
        outer.addLayout(self._build_presets())

        self.delta_label = QLabel(self)
        self.delta_label.setWordWrap(True)
        outer.addWidget(self.delta_label)

        self.alignment_label = QLabel(self)
        self.alignment_label.setObjectName("MetaValueMuted")
        self.alignment_label.setWordWrap(True)
        outer.addWidget(self.alignment_label)

        outer.addLayout(self._build_review())

        self._refresh()

    # -- construction -----------------------------------------------------

    def _add_field(
        self,
        grid: QGridLayout,
        column: int,
        title: str,
        font: QFont,
        on_edited: Callable[[], None],
    ) -> QLineEdit:
        label = QLabel(title, self)
        label.setObjectName("SectionLabel")
        grid.addWidget(label, 0, column)

        edit = QLineEdit(self)
        edit.setFont(font)
        edit.setFixedWidth(_FIELD_WIDTH)
        edit.setAlignment(Qt.AlignCenter)
        edit.editingFinished.connect(on_edited)
        grid.addWidget(edit, 1, column)
        return edit

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_XS)

        self.btn_set_in = QPushButton("Set In (I)", self)
        self.btn_set_out = QPushButton("Set Out (O)", self)
        self.btn_reset = QPushButton("Reset", self)
        self.btn_set_in.clicked.connect(self.set_in_requested)
        self.btn_set_out.clicked.connect(self.set_out_requested)
        self.btn_reset.clicked.connect(self.reset)

        for button in (self.btn_set_in, self.btn_set_out, self.btn_reset):
            button.setFocusPolicy(Qt.NoFocus)
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_presets(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_XS)

        self._preset_buttons: list[QPushButton] = []
        for seconds in STANDARD_TARGET_SECONDS:
            target = TargetDuration.of(seconds)
            button = QPushButton(target.label(), self)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedHeight(Metrics.ROW_HEIGHT - 4)
            button.clicked.connect(lambda _=False, t=target: self.apply_target(t))
            row.addWidget(button)
            self._preset_buttons.append(button)

        self.btn_custom = QPushButton("Custom...", self)
        self.btn_custom.setFocusPolicy(Qt.NoFocus)
        self.btn_custom.setFixedHeight(Metrics.ROW_HEIGHT - 4)
        self.btn_custom.clicked.connect(self._ask_custom_target)
        row.addWidget(self.btn_custom)
        self._preset_buttons.append(self.btn_custom)
        row.addStretch(1)

        # How a preset lands whole seconds on 29.97 / 59.94 / 23.976 footage.
        fit_label = QLabel("Fit", self)
        fit_label.setObjectName("MetaValueMuted")
        row.addWidget(fit_label)
        self.fit_combo = QComboBox(self)
        self.fit_combo.setFocusPolicy(Qt.NoFocus)
        self.fit_combo.addItem("Speed up 0.1%", Fit.SPEED)
        self.fit_combo.addItem("Hold end frame", Fit.HOLD_END)
        self.fit_combo.addItem("Hold start frame", Fit.HOLD_START)
        self.fit_combo.currentIndexChanged.connect(self._on_fit_changed)
        row.addWidget(self.fit_combo)
        return row

    def _build_review(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_XS)

        self.btn_preview = QPushButton("Preview Cut", self)
        self.btn_loop = QPushButton("Loop Cut", self)
        self.btn_loop.setCheckable(True)
        self.btn_preview.clicked.connect(self.preview_cut_requested)
        self.btn_loop.toggled.connect(self.loop_cut_toggled)

        for button in (self.btn_preview, self.btn_loop):
            button.setFocusPolicy(Qt.NoFocus)
            row.addWidget(button)
        row.addStretch(1)
        return row

    # -- state ------------------------------------------------------------

    def set_source(self, duration: MediaTime | None, rate: FrameRate) -> None:
        """Point the panel at a new source. Resets the range to the whole file."""
        self._rate = rate
        self._duration = duration.at_rate(rate) if duration is not None else None
        self._target = None
        self._trim = TrimRange.full(self._duration) if self._duration else None
        self._refresh()

    def set_trim(self, trim: TrimRange | None) -> None:
        """Programmatic: does not emit trim_changed."""
        self._trim = trim.at_rate(self._rate).clamped(self._duration) if trim else None
        self._refresh()

    def trim(self) -> TrimRange | None:
        return self._trim

    def target(self) -> TargetDuration | None:
        return self._target

    def set_target(self, target: TargetDuration | None) -> None:
        """Programmatic: restore a file's preset without moving its range."""
        self._target = target
        self._refresh()

    def fit(self) -> Fit:
        return self.fit_combo.currentData() or Fit.SPEED

    def reset(self) -> None:
        if self._duration is None:
            return
        self._target = None
        self._apply(TrimRange.full(self._duration))

    def apply_target(self, target: TargetDuration) -> None:
        """Set OUT to IN + `target`, and say what the frame grid can deliver."""
        if self._trim is None:
            return
        self._target = replace(target, fit=self.fit())
        self._apply(TrimRange.for_target(self._trim.in_point, self._target, self._duration))

    def set_loop_active(self, active: bool) -> None:
        """Reflect loop state driven from elsewhere (the transport, a shortcut)."""
        if self.btn_loop.isChecked() != active:
            self.btn_loop.setChecked(active)

    # -- editing ----------------------------------------------------------

    def _on_in_edited(self) -> None:
        point = parse_time_input(self.in_edit.text(), self._rate)
        if point is None or self._trim is None:
            self._refresh()  # reject: the previous value is the truth
            return
        self._apply(self._trim.with_in(point))

    def _on_out_edited(self) -> None:
        point = parse_time_input(self.out_edit.text(), self._rate)
        if point is None or self._trim is None:
            self._refresh()
            return
        self._apply(self._trim.with_out(point))

    def _on_duration_edited(self) -> None:
        length = parse_time_input(self.duration_edit.text(), self._rate)
        if length is None or length.frames <= 0 or self._trim is None:
            self._refresh()
            return
        # Duration is the derived field: editing it moves OUT, never IN.
        self._apply(self._trim.with_out(self._trim.in_point.offset_frames(length.frames)))

    def _on_fit_changed(self, _index: int) -> None:
        """Re-cut a preset for the new fit; a nudged range is left alone."""
        target, trim = self._target, self._trim
        if target is not None and trim is not None and (
            trim.frame_count == target.frames_at(self._rate)
        ):
            self.apply_target(target)
        else:
            self._refresh()

    def _ask_custom_target(self) -> None:
        current = float(self._target.seconds) if self._target else 30.0
        seconds, accepted = QInputDialog.getDouble(
            self, "Custom target", "Target duration (seconds):", current, 0.001, 86400.0, 3
        )
        if accepted:
            self.apply_target(TargetDuration.of(Fraction(seconds).limit_denominator(1000)))

    def _apply(self, trim: TrimRange) -> None:
        self._trim = trim.clamped(self._duration)
        self._refresh()
        self.trim_changed.emit(self._trim)

    # -- display ----------------------------------------------------------

    def _refresh(self) -> None:
        trim = self._trim
        enabled = trim is not None
        for widget in (
            self.in_edit,
            self.out_edit,
            self.duration_edit,
            self.btn_set_in,
            self.btn_set_out,
            self.btn_reset,
            self.btn_preview,
            self.btn_loop,
            *self._preset_buttons,
            self.fit_combo,
        ):
            widget.setEnabled(enabled)

        if trim is None:
            for edit in (self.in_edit, self.out_edit, self.duration_edit):
                edit.setText("--:--:--.---")
                edit.setToolTip("")
            self.delta_label.hide()
            self.alignment_label.hide()
            return

        for edit, point in (
            (self.in_edit, trim.in_point),
            (self.out_edit, trim.out_point),
            (self.duration_edit, trim.duration),
        ):
            edit.setText(point.to_clock())
            edit.setToolTip(f"{point.to_timecode()}    {point.frames} f")

        self._refresh_target_lines(trim)

    def _refresh_target_lines(self, trim: TrimRange) -> None:
        target = self._target
        if target is None:
            self.delta_label.hide()
            self.alignment_label.hide()
            return

        delta = trim.compare_to_target(target)
        self.delta_label.setText(delta.describe())
        self.delta_label.setStyleSheet(
            f"color: {Color.PASS if delta.is_exact else Color.WARNING}; font-size: 11px;"
        )
        self.delta_label.show()

        if target.is_frame_aligned(self._rate):
            self.alignment_label.hide()
            return

        cut = target.exact_cut(self._rate)
        if cut is not None and trim.frame_count == cut.source_frames:
            held = cut.held_frames
            if cut.fit is Fit.SPEED:
                how = (
                    f"{cut.source_frames} f ({float(cut.source_seconds):.3f} s) "
                    f"sped up {float(cut.speed - 1):.1%} with the sound, in sync"
                )
            else:
                edge = "start" if cut.fit is Fit.HOLD_START else "end"
                how = (
                    f"{cut.source_frames} f + {held} held "
                    f"frame{'s' if held != 1 else ''} at the {edge}"
                )
            self.alignment_label.setText(
                f"{float(target.seconds):.3f} s is not a frame boundary at "
                f"{self._rate.label()}. Export runs at {cut.rate.label()} fps: "
                f"{how} = {cut.frames} f, exactly {float(cut.seconds):.3f} s "
                f"(if the destination accepts {cut.rate.label()} fps)."
            )
            self.alignment_label.show()
            return
        if target.mode is TargetMode.EXACT:
            self.alignment_label.hide()  # range was nudged off the preset
            return

        # The request falls between two frames. Framecheck takes the one under
        # the target: platforms that police a :30 slot measure wall-clock
        # seconds, so a frame under passes and a frame over is rejected. Name
        # both cuts so the choice is visible rather than discovered later.
        low, high = _nearest_cuts(target, self._rate)
        chosen = target.frames_at(self._rate)
        over = high if high != chosen else low
        self.alignment_label.setText(
            f"{float(target.seconds):.3f} s is not a frame boundary at "
            f"{self._rate.label()} — cut at {chosen} f "
            f"({float(Fraction(chosen) / self._rate.value):.3f} s) to stay under. "
            f"{over} f would run {float(Fraction(over) / self._rate.value):.3f} s."
        )
        self.alignment_label.show()
