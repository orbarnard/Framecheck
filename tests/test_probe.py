"""Integration tests that run the bundled ffprobe against the media fixtures.

Skipped, not failed, when tests/fixtures/ or the vendored binaries are absent:
a fresh clone has neither.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.media.probe import ProbeError, build_probe_args, probe

from conftest import requires_fixtures

pytestmark = requires_fixtures


# --------------------------------------------------------------------------
# Argument construction (no subprocess)
# --------------------------------------------------------------------------


def test_build_probe_args_is_a_list_of_strings_ending_in_the_path() -> None:
    """A list, never a shell string -- that is what keeps spaces and Unicode intact."""
    path = Path(r"C:\media\clip with spaces & ampersand.mp4")
    args = build_probe_args(path)

    assert isinstance(args, list)
    assert all(isinstance(a, str) for a in args)
    assert args[-1] == str(path)
    assert "-print_format" in args
    assert args[args.index("-print_format") + 1] == "json"
    assert "-show_format" in args and "-show_streams" in args


# --------------------------------------------------------------------------
# Fixture probes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "frames", "rate"),
    [
        ("exact_30s_2997.mp4", 900, Fraction(30000, 1001)),
        ("over_by_one_frame_2997.mp4", 901, Fraction(30000, 1001)),
        ("clip_23976.mp4", 240, Fraction(24000, 1001)),
        ("clip_25_pal.mp4", 125, Fraction(25, 1)),
    ],
)
def test_frame_count_and_rate_of_each_clip(name: str, frames: int, rate: Fraction) -> None:
    info = probe(Path(__file__).parent / "fixtures" / name)
    assert info.frame_rate is not None
    assert info.frame_rate.value == rate
    assert info.frame_count == frames


def test_one_frame_over_thirty_seconds_is_detectable(fixtures_dir: Path) -> None:
    """The whole point of the tool: 901 frames is not 900."""
    exact = probe(fixtures_dir / "exact_30s_2997.mp4")
    over = probe(fixtures_dir / "over_by_one_frame_2997.mp4")

    assert exact.frame_count == 900
    assert over.frame_count == 901
    assert over.duration is not None and exact.duration is not None
    assert over.duration.frames - exact.duration.frames == 1
    assert exact.duration.to_timecode() == "00:00:30;00"
    assert over.duration.to_timecode() == "00:00:30;01"


def test_h264_mp4_container_and_codec(fixtures_dir: Path) -> None:
    info = probe(fixtures_dir / "exact_30s_2997.mp4")
    assert info.container_format is not None
    assert "mp4" in info.container_format
    assert info.video is not None
    assert info.video.codec == "h264"
    assert info.video.resolution == "320x240"
    assert info.video.is_interlaced is False
    assert info.video.likely_variable_frame_rate is False
    assert info.size_bytes and info.size_bytes > 0


def test_prores_mov(fixtures_dir: Path) -> None:
    info = probe(fixtures_dir / "prores_422.mov")
    assert info.video is not None
    assert info.video.codec == "prores"
    assert info.video.pixel_format == "yuv422p10le"
    assert info.frame_rate is not None
    assert info.frame_rate.value == Fraction(25, 1)
    assert info.frame_count == 50


def test_audio_stream_is_described(fixtures_dir: Path) -> None:
    info = probe(fixtures_dir / "audio_44100.mp4")
    assert info.has_audio
    assert info.audio is not None
    assert info.audio.codec == "aac"
    assert info.audio.sample_rate_hz == 44100
    assert info.audio.bitrate_kbps is not None and info.audio.bitrate_kbps > 0


def test_filename_with_spaces_and_unicode_probes_successfully(fixtures_dir: Path) -> None:
    """Path handling regression: the argument list must survive non-ASCII names."""
    info = probe(fixtures_dir / "clip with spaces éç作品.mp4")
    assert info.video is not None
    assert info.video.codec == "h264"
    assert info.frame_count == 50
    assert info.path.name == "clip with spaces éç作品.mp4"


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_missing_file_raises_probe_error(tmp_path: Path) -> None:
    with pytest.raises(ProbeError):
        probe(tmp_path / "does_not_exist.mp4")


def test_directory_raises_probe_error(tmp_path: Path) -> None:
    with pytest.raises(ProbeError):
        probe(tmp_path)


def test_text_file_with_a_video_extension_raises_rather_than_returning_garbage(
    tmp_path: Path,
) -> None:
    impostor = tmp_path / "not_really.mp4"
    impostor.write_text("this is plain text, not a container\n" * 8, encoding="utf-8")
    with pytest.raises(ProbeError):
        probe(impostor)
