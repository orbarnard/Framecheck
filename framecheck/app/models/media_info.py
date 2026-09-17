"""Parsed ffprobe output.

The UI never sees raw ffprobe JSON. It sees these dataclasses. The raw stream
dicts are kept on MediaInfo so later milestones (conformance, validation) can
read fields this milestone does not surface, without re-probing the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .media_time import FrameRate, MediaTime, Rounding


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_fraction(value: object) -> Fraction | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return Fraction(text).limit_denominator(1_000_000)
    except (ValueError, ZeroDivisionError):
        return None


def _clean(value: object) -> str | None:
    """Normalise an ffprobe string field, mapping absent markers to None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in ("N/A", "UNKNOWN"):
        return None
    return text


@dataclass(frozen=True)
class VideoStreamInfo:
    index: int
    codec: str | None = None
    codec_long: str | None = None
    profile: str | None = None
    level: int | None = None
    width: int | None = None
    height: int | None = None
    display_aspect_ratio: str | None = None
    sample_aspect_ratio: str | None = None
    pixel_format: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    # r_frame_rate: the rate the container advertises. For CFR content this is
    # the real rate; for VFR it is the least common multiple of frame periods.
    r_frame_rate: FrameRate | None = None
    # avg_frame_rate: frames / duration. Diverges from r_frame_rate on VFR.
    avg_frame_rate: FrameRate | None = None
    bitrate_bps: int | None = None
    duration_seconds: Fraction | None = None
    nb_frames: int | None = None
    field_order: str | None = None
    rotation: int | None = None

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None

    @property
    def is_interlaced(self) -> bool:
        """True when the container flags interlaced field order.

        Container metadata only. Content that is interlaced but flagged
        progressive needs frame analysis, which is a later milestone.
        """
        return bool(self.field_order) and self.field_order not in ("progressive",)

    @property
    def likely_variable_frame_rate(self) -> bool:
        """Heuristic VFR indicator from the two reported rates.

        A real VFR determination needs packet timestamp analysis (Milestone 4).
        This only says the two rates disagree by more than rounding noise.
        """
        if self.r_frame_rate is None or self.avg_frame_rate is None:
            return False
        if self.avg_frame_rate.value == 0:
            return False
        delta = abs(self.r_frame_rate.value - self.avg_frame_rate.value)
        return delta > Fraction(1, 100)

    @property
    def effective_frame_rate(self) -> FrameRate | None:
        """The rate to use for frame arithmetic and display."""
        return self.r_frame_rate or self.avg_frame_rate


@dataclass(frozen=True)
class AudioStreamInfo:
    index: int
    codec: str | None = None
    codec_long: str | None = None
    profile: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bitrate_bps: int | None = None
    duration_seconds: Fraction | None = None
    sample_format: str | None = None

    @property
    def bitrate_kbps(self) -> int | None:
        return round(self.bitrate_bps / 1000) if self.bitrate_bps else None


@dataclass(frozen=True)
class MediaInfo:
    """Everything ffprobe told us about one file."""

    path: Path
    container_format: str | None = None
    container_long_name: str | None = None
    duration_seconds: Fraction | None = None
    size_bytes: int | None = None
    bitrate_bps: int | None = None
    video: VideoStreamInfo | None = None
    audio: AudioStreamInfo | None = None
    other_video: tuple[VideoStreamInfo, ...] = ()
    other_audio: tuple[AudioStreamInfo, ...] = ()
    tags: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def frame_rate(self) -> FrameRate | None:
        return self.video.effective_frame_rate if self.video else None

    @property
    def duration(self) -> MediaTime | None:
        """Duration as an exact frame count, when a frame rate is known.

        Rounded to nearest: a 30s/29.97 file reporting 30.0300 seconds is 900
        frames, and flooring would lose the last frame.
        """
        rate = self.frame_rate
        if rate is None or self.duration_seconds is None:
            return None
        return MediaTime.from_seconds(self.duration_seconds, rate, Rounding.NEAREST)

    @property
    def frame_count(self) -> int | None:
        """Best available total frame count.

        Prefers the container's nb_frames, which is authoritative when present;
        falls back to duration * rate.
        """
        if self.video and self.video.nb_frames:
            return self.video.nb_frames
        duration = self.duration
        return duration.frames if duration else None

    @property
    def size_mb(self) -> float | None:
        return self.size_bytes / 1_048_576 if self.size_bytes else None

    @property
    def bitrate_mbps(self) -> float | None:
        return self.bitrate_bps / 1_000_000 if self.bitrate_bps else None


