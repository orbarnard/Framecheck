"""What the export will change, decided before anything is encoded.

`plan_conform` produces the list the CONFORM panel shows: one line per real
change, in the user's language. Fields that already match the target produce
nothing -- a list that says "video codec: h264 -> h264" trains people to stop
reading it, and the one line that mattered goes past unread.

The same list is stored on the ExportJob that builds the FFmpeg command, so the
promise the user approved and the command that runs cannot drift apart.
"""

from __future__ import annotations

import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from ..models.export_job import ConformAction, ExportJob, LoudnessResult
from ..models.media_info import MediaInfo
from ..models.media_time import FrameRate
from ..models.media_time import format_seconds as format_clock
from ..models.profile import Profile, TargetSpec
from ..models.trim import ExactCut, Fit, TargetDuration, TargetMode, TrimRange
from ..services.binaries import ffprobe_path, run_tool
from ..utils.paths import OutputDestination, build_output_path
from .ffmpeg_builder import needs_scaling, resolve_frame_rate

log = logging.getLogger(__name__)

# 300 packets is a second or two of wall clock and plenty to characterise the
# frame cadence; reading a whole file to answer "is this VFR" is not worth it.
VFR_PACKET_SAMPLE = 300
VFR_TIMEOUT_SECONDS = 30


def plan_conform(
    info: MediaInfo,
    target: TargetSpec,
    trim: TrimRange | None = None,
    loudness: LoudnessResult | None = None,
    normalize: bool = False,
    exact_cut: ExactCut | None = None,
) -> tuple[ConformAction, ...]:
    """Every change this export makes, and why."""
    actions: list[ConformAction] = []
    actions += _container_actions(info, target)
    actions += _video_actions(
        info, target, exact_cut is None or exact_cut.fit is Fit.CONVERT
    )
    actions += _audio_actions(info, target)
    actions += _loudness_actions(info, target, loudness, normalize)
    actions += _trim_actions(info, trim)
    actions += _exact_cut_actions(info, exact_cut)
    return tuple(actions)


def resolve_exact_cut(
    info: MediaInfo,
    target: TargetSpec,
    trim: TrimRange | None,
    duration: TargetDuration | None,
) -> tuple[ExactCut | None, TrimRange | None]:
    """The exact cut this export can make, and the trim to make it from.

    Only when the trim is still exactly the preset's source frames (a nudged
    OUT is the user's cut, not the preset's). Then, in order:

    * the destination keeps the source rate and accepts the whole rate: the
      preset's cut as planned (sped up, or holding a frame);
    * the destination converts the rate anyway (59.94 -> 30) to one that lands
      the slot: cut exactly the target in real time and let the conversion
      make the frames -- no speed change needed;
    * otherwise the trim is cut back to the longest that fits: a frame under
      is accepted, a frame over is not.
    """
    rate = info.frame_rate
    if duration is None or rate is None:
        return None, trim
    # A preset that spans the whole file (a master already cut to 29.988 s)
    # reaches here as no trim at all; it is still the preset's cut.
    span = trim if trim is not None else (TrimRange.full(info.duration) if info.duration else None)
    cut = duration.exact_cut(rate)
    if span is None or cut is None or span.frame_count != cut.source_frames:
        return None, trim
    resolved = resolve_frame_rate(info, target)
    if resolved == rate.value and (
        not target.allowed_frame_rates or cut.rate.value in target.allowed_frame_rates
    ):
        return cut, trim
    if resolved is not None and resolved != rate.value:
        # The destination converts anyway. Its own rate may not land the slot
        # (59.94 -> 29.97); the whole rate beside it does, if it is allowed.
        whole = Fraction(FrameRate(resolved).nominal)
        candidates = [resolved]
        if not target.allowed_frame_rates or whole in target.allowed_frame_rates:
            candidates.append(whole)
        for out in candidates:
            frames = duration.seconds * out
            if frames.denominator == 1:
                converted = ExactCut(rate, FrameRate(out), int(frames), int(frames), Fit.CONVERT)
                return converted, trim
    under = TargetDuration(duration.seconds, TargetMode.AT_OR_UNDER).frames_at(rate)
    return None, TrimRange(span.in_point, span.in_point.offset_frames(under))


