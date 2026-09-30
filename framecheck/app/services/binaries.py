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


def libmpv_filenames() -> tuple[str, ...]:
    """The file names python-mpv can find libmpv under, on this platform.

    Windows: python-mpv asks for `mpv-2.dll` or `libmpv-2.dll` by name.
    macOS: it asks ctypes.util.find_library("mpv"), which looks for
    `libmpv.dylib` first -- so that is the name the bundled copy must carry,
    whatever Homebrew called it.
    Elsewhere: the usual soname.
    """
    if sys.platform == "win32":
        return ("libmpv-2.dll", "mpv-2.dll")
    if sys.platform == "darwin":
        return ("libmpv.dylib", "libmpv.2.dylib")
    return ("libmpv.so.2", "libmpv.so")


def libmpv_filename() -> str:
    """The name `tools/fetch_binaries.py` writes the bundled libmpv under."""
    return libmpv_filenames()[0]


@lru_cache(maxsize=None)
def libmpv_dir() -> Path | None:
    """Directory holding the bundled libmpv, or None if it is not bundled."""
    candidate = vendor_dir() / "playback"
    if any((candidate / name).is_file() for name in libmpv_filenames()):
        return candidate
    return None


def register_libmpv_search_path() -> bool:
    """Make the bundled libmpv loadable by the `mpv` module.

    Must run before `import mpv`. python-mpv uses ctypes, which never looks at
    sys.path, so the bundled library is invisible unless its directory is put
    where the loader looks:

    Windows: `os.add_dll_directory`, plus PATH, which is what python-mpv's own
    `find_library` walks.
    macOS: `ctypes.util.find_library` reads DYLD_LIBRARY_PATH from
    `os.environ` at call time, so setting it here -- inside the process, not
    at launch, where the system strips it -- is enough.
    Linux: `find_library` consults ldconfig, not environment variables. A
    bundled copy is found through LD_LIBRARY_PATH only when the platform
    loader gets it at launch; a system libmpv works regardless.
    """
    directory = libmpv_dir()
    if directory is None:
        return False
    if sys.platform == "win32":
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(directory))
            except OSError:
                return False
        _prepend_env_path("PATH", directory)
    elif sys.platform == "darwin":
        _prepend_env_path("DYLD_LIBRARY_PATH", directory)
    else:
        _prepend_env_path("LD_LIBRARY_PATH", directory)
    return True


def _prepend_env_path(variable: str, directory: Path) -> None:
    current = os.environ.get(variable, "")
    entries = [str(directory)] + [e for e in current.split(os.pathsep) if e and e != str(directory)]
    os.environ[variable] = os.pathsep.join(entries)


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
