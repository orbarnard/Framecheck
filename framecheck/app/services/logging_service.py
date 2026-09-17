"""Application logging.

Logs go to a rotating file under the user's AppData, never to the UI by
default. "Open Log" in the Help menu reveals it.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Framecheck"

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_log_file: Path | None = None


def log_directory() -> Path:
    """Per-user log directory. Created on demand."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    directory = root / APP_NAME / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path | None:
    return _log_file


def configure_logging(verbose: bool = False) -> Path:
    """Install file and stderr handlers. Safe to call once at startup."""
    global _log_file

    directory = log_directory()
    _log_file = directory / "framecheck.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        _log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # A windowed build has no stderr; guard so logging does not raise.
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(stream_handler)

    logging.getLogger(__name__).info(
        "%s starting | python %s | %s %s",
        APP_NAME,
        platform.python_version(),
        platform.system(),
        platform.release(),
    )
    return _log_file
