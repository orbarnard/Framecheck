"""Parsing FFmpeg's loudnorm analysis, noise and all."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from framecheck.app.media.loudness import (
    LoudnessError,
    analyze_loudness,
    build_loudness_args,
    parse_loudness_output,
)
from framecheck.app.models.media_time import FrameRate, MediaTime
from framecheck.app.models.profile import AudioTarget
from framecheck.app.models.trim import TrimRange

from conftest import requires_fixtures

R2997 = FrameRate(Fraction(30000, 1001))

# Captured verbatim from ffmpeg 6.x, including the log lines either side --
# this is exactly what the parser has to cope with in the wild.
REAL_STDERR = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'spot.mov':
  Duration: 00:00:30.03, start: 0.000000, bitrate: 187654 kb/s
  Stream #0:1[0x2](eng): Audio: pcm_s24le (in24 / 0x34326E69), 48000 Hz, stereo, s32 (24 bit)
Stream mapping:
  Stream #0:1 -> #0:0 (pcm_s24le (native) -> pcm_s16le (native))
Output #0, null, to 'pipe:':
  Stream #0:0: Audio: pcm_s16le, 192000 Hz, stereo, s16, 6144 kb/s
size=N/A time=00:00:30.03 bitrate=N/A speed= 118x
[Parsed_loudnorm_0 @ 000001f0a1b2c3d0]
{
\t"input_i" : "-19.83",
\t"input_tp" : "-4.12",
\t"input_lra" : "6.70",
\t"input_thresh" : "-30.21",
\t"output_i" : "-24.02",
\t"output_tp" : "-8.31",
\t"output_lra" : "6.60",
\t"output_thresh" : "-34.38",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.02"
}
"""

SILENT_STDERR = """\
[Parsed_loudnorm_0 @ 0000021d]
{
\t"input_i" : "-inf",
\t"input_tp" : "-inf",
\t"input_lra" : "0.00",
\t"input_thresh" : "-inf",
\t"output_i" : "-inf",
\t"output_tp" : "-inf",
\t"output_lra" : "0.00",
\t"output_thresh" : "-inf",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.00"
}
"""


def test_parses_the_json_block_out_of_the_log_noise():
    result = parse_loudness_output(REAL_STDERR)
    assert result.integrated_lufs == pytest.approx(-19.83)
    assert result.true_peak_db == pytest.approx(-4.12)
    assert result.loudness_range == pytest.approx(6.70)
    assert result.threshold == pytest.approx(-30.21)


def test_raw_keeps_every_measurement_for_the_second_pass():
    raw = parse_loudness_output(REAL_STDERR).raw
    assert raw["input_i"] == "-19.83"
    assert raw["target_offset"] == "0.02"
    assert set(raw) >= {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}


def test_delta_to_target():
    assert parse_loudness_output(REAL_STDERR).delta_to(-24.0) == pytest.approx(-4.17)


def test_silence_reports_negative_infinity_rather_than_guessing():
    result = parse_loudness_output(SILENT_STDERR)
    assert result.integrated_lufs == float("-inf")
    assert result.true_peak_db == float("-inf")


def test_no_json_block_is_an_error():
    with pytest.raises(LoudnessError):
        parse_loudness_output("ffmpeg version 6.1\nStream map '0:a:0' matches no streams.\n")


def test_trailing_json_wins_when_several_blocks_appear():
    noisy = REAL_STDERR + '\n{"input_i" : "-11.50", "input_tp" : "-1.00", ' \
        '"input_lra" : "3.00", "input_thresh" : "-22.00", "target_offset" : "0.10"}\n'
    assert parse_loudness_output(noisy).integrated_lufs == pytest.approx(-11.50)


def test_loudness_args_shape():
    args = build_loudness_args(Path("C:/media/spot.mov"))
    assert "-hide_banner" in args
    assert args[args.index("-i") + 1] == str(Path("C:/media/spot.mov"))
    assert args[args.index("-map") + 1] == "0:a:0"
    assert args[-2:] == ["-f", "null"] or args[-3:] == ["-f", "null", "-"]
    chain = args[args.index("-af") + 1]
    assert chain.endswith("print_format=json")
    assert "-ss" not in args


def test_loudness_args_measure_only_the_trimmed_range():
    trim = TrimRange(MediaTime(300, R2997), MediaTime(1200, R2997))
    args = build_loudness_args(Path("C:/media/spot.mov"), trim)
    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-accurate_seek") + 1] == "-ss"
    assert args[args.index("-t") + 1] == "30.030000"


def test_loudness_args_take_their_target_from_the_profile():
    chain = build_loudness_args(
        Path("C:/x.mov"), None, AudioTarget(loudness_lkfs=-16.0, true_peak_db=-1.0)
    )
    joined = chain[chain.index("-af") + 1]
    assert "I=-16" in joined
    assert "TP=-1" in joined


def test_missing_file_is_a_clear_error():
    with pytest.raises(LoudnessError, match="not found"):
        analyze_loudness(Path("C:/nope/missing.mov"))


@requires_fixtures
def test_analyze_a_real_fixture(fixtures_dir):
    result = analyze_loudness(fixtures_dir / "audio_44100.mp4", timeout=60)
    assert result.raw["input_i"]
    assert result.integrated_lufs <= 0


@requires_fixtures
def test_a_file_without_audio_says_so(fixtures_dir):
    with pytest.raises(LoudnessError, match="no audio"):
        analyze_loudness(fixtures_dir / "clip_23976.mp4", timeout=60)


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------


@requires_fixtures
def test_a_short_source_is_measured_in_full(fixtures_dir):
    """Under the cap, nothing is sampled -- an ad is measured whole."""
    result = analyze_loudness(
        fixtures_dir / "audio_44100.mp4", max_seconds=180.0, source_seconds=2.0, timeout=60
    )
    assert not result.estimated


@requires_fixtures
def test_a_long_source_is_sampled_and_labelled(fixtures_dir):
    """Over the cap, the reading is partial and must say so."""
    result = analyze_loudness(
        fixtures_dir / "audio_44100.mp4", max_seconds=1.0, source_seconds=2.0, timeout=60
    )
    assert result.estimated
    assert result.analyzed_seconds == 1.0
    assert result.integrated_lufs <= 0


@requires_fixtures
def test_an_explicit_trim_is_never_sampled(fixtures_dir):
    """A user-chosen range is the thing being measured; do not second-guess it."""
    from fractions import Fraction

    from framecheck.app.models.media_time import FrameRate, MediaTime
    from framecheck.app.models.trim import TrimRange

    rate = FrameRate(Fraction(25, 1))
    trim = TrimRange(MediaTime(0, rate), MediaTime(25, rate))
    result = analyze_loudness(
        fixtures_dir / "audio_44100.mp4",
        trim,
        max_seconds=0.5,
        source_seconds=2.0,
        timeout=60,
    )
    assert not result.estimated
