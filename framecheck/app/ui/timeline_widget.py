"""The scrubber.

Frame-indexed from the outset: positions in and out of this widget are
MediaTime, and hit-testing converts pixels to a frame index, not to seconds.
The IN/OUT markers and the removed-range shade sit on top of the same
coordinate helpers.
"""

from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..models.media_time import FrameRate, MediaTime, Rounding
from ..models.trim import TrimRange
from .theme import Color, mono_font_family

TRACK_HEIGHT = 5
TRACK_HEIGHT_HOVER = 7
PLAYHEAD_WIDTH = 2
WIDGET_HEIGHT = 44
EDGE_PADDING = 10
MARKER_WIDTH = 4
MARKER_GRAB = 6  # pixels either side of a marker centre that count as a hit


class TimelineWidget(QWidget):
    """A single-range scrubber over the whole source duration."""

    # Emitted continuously while dragging so the picture tracks the pointer.
    position_requested = Signal(object)  # MediaTime
    scrub_started = Signal()
    scrub_finished = Signal()
    trim_changed = Signal(object)  # TrimRange, emitted live while dragging
    marker_drag_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rate = FrameRate(Fraction(25, 1))
        self._duration: MediaTime | None = None
        self._position = MediaTime.zero(self._rate)
        self._trim: TrimRange | None = None
        self._markers_enabled = True
        self._drag_marker: str | None = None  # "in" | "out"
        self._hover_x: int | None = None
        self._scrubbing = False

        self.setFixedHeight(WIDGET_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # keyboard shortcuts stay with the window

    # -- state ------------------------------------------------------------

    def set_rate(self, rate: FrameRate) -> None:
        self._rate = rate
        self._position = self._position.at_rate(rate)
        if self._duration is not None:
            self._duration = self._duration.at_rate(rate)
        if self._trim is not None:
            self._trim = self._trim.at_rate(rate)
        self.update()

    def set_duration(self, duration: MediaTime | None) -> None:
        self._duration = duration
        if duration is not None:
            self._rate = duration.rate
        self.update()

    def set_position(self, position: MediaTime) -> None:
        if position == self._position:
            return
        self._position = position
        self.update()

    def set_trim(self, trim: TrimRange | None) -> None:
        """Show `trim`. Programmatic, so it does not emit trim_changed."""
        self._trim = trim.at_rate(self._rate) if trim is not None else None
        self.update()

    def trim(self) -> TrimRange | None:
        return self._trim

    def set_markers_enabled(self, enabled: bool) -> None:
        """Hide the markers and the shade without discarding the range."""
        self._markers_enabled = bool(enabled)
        if not enabled:
            self._drag_marker = None
        self.update()

    def clear(self) -> None:
        self._duration = None
        self._position = MediaTime.zero(self._rate)
        self._trim = None
        self._drag_marker = None
        self._hover_x = None
        self.update()

    @property
    def has_media(self) -> bool:
        return self._duration is not None and self._duration.frames > 0

    # -- coordinates ------------------------------------------------------

    def _track_rect(self) -> QRectF:
        height = TRACK_HEIGHT_HOVER if (self._hover_x is not None or self._scrubbing) else TRACK_HEIGHT
        top = (self.height() - height) / 2
        return QRectF(EDGE_PADDING, top, max(1.0, self.width() - 2 * EDGE_PADDING), height)

    def frame_to_x(self, frames: int) -> float:
        """Pixel centre of a frame index."""
        if not self.has_media:
            return EDGE_PADDING
        track = self._track_rect()
        assert self._duration is not None
        ratio = frames / max(1, self._duration.frames)
        return track.left() + ratio * track.width()

    def x_to_time(self, x: float) -> MediaTime:
        """Frame under a pixel. Floors, so a click never lands past the frame."""
        if not self.has_media:
            return MediaTime.zero(self._rate)
        assert self._duration is not None
        track = self._track_rect()
        ratio = (x - track.left()) / max(1.0, track.width())
        ratio = min(max(ratio, 0.0), 1.0)
        frames = int(ratio * self._duration.frames)
        return MediaTime(min(frames, self._duration.frames), self._duration.rate)

    def _marker_at(self, x: float) -> str | None:
        """Which marker a pointer at `x` grabs, if any."""
        if self._trim is None or not self._markers_enabled or not self.has_media:
            return None
        in_dx = abs(x - self.frame_to_x(self._trim.in_point.frames))
        out_dx = abs(x - self.frame_to_x(self._trim.out_point.frames))
        if min(in_dx, out_dx) > MARKER_GRAB:
            return None
        # Ties go to OUT: the tail is what gets nudged, over and over.
        return "out" if out_dx <= in_dx else "in"

    # -- interaction ------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or not self.has_media:
            return
        marker = self._marker_at(event.position().x())
        if marker is not None:
            self._drag_marker = marker
            self._drag_marker_to(event)
            return
        self._scrubbing = True
        self.scrub_started.emit()
        self._emit_position_for(event.position().x())
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover_x = int(event.position().x())
        if self._drag_marker is not None:
            self._drag_marker_to(event)
            return
        if self._scrubbing:
            self._emit_position_for(event.position().x())
        elif self.has_media:
            hovered = self._marker_at(event.position().x())
            self.setCursor(Qt.SplitHCursor if hovered else Qt.PointingHandCursor)
            target = self.x_to_time(event.position().x())
            QToolTip.showText(event.globalPosition().toPoint(), target.to_clock(), self)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._drag_marker is not None:
            self._drag_marker = None
            self.marker_drag_finished.emit()
            self.update()
            return
        if not self._scrubbing:
            return
        self._scrubbing = False
        self.scrub_finished.emit()
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover_x = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def _emit_position_for(self, x: float) -> None:
        target = self.x_to_time(x)
        self._position = target
        self.position_requested.emit(target)

    def _drag_marker_to(self, event: QMouseEvent) -> None:
        """Move the held marker under the pointer and announce the new range."""
        if self._trim is None or self._duration is None:
            return
        limit = self._duration.frames
        frames = self.x_to_time(event.position().x()).frames
        if self._drag_marker == "in":
            # One frame of headroom either side: the points must never meet.
            point = MediaTime(min(frames, max(0, limit - 1)), self._duration.rate)
            trim = self._trim.with_in(point)
        else:
            point = MediaTime(min(max(frames, 1), limit), self._duration.rate)
            trim = self._trim.with_out(point)
        self._trim = trim.clamped(self._duration)
        QToolTip.showText(event.globalPosition().toPoint(), point.to_timecode(), self)
        self.trim_changed.emit(self._trim)
        self.update()

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track_rect()
        radius = track.height() / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(Color.TIMELINE_TRACK))
        painter.drawRoundedRect(track, radius, radius)

        if not self.has_media:
            painter.end()
            return

        playhead_x = self.frame_to_x(self._position.frames)
        trim = self._trim if self._markers_enabled else None

        if trim is None:
            elapsed = QRectF(
                track.left(), track.top(), max(0.0, playhead_x - track.left()), track.height()
            )
            painter.setBrush(QColor(Color.TIMELINE_RANGE))
            painter.drawRoundedRect(elapsed, radius, radius)
        else:
            in_x = self.frame_to_x(trim.in_point.frames)
            out_x = self.frame_to_x(trim.out_point.frames)
            # Clip to the pill so the square fills keep the track's shape.
            pill = QPainterPath()
            pill.addRoundedRect(track, radius, radius)
            painter.setClipPath(pill)

            # Head and tail are what gets thrown away: darker than the track.
            painter.setBrush(QColor(Color.TIMELINE_EXCLUDED))
            painter.drawRect(QRectF(track.left(), track.top(), in_x - track.left(), track.height()))
            painter.drawRect(QRectF(out_x, track.top(), track.right() - out_x, track.height()))

            painter.setBrush(QColor(Color.TIMELINE_RANGE))
            painter.drawRect(QRectF(in_x, track.top(), max(0.0, out_x - in_x), track.height()))

            played = min(max(playhead_x, in_x), out_x)
            painter.setBrush(QColor(Color.TIMELINE_RANGE).lighter(155))
            painter.drawRect(QRectF(in_x, track.top(), max(0.0, played - in_x), track.height()))
            painter.setClipping(False)

        # Hover ghost, drawn under the playhead so it never obscures it.
        if self._hover_x is not None and not self._scrubbing:
            hover_x = min(max(float(self._hover_x), track.left()), track.right())
            painter.setPen(QPen(QColor(Color.BORDER_STRONG), 1))
            painter.drawLine(int(hover_x), int(track.top() - 4), int(hover_x), int(track.bottom() + 4))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(Color.TIMELINE_PLAYHEAD))
        head = QRectF(
            playhead_x - PLAYHEAD_WIDTH / 2,
            track.top() - 5,
            PLAYHEAD_WIDTH,
            track.height() + 10,
        )
        painter.drawRoundedRect(head, 1, 1)

        if trim is not None:
            # Full-height handles: they must be grabbable without hunting for
            # the track, which is only a few pixels tall.
            painter.setBrush(QColor(Color.ACCENT))
            for frames in (trim.in_point.frames, trim.out_point.frames):
                x = self.frame_to_x(frames)
                painter.drawRoundedRect(
                    QRectF(x - MARKER_WIDTH / 2, 0.0, MARKER_WIDTH, float(self.height())), 1, 1
                )
        painter.end()


