"""FFmpeg argv construction.

Pure functions: no subprocess, no filesystem, no Qt. Everything that decides
*what* FFmpeg will do lives here so it can be unit-tested against an exact
argument list, and so the "technical details" panel can show the user the same
command that will actually run.

Two choices in here are deliberate and easy to get wrong:

* **Input seeking for trims.** `-ss` before `-i` lets FFmpeg jump via the index
  instead of decoding-and-discarding, and since Framecheck always re-encodes a
  trimmed export (never stream-copies), that seek is also frame-accurate --
  FFmpeg decodes from the preceding keyframe and starts output at the requested
  instant. `-accurate_seek` is stated explicitly rather than relied on as a
  default. Both `-ss` and `-t` come from exact `Fraction` arithmetic so a
  900-frame cut at 29.97 is 30.030000 s, not 30.029999.

* **Two-pass loudnorm.** Single-pass loudnorm is a dynamic, look-ahead limiter:
  it hits the target on average but reshapes the programme as it goes. Feeding
  the measurements from an analysis pass back in as `measured_*` turns it into
  a linear gain change with a known result, which is what delivery needs.
"""

from __future__ import annotations

import math
from fractions import Fraction

from ..models.export_job import ExportJob, LoudnessResult
from ..models.media_info import MediaInfo
from ..models.profile import AudioTarget, FrameRateBehavior, TargetSpec

# Broadcast loudness specs (ATSC A/85, EBU R128) quote an LRA ceiling rather
# than a value to hit; 7 LU is the common delivery figure and is what loudnorm
# wants as its LRA parameter. AudioTarget does not carry one.
DEFAULT_TARGET_LRA = 7.0

# x264 is the only video encoder Framecheck ships an export path for; the map
# exists so a profile naming a codec by its stream name still resolves.
_ENCODERS = {
    "h264": "libx264",
    "avc": "libx264",
    "hevc": "libx265",
    "h265": "libx265",
    "prores": "prores_ks",
}

_FASTSTART_CONTAINERS = ("mp4", "mov", "m4v")


class BuildError(ValueError):
    """The job cannot be turned into a runnable command."""


def format_seconds(value: Fraction) -> str:
    """Exact seconds as a fixed 6-decimal string.

    Rounds the rational directly; never round-trips through a float, because
    the whole point of the Fraction is that 900/(30000/1001) is exactly 30.03.
    """
    micros = int(value * 1_000_000 + Fraction(1, 2))
    if micros < 0:
        micros = 0
    return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"


def format_rate(value: Fraction) -> str:
    """Frame rate as the exact rational FFmpeg understands, e.g. 30000/1001."""
    return f"{value.numerator}/{value.denominator}"


def _bitrate(mbps: float) -> str:
    """Mb/s as an FFmpeg rate token: 20 -> '20M', 12.5 -> '12.5M'."""
    if float(mbps) == int(mbps):
        return f"{int(mbps)}M"
    return f"{mbps:g}M"


def resolve_frame_rate(info: MediaInfo, target: TargetSpec) -> Fraction | None:
    """The frame rate the export will be written at.

    PRESERVE_IF_ALLOWED keeps the source rate whenever the destination accepts
    it -- resampling frame rate always costs motion quality, so it happens only
    when the destination leaves no choice.
    """
    source = info.frame_rate.value if info.frame_rate else None
    preferred = target.preferred_frame_rate

    if target.frame_rate_behavior is FrameRateBehavior.FORCE:
        return preferred or source
    if source is None:
        return preferred
    if not target.allowed_frame_rates or source in target.allowed_frame_rates:
        return source
    if preferred is not None:
        return preferred
    # Not allowed and no stated preference: the closest legal rate is the least
    # damaging conversion available.
    return min(target.allowed_frame_rates, key=lambda r: abs(r - source))


def needs_scaling(info: MediaInfo, target: TargetSpec) -> bool:
    if target.width is None or target.height is None or info.video is None:
        return False
    return (info.video.width, info.video.height) != (target.width, target.height)


