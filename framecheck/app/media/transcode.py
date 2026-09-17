"""Running the encode: progress, cancellation, and not leaving wreckage behind.

Progress comes from `-progress pipe:1`, which emits machine-readable key=value
lines. It is reported as a fraction of `job.expected_duration_seconds` rather
than of the source length, because a trimmed export knows its exact output
duration up front -- so the bar is honest from the first frame instead of
jumping when the encoder passes the OUT point.

Cancelling deletes the partial file. A half-written MP4 sitting in a delivery
folder with a plausible name is the most dangerous artefact this program could
produce, and it will not produce one.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Callable

from ..models.export_job import ExportJob, ExportResult, ExportState
from ..services.binaries import NO_WINDOW_FLAGS, ffmpeg_path
from ..utils.paths import same_file
from .ffmpeg_builder import build_command_text, build_export_args

log = logging.getLogger(__name__)

ERROR_TAIL_LINES = 20


class TranscodeError(RuntimeError):
    """The export could not be started."""


def run_export(
    job: ExportJob,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ExportResult:
    """Encode `job`, reporting progress and honouring cancellation."""
    if same_file(job.output_path, job.source_path):
        # The source file is the one thing in this program that is sacred.
        raise TranscodeError("The export would overwrite its own source file")

    executable = ffmpeg_path()
    if executable is None:
        raise TranscodeError(
            "ffmpeg was not found. Run tools/fetch_binaries.py to download the "
            "bundled binaries."
        )

    if job.output_path.exists() and not job.overwrite:
        # FFmpeg's own `-n` refusal exits *zero* ("File already exists. Exiting.")
        # which would otherwise be reported as a successful export of a file we
        # never wrote. Answer the question ourselves instead of trusting it.
        return ExportResult(
            job=job,
            state=ExportState.FAILED,
            error=f"{job.output_path.name} already exists and overwrite is off",
        )

    args = build_export_args(job)
    log.info("export: %s", build_command_text(args, str(executable)))

    expected = job.expected_duration_seconds
    total_seconds = float(expected) if expected else 0.0
    tail: deque[str] = deque(maxlen=ERROR_TAIL_LINES)
    started = time.monotonic()

    process = subprocess.Popen(
        [str(executable), *args],
        stdout=subprocess.PIPE,
        # Merged so one reader thread cannot deadlock on a full pipe. Progress
        # lines are `key=value`, so they stay distinguishable from log text.
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=NO_WINDOW_FLAGS,
    )

    speed = ""
    frame = ""
    cancelled = False
    assert process.stdout is not None
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "out_time_us":
                if on_progress is not None and total_seconds > 0:
                    try:
                        done = max(0.0, int(value) / 1_000_000)
                    except ValueError:
                        continue
                    status = " ".join(p for p in (f"frame {frame}" if frame else "", speed) if p)
                    on_progress(min(1.0, done / total_seconds), status.strip())
            elif key == "frame":
                frame = value
            elif key == "speed":
                speed = value
            elif key not in _PROGRESS_KEYS:
                tail.append(line)
    finally:
        if cancelled:
            _terminate(process)
        else:
            process.wait()

    elapsed = time.monotonic() - started

    if cancelled:
        _remove_partial(job.output_path)
        return ExportResult(job=job, state=ExportState.CANCELLED, duration_seconds=elapsed)

    if process.returncode != 0:
        _remove_partial(job.output_path)
        message = "\n".join(tail) or f"ffmpeg exited with {process.returncode}"
        return ExportResult(
            job=job, state=ExportState.FAILED, error=message, duration_seconds=elapsed
        )

    if not job.output_path.exists():
        # A zero exit with no file is not a success, whatever FFmpeg says.
        message = "\n".join(tail) or "ffmpeg wrote no output file"
        return ExportResult(
            job=job, state=ExportState.FAILED, error=message, duration_seconds=elapsed
        )

    if on_progress is not None:
        on_progress(1.0, "")
    return ExportResult(
        job=job,
        state=ExportState.DONE,
        output_path=job.output_path,
        duration_seconds=elapsed,
    )


# Keys `-progress` emits; anything else on a `key=value` line is FFmpeg log text
# worth keeping for an error message.
_PROGRESS_KEYS = frozenset(
    {
        "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
        "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress",
    }
)


def _terminate(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - a wedged encoder
        process.kill()
        process.wait()


def _remove_partial(path: Path) -> None:
    """Delete a truncated output so it can never be mistaken for a deliverable."""
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:  # pragma: no cover - locked by a scanner
        log.warning("could not remove partial output %s: %s", path, exc)
