"""parse_probe_json against hand-written ffprobe-shaped payloads.

No ffprobe runs here: the point is that the parser survives whatever a real
container throws at it, including fields that are absent or literally "N/A".
"""

from __future__ import annotations

import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.models.media_info import (
    AudioStreamInfo,
    MediaInfo,
    VideoStreamInfo,
    parse_probe_json,
)

PATH = Path("C:/media/clip.mp4")

H264_VIDEO_STREAM = {
    "index": 0,
    "codec_type": "video",
    "codec_name": "h264",
    "codec_long_name": "H.264 / AVC / MPEG-4 AVC",
    "profile": "High",
    "level": 40,
    "width": 1920,
    "height": 1080,
    "display_aspect_ratio": "16:9",
    "sample_aspect_ratio": "1:1",
    "pix_fmt": "yuv420p",
    "color_space": "bt709",
    "color_range": "tv",
    "color_transfer": "bt709",
    "color_primaries": "bt709",
    "r_frame_rate": "30000/1001",
    "avg_frame_rate": "30000/1001",
    "bit_rate": "8000000",
    "duration": "30.030000",
    "nb_frames": "900",
    "field_order": "progressive",
    "disposition": {"attached_pic": 0, "default": 1},
}

AAC_AUDIO_STREAM = {
    "index": 1,
    "codec_type": "audio",
    "codec_name": "aac",
    "codec_long_name": "AAC (Advanced Audio Coding)",
    "profile": "LC",
    "sample_rate": "48000",
    "channels": 2,
    "channel_layout": "stereo",
    "bit_rate": "128000",
    "duration": "30.016000",
    "sample_fmt": "fltp",
}

H264_MP4 = {
    "format": {
        "filename": str(PATH),
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "30.030000",
        "size": "31457280",
        "bit_rate": "8380000",
        "tags": {"major_brand": "isom", "encoder": "Lavf60.16.100"},
    },
    "streams": [H264_VIDEO_STREAM, AAC_AUDIO_STREAM],
}

PRORES_MOV = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "2.000000",
        "size": "1395889",
        "bit_rate": "5583556",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "prores",
            "codec_long_name": "Apple ProRes (iCodec Pro)",
            "profile": "Standard",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv422p10le",
            "r_frame_rate": "25/1",
            "avg_frame_rate": "25/1",
            "nb_frames": "50",
            "field_order": "progressive",
        }
    ],
}


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


def test_parses_a_normal_h264_mp4() -> None:
    info = parse_probe_json(H264_MP4, PATH)

    assert isinstance(info, MediaInfo)
    assert info.path == PATH
    assert info.container_format == "mov,mp4,m4a,3gp,3g2,mj2"
    assert info.container_long_name == "QuickTime / MOV"
    assert info.duration_seconds == Fraction(3003, 100)
    assert info.size_bytes == 31457280
    assert info.bitrate_bps == 8380000
    assert info.tags["encoder"] == "Lavf60.16.100"

    assert isinstance(info.video, VideoStreamInfo)
    assert info.video.codec == "h264"
    assert info.video.profile == "High"
    assert info.video.level == 40
    assert info.video.resolution == "1920x1080"
    assert info.video.pixel_format == "yuv420p"
    assert info.video.color_space == "bt709"
    assert info.video.r_frame_rate is not None
    assert info.video.r_frame_rate.value == Fraction(30000, 1001)
    assert info.video.effective_frame_rate == info.video.r_frame_rate

    assert isinstance(info.audio, AudioStreamInfo)
    assert info.audio.codec == "aac"
    assert info.audio.sample_rate_hz == 48000
    assert info.audio.channels == 2
    assert info.audio.bitrate_kbps == 128

    assert info.has_video and info.has_audio
    assert info.other_video == () and info.other_audio == ()
    assert info.raw is H264_MP4


def test_parses_a_prores_mov_without_audio() -> None:
    info = parse_probe_json(PRORES_MOV, PATH)
    assert info.video is not None
    assert info.video.codec == "prores"
    assert info.video.profile == "Standard"
    assert info.video.pixel_format == "yuv422p10le"
    assert info.frame_rate is not None
    assert info.frame_rate.value == Fraction(25, 1)
    assert info.has_audio is False
    assert info.audio is None