def build_video_filters(info: MediaInfo, target: TargetSpec) -> list[str]:
    """Filter chain for picture geometry.

    Scale-to-fit then pad. Framecheck never crops on export: removing picture is
    a creative decision, and a tool that silently reframes a spot is a tool
    nobody should trust.
    """
    if not needs_scaling(info, target):
        return []
    width, height = target.width, target.height
    return [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        # Padding can leave a non-square SAR inherited from the source; state it.
        "setsar=1",
    ]


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_loudnorm_filter(
    audio: AudioTarget,
    loudness: LoudnessResult | None,
) -> str | None:
    """The loudnorm filter string, two-pass when measurements are available.

    Returns None when the target states no loudness, in which case levels are
    left exactly as they are.
    """
    if audio.loudness_lkfs is None:
        return None
    true_peak = audio.true_peak_db if audio.true_peak_db is not None else -2.0
    parts = [
        f"loudnorm=I={audio.loudness_lkfs:g}",
        f"TP={true_peak:g}",
        f"LRA={DEFAULT_TARGET_LRA:g}",
    ]

    raw = loudness.raw if loudness else {}
    measured = {
        "measured_I": _finite(raw.get("input_i")),
        "measured_TP": _finite(raw.get("input_tp")),
        "measured_LRA": _finite(raw.get("input_lra")),
        "measured_thresh": _finite(raw.get("input_thresh")),
    }
    if all(v is not None for v in measured.values()):
        parts += [f"{k}={v:g}" for k, v in measured.items()]
        offset = _finite(raw.get("target_offset"))
        if offset is not None:
            parts.append(f"offset={offset:g}")
        # linear=true is what makes the second pass a single gain change rather
        # than the dynamic compression a single pass applies.
        parts.append("linear=true")
    parts.append("print_format=summary")
    return ":".join(parts)


def build_export_args(job: ExportJob) -> list[str]:
    """The full argv for one export, excluding the executable itself."""
    info = job.source_info
    target = job.target
    args: list[str] = ["-hide_banner", "-nostdin"]
    args.append("-y" if job.overwrite else "-n")

    trim = job.trim if job.is_trimmed else None
    if trim is not None and trim.in_point.frames > 0:
        args += ["-accurate_seek", "-ss", format_seconds(trim.in_point.seconds)]

    args += ["-i", str(job.source_path)]

    if trim is not None:
        args += ["-t", format_seconds(trim.duration_seconds)]

    # Only the first video and first audio stream travel. A multi-track master
    # must not produce an output with surprise extra streams.
    args += ["-map", "0:v:0"]
    if info.has_audio:
        args += ["-map", "0:a:0?"]

    filters = build_video_filters(info, target)
    if filters:
        args += ["-vf", ",".join(filters)]

    args += ["-c:v", _ENCODERS.get(target.video_codec, target.video_codec)]
    args += ["-preset", "medium"]
    if target.video_profile:
        args += ["-profile:v", target.video_profile]
    args += ["-pix_fmt", target.pixel_format]

    if target.video_bitrate_mbps:
        maxrate = target.video_bitrate_max_mbps or target.video_bitrate_mbps * 1.5
        args += [
            "-b:v", _bitrate(target.video_bitrate_mbps),
            "-maxrate", _bitrate(maxrate),
            "-bufsize", _bitrate(maxrate * 2),
        ]
    else:
        args += ["-crf", "18"]

    rate = resolve_frame_rate(info, target)
    if rate is not None:
        if target.constant_frame_rate:
            # Duplicate/drop only. minterpolate invents frames that were never
            # shot, which is a visible change to the material, never a conform.
            args += ["-fps_mode", "cfr"]
        args += ["-r", format_rate(rate)]

    if not info.has_audio:
        # No audio in, no audio out. Synthesising silence would hide a missing
        # track behind a file that looks deliverable; conform.py raises it as
        # an action and validation flags it, and the user decides.
        args.append("-an")
    else:
        audio = target.audio
        if job.normalize_loudness:
            loudnorm = build_loudnorm_filter(audio, job.source_loudness)
            if loudnorm:
                args += ["-af", loudnorm]
        args += ["-c:a", audio.codec]
        args += ["-ac", str(audio.channels)]
        args += ["-ar", str(audio.sample_rate_hz)]
        args += ["-b:a", f"{audio.bitrate_kbps}k"]

    container = (target.container or "").lower()
    if target.faststart and container in _FASTSTART_CONTAINERS:
        args += ["-movflags", "+faststart"]

    args += ["-progress", "pipe:1", "-nostats"]
    args.append(str(job.output_path))
    return args


def build_command_text(args: list[str], executable: str = "ffmpeg") -> str:
    """The command as a copyable single line, for the log and details view.

    Quoting here is for human display only -- the real call passes a list, so
    nothing FFmpeg receives is ever shell-escaped.
    """
    parts = [executable, *args]
    return " ".join(f'"{p}"' if (" " in p or not p) else p for p in parts)
