"""The trim panel: three numbered steps and a verdict.

1 START (IN), 2 LENGTH (:06 to :90, or custom), 3 HOW to land that length
exactly -- speed up 0.1 %, or hold the last or first frame -- then a banner
saying what the export will measure, green or amber, with the fix one click
away. It is laid out so the wrong thing is hard to do: a method the footage
cannot make is locked with the reason, a file too short to speed up falls back
to a hold by itself, and a range dragged off its preset says so rather than
quietly exporting 29.988 s.

The banner reads the real export job when there is one (`set_outcome`), so a
destination whose frame-rate rules rule out an exact cut shows amber here too.

The time fields accept the formats a user might paste -- clock, timecode, or
bare seconds -- and revert rather than guess when they cannot parse one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..media.conform import ExportOutcome
from ..models.media_time import FrameRate, MediaTime, Rounding
from ..models.trim import (
    STANDARD_TARGET_SECONDS,
    Fit,
    TargetDuration,
    TrimRange,
    fit_options,
    resolve_fit,
    suggest_target,
)
from .theme import Color, Metrics

_FIELD_WIDTH = 112

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


# -- building blocks ----------------------------------------------------------


def _repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property the QSS keys on changes."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _step(number: int, text: str, parent: QWidget) -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(Metrics.GUTTER_SM)
    badge = QLabel(str(number), parent)
    badge.setObjectName("StepBadge")
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(20, 20)
    label = QLabel(text, parent)
    label.setObjectName("StepLabel")
    row.addWidget(badge)
    row.addWidget(label)
    return row, label


class FrameStrip(QWidget):
    """A picture of what a fit does to the frames: even cells when sped up, one
    highlighted cell at the edge that is held."""

    CELLS = 18

    def __init__(self, fit: Fit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fit = fit
        self.setFixedHeight(10)
        self.setMinimumWidth(120)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        gap = 2
        width = (self.width() - gap * (self.CELLS - 1)) / self.CELLS
        dim = self.isEnabled() and self.property("locked") is not True
        for i in range(self.CELLS):
            held = (self._fit is Fit.HOLD_END and i == self.CELLS - 1) or (
                self._fit is Fit.HOLD_START and i == 0
            )
            colour = Color.ACCENT if held else (Color.TEXT_TERTIARY if self._fit is Fit.SPEED else Color.BORDER_STRONG)
            painter.setBrush(QColor(colour if dim else Color.BORDER))
            painter.drawRoundedRect(QRectF(i * (width + gap), 0, width, self.height()), 2, 2)


class FitCard(QFrame):
    """One way to land the target exactly. The whole card is the click target."""

    chosen = Signal(object)  # Fit

    _CAPTIONS = {
        Fit.SPEED: "every frame, 0.1% faster",
        Fit.HOLD_END: "+ held at the end",
        Fit.HOLD_START: "+ held at the start",
    }

    def __init__(self, fit: Fit, title: str, group: QButtonGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FitCard")
        self.fit = fit
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(Metrics.GUTTER_SM)
        self.radio = QRadioButton(title, self)
        self.radio.setObjectName("FitCardTitle")
        group.addButton(self.radio)
        self.radio.clicked.connect(lambda: self.chosen.emit(self.fit))
        self.badge = QLabel(self)
        self.badge.setObjectName("FitBadge")
        head.addWidget(self.radio)
        head.addStretch(1)
        head.addWidget(self.badge)
        layout.addLayout(head)

        self.description = QLabel(self)
        self.description.setObjectName("FitCardText")
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(28)
        self.description.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.description)

        self.strip = FrameStrip(fit, self)
        layout.addWidget(self.strip)
        self.caption = QLabel(self._CAPTIONS[fit], self)
        self.caption.setObjectName("FitCardCaption")
        layout.addWidget(self.caption)

    def show_option(self, text: str, selected: bool, locked: bool, badge: str | None) -> None:
        self.description.setText(text)
        self.radio.setEnabled(not locked)
        self.radio.setChecked(selected)
        self.setCursor(Qt.ArrowCursor if locked else Qt.PointingHandCursor)
        for widget in (self, self.description, self.strip):
            widget.setProperty("selected", selected)
            widget.setProperty("locked", locked)
            _repolish(widget)
        self.strip.update()
        tone = {"BEST": "best", "AUTO": "auto", "LOCKED": "locked"}.get(badge or "", "")
        self.badge.setText("" if badge is None else ("Unavailable" if badge == "LOCKED" else badge))
        self.badge.setVisible(badge is not None)
        self.badge.setProperty("tone", tone)
        _repolish(self.badge)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self.radio.isEnabled() and event.button() == Qt.LeftButton:
            self.radio.setChecked(True)
            self.chosen.emit(self.fit)
        super().mousePressEvent(event)


class VerdictBanner(QFrame):
    """What the export will measure, green or amber, with the fix one click away."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.icon = QLabel(self)
        self.icon.setObjectName("BannerIcon")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedSize(28, 28)
        layout.addWidget(self.icon)

        text = QVBoxLayout()
        text.setSpacing(4)
        self.headline = QLabel(self)
        self.headline.setObjectName("BannerHeadline")
        text.addWidget(self.headline)
        self.detail = QLabel(self)
        self.detail.setObjectName("BannerDetail")
        self.detail.setWordWrap(True)
        text.addWidget(self.detail)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chips = [QLabel(self) for _ in range(4)]
        for chip in self.chips:
            chip.setObjectName("Chip")
            chips.addWidget(chip)
        chips.addStretch(1)
        text.addLayout(chips)
        layout.addLayout(text, 1)

        self.fix_button = QPushButton(self)
        self.fix_button.setObjectName("FixWarn")
        self.fix_button.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.fix_button)
        self.preview_button = QPushButton("Preview Cut", self)
        self.loop_button = QPushButton("Loop Cut", self)
        self.loop_button.setCheckable(True)
        for button in (self.preview_button, self.loop_button):
            button.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(button)

    def show_verdict(
        self, ok: bool, headline: str, detail: str = "", chips: tuple[str, ...] = (), fix: str | None = None
    ) -> None:
        tone = "ok" if ok else "warn"
        for widget in (self, self.icon):
            widget.setProperty("tone", tone)
            _repolish(widget)
        self.icon.setText("✓" if ok else "!")
        self.headline.setText(headline)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        for chip, text in zip(self.chips, (*chips, None, None, None, None)):
            chip.setText(text or "")
            chip.setVisible(bool(text))
        self.fix_button.setText(fix or "")
        self.fix_button.setVisible(fix is not None)