def _container_actions(info: MediaInfo, target: TargetSpec) -> list[ConformAction]:
    current = info.container_format or "unknown"
    # ffprobe reports a family for MP4-likes ("mov,mp4,m4a,..."); if the target
    # is a member of that family the file is already in the right container.
    family = {part.strip().lower() for part in current.split(",")}
    if target.container.lower() in family:
        return []
    return [
        ConformAction(
            label="Container",
            from_value=current,
            to_value=target.container,
            reason=f"Destination requires {target.container.upper()}",
        )
    ]


def _video_actions(
    info: MediaInfo, target: TargetSpec, include_rate: bool = True
) -> list[ConformAction]:
    actions: list[ConformAction] = []
    video = info.video
    if video is None:
        return actions

    if (video.codec or "").lower() != target.video_codec.lower():
        actions.append(
            ConformAction(
                label="Video codec",
                from_value=video.codec or "unknown",
                to_value=target.video_codec,
                reason="Re-encoded to the delivery codec",
            )
        )

    if needs_scaling(info, target):
        source_aspect = _aspect(video.width, video.height)
        target_aspect = _aspect(target.width, target.height)
        reason = "Scaled to the delivery frame size"
        if source_aspect is not None and source_aspect != target_aspect:
            reason = (
                "Aspect ratio differs: the picture is scaled to fit and "
                "padded with bars, never cropped. A reframed master is "
                "recommended instead"
            )
        actions.append(
            ConformAction(
                label="Resolution",
                from_value=video.resolution or "unknown",
                to_value=f"{target.width}x{target.height}",
                reason=reason,
                affects_picture_or_sound=True,
            )
        )

    if include_rate:  # an exact cut states its own rate change
        actions += _frame_rate_actions(info, target)

    source_mbps = video.bitrate_bps / 1_000_000 if video.bitrate_bps else None
    if target.video_bitrate_mbps and (
        source_mbps is None or abs(source_mbps - target.video_bitrate_mbps) > 0.5
    ):
        actions.append(
            ConformAction(
                label="Video bitrate",
                from_value=f"{source_mbps:.1f} Mb/s" if source_mbps else "unknown",
                to_value=f"{target.video_bitrate_mbps:g} Mb/s",
                reason="Encoded to the destination's bitrate",
            )
        )
    return actions


def _frame_rate_actions(info: MediaInfo, target: TargetSpec) -> list[ConformAction]:
    source = info.frame_rate
    resolved = resolve_frame_rate(info, target)
    if resolved is None or source is None:
        return []

    variable = bool(info.video and info.video.likely_variable_frame_rate)
    if resolved != source.value:
        return [
            ConformAction(
                label="Frame rate",
                from_value=("variable " if variable else "") + source.label(),
                to_value=f"{_rate_label(resolved)} CFR"
                if target.constant_frame_rate
                else _rate_label(resolved),
                reason="Source rate is not accepted by this destination; "
                "frames are duplicated or dropped, never interpolated",
                affects_picture_or_sound=True,
            )
        ]
    if variable and target.constant_frame_rate:
        return [
            ConformAction(
                label="Frame rate",
                from_value=f"variable (~{source.label()})",
                to_value=f"{source.label()} CFR",
                reason="Variable frame rate is made constant for delivery",
                affects_picture_or_sound=True,
            )
        ]
    return []