class TimecodeLabel(QWidget):
    """Current position / duration, in a font whose digits do not reflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position = "00:00:00.000"
        self._duration = "--:--:--.---"
        self._frame_text = ""
        self._font = QFont(mono_font_family(), 9)
        self._font.setStyleHint(QFont.Monospace)
        self.setFixedHeight(20)
        # Wide enough for "HH:MM:SS.mmm  /  HH:MM:SS.mmm" plus the frame counter
        # with clear air between them; they must never collide.
        self.setMinimumWidth(330)

    def set_times(self, position: MediaTime, duration: MediaTime | None) -> None:
        self._position = position.to_clock()
        self._duration = duration.to_clock() if duration else "--:--:--.---"
        total = duration.frames if duration else None
        self._frame_text = f"f {position.frames}" + (f" / {total}" if total is not None else "")
        self.update()

    def clear(self) -> None:
        self._position = "00:00:00.000"
        self._duration = "--:--:--.---"
        self._frame_text = ""
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setFont(self._font)
        metrics = painter.fontMetrics()

        x = 0
        painter.setPen(QColor(Color.TEXT))
        painter.drawText(x, metrics.ascent() + 2, self._position)
        x += metrics.horizontalAdvance(self._position)

        separator = "  /  "
        painter.setPen(QColor(Color.TEXT_DISABLED))
        painter.drawText(x, metrics.ascent() + 2, separator)
        x += metrics.horizontalAdvance(separator)

        painter.setPen(QColor(Color.TEXT_SECONDARY))
        painter.drawText(x, metrics.ascent() + 2, self._duration)

        if self._frame_text:
            frame_width = metrics.horizontalAdvance(self._frame_text)
            frame_x = self.width() - frame_width
            # Drop the counter rather than overlap the clock when space runs out.
            if frame_x > x + metrics.horizontalAdvance(self._duration) + 16:
                painter.setPen(QColor(Color.TEXT_DISABLED))
                painter.drawText(frame_x, metrics.ascent() + 2, self._frame_text)
        painter.end()


def snap_to_frame(seconds: float, rate: FrameRate) -> MediaTime:
    """Convenience for callers holding a float from an external source."""
    return MediaTime.from_seconds(seconds, rate, Rounding.NEAREST)
