"""Prove that libmpv draws a picture through Framecheck's player, headlessly.

    python tools/check_playback.py [--dumb] [path/to/clip.mp4]

Opens the real PlayerWidget off-screen (no main window), loads a clip, waits
for a frame to land in the video surface, then plays and frame-steps. Prints
PASS when the surface holds a picture with more than a handful of colours,
FAIL otherwise. With no clip given, the first generated test fixture is used
(python tools/make_fixtures.py).

Why this exists: on macOS and Linux the picture goes through mpv's render API
into a QOpenGLWidget, which is harder to get right than handing mpv a window
handle, and a broken setup shows as a silently black player rather than an
error. This is the quickest way to tell on a new machine, and the quickest way
to tell whether FRAMECHECK_GPU_DUMB_MODE changes anything: run it once
normally and once with --dumb.

On Windows the player embeds by window handle instead; this still runs, but a
window has to exist for that, so a real display is needed there.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("clip", nargs="?", help="a video file; default: a test fixture")
    parser.add_argument("--dumb", action="store_true", help="use mpv's single-pass renderer")
    parser.add_argument("--seconds", type=float, default=10.0, help="how long to wait for a frame")
    args = parser.parse_args(argv)

    if args.dumb:
        os.environ["FRAMECHECK_GPU_DUMB_MODE"] = "1"

    clip = Path(args.clip) if args.clip else _first_fixture()
    if clip is None or not clip.is_file():
        print("No clip. Pass one, or run: python tools/make_fixtures.py", file=sys.stderr)
        return 2

    # Imported here so --help works without Qt, and after the environment is set.
    from PySide6.QtWidgets import QApplication

    from framecheck.app.main import configure_surface_format
    from framecheck.app.media.playback import EMBEDS_BY_WINDOW_ID, MPV_AVAILABLE, MPV_IMPORT_ERROR
    from framecheck.app.ui.player_widget import PlayerWidget

    configure_surface_format()
    app = QApplication.instance() or QApplication([])

    if not MPV_AVAILABLE:
        print(f"FAIL: libmpv could not be loaded: {MPV_IMPORT_ERROR}")
        return 1
    print("renderer:", "window handle" if EMBEDS_BY_WINDOW_ID else "OpenGL render API")

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def colours() -> int:
        image = widget.surface.grabFramebuffer() if not EMBEDS_BY_WINDOW_ID else widget.surface.grab().toImage()
        step = 6
        return len(
            {
                image.pixel(x, y) & 0xFFFFFF
                for y in range(0, image.height(), step)
                for x in range(0, image.width(), step)
            }
        )

    widget = PlayerWidget()
    widget.resize(640, 420)
    widget.show()
    widget.load(clip)
    pump(0.5)
    if not widget._attached:
        print(f"FAIL: the engine did not attach: {widget.engine.unavailable_reason or 'unknown'}")
        return 1

    start = time.monotonic()
    seen = 0
    while time.monotonic() - start < args.seconds:
        pump(0.25)
        seen = colours()
        if seen > 50:
            break
    print(f"first frame: {seen} distinct colours after {time.monotonic() - start:.1f}s")

    widget.engine.play()
    pump(1.0)
    playing = widget.engine.position
    widget.engine.pause()
    widget.engine.step_frames(2)
    pump(0.3)
    stepped = widget.engine.position
    print(f"after 1s of play: {playing} | after two frame steps: {stepped}")
    after = colours()
    print(f"picture while paused: {after} distinct colours")

    widget.shutdown()
    widget.close()
    pump(0.2)

    ok = seen > 50 and after > 50 and widget.engine.duration is not None
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def _first_fixture() -> Path | None:
    fixtures = ROOT / "tests" / "fixtures"
    for name in ("clip_25_pal.mp4", "clip_23976.mp4"):
        if (fixtures / name).is_file():
            return fixtures / name
    found = sorted(fixtures.glob("*.mp4")) if fixtures.is_dir() else []
    return found[0] if found else None


if __name__ == "__main__":
    raise SystemExit(main())