def _audio_actions(info: MediaInfo, target: TargetSpec) -> list[ConformAction]:
    audio = info.audio
    spec = target.audio
    if audio is None:
        return [
            ConformAction(
                label="Audio",
                from_value="No audio stream",
                to_value="Silent output",
                reason="This file has no audio. Framecheck will not invent "
                "silence to pad a track; the output stays silent video.",
                affects_picture_or_sound=True,
            )
        ]

    actions: list[ConformAction] = []
    if (audio.codec or "").lower() != spec.codec.lower():
        actions.append(
            ConformAction(
                label="Audio codec",
                from_value=audio.codec or "unknown",
                to_value=spec.codec,
                reason="Re-encoded to the delivery codec",
            )
        )
    if audio.channels and audio.channels != spec.channels:
        actions.append(
            ConformAction(
                label="Audio channels",
                from_value=audio.channel_layout or f"{audio.channels} ch",
                to_value=f"{spec.channels} ch",
                reason="Downmixed to the delivery channel count"
                if audio.channels > spec.channels
                else "Upmixed to the delivery channel count",
                affects_picture_or_sound=True,
            )
        )
    if audio.sample_rate_hz and audio.sample_rate_hz != spec.sample_rate_hz:
        actions.append(
            ConformAction(
                label="Sample rate",
                from_value=f"{audio.sample_rate_hz / 1000:g} kHz",
                to_value=f"{spec.sample_rate_hz / 1000:g} kHz",
                reason="Resampled for delivery",
            )
        )
    source_kbps = audio.bitrate_kbps
    if source_kbps is None or abs(source_kbps - spec.bitrate_kbps) > 8:
        actions.append(
            ConformAction(
                label="Audio bitrate",
                from_value=f"{source_kbps} kb/s" if source_kbps else "unknown",
                to_value=f"{spec.bitrate_kbps} kb/s",
                reason="Encoded to the destination's bitrate",
            )
        )
    return actions


def _loudness_actions(
    info: MediaInfo,
    target: TargetSpec,
    loudness: LoudnessResult | None,
    normalize: bool,
) -> list[ConformAction]:
    goal = target.audio.loudness_lkfs
    if not normalize or goal is None or not info.has_audio:
        return []
    if loudness is None:
        return [
            ConformAction(
                label="Loudness",
                from_value="not measured",
                to_value=f"{goal:g} LKFS",
                reason="Without an analysis pass the correction is a single-pass "
                "approximation, not an exact gain change",
                affects_picture_or_sound=True,
            )
        ]
    delta = loudness.delta_to(goal)
    if abs(delta) < 0.1:
        return []
    return [
        ConformAction(
            label="Loudness",
            from_value=f"{loudness.integrated_lufs:.1f} LUFS",
            to_value=f"{goal:g} LKFS",
            reason=f"{delta:+.1f} dB applied as a linear gain change",
            affects_picture_or_sound=True,
        )
    ]


def _trim_actions(info: MediaInfo, trim: TrimRange | None) -> list[ConformAction]:
    if trim is None or trim.is_empty:
        return []
    source_duration = info.duration
    if source_duration is not None and trim.is_full(source_duration):
        return []
    source_frames = info.frame_count
    from_text = format_clock(info.duration_seconds)
    if source_frames:
        from_text = f"{from_text} ({source_frames} frames)"
    return [
        ConformAction(
            label="Trim",
            from_value=from_text,
            to_value=f"{format_clock(trim.duration_seconds)} "
            f"({trim.frame_count} frames)",
            reason=f"IN {trim.in_point.to_timecode()} / "
            f"OUT {trim.out_point.to_timecode()} (OUT exclusive)",
        )
    ]


def _exact_cut_actions(info: MediaInfo, cut: ExactCut | None) -> list[ConformAction]:
    if cut is None or info.frame_rate is None:
        return []
    held = cut.held_frames
    if cut.fit is Fit.CONVERT:
        return [
            ConformAction(
                label="Exact duration",
                from_value=f"{format_clock(cut.seconds)} of source at {info.frame_rate.label()}",
                to_value=f"{format_clock(cut.seconds)} ({cut.frames} f at {cut.rate.label()})",
                reason="Cut at exactly the target in real time; the frame rate "
                "conversion makes the frames, so sound is untouched and in sync",
            )
        ]
    if cut.fit is Fit.SPEED:
        how = f"picture and sound sped up {float(cut.speed - 1):.1%}, in sync"
    else:
        edge = "start" if cut.fit is Fit.HOLD_START else "end"
        how = f"{held} frame{'s' if held != 1 else ''} held at the {edge}, audio unchanged"
    return [
        ConformAction(
            label="Exact duration",
            from_value=f"{cut.source_frames} f at {info.frame_rate.label()}",
            to_value=f"{format_clock(cut.seconds)} ({cut.frames} f at {cut.rate.label()})",
            reason=f"Every source frame shown once at {cut.rate.label()} fps; {how}",
            affects_picture_or_sound=True,
        )
    ]


