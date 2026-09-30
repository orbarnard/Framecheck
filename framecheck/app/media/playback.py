"""Embedded playback via libmpv.

Two ways of getting mpv's picture on screen, chosen by platform:

Windows: mpv renders into a native child window handle we hand it (`wid`), on
its own thread, so decoding never touches the Qt event loop.

macOS (and Linux): mpv has no window embedding there -- the Cocoa backend that
took a `wid` was removed in mpv 0.37 -- so the render API is used instead. mpv
draws each frame into the OpenGL framebuffer of a QOpenGLWidget when that widget
paints, and asks for a repaint through `render_update` when a new frame is due.
Decoding still happens on mpv's threads; only the final blit shares the GUI
thread, as it must.

State reaches the UI by polling from a Qt timer rather than by mpv property
observers. Observer callbacks fire on mpv's thread, and one mechanism in one
thread is easier to reason about than two. The poll is a handful of property
reads at 20 Hz -- immaterial next to decoding.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..models.media_time import FrameRate, MediaTime, Rounding
from ..services.binaries import libmpv_filename, register_libmpv_search_path

log = logging.getLogger(__name__)

# Only Windows lets mpv adopt a foreign window. Everywhere else the picture goes
# through the render API into a GL widget. See the module docstring.
EMBEDS_BY_WINDOW_ID = sys.platform == "win32"

# Import mpv only after the bundled library directory is registered: python-mpv
# resolves libmpv at import time via ctypes.
_LIBMPV_REGISTERED = register_libmpv_search_path()

try:
    import mpv as _mpv_module

    MPV_AVAILABLE = True
    MPV_IMPORT_ERROR: str | None = None
except Exception as exc:  # OSError when the DLL is missing, ImportError otherwise
    _mpv_module = None  # type: ignore[assignment]
    MPV_AVAILABLE = False
    MPV_IMPORT_ERROR = str(exc)
    log.error("libmpv unavailable: %s", exc)


POLL_INTERVAL_MS = 50

_MPV_LOG_LEVELS = {
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "v": logging.DEBUG,
    "debug": logging.DEBUG,
    "trace": logging.DEBUG,
}


class PlaybackEngine(QObject):
    """Owns the libmpv instance and exposes it as Qt signals and slots.

    Positions cross this boundary as MediaTime, never as bare floats: mpv's
    `time-pos` is a display hint that gets quantised to a frame immediately.
    """

    position_changed = Signal(object)  # MediaTime
    duration_changed = Signal(object)  # MediaTime | None
    paused_changed = Signal(bool)
    loaded = Signal(Path)
    end_reached = Signal()
    error = Signal(str)
    # Render API only: mpv has a new frame (or a redraw is due). Emitted from
    # mpv's thread; the GL widget's slot runs queued on the GUI thread.
    render_update = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mpv = None
        self._render_ctx = None
        # ctypes callbacks must outlive the C side's use of them, so they are
        # held here rather than left to the garbage collector.
        self._get_proc_address_fn = None
        # Set for the duration of shutdown: taking the renderer away ends the
        # current file with an error mpv is right about but nobody needs to see.
        self._closing = False
        self._path: Path | None = None
        self._rate: FrameRate = FrameRate(Fraction(25, 1))  # replaced on load
        self._duration: MediaTime | None = None
        self._last_position: MediaTime | None = None
        self._last_paused: bool | None = None
        self._loaded_emitted = False

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # -- lifecycle --------------------------------------------------------

    @property
    def available(self) -> bool:
        return MPV_AVAILABLE

    @property
    def unavailable_reason(self) -> str:
        if MPV_AVAILABLE:
            return ""
        if not _LIBMPV_REGISTERED:
            return (
                f"{libmpv_filename()} was not found in vendor/playback.\n"
                "Run: python tools/fetch_binaries.py"
            )
        return f"libmpv could not be loaded: {MPV_IMPORT_ERROR}"

    @property
    def uses_render_api(self) -> bool:
        return not EMBEDS_BY_WINDOW_ID

    def attach(self, window_id: int) -> bool:
        """Create the mpv instance rendering into `window_id` (Windows).

        The handle must already exist -- call after the container widget has a
        native window.
        """
        if not MPV_AVAILABLE or self._mpv is not None:
            return self._mpv is not None
        if not self._create(wid=str(int(window_id)), vo="gpu"):
            return False
        log.info("mpv attached to window id %s", window_id)
        return True

    def attach_render(self, get_proc_address: Callable[[bytes], int]) -> bool:
        """Create the mpv instance drawing through the render API (macOS, Linux).

        `get_proc_address` resolves an OpenGL function name to its address in
        the *current* GL context, so this must be called with the GL widget's
        context current -- from its initializeGL, in practice. Frames are then
        drawn by `render()` from the widget's paintGL.
        """
        if not MPV_AVAILABLE or self._mpv is not None:
            return self._mpv is not None
        # Software decoding, deliberately. Hardware decoding through the
        # render API means a GL interop with the host context, which is the
        # one part of this path that cannot be tested without the real GPU:
        # VideoToolbox on a Mac has not been tried, and the VA-API probe under
        # Mesa was seen to leave a black picture in headless testing. Delivery
        # spots are short and 1080p-ish; software decode is well within budget
        # for them. Try hwdec="auto-safe" here once it has been seen working
        # on a real Mac.
        options = {"vo": "libmpv", "hwdec": "no"}
        # Escape hatch: mpv's single-pass renderer, no intermediate
        # framebuffers. Under Mesa's software GL the full renderer produced a
        # black picture about half the time in headless testing while this
        # mode never did; a real GPU is not expected to need it. If a Mac
        # shows a black player, this is the first thing to try:
        #     FRAMECHECK_GPU_DUMB_MODE=1 open -a Framecheck
        # (tools/check_playback.py exercises both.)
        if os.environ.get("FRAMECHECK_GPU_DUMB_MODE", "").strip() not in ("", "0"):
            options["gpu_dumb_mode"] = "yes"
        if not self._create(**options):
            return False

        def _resolve(_ctx, name: bytes) -> int:
            try:
                return int(get_proc_address(name) or 0)
            except Exception:  # never let an exception cross into C
                log.debug("get_proc_address failed for %r", name, exc_info=True)
                return 0

        self._get_proc_address_fn = _mpv_module.MpvGlGetProcAddressFn(_resolve)
        try:
            self._render_ctx = _mpv_module.MpvRenderContext(
                self._mpv,
                "opengl",
                opengl_init_params={"get_proc_address": self._get_proc_address_fn},
            )
            self._render_ctx.update_cb = self.render_update.emit
        except Exception as exc:
            log.exception("failed to create the mpv render context")
            self._render_ctx = None
            self._mpv.terminate()
            self._mpv = None
            self.error.emit(f"Could not start the video renderer: {exc}")
            return False
        log.info("mpv attached through the OpenGL render API")
        return True

    def render(self, fbo: int, width: int, height: int) -> None:
        """Draw the current frame into `fbo` (device pixels). GL context current."""
        if self._render_ctx is None or width <= 0 or height <= 0:
            return
        try:
            # mpv treats the update flag as consumed by a render; asking first
            # keeps its frame queue moving even when Qt repaints for its own
            # reasons (resize, expose).
            self._render_ctx.update()
            self._render_ctx.render(
                flip_y=True, opengl_fbo={"w": int(width), "h": int(height), "fbo": int(fbo)}
            )
        except Exception:
            log.debug("mpv render failed", exc_info=True)

    def release_render_context(self) -> None:
        """Free the render context. Must run with the GL context current, and
        before that context is destroyed -- mpv releases its GL objects here."""
        if self._render_ctx is None:
            return
        try:
            self._render_ctx.update_cb = None
            self._render_ctx.free()
        except Exception:  # pragma: no cover - teardown best effort
            log.exception("error freeing the mpv render context")
        self._render_ctx = None
        log.info("mpv render context released")

    def _create(self, **options) -> bool:
        options.setdefault("hwdec", "auto-safe")
        try:
            self._mpv = _mpv_module.MPV(
                **options,
                # Keep the last frame on screen at EOF instead of tearing down
                # the render window, which would flash the container.
                keep_open="yes",
                keep_open_pause="yes",
                idle="yes",
                # No force-window: while idle, mpv creates no render window, so
                # the Qt overlay ("No file loaded", playback errors) is visible.
                # A native child window would otherwise composite above it.
                # Framecheck owns the keyboard; mpv's own bindings would fight
                # our shortcuts and swallow keys the main window needs.
                input_default_bindings=False,
                input_vo_keyboard=False,
                osc=False,
                osd_level=0,
                # Audio must not be resampled away from the source rate here --
                # this is a monitoring path, not an export path.
                audio_display="no",
                cache="yes",
                log_handler=self._on_mpv_log,
                loglevel="warn",
            )
        except Exception as exc:
            log.exception("failed to create mpv instance")
            self._mpv = None
            self.error.emit(f"Could not start the video engine: {exc}")
            return False

        @self._mpv.event_callback("end-file")
        def _on_end_file(event) -> None:  # runs on mpv's thread
            self._handle_end_file(event)

        self._timer.start()
        return True

    def shutdown(self) -> None:
        """Terminate mpv before Qt destroys the window or GL context it draws into.

        With the render API, call this with the GL widget's context current:
        mpv gives its GL objects back here.
        """
        self._timer.stop()
        self._closing = True
        self.release_render_context()
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception:  # pragma: no cover - teardown best effort
                log.exception("error terminating mpv")
            self._mpv = None
            log.info("mpv terminated")

    # -- loading ----------------------------------------------------------

    def load(self, path: Path, frame_rate: FrameRate | None = None) -> None:
        """Open `path`. `frame_rate` comes from ffprobe and drives frame math."""
        if self._mpv is None:
            return
        self._path = Path(path)
        self._rate = frame_rate or FrameRate(Fraction(25, 1))
        self._duration = None
        self._last_position = None
        self._loaded_emitted = False
        log.info("player loading %s", path)
        try:
            self._mpv.command("loadfile", str(path), "replace")
            self._mpv.pause = True
        except Exception as exc:
            log.exception("loadfile failed")
            self.error.emit(f"Could not open this file for playback: {exc}")

    def set_frame_rate(self, rate: FrameRate) -> None:
        """Update the rate used to quantise positions once ffprobe reports it."""
        self._rate = rate
        if self._duration is not None:
            self._duration = self._duration.at_rate(rate)
            self.duration_changed.emit(self._duration)

    def unload(self) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        self._path = None
        self._duration = None
        self._last_position = None

    # -- transport --------------------------------------------------------

    @property
    def rate(self) -> FrameRate:
        return self._rate

    @property
    def duration(self) -> MediaTime | None:
        return self._duration

    @property
    def position(self) -> MediaTime:
        return self._last_position or MediaTime.zero(self._rate)

    @property
    def is_paused(self) -> bool:
        if self._mpv is None:
            return True
        try:
            return bool(self._mpv.pause)
        except Exception:
            return True

    def play(self) -> None:
        self._set_pause(False)

    def pause(self) -> None:
        self._set_pause(True)

    def toggle_pause(self) -> None:
        self._set_pause(not self.is_paused)

    def _set_pause(self, value: bool) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.pause = value
        except Exception:
            log.debug("could not set pause=%s", value, exc_info=True)

    def seek(self, target: MediaTime, *, exact: bool = True) -> None:
        """Seek to an absolute position.

        `exact` decodes to the precise frame rather than snapping to the nearest
        keyframe. Slower, but it is the only mode in which what the user sees
        matches what a frame-accurate export would produce.
        """
        if self._mpv is None:
            return
        clamped = target.clamped(hi=self._duration) if self._duration else target
        try:
            self._mpv.command(
                "seek",
                f"{clamped.seconds_float:.6f}",
                "absolute",
                "exact" if exact else "keyframes",
            )
        except Exception:
            log.debug("seek failed", exc_info=True)

    def seek_seconds(self, seconds: float, *, exact: bool = True) -> None:
        self.seek(MediaTime.from_seconds(max(0.0, seconds), self._rate, Rounding.NEAREST), exact=exact)

    def step_frames(self, delta: int) -> None:
        """Step `delta` frames. Negative steps backward.

        Uses mpv's own frame-step commands rather than a computed seek: mpv
        knows the real frame boundaries of the decoded stream, including on VFR
        sources where our nominal rate would drift.
        """
        if self._mpv is None or delta == 0:
            return
        command = "frame-step" if delta > 0 else "frame-back-step"
        try:
            self._mpv.pause = True
            for _ in range(abs(delta)):
                self._mpv.command(command)
        except Exception:
            log.debug("frame step failed", exc_info=True)

    def jump_seconds(self, delta: float) -> None:
        current = self.position
        target = current.offset_seconds(Fraction(delta).limit_denominator(1000))
        self.seek(target)

    def seek_to_end_window(self, window_seconds: float = 5.0) -> None:
        """Jump to `window_seconds` before the end -- the tail-check shortcut."""
        if self._duration is None:
            return
        target = self._duration.offset_seconds(-Fraction(window_seconds).limit_denominator(1000))
        self.seek(target)

    # -- audio ------------------------------------------------------------

    def set_volume(self, value: int) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.volume = max(0, min(100, int(value)))
        except Exception:
            pass

    def set_muted(self, muted: bool) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.mute = bool(muted)
        except Exception:
            pass

    # -- internals --------------------------------------------------------

    def _poll(self) -> None:
        if self._mpv is None:
            return
        try:
            time_pos = self._mpv.time_pos
            duration = self._mpv.duration
            paused = self._mpv.pause
        except Exception:
            return

        if duration is not None and self._duration is None:
            self._duration = MediaTime.from_seconds(duration, self._rate, Rounding.NEAREST)
            self.duration_changed.emit(self._duration)
            if not self._loaded_emitted and self._path is not None:
                self._loaded_emitted = True
                self.loaded.emit(self._path)

        if time_pos is not None:
            position = MediaTime.from_seconds(max(0.0, time_pos), self._rate, Rounding.NEAREST)
            if position != self._last_position:
                self._last_position = position
                self.position_changed.emit(position)

        if paused is not None and paused != self._last_paused:
            self._last_paused = bool(paused)
            self.paused_changed.emit(self._last_paused)

    def _handle_end_file(self, event) -> None:
        """Called on mpv's thread; Qt queues the emitted signals for the UI."""
        if self._closing:
            return
        data = getattr(event, "data", None)
        reason = getattr(data, "reason", None)
        reason_text = getattr(reason, "value", reason)
        if isinstance(reason_text, bytes):
            reason_text = reason_text.decode("utf-8", "replace")

        if reason_text in ("error", 4):
            message = getattr(data, "error", None) or "playback error"
            if isinstance(message, bytes):
                message = message.decode("utf-8", "replace")
            log.warning("mpv end-file error: %s", message)
            self.error.emit(
                "This file could not be played directly. A preview proxy may be "
                f"required.\n({message})"
            )
        elif reason_text in ("eof", 0):
            self.end_reached.emit()

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        log.log(_MPV_LOG_LEVELS.get(level, logging.DEBUG), "mpv/%s: %s", prefix, text.strip())