def parse_probe_json(data: dict, path: Path) -> MediaInfo:
    """Build a MediaInfo from ffprobe's ``-show_format -show_streams`` JSON.

    Tolerates missing fields throughout: exotic containers omit plenty, and a
    partial answer is more useful than an exception.
    """
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    videos: list[VideoStreamInfo] = []
    audios: list[AudioStreamInfo] = []

    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video":
            # Cover art and thumbnails appear as video streams. Exclude them so
            # an MP3 with artwork is not treated as a video file.
            disposition = stream.get("disposition") or {}
            if disposition.get("attached_pic"):
                continue
            videos.append(_parse_video_stream(stream))
        elif kind == "audio":
            audios.append(_parse_audio_stream(stream))

    return MediaInfo(
        path=path,
        container_format=_clean(fmt.get("format_name")),
        container_long_name=_clean(fmt.get("format_long_name")),
        duration_seconds=_to_fraction(fmt.get("duration")),
        size_bytes=_to_int(fmt.get("size")),
        bitrate_bps=_to_int(fmt.get("bit_rate")),
        video=videos[0] if videos else None,
        audio=audios[0] if audios else None,
        other_video=tuple(videos[1:]),
        other_audio=tuple(audios[1:]),
        tags={str(k): str(v) for k, v in (fmt.get("tags") or {}).items()},
        raw=data,
    )


def _parse_video_stream(stream: dict) -> VideoStreamInfo:
    rotation = None
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            rotation = _to_int(side_data.get("rotation"))
    if rotation is None:
        rotation = _to_int((stream.get("tags") or {}).get("rotate"))

    return VideoStreamInfo(
        index=_to_int(stream.get("index")) or 0,
        codec=_clean(stream.get("codec_name")),
        codec_long=_clean(stream.get("codec_long_name")),
        profile=_clean(stream.get("profile")),
        level=_to_int(stream.get("level")),
        width=_to_int(stream.get("width")),
        height=_to_int(stream.get("height")),
        display_aspect_ratio=_clean(stream.get("display_aspect_ratio")),
        sample_aspect_ratio=_clean(stream.get("sample_aspect_ratio")),
        pixel_format=_clean(stream.get("pix_fmt")),
        color_space=_clean(stream.get("color_space")),
        color_range=_clean(stream.get("color_range")),
        color_transfer=_clean(stream.get("color_transfer")),
        color_primaries=_clean(stream.get("color_primaries")),
        r_frame_rate=FrameRate.parse(stream.get("r_frame_rate")),
        avg_frame_rate=FrameRate.parse(stream.get("avg_frame_rate")),
        bitrate_bps=_to_int(stream.get("bit_rate")),
        duration_seconds=_to_fraction(stream.get("duration")),
        nb_frames=_to_int(stream.get("nb_frames")),
        field_order=_clean(stream.get("field_order")),
        rotation=rotation,
    )


def _parse_audio_stream(stream: dict) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=_to_int(stream.get("index")) or 0,
        codec=_clean(stream.get("codec_name")),
        codec_long=_clean(stream.get("codec_long_name")),
        profile=_clean(stream.get("profile")),
        sample_rate_hz=_to_int(stream.get("sample_rate")),
        channels=_to_int(stream.get("channels")),
        channel_layout=_clean(stream.get("channel_layout")),
        bitrate_bps=_to_int(stream.get("bit_rate")),
        duration_seconds=_to_fraction(stream.get("duration")),
        sample_format=_clean(stream.get("sample_fmt")),
    )
