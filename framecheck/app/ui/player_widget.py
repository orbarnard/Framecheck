"""The preview player: video surface, scrubber, and transport controls.

The video is the workspace, so the surface takes all available space and the
controls sit under it in a single compact row.

Icons are painted rather than drawn from a font. Symbol fonts on Windows render
transport glyphs inconsistently (some at emoji weight, some missing), and a
media tool with a wrong-looking play button reads as unfinished.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..media.playback import PlaybackEngine
from ..models.media_time import FrameRate, MediaTime
from ..models.trim import TrimRange
from .theme import Color, Metrics
from .timeline_widget import TimecodeLabel, TimelineWidget


class Icon(Enum):
    PLAY = "play"
    PAUSE = "pause"
    STEP_BACK = "step_back"
    STEP_FORWARD = "step_forward"
    JUMP_BACK = "jump_back"
    JUMP_FORWARD = "jump_forward"
    START = "start"
    TAIL = "tail"
    VOLUME = "volume"
    MUTED = "muted"
    MARK_IN = "mark_in"
    MARK_OUT = "mark_out"
    LOOP = "loop"


class IconButton(QPushButton):
    """A transport button that paints its own vector glyph."""

    def __init__(
        self,
        icon: Icon,
        tooltip: str,
        parent: QWidget | None = None,
        primary: bool = False,
    ) -> None:
        super().__init__(parent)
        self._icon = icon
        self.setObjectName("TransportPrimary" if primary else "Transport")
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # transport must never steal the shortcuts

    def set_icon(self, icon: Icon) -> None:
        if icon is not self._icon:
            self._icon = icon
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)  # stylesheet paints the background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(Color.TEXT_DISABLED if not self.isEnabled() else self.palette().buttonText().color().name())
        # The stylesheet drives the text colour; read it back so hover states apply.
        color = QColor(Color.TEXT_DISABLED) if not self.isEnabled() else QColor(Color.TEXT)
        _paint_icon(painter, self.rect(), self._icon, color)
        painter.end()


def _paint_icon(painter: QPainter, bounds, icon: Icon, color: QColor) -> None:
    """Draw `icon` centred in `bounds` at a fixed 12px optical size."""
    size = 12.0
    box = QRectF(
        bounds.center().x() - size / 2 + 0.5,
        bounds.center().y() - size / 2 + 0.5,
        size,
        size,
    )
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    stroke = QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    if icon is Icon.PLAY:
        path = QPainterPath()
        path.moveTo(box.left() + 1.5, box.top())
        path.lineTo(box.right() - 0.5, box.center().y())
        path.lineTo(box.left() + 1.5, box.bottom())
        path.closeSubpath()
        painter.drawPath(path)
    elif icon is Icon.PAUSE:
        bar = box.width() * 0.28
        painter.drawRoundedRect(QRectF(box.left() + 1, box.top(), bar, box.height()), 1, 1)
        painter.drawRoundedRect(QRectF(box.right() - bar - 1, box.top(), bar, box.height()), 1, 1)
    elif icon in (Icon.STEP_BACK, Icon.STEP_FORWARD):
        forward = icon is Icon.STEP_FORWARD
        path = QPainterPath()
        if forward:
            path.moveTo(box.left() + 1, box.top())
            path.lineTo(box.right() - 3, box.center().y())
            path.lineTo(box.left() + 1, box.bottom())
        else:
            path.moveTo(box.right() - 1, box.top())
            path.lineTo(box.left() + 3, box.center().y())
            path.lineTo(box.right() - 1, box.bottom())
        path.closeSubpath()
        painter.drawPath(path)
        bar_x = box.right() - 1.5 if forward else box.left()
        painter.drawRoundedRect(QRectF(bar_x, box.top(), 1.6, box.height()), 0.8, 0.8)
    elif icon in (Icon.JUMP_BACK, Icon.JUMP_FORWARD):
        forward = icon is Icon.JUMP_FORWARD
        for index in range(2):
            offset = index * (box.width() * 0.42)
            path = QPainterPath()
            if forward:
                left = box.left() + offset
                path.moveTo(left, box.top() + 1)
                path.lineTo(left + box.width() * 0.45, box.center().y())
                path.lineTo(left, box.bottom() - 1)
            else:
                right = box.right() - offset
                path.moveTo(right, box.top() + 1)
                path.lineTo(right - box.width() * 0.45, box.center().y())
                path.lineTo(right, box.bottom() - 1)
            path.closeSubpath()
            painter.drawPath(path)
    elif icon is Icon.START:
        painter.drawRoundedRect(QRectF(box.left(), box.top(), 1.6, box.height()), 0.8, 0.8)
        path = QPainterPath()
        path.moveTo(box.right(), box.top() + 1)
        path.lineTo(box.left() + 3, box.center().y())
        path.lineTo(box.right(), box.bottom() - 1)
        path.closeSubpath()
        painter.drawPath(path)
    elif icon is Icon.TAIL:
        # Bracket around the tail of a bar: "jump to the last few seconds".
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(box.left(), box.center().y()), QPointF(box.right() - 3.5, box.center().y()))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(box.right() - 2, box.top() + 1, 2, box.height() - 2), 1, 1)
    elif icon in (Icon.MARK_IN, Icon.MARK_OUT):
        # A bracket facing the kept side: "[" marks IN, "]" marks OUT.
        opening = icon is Icon.MARK_IN
        stem_x = box.left() if opening else box.right() - 2
        painter.drawRoundedRect(QRectF(stem_x, box.top(), 2, box.height()), 1, 1)
        arm = box.width() * 0.45
        arm_x = box.left() + 2 if opening else box.right() - 2 - arm
        painter.drawRect(QRectF(arm_x, box.top(), arm, 1.6))
        painter.drawRect(QRectF(arm_x, box.bottom() - 1.6, arm, 1.6))
    elif icon is Icon.LOOP:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(stroke)
        ring = QRectF(box.left(), box.top() + 2, box.width(), box.height() - 4)
        painter.drawRoundedRect(ring, 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        path = QPainterPath()
        tip = box.center().x()
        path.moveTo(tip, box.top() - 0.5)
        path.lineTo(tip + 3, box.top() + 2)
        path.lineTo(tip, box.top() + 4.5)
        path.closeSubpath()
        painter.drawPath(path)
    elif icon in (Icon.VOLUME, Icon.MUTED):
        path = QPainterPath()
        path.moveTo(box.left(), box.center().y() - 2)
        path.lineTo(box.left() + 3, box.center().y() - 2)
        path.lineTo(box.left() + 6, box.top() + 1)
        path.lineTo(box.left() + 6, box.bottom() - 1)
        path.lineTo(box.left() + 3, box.center().y() + 2)
        path.lineTo(box.left(), box.center().y() + 2)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(stroke)
        if icon is Icon.MUTED:
            painter.drawLine(
                QPointF(box.right() - 4, box.center().y() - 2.5),
                QPointF(box.right(), box.center().y() + 2.5),
            )
            painter.drawLine(
                QPointF(box.right() - 4, box.center().y() + 2.5),
                QPointF(box.right(), box.center().y() - 2.5),
            )
        else:
            painter.drawArc(
                QRectF(box.left() + 4, box.center().y() - 4, 7, 8), -60 * 16, 120 * 16
            )


class VideoSurface(QWidget):
    """The native child window libmpv renders into.

    Qt must not paint over this area, and mpv must own the handle for its whole
    lifetime -- hence the native-window attributes and opaque paint event.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:
        # Only reached before mpv attaches, or if it fails to.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Color.VIDEO_BACKDROP))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class PlayerWidget(QWidget):
    """Video surface plus transport. Owns the playback engine."""

    error = Signal(str)
    position_changed = Signal(object)  # MediaTime
    trim_changed = Signal(object)  # TrimRange

    JUMP_SECONDS = 1.0
    TAIL_SECONDS = 5.0
    CUT_WINDOW_SECONDS = 2.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = PlaybackEngine(self)
        self._attached = False
        self._resume_after_scrub = False
        self._muted = False
        self._trim: TrimRange | None = None
        self._preview_stop: MediaTime | None = None
        self._looping = False
        self._loop_seeking = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.surface = VideoSurface(self)
        self.surface.clicked.connect(self.toggle_play)

        # Overlay for the states where there is no picture to show. It is an
        # ordinary Qt child: mpv is configured without force-window, so no
        # native render window exists to composite above it while idle.
        self.overlay = QLabel(self.surface)
        self.overlay.setAlignment(Qt.AlignCenter)
        self.overlay.setWordWrap(True)
        self.overlay.setStyleSheet(
            f"color: {Color.TEXT_TERTIARY}; background-color: {Color.VIDEO_BACKDROP}; font-size: 12px;"
        )
        self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.surface, 1)
        layout.addWidget(self._build_controls())

        self.engine.position_changed.connect(self._on_position)
        self.engine.duration_changed.connect(self._on_duration)
        self.engine.paused_changed.connect(self._on_paused)
        self.engine.error.connect(self.error)
        self.engine.end_reached.connect(self._on_end)

        self.set_controls_enabled(False)
        self.btn_play.set_icon(Icon.PLAY)
        self.show_message("No file loaded")

    # -- construction -----------------------------------------------------

    def _build_controls(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("TransportBar")
        bar.setStyleSheet(
            f"#TransportBar {{ background-color: {Color.SURFACE}; "
            f"border-top: 1px solid {Color.SEPARATOR}; }}"
        )

        outer = QVBoxLayout(bar)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER_SM, Metrics.GUTTER, Metrics.GUTTER_SM)
        outer.setSpacing(Metrics.GUTTER_XS)

        self.timeline = TimelineWidget(bar)
        self.timeline.position_requested.connect(self._on_scrub_position)
        self.timeline.scrub_started.connect(self._on_scrub_started)
        self.timeline.scrub_finished.connect(self._on_scrub_finished)
        self.timeline.trim_changed.connect(self._on_timeline_trim)
        outer.addWidget(self.timeline)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Metrics.GUTTER_XS)

        self.btn_start = IconButton(Icon.START, "Go to start  (Home)", bar)
        self.btn_jump_back = IconButton(Icon.JUMP_BACK, "Back 1 second  (Left)", bar)
        self.btn_step_back = IconButton(Icon.STEP_BACK, "Previous frame  (,)", bar)
        self.btn_play = IconButton(Icon.PLAY, "Play / pause  (Space)", bar, primary=True)
        self.btn_step_forward = IconButton(Icon.STEP_FORWARD, "Next frame  (.)", bar)
        self.btn_jump_forward = IconButton(Icon.JUMP_FORWARD, "Forward 1 second  (Right)", bar)
        self.btn_tail = IconButton(Icon.TAIL, "Last 5 seconds  (End)", bar)
        self.btn_mark_in = IconButton(Icon.MARK_IN, "Set IN here  (I)", bar)
        self.btn_mark_out = IconButton(Icon.MARK_OUT, "Set OUT here  (O)", bar)
        self.btn_loop = IconButton(Icon.LOOP, "Loop the cut point", bar)
        self.btn_loop.setCheckable(True)

        self.btn_start.clicked.connect(self.go_to_start)
        self.btn_jump_back.clicked.connect(lambda: self.jump(-self.JUMP_SECONDS))
        self.btn_step_back.clicked.connect(lambda: self.step_frames(-1))
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_step_forward.clicked.connect(lambda: self.step_frames(1))
        self.btn_jump_forward.clicked.connect(lambda: self.jump(self.JUMP_SECONDS))
        self.btn_tail.clicked.connect(self.go_to_tail)
        self.btn_mark_in.clicked.connect(self.set_in)
        self.btn_mark_out.clicked.connect(self.set_out)
        self.btn_loop.clicked.connect(self.toggle_loop_cut)

        self._transport_buttons = (
            self.btn_start,
            self.btn_jump_back,
            self.btn_step_back,
            self.btn_play,
            self.btn_step_forward,
            self.btn_jump_forward,
            self.btn_tail,
            self.btn_mark_in,
            self.btn_mark_out,
            self.btn_loop,
        )
        for button in self._transport_buttons:
            row.addWidget(button)

        row.addSpacing(Metrics.GUTTER)
        self.timecode = TimecodeLabel(bar)
        row.addWidget(self.timecode)
        row.addStretch(1)

        self.btn_mute = IconButton(Icon.VOLUME, "Mute  (M)", bar)
        self.btn_mute.clicked.connect(self.toggle_mute)
        row.addWidget(self.btn_mute)

        self.volume_slider = QSlider(Qt.Horizontal, bar)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setFocusPolicy(Qt.NoFocus)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        row.addWidget(self.volume_slider)

        outer.addLayout(row)
        return bar

    # -- lifecycle --------------------------------------------------------

    def attach_engine(self) -> bool:
        """Create the mpv instance. Call once the widget is shown.

        Deferred rather than done in __init__ because winId() must resolve to a
        real HWND, which only happens after the widget is realised.
        """
        if self._attached:
            return True
        if not self.engine.available:
            self.show_message(self.engine.unavailable_reason)
            return False
        self._attached = self.engine.attach(int(self.surface.winId()))
        if self._attached:
            self.show_message("No file loaded")
        return self._attached

    def shutdown(self) -> None:
        self.engine.shutdown()

    # -- media ------------------------------------------------------------

    def load(self, path: Path, rate: FrameRate | None = None) -> None:
        if not self._attached and not self.attach_engine():
            return
        self.hide_message()
        self._cancel_cut_modes()
        self._trim = None
        self.timeline.clear()
        self.timecode.clear()
        if rate is not None:
            self.timeline.set_rate(rate)
        self.engine.load(path, rate)
        self.set_controls_enabled(True)

    def set_frame_rate(self, rate: FrameRate) -> None:
        """Apply the probed rate once it arrives, without reloading."""
        self.engine.set_frame_rate(rate)
        self.timeline.set_rate(rate)

    def clear(self) -> None:
        self.engine.unload()
        self._cancel_cut_modes()
        self._trim = None
        self.timeline.clear()
        self.timecode.clear()
        self.set_controls_enabled(False)
        self.btn_play.set_icon(Icon.PLAY)
        self.show_message("No file loaded")

    # -- transport --------------------------------------------------------

    def toggle_play(self) -> None:
        if self._controls_enabled:
            self._cancel_cut_modes()
            self.engine.toggle_pause()

    def pause(self) -> None:
        self.engine.pause()

    def step_frames(self, delta: int) -> None:
        if self._controls_enabled:
            self.engine.step_frames(delta)

    def jump(self, seconds: float) -> None:
        if self._controls_enabled:
            self.engine.jump_seconds(seconds)

    def go_to_start(self) -> None:
        if self._controls_enabled:
            self.engine.seek(MediaTime.zero(self.engine.rate))

    def go_to_tail(self) -> None:
        """Jump to the last five seconds -- where delivery problems live."""
        if self._controls_enabled:
            self.engine.seek_to_end_window(self.TAIL_SECONDS)

    def go_to_end(self) -> None:
        duration = self.engine.duration
        if self._controls_enabled and duration is not None:
            self.engine.seek(duration.offset_frames(-1))

    # -- trim -------------------------------------------------------------

    def trim(self) -> TrimRange | None:
        return self._trim

    def set_trim(self, trim: TrimRange | None) -> None:
        """Programmatic: updates the scrubber without re-emitting trim_changed."""
        self._trim = trim
        self.timeline.set_trim(trim)

    def set_in(self) -> None:
        """IN = the current playhead."""
        self._move_point(in_point=self.engine.position)

    def set_out(self) -> None:
        """OUT = one frame past the playhead, because OUT is exclusive."""
        self._move_point(out_point=self.engine.position.offset_frames(1))

    def _move_point(
        self, in_point: MediaTime | None = None, out_point: MediaTime | None = None
    ) -> None:
        if not self._controls_enabled:
            return
        duration = self.engine.duration
        base = self._trim
        if base is None:
            if duration is None:
                return
            base = TrimRange.full(duration)
        trim = base.with_in(in_point) if in_point is not None else base.with_out(out_point)
        self.set_trim(trim.clamped(duration))
        self.trim_changed.emit(self._trim)

    def _cut_out_point(self) -> MediaTime | None:
        """OUT for the cut-review modes, falling back to the source end."""
        if self._trim is not None:
            return self._trim.out_point
        return self.engine.duration

    def _cut_window_start(self, out: MediaTime) -> MediaTime:
        start = out.offset_seconds(-self.CUT_WINDOW_SECONDS)
        if self._trim is not None:
            start = start.clamped(lo=self._trim.in_point)
        return start

    def preview_cut(self) -> None:
        """Play the last two seconds up to OUT, then stop dead on it."""
        out = self._cut_out_point()
        if not self._controls_enabled or out is None:
            return
        self.set_loop_cut(False)
        self._preview_stop = out
        self.engine.seek(self._cut_window_start(out))
        self.engine.play()

    def toggle_loop_cut(self) -> None:
        self.set_loop_cut(not self._looping)

    def set_loop_cut(self, enabled: bool) -> None:
        """Loop [OUT - 2s, OUT] until something else takes over."""
        out = self._cut_out_point()
        enabled = bool(enabled) and self._controls_enabled and out is not None
        self._looping = enabled
        self._loop_seeking = False
        self.btn_loop.setChecked(enabled)
        if not enabled:
            return
        self._preview_stop = None
        assert out is not None
        self.engine.seek(self._cut_window_start(out))
        self.engine.play()

    def _cancel_cut_modes(self) -> None:
        self._preview_stop = None
        if self._looping:
            self.set_loop_cut(False)

    def _on_timeline_trim(self, trim: TrimRange) -> None:
        self._trim = trim
        self.trim_changed.emit(trim)

    def _handle_cut_modes(self, position: MediaTime) -> None:
        """Enforce the OUT boundary from real positions, never from a timer."""
        if self._preview_stop is not None and position.frames >= self._preview_stop.frames:
            stop = self._preview_stop
            self._preview_stop = None
            self.engine.pause()
            self.engine.seek(stop)
            return
        if not self._looping:
            return
        out = self._cut_out_point()
        if out is None:
            return
        if position.frames < out.frames:
            self._loop_seeking = False
        elif not self._loop_seeking:
            # mpv keeps reporting the old position for a poll or two after a
            # seek; without this latch the window restarts several times.
            self._loop_seeking = True
            self.engine.seek(self._cut_window_start(out))
            self.engine.play()

    def toggle_mute(self) -> None:
        self.set_muted(not self._muted)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.engine.set_muted(self._muted)
        self.btn_mute.set_icon(Icon.MUTED if self._muted else Icon.VOLUME)

    @property
    def is_muted(self) -> bool:
        return self._muted

    def set_volume(self, value: int) -> None:
        self.volume_slider.setValue(int(value))

    @property
    def volume(self) -> int:
        return self.volume_slider.value()

    # -- state ------------------------------------------------------------

    def set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        for button in self._transport_buttons:
            button.setEnabled(enabled)

    def show_message(self, text: str) -> None:
        self.overlay.setText(text)
        self.overlay.setGeometry(self.surface.rect())
        self.overlay.show()
        self.overlay.raise_()

    def hide_message(self) -> None:
        self.overlay.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.surface.rect())

    # -- engine signals ---------------------------------------------------

    def _on_position(self, position: MediaTime) -> None:
        self.timeline.set_position(position)
        self.timecode.set_times(position, self.engine.duration)
        self._handle_cut_modes(position)
        self.position_changed.emit(position)

    def _on_duration(self, duration: MediaTime | None) -> None:
        self.timeline.set_duration(duration)
        self.timecode.set_times(self.engine.position, duration)

    def _on_paused(self, paused: bool) -> None:
        # With no file loaded mpv sits unpaused and idle; showing a pause glyph
        # then would claim something is playing.
        if not self._controls_enabled:
            self.btn_play.set_icon(Icon.PLAY)
            return
        self.btn_play.set_icon(Icon.PLAY if paused else Icon.PAUSE)

    def _on_end(self) -> None:
        self.btn_play.set_icon(Icon.PLAY)

    # -- scrubbing --------------------------------------------------------

    def _on_scrub_started(self) -> None:
        # Pause while dragging so exact seeks are not fighting playback, then
        # restore whatever the user had going.
        self._cancel_cut_modes()
        self._resume_after_scrub = not self.engine.is_paused
        self.engine.pause()

    def _on_scrub_position(self, target: MediaTime) -> None:
        self.engine.seek(target)

    def _on_scrub_finished(self) -> None:
        if self._resume_after_scrub:
            self.engine.play()
        self._resume_after_scrub = False

    def _on_volume_changed(self, value: int) -> None:
        self.engine.set_volume(value)
        if value == 0:
            self.btn_mute.set_icon(Icon.MUTED)
        elif not self._muted:
            self.btn_mute.set_icon(Icon.VOLUME)