def _aspect(width: int | None, height: int | None) -> Fraction | None:
    if not width or not height:
        return None
    return Fraction(width, height)


def _rate_label(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def build_job(
    info: MediaInfo,
    profile: Profile,
    *,
    destination: OutputDestination | None = None,
    trim: TrimRange | None = None,
    target_duration: TargetDuration | None = None,
    loudness: LoudnessResult | None = None,
    normalize: bool = False,
    overwrite: bool = False,
    filename: str | None = None,
    additional_profiles: tuple[Profile, ...] = (),
) -> ExportJob:
    """Assemble a complete, runnable ExportJob including its output path."""
    destination = destination or OutputDestination.same_as_source()
    output_path = build_output_path(
        info.path,
        destination,
        suffix_tag=profile.filename_tag(),
        extension=profile.output_extension,
        filename=filename,
    )
    trim = trim if (trim is not None and not trim.is_empty) else None
    cut, trim = resolve_exact_cut(info, profile.target, trim, target_duration)
    return ExportJob(
        source_path=info.path,
        source_info=info,
        output_path=output_path,
        target=profile.target,
        profile=profile,
        additional_profiles=additional_profiles,
        trim=trim,
        exact_cut=cut,
        normalize_loudness=normalize,
        source_loudness=loudness,
        actions=plan_conform(info, profile.target, trim, loudness, normalize, cut),
        overwrite=overwrite,
    )


class ExportOutcome(NamedTuple):
    """What the written file will measure: the trim panel's banner shows this,
    so the promise on screen comes from the same job that encodes."""

    seconds: Fraction | None
    frames: int | None
    rate: Fraction | None


def export_outcome(job: ExportJob) -> ExportOutcome:
    cut = job.exact_cut
    rate = cut.rate.value if cut else resolve_frame_rate(job.source_info, job.target)
    seconds = job.picture_seconds or job.expected_duration_seconds
    frames = cut.frames if cut else (
        int(seconds * rate + Fraction(1, 2)) if seconds is not None and rate else None
    )
    return ExportOutcome(seconds, frames, rate)


def build_frame_rate_mode_args(path: Path) -> list[str]:
    """ffprobe arguments for the packet-timestamp cadence sample."""
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time",
        "-print_format",
        "json",
        "-read_intervals",
        f"%+#{VFR_PACKET_SAMPLE}",
        str(path),
    ]


def detect_frame_rate_mode(path: Path) -> str:
    """Report "cfr", "vfr" or "unknown" from real packet timestamps.

    `MediaInfo.likely_variable_frame_rate` only compares the two rates the
    container advertises, which a remux can make agree on a file that is in
    fact variable. This looks at the frames.
    """
    executable = ffprobe_path()
    if executable is None:
        return "unknown"
    try:
        result = run_tool(executable, build_frame_rate_mode_args(path), timeout=VFR_TIMEOUT_SECONDS)
        packets = json.loads(result.stdout or "{}").get("packets") or []
    except Exception as exc:  # timeout, bad JSON, dead share
        log.debug("frame rate mode detection failed for %s: %s", path, exc)
        return "unknown"

    times = sorted(
        float(p["pts_time"])
        for p in packets
        if p.get("pts_time") not in (None, "N/A")
    )
    if len(times) < 4:
        return "unknown"

    # Packets arrive in decode order with B-frames, so sort first; what matters
    # is whether the presentation grid is evenly spaced.
    deltas = [b - a for a, b in zip(times, times[1:]) if b > a]
    if len(deltas) < 3:
        return "unknown"
    mean = sum(deltas) / len(deltas)
    spread = max(deltas) - min(deltas)
    # A 1 % spread comfortably covers timebase rounding on a CFR file and is far
    # below the jitter any genuinely variable source shows.
    return "cfr" if spread <= max(mean * 0.01, 0.001) else "vfr"