# --------------------------------------------------------------------------
# Hostile payloads
# --------------------------------------------------------------------------


def test_cover_art_is_not_treated_as_a_video_stream() -> None:
    """An MP3 with artwork reports a video stream; it is not a video file."""
    data = {
        "format": {"format_name": "mp3", "duration": "180.0"},
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 2,
            },
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "width": 600,
                "height": 600,
                "disposition": {"attached_pic": 1},
            },
        ],
    }
    info = parse_probe_json(data, PATH)
    assert info.video is None
    assert info.other_video == ()
    assert info.has_video is False
    assert info.has_audio is True
    assert info.frame_rate is None
    assert info.duration is None
    assert info.frame_count is None


def test_na_and_missing_fields_become_none_not_the_string() -> None:
    data = {
        "format": {
            "format_name": "N/A",
            "format_long_name": "unknown",
            "duration": "N/A",
            "size": "N/A",
            "bit_rate": "N/A",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "N/A",
                "profile": "unknown",
                "width": "N/A",
                "height": "",
                "r_frame_rate": "0/0",
                "avg_frame_rate": "N/A",
                "nb_frames": "N/A",
                "field_order": "unknown",
                "duration": "N/A",
                "bit_rate": "N/A",
            },
            {"codec_type": "audio", "codec_name": "N/A", "sample_rate": "N/A", "channels": "N/A"},
        ],
    }
    info = parse_probe_json(data, PATH)

    for value in (
        info.container_format,
        info.container_long_name,
        info.duration_seconds,
        info.size_bytes,
        info.bitrate_bps,
    ):
        assert value is None

    assert info.video is not None
    for value in (
        info.video.codec,
        info.video.profile,
        info.video.width,
        info.video.height,
        info.video.r_frame_rate,
        info.video.avg_frame_rate,
        info.video.nb_frames,
        info.video.field_order,
        info.video.resolution,
    ):
        assert value is None
    assert info.video.index == 0
    assert info.video.is_interlaced is False
    assert info.video.likely_variable_frame_rate is False
    assert info.audio is not None
    assert info.audio.codec is None
    assert info.audio.sample_rate_hz is None
    assert info.audio.bitrate_kbps is None


def test_completely_empty_payload_does_not_raise() -> None:
    info = parse_probe_json({}, PATH)
    assert info.video is None and info.audio is None
    assert info.tags == {}
    assert info.duration is None
    assert info.frame_count is None
    assert info.size_mb is None
    assert info.bitrate_mbps is None


def test_first_stream_of_each_kind_wins_and_the_rest_are_kept() -> None:
    data = {
        "format": {"format_name": "matroska,webm"},
        "streams": [
            dict(H264_VIDEO_STREAM, index=0, codec_name="h264"),
            dict(H264_VIDEO_STREAM, index=1, codec_name="hevc"),
            dict(H264_VIDEO_STREAM, index=2, codec_name="vp9"),
            dict(AAC_AUDIO_STREAM, index=3, codec_name="aac"),
            dict(AAC_AUDIO_STREAM, index=4, codec_name="ac3"),
            {"index": 5, "codec_type": "subtitle", "codec_name": "subrip"},
        ],
    }
    info = parse_probe_json(data, PATH)
    assert info.video is not None and info.video.codec == "h264"
    assert [s.codec for s in info.other_video] == ["hevc", "vp9"]
    assert info.audio is not None and info.audio.codec == "aac"
    assert [s.codec for s in info.other_audio] == ["ac3"]


@pytest.mark.parametrize(
    ("stream_extra", "expected"),
    [
        ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}, -90),
        ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 180}]}, 180),
        ({"tags": {"rotate": "90"}}, 90),
        ({"side_data_list": [{"side_data_type": "Display Matrix"}], "tags": {"rotate": "270"}}, 270),
        ({}, None),
    ],
)
def test_rotation_from_side_data_or_tags(stream_extra: dict, expected: int | None) -> None:
    data = {"streams": [dict(H264_VIDEO_STREAM, **stream_extra)]}
    info = parse_probe_json(data, PATH)
    assert info.video is not None
    assert info.video.rotation == expected


