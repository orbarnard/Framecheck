"""ffprobe invocation.

The only place ffprobe arguments are constructed. Returns MediaInfo; callers
never see JSON. Runs synchronously -- call it from a worker, not the UI thread.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models.media_info import MediaInfo, parse_probe_json
from ..services.binaries import ToolNotFoundError, ffprobe_path, run_tool

log = logging.getLogger(__name__)

# Long enough for a slow network drive, short enough that a hung probe does not
# occupy a thread-pool slot forever.
PROBE_TIMEOUT_SECONDS = 60


class ProbeError(RuntimeError):
    """ffprobe could not describe the file."""


def build_probe_args(path: Path) -> list[str]:
    """Arguments for a full format+stream probe of `path`.

    Separated out so it can be unit-tested and logged without running anything.
    """
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        # Rotation and similar live in side data, not the stream fields.
        "-show_entries",
        "stream_side_data=rotation",
        str(path),
    ]


def probe(path: Path) -> MediaInfo:
    """Inspect `path` with ffprobe.

    Raises ProbeError for anything the caller should show the user: a missing
    file, a missing binary, a non-media file, a timeout.
    """
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"File not found: {path}")

    executable = ffprobe_path()
    if executable is None:
        raise ProbeError(
            "ffprobe was not found. Run tools/fetch_binaries.py to download the "
            "bundled binaries."
        )

    args = build_probe_args(path)
    log.debug("ffprobe %s", " ".join(args))
    try:
        result = run_tool(executable, args, timeout=PROBE_TIMEOUT_SECONDS)
    except ToolNotFoundError as exc:
        raise ProbeError(str(exc)) from exc
    except Exception as exc:  # timeout, OSError on a dead network share
        raise ProbeError(f"Could not inspect file: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        message = detail[-1] if detail else f"ffprobe exited with {result.returncode}"
        raise ProbeError(message)

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unreadable output: {exc}") from exc

    if not data.get("streams") and not data.get("format"):
        raise ProbeError("No media streams found in this file")

    info = parse_probe_json(data, path)

    # ffprobe omits format.size for some containers; the filesystem knows.
    if info.size_bytes is None:
        try:
            object.__setattr__(info, "size_bytes", path.stat().st_size)
        except OSError:
            pass

    log.info(
        "probed %s: %s %s %s",
        path.name,
        info.container_format,
        info.video.codec if info.video else "no-video",
        info.video.resolution if info.video else "",
    )
    return info
