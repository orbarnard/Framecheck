"""Local preferences, backed by QSettings.

Typed accessors only -- no raw key strings escape this module, so a renamed
preference is a one-line change here rather than a grep across the UI.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

from ..utils.paths import OutputDestination, OutputMode

ORGANISATION = "Framecheck"
APPLICATION = "Framecheck"

_MAX_RECENT = 10


class Settings:
    """Thin typed wrapper over QSettings."""

    def __init__(self) -> None:
        self._s = QSettings(QSettings.IniFormat, QSettings.UserScope, ORGANISATION, APPLICATION)

    # -- window -----------------------------------------------------------

    @property
    def window_geometry(self) -> QByteArray | None:
        value = self._s.value("window/geometry")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._s.setValue("window/geometry", value)

    @property
    def window_state(self) -> QByteArray | None:
        value = self._s.value("window/state")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    @window_state.setter
    def window_state(self, value: QByteArray) -> None:
        self._s.setValue("window/state", value)

    # -- folders ----------------------------------------------------------

    @property
    def last_input_dir(self) -> Path | None:
        return self._read_path("paths/last_input_dir")

    @last_input_dir.setter
    def last_input_dir(self, value: Path) -> None:
        self._s.setValue("paths/last_input_dir", str(value))

    @property
    def last_output_dir(self) -> Path | None:
        return self._read_path("paths/last_output_dir")

    @last_output_dir.setter
    def last_output_dir(self, value: Path) -> None:
        self._s.setValue("paths/last_output_dir", str(value))

    @property
    def output_destination(self) -> OutputDestination:
        mode_value = str(self._s.value("output/mode", OutputMode.SAME_AS_SOURCE.value))
        try:
            mode = OutputMode(mode_value)
        except ValueError:
            mode = OutputMode.SAME_AS_SOURCE
        custom = self._read_path("output/custom_dir")
        if mode is OutputMode.CUSTOM and custom is None:
            # The remembered folder is gone (unplugged drive, deleted share).
            # Fall back rather than showing a destination that cannot be used.
            return OutputDestination.same_as_source()
        return OutputDestination(mode, custom)

    @output_destination.setter
    def output_destination(self, value: OutputDestination) -> None:
        self._s.setValue("output/mode", value.mode.value)
        self._s.setValue("output/custom_dir", str(value.custom_dir) if value.custom_dir else "")

    # -- recents ----------------------------------------------------------

    @property
    def recent_files(self) -> list[Path]:
        return self._read_path_list("recent/files")

    def push_recent_file(self, path: Path) -> None:
        self._push_recent("recent/files", path)

    @property
    def recent_folders(self) -> list[Path]:
        return self._read_path_list("recent/folders")

    def push_recent_folder(self, path: Path) -> None:
        self._push_recent("recent/folders", path)

    def clear_recents(self) -> None:
        self._s.remove("recent/files")
        self._s.remove("recent/folders")

    # -- behaviour --------------------------------------------------------

    @property
    def recursive_scan(self) -> bool:
        return self._s.value("scan/recursive", False, type=bool)

    @recursive_scan.setter
    def recursive_scan(self, value: bool) -> None:
        self._s.setValue("scan/recursive", bool(value))

    @property
    def volume(self) -> int:
        return max(0, min(100, self._s.value("player/volume", 100, type=int)))

    @volume.setter
    def volume(self, value: int) -> None:
        self._s.setValue("player/volume", max(0, min(100, int(value))))

    @property
    def muted(self) -> bool:
        return self._s.value("player/muted", False, type=bool)

    @muted.setter
    def muted(self, value: bool) -> None:
        self._s.setValue("player/muted", bool(value))

    @property
    def default_profile_id(self) -> str:
        """Reserved for the Milestone 3 profile engine."""
        return str(self._s.value("profiles/default", "") or "")

    @default_profile_id.setter
    def default_profile_id(self, value: str) -> None:
        self._s.setValue("profiles/default", value)

    # -- internals --------------------------------------------------------

    def _read_path(self, key: str) -> Path | None:
        raw = self._s.value(key, "")
        if not raw:
            return None
        path = Path(str(raw))
        try:
            return path if path.is_dir() or path.is_file() else None
        except OSError:
            # A disconnected network path raises rather than returning False.
            return None

    def _read_path_list(self, key: str) -> list[Path]:
        raw = self._s.value(key, [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        result: list[Path] = []
        for item in raw or []:
            path = Path(str(item))
            try:
                if path.exists():
                    result.append(path)
            except OSError:
                continue
        return result

    def _push_recent(self, key: str, path: Path) -> None:
        text = str(path)
        existing = [str(p) for p in self._read_path_list(key)]
        entries = [text] + [e for e in existing if e.lower() != text.lower()]
        self._s.setValue(key, entries[:_MAX_RECENT])

    def sync(self) -> None:
        self._s.sync()
