"""Locating the bundled libmpv per platform, and making python-mpv see it.

python-mpv finds libmpv by name through ctypes at import time and never looks
at sys.path, so which name the bundled copy carries, and which environment
variable points at it, is different on every platform. These tests pin that
down without loading any library.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from framecheck.app.services import binaries


@pytest.fixture
def vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty vendor/ the module resolves to, with the caches cleared."""
    monkeypatch.setattr(binaries, "vendor_dir", lambda: tmp_path)
    binaries.libmpv_dir.cache_clear()
    yield tmp_path
    binaries.libmpv_dir.cache_clear()


def _platform(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(binaries.sys, "platform", name)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", "libmpv-2.dll"),
        ("darwin", "libmpv.dylib"),
        ("linux", "libmpv.so.2"),
    ],
)
def test_bundled_libmpv_name_is_what_python_mpv_asks_for(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    _platform(monkeypatch, platform)
    assert binaries.libmpv_filename() == expected


@pytest.mark.parametrize(
    ("platform", "present"),
    [
        ("win32", "mpv-2.dll"),
        ("win32", "libmpv-2.dll"),
        ("darwin", "libmpv.dylib"),
        ("darwin", "libmpv.2.dylib"),
        ("linux", "libmpv.so.2"),
    ],
)
def test_libmpv_dir_accepts_every_name_the_platform_loader_would(
    vendor: Path, monkeypatch: pytest.MonkeyPatch, platform: str, present: str
) -> None:
    _platform(monkeypatch, platform)
    playback = vendor / "playback"
    playback.mkdir()
    (playback / present).write_bytes(b"")
    assert binaries.libmpv_dir() == playback


def test_libmpv_dir_ignores_another_platforms_library(
    vendor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _platform(monkeypatch, "darwin")
    playback = vendor / "playback"
    playback.mkdir()
    (playback / "libmpv-2.dll").write_bytes(b"")
    assert binaries.libmpv_dir() is None
    assert binaries.register_libmpv_search_path() is False


def test_macos_registration_puts_the_bundle_first_on_dyld_library_path(
    vendor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _platform(monkeypatch, "darwin")
    playback = vendor / "playback"
    playback.mkdir()
    (playback / "libmpv.dylib").write_bytes(b"")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/somewhere/else")

    assert binaries.register_libmpv_search_path() is True
    entries = os.environ["DYLD_LIBRARY_PATH"].split(os.pathsep)
    assert entries == [str(playback), "/somewhere/else"]


def test_registration_is_idempotent(vendor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform(monkeypatch, "darwin")
    playback = vendor / "playback"
    playback.mkdir()
    (playback / "libmpv.dylib").write_bytes(b"")
    monkeypatch.delenv("DYLD_LIBRARY_PATH", raising=False)

    binaries.register_libmpv_search_path()
    binaries.register_libmpv_search_path()
    assert os.environ["DYLD_LIBRARY_PATH"] == str(playback)


def test_windows_registration_prepends_path(vendor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _platform(monkeypatch, "win32")
    playback = vendor / "playback"
    playback.mkdir()
    (playback / "libmpv-2.dll").write_bytes(b"")
    monkeypatch.setenv("PATH", "C:\\Windows")
    # add_dll_directory exists only on Windows; the PATH half is what is testable here.
    monkeypatch.delattr(binaries.os, "add_dll_directory", raising=False)

    assert binaries.register_libmpv_search_path() is True
    assert os.environ["PATH"].split(os.pathsep)[0] == str(playback)
