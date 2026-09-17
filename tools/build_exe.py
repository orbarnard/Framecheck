"""Build the distributable Windows package.

    python tools/build_exe.py [--zip] [--clean]

Produces `dist/Framecheck/` (PyInstaller onedir -- see build/framecheck.spec for
why it must not be onefile) and, with --zip, `dist/Framecheck-0.1.0-win64.zip`
ready to attach to a GitHub release.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "build" / "framecheck.spec"
WORK = ROOT / "build" / "build"
DIST = ROOT / "dist"
OUT = DIST / "Framecheck"
VERSION = "0.1.0"

# Downloaded by tools/fetch_binaries.py. Without these the build would succeed
# and the app would be unusable, so refuse up front.
REQUIRED = [
    Path("vendor/ffmpeg/ffmpeg.exe"),
    Path("vendor/ffmpeg/ffprobe.exe"),
    Path("vendor/playback/libmpv-2.dll"),
]

ICON = Path("assets/framecheck.ico")


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def check_prerequisites() -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    if missing:
        listed = "\n".join(f"  {p.as_posix()}" for p in missing)
        raise SystemExit(
            f"Missing bundled binaries:\n{listed}\n\n"
            "Run this first:\n  python tools/fetch_binaries.py"
        )

    if not (ROOT / ICON).is_file():
        print(f"{ICON.as_posix()} missing; generating it")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
        if not (ROOT / ICON).is_file():
            raise SystemExit(f"tools/make_icon.py did not produce {ICON.as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Framecheck Windows package.")
    parser.add_argument("--zip", action="store_true", help="also produce the release zip")
    parser.add_argument("--clean", action="store_true", help="discard cached build state first")
    args = parser.parse_args(argv)

    check_prerequisites()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--workpath",
        str(WORK),
        "--distpath",
        str(DIST),
    ]
    if args.clean:
        command.append("--clean")

    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    print(f"\nBuilt: {OUT}")
    print(f"Size:  {human(folder_size(OUT))}")

    if args.zip:
        archive = DIST / f"Framecheck-{VERSION}-win64"
        path = shutil.make_archive(str(archive), "zip", root_dir=DIST, base_dir=OUT.name)
        print(f"Zip:   {path} ({human(Path(path).stat().st_size)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
