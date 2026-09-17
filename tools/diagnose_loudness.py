"""Find out why loudness analysis is slow for a given file.

    python tools/diagnose_loudness.py "C:\\path\\to\\your file.mov"

Separates the three possible causes -- reading the bytes, decoding the audio,
and the loudnorm filter -- so the fix targets the real one instead of the
plausible one.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framecheck.app.services.binaries import NO_WINDOW_FLAGS, ffmpeg_path  # noqa: E402

CHUNK = 8 * 1024 * 1024


def time_raw_read(path: Path) -> float:
    """Pure I/O: how long the operating system takes to hand us the bytes."""
    start = time.time()
    read = 0
    with path.open("rb") as handle:
        while handle.read(CHUNK):
            read += CHUNK
    return time.time() - start


def time_ffmpeg(ff: str, path: Path, label: str, extra: list[str]) -> float:
    args = [ff, "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-sn", "-dn",
            "-map", "0:a:0", *extra, "-f", "null", "-"]
    start = time.time()
    result = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW_FLAGS)
    elapsed = time.time() - start
    status = "ok" if result.returncode == 0 else f"rc={result.returncode}"
    print(f"  {label:36} {elapsed:7.2f}s  {status}")
    return elapsed


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"not found: {path}")
        return 1

    ff = ffmpeg_path()
    if ff is None:
        print("ffmpeg not found; run tools/fetch_binaries.py")
        return 1

    size_mb = path.stat().st_size / 1_048_576
    print(f"\nfile: {path.name}")
    print(f"size: {size_mb:.1f} MB")
    print(f"path: {path.parent}")

    print("\ntimings")
    first = time_raw_read(path)
    print(f"  {'raw byte read (cold)':36} {first:7.2f}s  "
          f"{size_mb / max(first, 0.001):6.1f} MB/s")
    second = time_raw_read(path)
    print(f"  {'raw byte read (warm, cached)':36} {second:7.2f}s  "
          f"{size_mb / max(second, 0.001):6.1f} MB/s")

    decode = time_ffmpeg(ff, path, "decode audio only", [])
    loudnorm = time_ffmpeg(
        ff, path, "loudnorm analysis (what the app runs)",
        ["-af", "loudnorm=I=-24:TP=-2:LRA=7:print_format=json"],
    )

    print("\nverdict")
    if first > 3 * second and first > 5:
        print("  The first read was far slower than the second: the file was not")
        print("  local. This is cloud sync (OneDrive/Dropbox) hydrating it, or a")
        print("  network drive. Analysis cannot start until the bytes arrive.")
        print("  Fix: right-click the folder -> 'Always keep on this device',")
        print("  or work from a local copy.")
    elif loudnorm > decode * 3 and loudnorm > 5:
        print("  The filter dominates. Worth switching the display measurement to")
        print("  ebur128 and keeping loudnorm only for the export pass.")
    elif decode > 5:
        print("  Decoding the audio itself is slow -- unusual; check the codec.")
    else:
        print("  Everything here is fast. If the app still feels slow, the wait is")
        print("  elsewhere: run with -v and check the log timings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