def test_side_data_rotation_wins_over_the_legacy_rotate_tag() -> None:
    data = {
        "streams": [
            dict(
                H264_VIDEO_STREAM,
                side_data_list=[{"rotation": -90}],
                tags={"rotate": "180"},
            )
        ]
    }
    info = parse_probe_json(data, PATH)
    assert info.video is not None
    assert info.video.rotation == -90


# --------------------------------------------------------------------------
# Derived properties
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("r_rate", "avg_rate", "expected"),
    [
        ("30000/1001", "30000/1001", False),
        ("25/1", "25/1", False),
        ("30000/1001", "24000/1001", True),
        ("30/1", "2997/100", True),
        ("0/0", "25/1", False),
        ("25/1", "N/A", False),
        ("0/0", "0/0", False),
    ],
)
def test_likely_variable_frame_rate(r_rate: str, avg_rate: str, expected: bool) -> None:
    data = {"streams": [dict(H264_VIDEO_STREAM, r_frame_rate=r_rate, avg_frame_rate=avg_rate)]}
    info = parse_probe_json(data, PATH)
    assert info.video is not None
    assert info.video.likely_variable_frame_rate is expected


@pytest.mark.parametrize(
    ("field_order", "expected"),
    [("tt", True), ("bb", True), ("tb", True), ("progressive", False), (None, False)],
)
def test_is_interlaced(field_order: str | None, expected: bool) -> None:
    stream = dict(H264_VIDEO_STREAM)
    if field_order is None:
        stream.pop("field_order")
    else:
        stream["field_order"] = field_order
    info = parse_probe_json({"streams": [stream]}, PATH)
    assert info.video is not None
    assert info.video.is_interlaced is expected


def test_duration_is_an_exact_frame_count() -> None:
    info = parse_probe_json(H264_MP4, PATH)
    duration = info.duration
    assert duration is not None
    assert duration.frames == 900
    assert duration.to_timecode() == "00:00:30;00"


def test_duration_rounds_to_nearest_so_the_last_frame_is_not_lost() -> None:
    """30.0299 s at 29.97 is 900 frames; flooring would report 899."""
    data = {
        "format": {"duration": "30.029900"},
        "streams": [dict(H264_VIDEO_STREAM, nb_frames="N/A")],
    }
    info = parse_probe_json(data, PATH)
    assert info.duration is not None
    assert info.duration.frames == 900


def test_frame_count_prefers_nb_frames_over_the_computed_value() -> None:
    data = {
        "format": {"duration": "30.030000"},
        "streams": [dict(H264_VIDEO_STREAM, nb_frames="901")],
    }
    info = parse_probe_json(data, PATH)
    assert info.duration is not None and info.duration.frames == 900
    assert info.frame_count == 901


def test_frame_count_falls_back_to_duration_times_rate() -> None:
    data = {
        "format": {"duration": "5.000000"},
        "streams": [dict(H264_VIDEO_STREAM, nb_frames="N/A", r_frame_rate="25/1")],
    }
    info = parse_probe_json(data, PATH)
    assert info.frame_count == 125


def test_size_and_bitrate_conversions() -> None:
    info = parse_probe_json(H264_MP4, PATH)
    assert info.size_mb == pytest.approx(30.0)
    assert info.bitrate_mbps == pytest.approx(8.38)


@pytest.mark.parametrize("zero", ["0", 0])
def test_zero_size_and_bitrate_read_as_none(zero: object) -> None:
    data = {"format": {"size": zero, "bit_rate": zero}}
    info = parse_probe_json(data, PATH)
    assert info.size_mb is None
    assert info.bitrate_mbps is None


# --------------------------------------------------------------------------
# Layering
# --------------------------------------------------------------------------


def test_models_and_utils_import_no_qt() -> None:
    """models/ and utils/ must stay usable from a worker with no GUI stack."""
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import framecheck.app.models.media_info\n"
        "import framecheck.app.models.media_time\n"
        "import framecheck.app.models.media_file\n"
        "import framecheck.app.utils.paths\n"
        "assert not any(m.startswith('PySide6') for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith('PySide6'))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
