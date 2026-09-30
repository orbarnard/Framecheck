"""Build the distributable macOS package.

    python tools/build_mac.py [--dmg] [--sign] [--clean]

Produces `dist/Framecheck.app` (PyInstaller onedir wrapped in an app bundle --
see build/framecheck.spec for why it must not be onefile) and, with --dmg,
`dist/Framecheck-<version>-macos-<arch>.dmg`: a drag-to-Applications disk
image ready to attach to a GitHub release.

The build is for the architecture of the Mac it runs on. The Homebrew
libraries it bundles are single-arch, so an Apple Silicon DMG and an Intel DMG
are two builds on two machines (or two CI runners).

Signing is optional and off by default. Without it PyInstaller ad-hoc signs the
bundle, which runs on the build machine but is stopped by Gatekeeper on any
other Mac until the user allows it once (the DMG carries a readme explaining
how). With --sign and a Developer ID certificate in the keychain, the bundle
gets a real signature with the hardened runtime, and the DMG is notarised and
stapled. --sign refuses to run without the Apple credentials, because a signed
but un-notarised build is still stopped by Gatekeeper and would ship without
the readme; --skip-notarization is the explicit way to get such a build.

    FRAMECHECK_CODESIGN_IDENTITY   "Developer ID Application: Name (TEAMID)"
    APPLE_ID                       the Apple ID that owns the certificate
    APPLE_TEAM_ID                  the ten-character team id
    APPLE_APP_PASSWORD             an app-specific password for that Apple ID

The version comes from `framecheck/__init__.py` and nowhere else: bump it there
and every artefact follows.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "build" / "framecheck.spec"
WORK = ROOT / "build" / "build"
DIST = ROOT / "dist"
APP = DIST / "Framecheck.app"
VERSION = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "framecheck" / "__init__.py").read_text(encoding="utf-8")
).group(1)
ARCH = platform.machine()  # arm64 or x86_64

# Put in place by tools/fetch_binaries.py. Without these the build would succeed
# and the app would be unusable, so refuse up front.
REQUIRED = [
    Path("vendor/ffmpeg/ffmpeg"),
    Path("vendor/ffmpeg/ffprobe"),
    Path("vendor/playback/libmpv.dylib"),
]

ICON = Path("assets/framecheck.icns")


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def check_prerequisites() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This builds the macOS package and has to run on a Mac.")

    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    if missing:
        listed = "\n".join(f"  {p.as_posix()}" for p in missing)
        raise SystemExit(
            f"Missing bundled binaries:\n{listed}\n\n"
            "Run this first:\n  brew install mpv\n  python tools/fetch_binaries.py"
        )

    if not (ROOT / ICON).is_file():
        print(f"{ICON.as_posix()} missing; generating it")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
        if not (ROOT / ICON).is_file():
            raise SystemExit(f"tools/make_icon.py did not produce {ICON.as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Framecheck macOS package.")
    parser.add_argument("--dmg", action="store_true", help="also produce the release disk image")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="sign with FRAMECHECK_CODESIGN_IDENTITY and notarise the DMG with the Apple credentials",
    )
    parser.add_argument(
        "--skip-notarization",
        action="store_true",
        help="with --sign: sign only. The DMG then still needs the Gatekeeper workaround, and says so",
    )
    parser.add_argument("--clean", action="store_true", help="discard cached build state first")
    args = parser.parse_args(argv)

    check_prerequisites()

    identity = os.environ.get("FRAMECHECK_CODESIGN_IDENTITY", "").strip()
    if args.sign and not identity:
        raise SystemExit("--sign needs FRAMECHECK_CODESIGN_IDENTITY in the environment.")
    notarise = args.sign and not args.skip_notarization
    if notarise and not notarization_credentials():
        # Refused up front rather than after a long build: a signed but
        # un-notarised DMG is still blocked by Gatekeeper, and a release made
        # of one would ship without the readme that explains the workaround.
        raise SystemExit(
            "--sign notarises the DMG, which needs APPLE_ID, APPLE_TEAM_ID and "
            "APPLE_APP_PASSWORD in the environment. Set them, or pass "
            "--skip-notarization to sign without notarising."
        )
    if not args.sign:
        # The spec reads the variable; make sure a stray value does not sign
        # a build nobody asked to be signed.
        os.environ.pop("FRAMECHECK_CODESIGN_IDENTITY", None)
        identity = ""

    # A previous bundle would be updated in place, leaving stale files behind.
    if APP.exists():
        shutil.rmtree(APP)

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
    if not APP.is_dir():
        raise SystemExit(f"PyInstaller finished but {APP} is missing")

    print(f"\nBuilt: {APP}")
    print(f"Size:  {human(folder_size(APP))}")

    if identity:
        verify_signature(APP)

    if args.dmg:
        return build_dmg(identity, notarise)
    return 0


def notarization_credentials() -> tuple[str, str, str] | None:
    apple_id = os.environ.get("APPLE_ID", "").strip()
    team_id = os.environ.get("APPLE_TEAM_ID", "").strip()
    password = os.environ.get("APPLE_APP_PASSWORD", "").strip()
    if apple_id and team_id and password:
        return apple_id, team_id, password
    return None


def verify_signature(bundle: Path) -> None:
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)], check=True
    )
    print("Signed: codesign verification passed")


def build_dmg(identity: str, notarise: bool) -> int:
    """Wrap the bundle in a compressed, drag-to-Applications disk image."""
    dmg = DIST / f"Framecheck-{VERSION}-macos-{ARCH}.dmg"
    staging = WORK / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # ditto preserves symlinks, resource forks and the code signature, which
    # a plain copy can quietly break.
    subprocess.run(["ditto", str(APP), str(staging / APP.name)], check=True)
    (staging / "Applications").symlink_to("/Applications")
    if not notarise:
        # Anything short of notarised gets stopped by Gatekeeper; the readme
        # says what to do about it.
        shutil.copy2(ROOT / "build" / "dmg_readme.txt", staging / "READ ME FIRST.txt")

    if dmg.exists():
        dmg.unlink()
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            f"Framecheck {VERSION}",
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            "-imagekey",
            "zlib-level=9",
            str(dmg),
        ],
        check=True,
    )
    print(f"DMG:   {dmg} ({human(dmg.stat().st_size)})")

    if identity:
        subprocess.run(["codesign", "--sign", identity, "--timestamp", str(dmg)], check=True)
    if notarise:
        notarize(dmg)
    else:
        print("Not notarised: Gatekeeper will stop this build; the readme inside explains.")
    return 0


def notarize(dmg: Path) -> None:
    credentials = notarization_credentials()
    if credentials is None:  # main() checks before building; belt and braces
        raise SystemExit("notarisation needs APPLE_ID, APPLE_TEAM_ID and APPLE_APP_PASSWORD")
    apple_id, team_id, password = credentials
    subprocess.run(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(dmg),
            "--apple-id",
            apple_id,
            "--team-id",
            team_id,
            "--password",
            password,
            "--wait",
        ],
        check=True,
    )
    # Staple the ticket so Gatekeeper can verify the download offline.
    subprocess.run(["xcrun", "stapler", "staple", str(dmg)], check=True)
    print("Notarised and stapled")


if __name__ == "__main__":
    raise SystemExit(main())
