"""Generate small synthetic media files for the test suite.

Fixtures are generated rather than committed: they are reproducible from this
script, and a repository of binary video files ages badly.

    python tools/make_fixtures.py

Every clip is tiny (320x240) but technically exact -- the point is the header
values (frame rate, frame count, codec, sample rate), not the picture.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

sys.path.insert(0, str(ROOT))
from framecheck.app.services.binaries import NO_WINDOW_FLAGS, ffmpeg_path  # noqa: E402

SIZE = "320x240"

# (filename, ffmpeg arguments after the shared input flags)
SPECS: list[tuple[str, list[str]]] = [
    # Exactly 30.000s at 29.97 -> 900 frames. The canonical "on spec" case.
    (
        "exact_30s_2997.mp4",
        ["-frames:v", "900", "-r", "30000/1001", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ),
    # One frame over :30 -- the case Framecheck exists to catch.
    (
        "over_by_one_frame_2997.mp4",
        ["-frames:v", "901", "-r", "30000/1001", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ),
    # 23.976 film rate, must survive round-tripping as 24000/1001.
    (
        "clip_23976.mp4",
        ["-frames:v", "240", "-r", "24000/1001", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ),
    ("clip_25_pal.mp4", ["-frames:v", "125", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p"]),
    # ProRes in a MOV: the format that most often will not play in Windows
    # Media Player and arrives from edit suites.
    (
        "prores_422.mov",
        ["-frames:v", "50", "-r", "25", "-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"],
    ),
    # 44.1 kHz audio against a 48 kHz delivery preference.
    (
        "audio_44100.mp4",
        ["-frames:v", "50", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-ar", "44100", "-c:a", "aac", "-b:a", "128k"],
    ),
    # A filename with spaces and non-ASCII characters, to prove path handling.
    (
        "clip with spaces éç作品.mp4",
        ["-frames:v", "50", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ),
]


def build(ffmpeg: Path, name: str, args: list[str]) -> bool:
    target = FIXTURES / name
    if target.exists():
        print(f"  = {name}")
        return True

    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={SIZE}:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        *args,
        "-shortest" if "-c:a" in args else "-an",
        str(target),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, creationflags=NO_WINDOW_FLAGS
    )
    if result.returncode != 0:
        print(f"  ! {name}: {result.stderr.strip()[:300]}")
        return False
    print(f"  + {name}")
    return True


def main() -> int:
    # One fixture name is deliberately non-ASCII; the Windows console defaults
    # to cp1252 and would raise on printing it.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        print("ffmpeg not found. Run tools/fetch_binaries.py first.", file=sys.stderr)
        return 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"fixtures -> {FIXTURES}")
    ok = all([build(ffmpeg, name, args) for name, args in SPECS])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
