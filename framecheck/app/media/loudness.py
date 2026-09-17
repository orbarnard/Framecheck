"""Loudness measurement via FFmpeg's loudnorm analysis pass.

This is the *first* of the two loudnorm passes. It decodes the audio, measures
it, and keeps every number FFmpeg reported so the encode pass can hand them
straight back (see `ffmpeg_builder.build_loudnorm_filter`). Measuring the
trimmed range rather than the whole file matters: a :30 lifted out of a
five-minute master has its own integrated loudness.

FFmpeg prints the analysis JSON to **stderr**, after its usual log noise, so the
parser hunts for the last well-formed `{...}` block rather than assuming the
stream is JSON.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path

from ..models.export_job import LoudnessResult
from ..models.profile import AudioTarget
from ..models.trim import TrimRange
from ..services.binaries import ToolNotFoundError, ffmpeg_path, run_tool
from .ffmpeg_builder import DEFAULT_TARGET_LRA, format_seconds

log = logging.getLogger(__name__)

# Decoding a long master takes real time; this is generous enough for a feature
# on a network share and short enough not to hold a thread pool slot forever.
LOUDNESS_TIMEOUT_SECONDS = 600

# loudnorm emits a flat object, so a non-nested brace match is enough.
_JSON_BLOCK = re.compile(r"\{[^{}]*\}")


class LoudnessError(RuntimeError):
    """The file's loudness could not be measured."""


def build_loudness_args(
    path: Path,
    trim: TrimRange | None = None,
    target: AudioTarget | None = None,
) -> list[str]:
    """Arguments for the analysis pass. Separated out so it is testable dry.

    `target` only shapes the I/TP/LRA values loudnorm reports `target_offset`
    against; the measured values are independent of it.
    """
    loudness = target.loudness_lkfs if target and target.loudness_lkfs is not None else -24.0
    true_peak = target.true_peak_db if target and target.true_peak_db is not None else -2.0

    args = ["-hide_banner", "-nostdin"]
    if trim is not None and not trim.is_empty and trim.in_point.frames > 0:
        args += ["-accurate_seek", "-ss", format_seconds(trim.in_point.seconds)]
    args += ["-i", str(path)]
    if trim is not None and not trim.is_empty:
        args += ["-t", format_seconds(trim.duration_seconds)]
    args += [
        # Analysis reads audio only. Without -vn FFmpeg still sets up and pulls
        # the video stream, which on a 600 MB ProRes master is the entire cost
        # of the operation.
        "-vn",
        "-sn",
        "-dn",
        # Fails loudly when the file has no audio, which is the answer we want.
        "-map", "0:a:0",
        "-af",
        f"loudnorm=I={loudness:g}:TP={true_peak:g}:LRA={DEFAULT_TARGET_LRA:g}:print_format=json",
        "-f", "null",
        "-",
    ]
    return args


def parse_loudness_output(stderr: str) -> LoudnessResult:
    """Pull the loudnorm JSON out of FFmpeg's stderr.

    Raises LoudnessError when no parseable block is present -- which is what
    happens when there was no audio to measure.
    """
    blocks = _JSON_BLOCK.findall(stderr or "")
    for text in reversed(blocks):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if "input_i" not in data:
            continue
        return LoudnessResult(
            # Silence reports -inf (or the -70 floor). Keep the value honest;
            # callers decide whether normalising silence makes sense.
            integrated_lufs=_as_float(data.get("input_i")),
            true_peak_db=_as_optional_float(data.get("input_tp")),
            loudness_range=_as_optional_float(data.get("input_lra")),
            threshold=_as_optional_float(data.get("input_thresh")),
            raw={str(k): str(v) for k, v in data.items()},
        )
    raise LoudnessError("FFmpeg produced no loudness measurement for this file")


def _as_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("-inf")


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def analyze_loudness(
    path: Path,
    trim: TrimRange | None = None,
    timeout: float = LOUDNESS_TIMEOUT_SECONDS,
    target: AudioTarget | None = None,
    max_seconds: float | None = None,
    source_seconds: float | None = None,
) -> LoudnessResult:
    """Measure integrated loudness of `path` (optionally only `trim`).

    `max_seconds` caps how much audio is measured. When the programme is longer
    than the cap, a window from the middle is used and the result is flagged
    `estimated` -- long-form content stays responsive, and the reading is
    honestly labelled rather than quietly partial. Export re-measures in full
    before normalising.
    """
    path = Path(path)
    if not path.is_file():
        raise LoudnessError(f"File not found: {path}")

    estimated = False
    analyzed = source_seconds
    if (
        max_seconds
        and source_seconds
        and source_seconds > max_seconds
        and trim is None
    ):
        estimated = True
        analyzed = max_seconds
        start = max(0.0, (source_seconds - max_seconds) / 2)
        trim = None  # windowing is expressed directly in the arguments below

    executable = ffmpeg_path()
    if executable is None:
        raise LoudnessError(
            "ffmpeg was not found. Run tools/fetch_binaries.py to download the "
            "bundled binaries."
        )

    args = build_loudness_args(path, trim, target)
    if estimated:
        # Insert the sampling window: input seek before -i, duration after it.
        index = args.index("-i")
        args[index:index] = ["-accurate_seek", "-ss", f"{start:.3f}"]
        args.insert(args.index("-i") + 2, "-t")
        args.insert(args.index("-t") + 1, f"{max_seconds:.3f}")

    started = time.monotonic()
    log.debug("loudnorm analysis: %s", " ".join(args))
    try:
        result = run_tool(executable, args, timeout=timeout)
    except ToolNotFoundError as exc:
        raise LoudnessError(str(exc)) from exc
    except Exception as exc:  # timeout, OSError on a dead share
        raise LoudnessError(f"Could not measure loudness: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"ffmpeg exited with {result.returncode}"
        if "matches no streams" in (result.stderr or ""):
            raise LoudnessError("This file has no audio stream to measure")
        raise LoudnessError(tail)

    measured = parse_loudness_output(result.stderr or "")
    measured = replace(measured, estimated=estimated, analyzed_seconds=analyzed)
    elapsed = time.monotonic() - started
    log.info(
        "measured %s at %.1f LUFS in %.1fs%s",
        path.name,
        measured.integrated_lufs,
        elapsed,
        f" (estimated from {analyzed:.0f}s)" if estimated else "",
    )
    return measured