# -- the panel ------------------------------------------------------------------


class TrimPanel(QWidget):
    """Numbered steps -- start, length, how -- and a verdict. Owns only the range."""

    trim_changed = Signal(object)  # TrimRange
    preview_cut_requested = Signal()
    loop_cut_toggled = Signal(bool)
    set_in_requested = Signal()
    set_out_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrimPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._rate = FrameRate(Fraction(25, 1))
        self._duration: MediaTime | None = None
        self._trim: TrimRange | None = None
        self._target: TargetDuration | None = None
        self._fit_pref = Fit.SPEED  # what the user picked; a fallback never overwrites it
        self._auto = False  # the current target's fit was switched because footage ran out
        self._too_short_for: TargetDuration | None = None  # last preset the footage could not make
        self._outcome: ExportOutcome | None = None  # from the real export job, when there is one

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER + 4, Metrics.GUTTER, Metrics.GUTTER + 4, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER)
        outer.addLayout(self._build_start_and_length())
        outer.addLayout(self._build_how())

        self.banner = VerdictBanner(self)
        self.banner.fix_button.clicked.connect(self._apply_suggestion)
        self.banner.preview_button.clicked.connect(self.preview_cut_requested)
        self.banner.loop_button.toggled.connect(self.loop_cut_toggled)
        self.btn_preview, self.btn_loop = self.banner.preview_button, self.banner.loop_button
        outer.addWidget(self.banner)
        outer.addWidget(self._build_footer())

        self._refresh()

    # -- construction -----------------------------------------------------

    def _time_edit(self, on_edited: Callable[[], None], name: str) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setObjectName("TimecodeEdit")
        edit.setFixedWidth(_FIELD_WIDTH)
        edit.setAlignment(Qt.AlignCenter)
        edit.setAccessibleName(name)
        edit.editingFinished.connect(on_edited)
        return edit

    def _ghost(self, text: str, handler) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("Ghost")
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(handler)
        return button

    def _build_start_and_length(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_SM)

        start, _ = _step(1, "START", self)
        row.addLayout(start)
        self.in_edit = self._time_edit(self._on_in_edited, "IN point")
        row.addWidget(self.in_edit)
        self.btn_set_in = self._ghost("Set In (I)", self.set_in_requested)
        row.addWidget(self.btn_set_in)
        row.addSpacing(Metrics.GUTTER * 2)

        length, _ = _step(2, "LENGTH", self)
        row.addLayout(length)
        segments = QHBoxLayout()
        segments.setSpacing(0)
        self._segments: list[tuple[QPushButton, int | None]] = []
        choices = [*STANDARD_TARGET_SECONDS, None]
        for index, seconds in enumerate(choices):
            text = TargetDuration.of(seconds).label() if seconds else "Custom…"
            button = QPushButton(text, self)
            button.setObjectName("Segment")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("pos", "first" if index == 0 else "last" if index == len(choices) - 1 else "mid")
            if seconds:
                button.clicked.connect(lambda _=False, s=seconds: self.apply_target(TargetDuration.of(s)))
            else:
                button.clicked.connect(self._ask_custom_target)
            segments.addWidget(button)
            self._segments.append((button, seconds))
        row.addLayout(segments)
        row.addStretch(1)
        return row

    def _build_how(self) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setSpacing(Metrics.GUTTER_SM)
        head, self.how_label = _step(3, "HOW TO HIT IT", self)
        self.rate_note = QLabel(self)
        self.rate_note.setObjectName("MetaValueMuted")
        head.addSpacing(Metrics.GUTTER_XS)
        head.addWidget(self.rate_note)
        head.addStretch(1)
        section.addLayout(head)

        self.placeholder = QLabel(self)
        self.placeholder.setObjectName("Placeholder")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        section.addWidget(self.placeholder)

        self.cards_host = QWidget(self)
        cards = QHBoxLayout(self.cards_host)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(Metrics.GUTTER - 2)
        group = QButtonGroup(self)
        self.cards = {
            Fit.SPEED: FitCard(Fit.SPEED, "Speed up 0.1%", group, self.cards_host),
            Fit.HOLD_END: FitCard(Fit.HOLD_END, "Hold end frame", group, self.cards_host),
            Fit.HOLD_START: FitCard(Fit.HOLD_START, "Hold start frame", group, self.cards_host),
        }
        for card in self.cards.values():
            card.chosen.connect(self._on_fit_chosen)
            cards.addWidget(card, 1)
        section.addWidget(self.cards_host)
        return section

    def _build_footer(self) -> QWidget:
        footer = QWidget(self)
        footer.setObjectName("TrimFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, Metrics.GUTTER_SM, 0, 0)
        row.setSpacing(Metrics.GUTTER_SM)
        out_label = QLabel("OUT", footer)
        out_label.setObjectName("SectionLabel")
        row.addWidget(out_label)
        self.out_edit = self._time_edit(self._on_out_edited, "OUT point")
        row.addWidget(self.out_edit)
        self.btn_set_out = self._ghost("Set Out (O)", self.set_out_requested)
        self.btn_reset = self._ghost("Reset", self.reset)
        row.addWidget(self.btn_set_out)
        row.addWidget(self.btn_reset)
        row.addStretch(1)
        hint = QLabel("Dragging OUT makes it your own cut. Pick a length again to go back to exact.", footer)
        hint.setObjectName("MetaValueMuted")
        row.addWidget(hint)
        return footer

    # -- state ------------------------------------------------------------

    def set_source(self, duration: MediaTime | None, rate: FrameRate) -> None:
        """Point the panel at a new source. Resets the range to the whole file."""
        self._rate = rate
        self._duration = duration.at_rate(rate) if duration is not None else None
        self._target = None
        self._auto = False
        self._too_short_for = None
        self._outcome = None
        self._trim = TrimRange.full(self._duration) if self._duration else None
        self._refresh()

    def set_trim(self, trim: TrimRange | None) -> None:
        """Programmatic (the timeline markers): does not emit trim_changed."""
        self._trim = trim.at_rate(self._rate).clamped(self._duration) if trim else None
        self._too_short_for = None
        self._drop_stale_target()
        self._refresh()

    def trim(self) -> TrimRange | None:
        return self._trim

    def target(self) -> TargetDuration | None:
        return self._target

    def set_target(self, target: TargetDuration | None) -> None:
        """Programmatic: restore a file's preset without moving its range."""
        self._target = target
        self._drop_stale_target()
        self._refresh()

    def fit(self) -> Fit:
        return self._fit_pref

    def set_outcome(self, outcome: ExportOutcome | None) -> None:
        """What the pending export job will actually write; None without one."""
        self._outcome = outcome
        self._refresh_banner()

    def reset(self) -> None:
        if self._duration is None:
            return
        self._target = None
        self._too_short_for = None
        self._apply(TrimRange.full(self._duration))

    def apply_target(self, target: TargetDuration) -> None:
        """Cut `target` from IN exactly, falling back to a hold if the footage
        is too short to speed up. Footage too short for any fit leaves the
        range at the end of the file and says so."""
        if self._trim is None:
            return
        wanted, self._auto = resolve_fit(
            replace(target, fit=self._fit_pref), self._rate, self._available_frames()
        )
        trim = TrimRange.for_target(self._trim.in_point, wanted, self._duration)
        if trim.frame_count == wanted.frames_at(self._rate):
            self._target, self._too_short_for = wanted, None
        else:
            self._target, self._too_short_for = None, wanted
        self._apply(trim)

    def set_loop_active(self, active: bool) -> None:
        """Reflect loop state driven from elsewhere (the transport, a shortcut)."""
        if self.btn_loop.isChecked() != active:
            self.btn_loop.setChecked(active)

    def _available_frames(self) -> int | None:
        if self._duration is None or self._trim is None:
            return None
        return max(0, self._duration.frames - self._trim.in_point.frames)

    def _drop_stale_target(self) -> None:
        """A range that no longer matches its preset is the user's own cut."""
        target, trim = self._target, self._trim
        if target is not None and (trim is None or trim.frame_count != target.frames_at(self._rate)):
            self._target, self._auto = None, False

    # -- editing ----------------------------------------------------------

    def _on_in_edited(self) -> None:
        point = parse_time_input(self.in_edit.text(), self._rate)
        if point is None or self._trim is None:
            self._refresh()  # reject: the previous value is the truth
            return
        self._edit(self._trim.with_in(point))

    def _on_out_edited(self) -> None:
        point = parse_time_input(self.out_edit.text(), self._rate)
        if point is None or self._trim is None:
            self._refresh()
            return
        self._edit(self._trim.with_out(point))

    def _edit(self, trim: TrimRange) -> None:
        self._trim = trim.clamped(self._duration)
        self._too_short_for = None
        self._drop_stale_target()
        self._apply(self._trim)

    def _on_fit_chosen(self, fit: Fit) -> None:
        self._fit_pref = fit
        if self._target is not None:
            self.apply_target(self._target)  # re-cut from IN for the new fit
        else:
            self._refresh()

    def _apply_suggestion(self) -> None:
        suggestion = self._suggestion()
        if suggestion is not None:
            self.apply_target(suggestion)

    def _ask_custom_target(self) -> None:
        current = float(self._target.seconds) if self._target else 30.0
        seconds, accepted = QInputDialog.getDouble(
            self, "Custom length", "Length (seconds):", current, 0.001, 86400.0, 3
        )
        if accepted:
            self.apply_target(TargetDuration.of(Fraction(seconds).limit_denominator(1000)))
        else:
            self._refresh()  # un-check the segment the click just checked

    def _apply(self, trim: TrimRange) -> None:
        self._trim = trim.clamped(self._duration)
        self._refresh()
        self.trim_changed.emit(self._trim)

    # -- display ----------------------------------------------------------

    def _refresh(self) -> None:
        trim = self._trim
        enabled = trim is not None
        for widget in (
            self.in_edit, self.out_edit, self.btn_set_in, self.btn_set_out, self.btn_reset,
            *(button for button, _ in self._segments), self.cards_host,
        ):
            widget.setEnabled(enabled)

        if trim is None:
            for edit in (self.in_edit, self.out_edit):
                edit.setText("--:--:--.---")
                edit.setToolTip("")
        else:
            for edit, point in ((self.in_edit, trim.in_point), (self.out_edit, trim.out_point)):
                edit.setText(point.to_clock())
                edit.setToolTip(f"{point.to_timecode()}    {point.frames} f")

        target = self._target
        standard = target is not None and target.seconds in STANDARD_TARGET_SECONDS
        for button, seconds in self._segments:
            if seconds is None:
                button.setChecked(target is not None and not standard)
            else:
                button.setChecked(target is not None and target.seconds == seconds)

        self._refresh_how()
        self._refresh_banner()

    def _refresh_how(self) -> None:
        target, rate = self._target, self._rate
        if self._trim is None or target is None:
            self.how_label.setText("HOW TO HIT IT")
            self.rate_note.setText("")
            self.placeholder.setText("Pick a length in step 2 and Framecheck makes the export exact.")
            self.placeholder.show()
            self.cards_host.hide()
            return

        self.how_label.setText(f"HOW TO HIT EXACTLY {float(target.seconds):.3f} s")
        options = fit_options(target, rate, self._available_frames())
        if not options:
            self.rate_note.setText(f"source {rate.label()} fps")
            self.placeholder.setText(
                f"{float(target.seconds):.3f} s is a whole number of frames at {rate.label()} fps. "
                "Nothing to adjust."
            )
            self.placeholder.show()
            self.cards_host.hide()
            return

        self.rate_note.setText(f"source {rate.label()} fps → export {options[0].cut.rate.label()} fps")
        self.placeholder.hide()
        self.cards_host.show()
        for option in options:
            selected = option.fit is target.fit
            held = option.cut.held_frames
            frames = f"{held} extra frame{'s' if held != 1 else ''}"
            if not option.available:
                text, badge = option.reason or "", "LOCKED"
            elif option.fit is Fit.SPEED:
                text = "Picture and sound together, 0.1% faster. Nobody can see or hear it."
                badge = "BEST"
            else:
                edge = "Last" if option.fit is Fit.HOLD_END else "First"
                text = f"{edge} frame shows for {frames}. Sound keeps its speed."
                badge = "AUTO" if selected and self._auto else None
            self.cards[option.fit].show_option(text, selected, not option.available, badge)

    def _suggestion(self) -> TargetDuration | None:
        seconds = self._shown_seconds()
        if seconds is None or self._trim is None:
            return None
        return suggest_target(seconds, self._rate, self._available_frames())

    def _shown_seconds(self) -> Fraction | None:
        if self._outcome is not None and self._outcome.seconds is not None:
            return self._outcome.seconds
        if self._trim is None:
            return None
        cut = self._target.exact_cut(self._rate) if self._target else None
        return cut.seconds if cut else self._trim.duration_seconds

    def _refresh_banner(self) -> None:
        trim, target = self._trim, self._target
        seconds = self._shown_seconds()
        if trim is None or seconds is None:
            self.banner.hide()
            return
        self.banner.show()

        outcome = self._outcome
        if outcome is not None and outcome.rate:
            frames, rate = outcome.frames, FrameRate(outcome.rate)
        else:
            cut = target.exact_cut(self._rate) if target else None
            frames, rate = (cut.frames, cut.rate) if cut else (trim.frame_count, self._rate)

        wanted = target.seconds if target else None
        exact = seconds == wanted if wanted is not None else (
            seconds.denominator == 1 and int(seconds) in STANDARD_TARGET_SECONDS
        )
        length = f"{float(seconds):.3f} s"

        if exact:
            cut = target.exact_cut(self._rate) if target else None
            if cut is not None and cut.fit is not Fit.SPEED:
                edge = "end" if cut.fit is Fit.HOLD_END else "start"
                sound = f"{edge} frame held"
            else:
                sound = "sound in sync"
            chips = (f"{frames} frames", f"{rate.label()} fps", sound)
            if outcome is None:
                chips += ("pick a destination to confirm",)
            self.banner.show_verdict(True, f"Export will be exactly {length}", chips=chips)
            return

        if self._too_short_for is not None:
            footage = Fraction(self._available_frames() or 0) / self._rate.value
            headline = f"Export will be {length}: too short for {self._too_short_for.label()}"
            detail = f"Only {float(footage):.3f} s of footage after IN. Move IN earlier or pick a shorter length."
        elif target is not None:
            headline = f"Export will be {length}: not exactly {target.label()}"
            detail = "This destination's frame-rate rules rule out an exact cut here. See CONFORM for what it will do."
        else:
            headline = f"Export will be {length}: not a standard length"
            detail = "Publishers reject spots that aren't exactly :06, :15, :30, :60 or :90."
        suggestion = self._suggestion()
        fix = None
        if suggestion is not None and (wanted is None or suggestion.seconds != wanted):
            fix = f"Make it {suggestion.label()}"
        self.banner.show_verdict(False, headline, detail, fix=fix)
