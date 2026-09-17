"""Locating the bundled runtime binaries, and running them without side effects.

Every subprocess in Framecheck goes through `run_tool` or uses `NO_WINDOW_FLAGS`.
Under a windowed PyInstaller build, a subprocess without CREATE_NO_WINDOW flashes
a console window on screen for every probe -- which, with a folder of 40 files,
looks like the app is malfunctioning.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Suppress the console window for child processes on Windows.
NO_WINDOW_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resource_root() -> Path:
    """Directory that contains `vendor/`, in both dev and frozen builds."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    # framecheck/app/services/binaries.py -> repository root
    return Path(__file__).resolve().parents[3]


def vendor_dir() -> Path:
    return resource_root() / "vendor"


def _find_executable(name: str, vendor_subdir: str) -> Path | None:
    """Prefer the bundled binary; fall back to one on PATH."""
    exe = f"{name}.exe" if os.name == "nt" else name
    bundled = vendor_dir() / vendor_subdir / exe
    if bundled.is_file():
        return bundled
    on_path = shutil.which(name)
    return Path(on_path) if on_path else None


@lru_cache(maxsize=None)
def ffprobe_path() -> Path | None:
    return _find_executable("ffprobe", "ffmpeg")


@lru_cache(maxsize=None)
def ffmpeg_path() -> Path | None:
    return _find_executable("ffmpeg", "ffmpeg")


@lru_cache(maxsize=None)
def libmpv_dir() -> Path | None:
    """Directory holding libmpv-2.dll, or None if it is not bundled."""
    candidate = vendor_dir() / "playback"
    if (candidate / "libmpv-2.dll").is_file() or (candidate / "mpv-2.dll").is_file():
        return candidate
    return None


def register_libmpv_search_path() -> bool:
    """Make libmpv-2.dll loadable by the `mpv` module.

    Must run before `import mpv`. python-mpv uses ctypes.CDLL, which on Windows
    searches the DLL directories, not sys.path -- so a bundled DLL is invisible
    unless we add its directory explicitly.
    """
    directory = libmpv_dir()
    if directory is None:
        return False
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(directory))
        except OSError:
            return False
    os.environ["PATH"] = f"{directory}{os.pathsep}" + os.environ.get("PATH", "")
    return True


class ToolNotFoundError(RuntimeError):
    """A required bundled binary is missing."""


def run_tool(
    executable: Path,
    args: list[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a media tool with an argument list. Never a shell string.

    Passing a list keeps paths with spaces, quotes, ampersands and Unicode
    intact without any escaping of our own.
    """
    if not executable.is_file():
        raise ToolNotFoundError(f"missing executable: {executable}")
    return subprocess.run(
        [str(executable), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW_FLAGS,
    )


def missing_binaries() -> list[str]:
    """Names of required binaries that could not be located."""
    missing = []
    if ffprobe_path() is None:
        missing.append("ffprobe")
    if ffmpeg_path() is None:
        missing.append("ffmpeg")
    if libmpv_dir() is None:
        missing.append("libmpv")
    return missing
