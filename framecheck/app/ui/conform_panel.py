"""Conform panel: what the export will change, before anything is encoded.

A pure renderer over ExportJob / ConformAction / LoudnessResult. It decides
nothing; the job it is handed already holds every decision.
"""

from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.export_job import ConformAction, ExportJob, LoudnessResult
from ..models.media_time import FrameRate, format_seconds
from ..models.profile import FrameRateBehavior
from .theme import Color, Metrics

_DASH = "--"
_KEY_WIDTH = 72
_ARROW = "→"


def _rate_text(value: Fraction | None) -> str:
    if not value or value <= 0:
        return _DASH
    return f"{FrameRate(value).label()} fps"


def _khz(hertz: int | None) -> str | None:
    if not hertz:
        return None
    return f"{hertz / 1000:g} kHz"


def _joined(*parts: str | None) -> str:
    kept = [p for p in parts if p]
    return " · ".join(kept) if kept else _DASH


class ConformPanel(QWidget):
    """Right-hand CONFORM panel. States: empty, job."""

    normalize_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConformPanel")

        self._job: ExportJob | None = None
        self._actions: tuple[ConformAction, ...] = ()
        self._measured: LoudnessResult | None = None
        self._target_lkfs: float | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER_SM)

        # The inspector tab bar names this panel; an in-panel header repeats it.
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_empty_page())

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._stack.addWidget(self._scroll)

        self._rebuild()
        self.show_empty()

    # -- construction ----------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addStretch(1)

        title = QLabel("Nothing to conform")
        title.setObjectName("MetaValueMuted")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Choose a destination to see what would change.")
        hint.setObjectName("MetaKey")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)
        return page

    # -- api -------------------------------------------------------------

    def set_job(self, job: ExportJob | None) -> None:
        self._job = job
        if job is None:
            self._actions = ()
            self._measured = None
            self._target_lkfs = None
            self.show_empty()
            self._rebuild()
            return
        self._actions = job.actions
        self._measured = job.source_loudness
        self._target_lkfs = job.target.audio.loudness_lkfs
        self._rebuild()
        self._stack.setCurrentIndex(1)

    def set_actions(self, actions: list[ConformAction]) -> None:
        self._actions = tuple(actions or ())
        self._rebuild()

    def set_loudness(self, measured: LoudnessResult | None, target_lkfs: float | None) -> None:
        self._measured = measured
        self._target_lkfs = target_lkfs
        self._rebuild()

    def show_empty(self) -> None:
        self._stack.setCurrentIndex(0)

    # -- rendering -------------------------------------------------------

    def _rebuild(self) -> None:
        body = QWidget()
        body.setObjectName("ConformPanel")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, Metrics.GUTTER_SM, 0)
        column.setSpacing(Metrics.GUTTER)

        job = self._job
        if job is not None:
            column.addLayout(self._build_comparison(job))
        column.addLayout(self._build_actions())
        column.addLayout(self._build_loudness())
        column.addStretch(1)

        self._scroll.setWidget(body)

    def _section(self, column: QVBoxLayout, title: str) -> QVBoxLayout:
        """Heading, hairline, and an empty rows layout -- as INSPECT does it."""
        label = QLabel(title)
        label.setObjectName("SectionLabel")
        column.addWidget(label)

        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedHeight(1)
        column.addWidget(separator)

        rows = QVBoxLayout()
        rows.setContentsMargins(0, Metrics.GUTTER_XS, 0, 0)
        rows.setSpacing(Metrics.GUTTER_XS)
        column.addLayout(rows)
        return rows

    # -- source / output comparison --------------------------------------

    def _build_comparison(self, job: ExportJob) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER_XS)
        rows = self._section(column, f"SOURCE {_ARROW} OUTPUT")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(Metrics.GUTTER_SM)
        grid.setVerticalSpacing(Metrics.GUTTER_XS)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        for row, (key, source, output) in enumerate(_comparison_fields(job)):
            key_label = QLabel(key)
            key_label.setObjectName("MetaKey")
            key_label.setFixedWidth(_KEY_WIDTH)
            key_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            grid.addWidget(key_label, row, 0)

            source_label = QLabel(source)
            source_label.setWordWrap(True)
            source_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            source_label.setStyleSheet(f"color: {Color.TEXT_SECONDARY}; font-size: 11px;")
            grid.addWidget(source_label, row, 1)

            arrow = QLabel(_ARROW)
            arrow.setObjectName("MetaValueMuted")
            arrow.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            grid.addWidget(arrow, row, 2)

            changed = source != output and source != _DASH and output != _DASH
            output_label = QLabel(output)
            output_label.setWordWrap(True)
            output_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            if changed:
                output_label.setStyleSheet(
                    f"color: {Color.ACCENT}; font-size: 11px; font-weight: 600;"
                )
            else:
                output_label.setStyleSheet(f"color: {Color.TEXT_TERTIARY}; font-size: 11px;")
            grid.addWidget(output_label, row, 3)

        rows.addLayout(grid)
        return column

    # -- actions ----------------------------------------------------------

    def _build_actions(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER_XS)
        rows = self._section(column, "WHAT WILL CHANGE")

        if not self._actions:
            message = QLabel("No changes needed — the source already meets this specification.")
            message.setWordWrap(True)
            message.setStyleSheet(f"color: {Color.PASS}; font-size: 11px;")
            rows.addWidget(message)
            return column

        # Changes to picture or sound outrank container-level rewrites.
        ordered = sorted(self._actions, key=lambda a: not a.affects_picture_or_sound)
        for action in ordered:
            rows.addWidget(self._build_action(action))
        return column

    def _build_action(self, action: ConformAction) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(Metrics.GUTTER_SM)

        marker = QLabel("•" if action.affects_picture_or_sound else "")
        marker.setFixedWidth(8)
        marker.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        if action.affects_picture_or_sound:
            marker.setStyleSheet(f"color: {Color.WARNING}; font-size: 11px;")
            marker.setToolTip("Alters picture or sound, not just the container")
        top.addWidget(marker, 0, Qt.AlignTop)

        label = QLabel(action.label or "Change")
        label.setObjectName("MetaKey")
        label.setFixedWidth(_KEY_WIDTH)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        top.addWidget(label, 0, Qt.AlignTop)

        change = QLabel(f"{action.from_value or _DASH} {_ARROW} {action.to_value or _DASH}")
        change.setObjectName("MetaValueMono")
        change.setWordWrap(True)
        change.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        change.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        change.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(change, 1)

        layout.addLayout(top)

        if action.reason:
            reason = QLabel(action.reason)
            reason.setObjectName("MetaValueMuted")
            reason.setWordWrap(True)
            indent = QHBoxLayout()
            indent.setContentsMargins(0, 0, 0, 0)
            indent.setSpacing(0)
            indent.addSpacing(8 + Metrics.GUTTER_SM)
            indent.addWidget(reason, 1)
            layout.addLayout(indent)

        return row

    # -- loudness ---------------------------------------------------------

    def _build_loudness(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Metrics.GUTTER_XS)
        rows = self._section(column, "LOUDNESS")

        measured = self._measured
        target = self._target_lkfs

        has_audio = self._job.source_info.has_audio if self._job else True
        if measured is None and not has_audio:
            # A silent file is never "measuring": there is nothing to measure.
            source_text = "No audio stream"
            delta_text = _DASH
        elif measured is None:
            # Never blank, never zero: an unmeasured file is not a quiet one.
            source_text = "Measuring…"
            delta_text = "Measuring…"
        elif measured.estimated:
            # Say so plainly. A sampled reading is useful but must not be taken
            # for a full measurement; export re-measures before normalising.
            window = f" from {measured.analyzed_seconds:.0f}s" if measured.analyzed_seconds else ""
            source_text = f"~{measured.integrated_lufs:.1f} LUFS (estimate{window})"
            delta_text = (
                f"~{measured.delta_to(target):+.1f} dB" if target is not None else _DASH
            )
        else:
            source_text = f"{measured.integrated_lufs:.1f} LUFS"
            delta_text = f"{measured.delta_to(target):+.1f} dB" if target is not None else _DASH

        target_text = f"{target:.1f} LKFS" if target is not None else "Not specified"

        self._add_row(rows, "Source loudness", source_text)
        self._add_row(rows, "Target loudness", target_text)
        self._add_row(rows, "Expected change", delta_text)

        # Offering normalisation before the analysis pass lands would silently
        # downgrade it to a single-pass approximation, which is not accurate
        # enough for delivery. Hold the control until the measurement exists.
        awaiting_measurement = measured is None and has_audio
        label = (
            "Normalize on export  (measuring…)"
            if awaiting_measurement
            else "Normalize on export"
        )
        checkbox = QCheckBox(label)
        checkbox.setChecked(bool(self._job.normalize_loudness) if self._job else False)
        checkbox.setEnabled(target is not None and not awaiting_measurement and has_audio)
        if target is None:
            checkbox.setToolTip("This destination specifies no loudness target.")
        elif not has_audio:
            checkbox.setToolTip("This file has no audio to normalize.")
        elif awaiting_measurement:
            checkbox.setToolTip(
                "Waiting for the loudness analysis pass. Normalising without it "
                "would be an approximation rather than an exact correction."
            )
        # Connected after setChecked so restoring state never looks like a click.
        checkbox.toggled.connect(self.normalize_toggled)
        rows.addWidget(checkbox)
        return column

    def _add_row(self, rows: QVBoxLayout, key: str, value: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        key_label = QLabel(key)
        key_label.setObjectName("MetaKey")
        key_label.setFixedWidth(_KEY_WIDTH + 24)
        layout.addWidget(key_label)

        value_label = QLabel(value)
        value_label.setObjectName("MetaValueMono" if value != _DASH else "MetaValueMuted")
        value_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout.addWidget(value_label, 1)
        rows.addWidget(row)


def _comparison_fields(job: ExportJob) -> list[tuple[str, str, str]]:
    """The five fields a user checks before pressing export: key, source, output."""
    info = job.source_info
    target = job.target
    video = info.video
    audio = info.audio

    codec_src = video.codec.upper() if video and video.codec else _DASH
    codec_out = target.video_codec.upper() if target.video_codec else _DASH

    res_src = (video.resolution if video else None) or _DASH
    res_out = f"{target.width}x{target.height}" if target.width and target.height else res_src

    rate = info.frame_rate
    rate_src = _rate_text(rate.value if rate else None)
    if video is not None and video.likely_variable_frame_rate:
        rate_src = f"{rate_src} VFR"

    preferred = target.preferred_frame_rate
    forced = target.frame_rate_behavior is FrameRateBehavior.FORCE
    disallowed = bool(
        rate and target.allowed_frame_rates and rate.value not in target.allowed_frame_rates
    )
    out_rate = preferred if (preferred and (forced or disallowed)) else (rate.value if rate else None)
    rate_out = _rate_text(out_rate)
    if rate_out != _DASH and target.constant_frame_rate:
        rate_out = f"{rate_out} CFR"

    if audio is None:
        audio_src = "No audio"
    else:
        audio_src = _joined(
            audio.codec.upper() if audio.codec else None,
            f"{audio.channels} ch" if audio.channels else None,
            _khz(audio.sample_rate_hz),
        )
    audio_target = target.audio
    if audio is None:
        # The encoder emits -an for a source with no audio; promising the
        # profile's AAC track here would contradict the action list below and
        # the file the user actually receives.
        audio_out = "None (silent)"
    else:
        audio_out = _joined(
            audio_target.codec.upper() if audio_target.codec else None,
            f"{audio_target.channels} ch" if audio_target.channels else None,
            _khz(audio_target.sample_rate_hz),
        )

    source_duration = info.duration
    duration_src = source_duration.to_clock() if source_duration else format_seconds(info.duration_seconds)
    if job.trim is not None and job.is_trimmed:
        duration_out = job.trim.duration.to_clock()
    else:
        duration_out = duration_src

    return [
        ("Codec", codec_src, codec_out),
        ("Resolution", res_src, res_out),
        ("Frame rate", rate_src, rate_out),
        ("Audio", audio_src, audio_out),
        ("Duration", duration_src, duration_out),
    ]
