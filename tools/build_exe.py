"""Build the distributable Windows package.

    python tools/build_exe.py [--zip] [--installer] [--clean]

Produces `dist/Framecheck/` (PyInstaller onedir -- see build/framecheck.spec for
why it must not be onefile) and, with --zip, `dist/Framecheck-<version>-win64.zip`
ready to attach to a GitHub release.

The version comes from `framecheck/__init__.py` and nowhere else: bump it there
and every artefact follows.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "build" / "framecheck.spec"
WORK = ROOT / "build" / "build"
DIST = ROOT / "dist"
OUT = DIST / "Framecheck"
VERSION = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "framecheck" / "__init__.py").read_text(encoding="utf-8")
).group(1)

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
    parser.add_argument(
        "--installer",
        action="store_true",
        help="also produce FramecheckSetup-<version>.exe (needs Inno Setup 6)",
    )
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

    if args.installer:
        return build_installer()

    return 0


def find_iscc() -> Path | None:
    """Locate the Inno Setup compiler.

    winget installs it per-user under Local\\Programs, not into Program Files,
    so check both.
    """
    candidates = [
        Path.home() / "AppData/Local/Programs/Inno Setup 6/ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("ISCC")
    return Path(found) if found else None


def shipped_spec_hashes() -> list[str]:
    """SHA-1 of every spec file any release could have installed.

    The installer uses these to tell a spec the user edited (or added) inside
    the install folder from one Framecheck put there, and rescues the former
    before the upgrade overwrites it. Every version in git history counts, in
    both line-ending forms, because a release may have been built from either.
    """
    contents = {p.read_bytes() for p in (ROOT / "specs").glob("*.json")}
    try:
        log = subprocess.run(
            ["git", "log", "--all", "--format=", "--raw", "--no-abbrev", "--", "specs/*.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        blobs = {b for b in re.findall(r"\b[0-9a-f]{40}\b", log) if set(b) != {"0"}}
        for blob in blobs:
            contents.add(subprocess.run(
                ["git", "cat-file", "-p", blob], cwd=ROOT, capture_output=True, check=True
            ).stdout)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"warning: no git history for specs ({exc}); only current specs are known")
    hashes = set()
    for data in contents:
        lf = data.replace(b"\r\n", b"\n")
        hashes |= {hashlib.sha1(v).hexdigest() for v in (lf, lf.replace(b"\n", b"\r\n"))}
    return sorted(hashes)


def build_installer() -> int:
    """Compile the single-file installer from the onedir build."""
    iscc = find_iscc()
    if iscc is None:
        print(
            "\nInno Setup 6 not found. Install it with:\n"
            "    winget install JRSoftware.InnoSetup\n"
            "or from https://jrsoftware.org/isdl.php",
            file=sys.stderr,
        )
        return 1

    script = ROOT / "build" / "framecheck.iss"
    defines = [
        f"/DAppVersion={VERSION}",
        f"/DKnownSpecHashes=;{';'.join(shipped_spec_hashes())};",
    ]
    result = subprocess.run([str(iscc), *defines, str(script)], cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    setup = DIST / f"FramecheckSetup-{VERSION}.exe"
    if setup.is_file():
        print(f"Setup: {setup} ({human(setup.stat().st_size)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
