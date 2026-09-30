"""Put the runtime binaries Framecheck needs into vendor/.

These are NOT committed to the repository. Run once after cloning:

    python tools/fetch_binaries.py

Windows: downloads a static FFmpeg (gyan.dev) and libmpv (shinchiro).

macOS: there is no static libmpv to download, so the binaries come from
Homebrew -- `brew install mpv` (which brings FFmpeg with it) -- and are copied
into vendor/ from there. They depend on Homebrew's shared libraries by absolute
path, which is fine in a checkout; the PyInstaller build collects every one of
those libraries into the app bundle and rewrites the paths.

Provenance of what was fetched is recorded in vendor/PROVENANCE.json so the
build is reproducible and the third-party notices stay honest.

Licensing note: the FFmpeg and libmpv builds used here are GPL builds (they
include libx264). See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

# What python-mpv looks for, per platform. Kept in step with
# framecheck/app/services/binaries.py, which is not imported here so this
# script stays runnable from a bare checkout.
LIBMPV_NAME = "libmpv.dylib" if sys.platform == "darwin" else "libmpv-2.dll"

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
MPV_RELEASES = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"

UA = {"User-Agent": "framecheck-fetch-binaries/1.0"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _find_7z_extractor() -> list[str] | None:
    """Return an argv prefix that extracts a .7z into the current directory.

    Prefers 7-Zip when installed. Falls back to the bsdtar shipped with
    Windows 10+, which reads 7z (including the BCJ2 filter that py7zr cannot).
    """
    for candidate in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        if candidate.exists():
            return [str(candidate), "x", "-y"]

    system_tar = Path(r"C:\Windows\System32\tar.exe")
    if system_tar.exists():
        return [str(system_tar), "-xf"]

    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return [found, "x", "-y"]
    return None


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)
    print(f"  -> {dest.name} ({dest.stat().st_size / 1_048_576:.1f} MB)")


def fetch_ffmpeg(provenance: dict) -> None:
    target = VENDOR / "ffmpeg"
    target.mkdir(parents=True, exist_ok=True)
    # Recorded even when the download is skipped, so PROVENANCE.json always
    # describes what is actually sitting in vendor/.
    provenance["ffmpeg"] = {
        "url": FFMPEG_URL,
        "license": "GPL-3.0-or-later (gyan.dev build; includes libx264)",
    }
    if (target / "ffmpeg.exe").exists() and (target / "ffprobe.exe").exists():
        print("ffmpeg: already present, skipping")
        return

    print("ffmpeg:")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        _download(FFMPEG_URL, archive)
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                name = Path(member).name
                if name in ("ffmpeg.exe", "ffprobe.exe"):
                    with zf.open(member) as src, (target / name).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"  extracted {name}")
                elif name in ("LICENSE", "LICENSE.txt", "COPYING.GPLv3") or name.startswith("LICENSE"):
                    with zf.open(member) as src, (target / f"FFMPEG_{name}").open("wb") as dst:
                        shutil.copyfileobj(src, dst)


def fetch_libmpv(provenance: dict) -> None:
    target = VENDOR / "playback"
    target.mkdir(parents=True, exist_ok=True)
    if (target / "libmpv-2.dll").exists():
        print("libmpv: already present, skipping")
        return

    extractor = _find_7z_extractor()
    if extractor is None:
        print(
            "libmpv: need an archiver that reads .7z. Install 7-Zip, or use "
            "Windows 10+ where C:\\Windows\\System32\\tar.exe can read 7z.",
            file=sys.stderr,
        )
        return

    print("libmpv:")
    release = json.loads(_get(MPV_RELEASES))
    assets = [a for a in release.get("assets", []) if a["name"].startswith("mpv-dev-x86_64-2")]
    if not assets:
        print("libmpv: no matching asset in latest release", file=sys.stderr)
        return
    asset = sorted(assets, key=lambda a: a["name"])[-1]

    # ignore_cleanup_errors: py7zr can hold the archive handle open on Windows
    # long enough to break TemporaryDirectory teardown.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        archive = Path(tmp) / asset["name"]
        _download(asset["browser_download_url"], archive)
        extract_to = Path(tmp) / "x"
        extract_to.mkdir()
        result = subprocess.run(
            [*extractor, str(archive)],
            cwd=extract_to,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"libmpv: extraction failed: {result.stderr.strip()[:400]}", file=sys.stderr)
            return
        for dll in extract_to.rglob("*mpv-2.dll"):
            shutil.copy2(dll, target / "libmpv-2.dll")
            print("  extracted libmpv-2.dll")
            break
        else:
            print("libmpv: dll vanished after extract", file=sys.stderr)
            return

    provenance["libmpv"] = {
        "url": asset["browser_download_url"],
        "release": release.get("tag_name"),
        "license": "GPL-2.0-or-later (build links GPL FFmpeg)",
    }


# -- macOS: Homebrew ---------------------------------------------------------


def _brew_prefix() -> Path | None:
    found = shutil.which("brew")
    if found is None:
        for candidate in (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew")):
            if candidate.is_file():
                found = str(candidate)
                break
    if found is None:
        return None
    result = subprocess.run([found, "--prefix"], capture_output=True, text=True)
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def _brew_formula_info(prefix: Path, name: str) -> dict:
    """Version and upstream source of an installed formula, for provenance."""
    brew = prefix / "bin" / "brew"
    try:
        result = subprocess.run(
            [str(brew), "info", "--json=v2", name], capture_output=True, text=True, check=True
        )
        formula = json.loads(result.stdout)["formulae"][0]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, IndexError):
        return {}
    installed = formula.get("installed") or [{}]
    return {
        "formula": formula.get("full_name", name),
        "version": installed[0].get("version") or formula.get("versions", {}).get("stable"),
        "upstream_source": formula.get("urls", {}).get("stable", {}).get("url"),
        "tap_git_head": formula.get("tap_git_head"),
    }


def _copy_real_file(source: Path, dest: Path) -> None:
    # Homebrew's bin/ and lib/ entries are symlinks into the Cellar; copy the
    # file itself, and keep its mode so executables stay executable.
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.resolve(), dest)


def _keg_licences(prefix: Path, formula: str) -> list[Path]:
    keg = (prefix / "opt" / formula).resolve()
    return sorted(
        p for p in keg.iterdir() if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING"))
    ) if keg.is_dir() else []


def fetch_macos(provenance: dict) -> int:
    prefix = _brew_prefix()
    if prefix is None:
        print(
            "Homebrew not found. Install it from https://brew.sh, then:\n"
            "    brew install mpv\n"
            "and run this script again.",
            file=sys.stderr,
        )
        return 1

    wanted = {
        "ffmpeg": prefix / "bin" / "ffmpeg",
        "ffprobe": prefix / "bin" / "ffprobe",
        LIBMPV_NAME: prefix / "lib" / "libmpv.dylib",
    }
    missing = [name for name, path in wanted.items() if not path.exists()]
    if missing:
        print(
            f"Not found under {prefix}: {', '.join(missing)}. Run:\n"
            "    brew install mpv\n"
            "(mpv depends on ffmpeg, so that installs both.)",
            file=sys.stderr,
        )
        return 1

    machine = platform.machine()
    macos = platform.mac_ver()[0]
    print(f"Homebrew at {prefix} ({machine}, macOS {macos})")

    ffmpeg_dir = VENDOR / "ffmpeg"
    for name in ("ffmpeg", "ffprobe"):
        _copy_real_file(wanted[name], ffmpeg_dir / name)
        print(f"  copied {name}")
    for licence in _keg_licences(prefix, "ffmpeg"):
        shutil.copy2(licence, ffmpeg_dir / f"FFMPEG_{licence.name}")

    _copy_real_file(wanted[LIBMPV_NAME], VENDOR / "playback" / LIBMPV_NAME)
    print(f"  copied {LIBMPV_NAME}")

    common = {"source": "homebrew", "prefix": str(prefix), "arch": machine, "macos": macos}
    provenance["ffmpeg"] = {
        **common,
        **_brew_formula_info(prefix, "ffmpeg"),
        "license": "GPL-3.0-or-later (Homebrew build; includes libx264)",
    }
    provenance["libmpv"] = {
        **common,
        **_brew_formula_info(prefix, "mpv"),
        "license": "GPL-2.0-or-later (build links GPL FFmpeg)",
    }
    return 0


def main() -> int:
    VENDOR.mkdir(exist_ok=True)
    provenance_path = VENDOR / "PROVENANCE.json"
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}

    if sys.platform == "darwin":
        status = fetch_macos(provenance)
        if status:
            return status
    elif sys.platform == "win32":
        fetch_ffmpeg(provenance)
        fetch_libmpv(provenance)
    else:
        print(
            "No bundled binaries are fetched on this platform. Install ffmpeg and "
            "libmpv from your distribution; Framecheck falls back to them on PATH.",
            file=sys.stderr,
        )
        return 1

    provenance_path.write_text(json.dumps(provenance, indent=2))
    print(f"\nWrote {provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
