"""Exact frame/time arithmetic -- the layer a frame-accurate trim rests on."""

from __future__ import annotations

from fractions import Fraction

import pytest

from framecheck.app.models.media_time import (
    COMMON_RATES,
    FrameRate,
    MediaTime,
    Rounding,
    format_duration_short,
    format_seconds,
)

R_2997 = FrameRate(Fraction(30000, 1001))
R_23976 = FrameRate(Fraction(24000, 1001))
R_5994 = FrameRate(Fraction(60000, 1001))
R_25 = FrameRate(Fraction(25, 1))
R_24 = FrameRate(Fraction(24, 1))
R_30 = FrameRate(Fraction(30, 1))


# --------------------------------------------------------------------------
# FrameRate.parse
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30000/1001", Fraction(30000, 1001)),
        ("24000/1001", Fraction(24000, 1001)),
        ("25/1", Fraction(25, 1)),
        ("0/0", None),
        ("", None),
        ("   ", None),
        (None, None),
        ("garbage", None),
        ("N/A", None),
        (23.976, Fraction(24000, 1001)),
        (29.97, Fraction(30000, 1001)),
        (30, Fraction(30, 1)),
        (-1, None),
        (-23.976, None),
        (0, None),
        (Fraction(25, 1), Fraction(25, 1)),
        (Fraction(-25, 1), None),
    ],
)
def test_parse_returns_exact_rational_or_none(raw: object, expected: Fraction | None) -> None:
    parsed = FrameRate.parse(raw)
    if expected is None:
        assert parsed is None
    else:
        assert parsed is not None
        assert parsed.value == expected


@pytest.mark.parametrize("raw", [23.976, "23.976", Fraction(23976, 1000)])
def test_near_miss_decimals_snap_to_the_exact_rational(raw: object) -> None:
    """A probe reporting 23.976 must still give frame-exact 24000/1001."""
    parsed = FrameRate.parse(raw)
    assert parsed is not None
    assert parsed.value == Fraction(24000, 1001)


def test_parse_of_a_frame_rate_is_the_same_frame_rate() -> None:
    assert FrameRate.parse(R_2997) is R_2997


@pytest.mark.parametrize(
    ("rate", "drop"),
    [(R_2997, True), (R_5994, True), (R_24, False), (R_25, False), (R_30, False), (R_23976, False)],
)
def test_is_drop_frame_only_for_2997_and_5994(rate: FrameRate, drop: bool) -> None:
    assert rate.is_drop_frame is drop


@pytest.mark.parametrize(
    ("rate", "nominal", "label"),
    [
        (R_2997, 30, "29.97"),
        (R_25, 25, "25"),
        (R_23976, 24, "23.976"),
        (R_24, 24, "24"),
        (R_5994, 60, "59.94"),
    ],
)
def test_nominal_and_label(rate: FrameRate, nominal: int, label: str) -> None:
    assert rate.nominal == nominal
    assert rate.label() == label
    assert str(rate) == label


def test_zero_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        FrameRate(Fraction(0, 1))


# --------------------------------------------------------------------------
# from_seconds rounding
# --------------------------------------------------------------------------


def test_floor_ceil_and_nearest_disagree_between_frames() -> None:
    between = Fraction(103, 10) / R_25.value  # 10.3 frames in
    assert MediaTime.from_seconds(between, R_25, Rounding.FLOOR).frames == 10
    assert MediaTime.from_seconds(between, R_25, Rounding.CEIL).frames == 11
    assert MediaTime.from_seconds(between, R_25, Rounding.NEAREST).frames == 10


def test_nearest_rounds_half_up() -> None:
    """Half-to-even would send frame 10.5 down to 10; a marker drag reads better up."""
    half = Fraction(21, 2) / R_25.value  # exactly 10.5 frames
    assert MediaTime.from_seconds(half, R_25, Rounding.NEAREST).frames == 11
    also_half = Fraction(23, 2) / R_25.value  # exactly 11.5 frames
    assert MediaTime.from_seconds(also_half, R_25, Rounding.NEAREST).frames == 12


def test_exact_frame_boundary_is_unaffected_by_rounding_mode() -> None:
    exact = Fraction(10) / R_25.value
    frames = {
        MediaTime.from_seconds(exact, R_25, mode).frames for mode in Rounding
    }
    assert frames == {10}


@pytest.mark.parametrize("seconds", [-0.001, -1, -1000, Fraction(-1, 3)])
def test_negative_seconds_clamp_to_zero(seconds: float | Fraction | int) -> None:
    assert MediaTime.from_seconds(seconds, R_25).frames == 0


def test_negative_frame_index_is_rejected() -> None:
    with pytest.raises(ValueError):
        MediaTime(-1, R_25)


def test_zero_helper() -> None:
    assert MediaTime.zero(R_25) == MediaTime(0, R_25)


# --------------------------------------------------------------------------
# The duration cases that matter
# --------------------------------------------------------------------------


def test_nine_hundred_frames_at_2997_is_exactly_thirty_seconds_of_timecode() -> None:
    mt = MediaTime(900, R_2997)
    assert mt.seconds == Fraction(900 * 1001, 30000)
    assert mt.to_timecode() == "00:00:30;00"


def test_one_frame_over_thirty_is_one_frame_apart() -> None:
    exact = MediaTime(900, R_2997)
    over = MediaTime(901, R_2997)
    assert over.to_timecode() == "00:00:30;01"
    assert over.frames - exact.frames == 1
    assert (over - exact).frames == 1


def test_comparing_two_durations_is_an_integer_comparison() -> None:
    """No float seconds anywhere in the ordering, so 'over by one frame' is exact."""
    exact = MediaTime(900, R_2997)
    over = MediaTime(901, R_2997)
    assert exact < over
    assert exact != over
    assert isinstance(exact.frames, int) and isinstance(over.frames, int)
    assert isinstance(exact.seconds, Fraction)
    assert sorted([over, exact]) == [exact, over]


def test_thirty_seconds_at_pal_is_seven_hundred_and_fifty_frames() -> None:
    assert MediaTime.from_seconds(30.0, R_25).frames == 750
    assert MediaTime.from_seconds(Fraction(30), R_25).frames == 750


@pytest.mark.parametrize("rate_value", COMMON_RATES)
@pytest.mark.parametrize("frames", [0, 1, 899, 900, 1801])
def test_seconds_round_trip_back_to_the_same_frame(rate_value: Fraction, frames: int) -> None:
    """The property frame-accurate trimming depends on."""
    rate = FrameRate(rate_value)
    mt = MediaTime(frames, rate)
    assert MediaTime.from_seconds(mt.seconds, rate) == mt


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------


def test_at_rate_reexpresses_the_same_instant() -> None:
    thirty_seconds_ish = MediaTime(900, R_2997)  # 30.03 s
    assert thirty_seconds_ish.at_rate(R_25).frames == 751  # 30.03 * 25 = 750.75
    assert thirty_seconds_ish.at_rate(R_25, Rounding.FLOOR).frames == 750


def test_at_rate_with_the_same_rate_is_identity() -> None:
    mt = MediaTime(900, R_2997)
    assert mt.at_rate(R_2997) is mt


def test_offset_frames_clamps_at_zero() -> None:
    assert MediaTime(3, R_25).offset_frames(-10).frames == 0
    assert MediaTime(3, R_25).offset_frames(4).frames == 7


def test_offset_seconds_clamps_at_zero() -> None:
    assert MediaTime(3, R_25).offset_seconds(-10).frames == 0
    assert MediaTime(0, R_25).offset_seconds(1).frames == 25


def test_addition_and_subtraction_floor_at_zero() -> None:
    a = MediaTime(3, R_25)
    b = MediaTime(10, R_25)
    assert (a + b).frames == 13
    assert (b - a).frames == 7
    assert (a - b).frames == 0


def test_addition_across_rates_converts_first() -> None:
    assert (MediaTime(25, R_25) + MediaTime(900, R_2997)).frames == 25 + 751


@pytest.mark.parametrize(
    ("frames", "expected"), [(5, 10), (15, 15), (50, 20), (10, 10), (20, 20)]
)
def test_clamped_between_bounds(frames: int, expected: int) -> None:
    lo, hi = MediaTime(10, R_25), MediaTime(20, R_25)
    assert MediaTime(frames, R_25).clamped(lo, hi).frames == expected


def test_clamped_with_open_bounds() -> None:
    mt = MediaTime(15, R_25)
    assert mt.clamped() == mt
    assert mt.clamped(lo=MediaTime(20, R_25)).frames == 20
    assert mt.clamped(hi=MediaTime(5, R_25)).frames == 5


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------


def test_to_clock_truncates_rather_than_rounds() -> None:
    """Frame 2 at 29.97 is 0.0667s; a rounded readout would claim 0.067."""
    mt = MediaTime(2, R_2997)
    assert float(mt.seconds) == pytest.approx(0.0667333, rel=1e-6)
    assert mt.to_clock() == "00:00:00.066"


def test_format_seconds_truncates_and_never_rounds_up_a_second() -> None:
    assert format_seconds(Fraction(19999, 10000)) == "00:00:01.999"
    assert format_seconds(Fraction(999999, 1000000)) == "00:00:00.999"


@pytest.mark.parametrize(
    ("frames", "rate", "expected"),
    [
        (0, R_25, "00:00:00:00"),
        (125, R_25, "00:00:05:00"),
        (24 * 3600 + 5, R_24, "01:00:00:05"),
        (23, R_24, "00:00:00:23"),
        (24, R_24, "00:00:01:00"),
    ],
)
def test_non_drop_timecode(frames: int, rate: FrameRate, expected: str) -> None:
    assert MediaTime(frames, rate).to_timecode() == expected


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        (0, "00:00:00;00"),
        (1799, "00:00:59;29"),
        # Frames ;00 and ;01 are dropped at the top of each minute except every tenth.
        (1800, "00:01:00;02"),
        (17981, "00:09:59;29"),
        (17982, "00:10:00;00"),
    ],
)
def test_drop_frame_timecode_at_minute_boundaries(frames: int, expected: str) -> None:
    assert MediaTime(frames, R_2997).to_timecode() == expected


def test_drop_frame_uses_a_semicolon_and_non_drop_a_colon() -> None:
    assert ";" in MediaTime(100, R_2997).to_timecode()
    assert ";" not in MediaTime(100, R_25).to_timecode()


def test_str_of_media_time_is_the_clock() -> None:
    mt = MediaTime(900, R_2997)
    assert str(mt) == mt.to_clock() == "00:00:30.030"


@pytest.mark.parametrize(
    ("seconds", "decimals", "expected"),
    [
        (None, 3, "--:--:--.---"),
        (None, 0, "--:--:--"),
        (0, 3, "00:00:00.000"),
        (-5, 3, "00:00:00.000"),
        (3725.5, 0, "01:02:05"),
        (Fraction(3003, 100), 3, "00:00:30.030"),
    ],
)
def test_format_seconds(seconds: object, decimals: int, expected: str) -> None:
    assert format_seconds(seconds, decimals) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "--:--"), (0, "0:00"), (59.9, "0:59"), (60, "1:00"), (3725, "1:02:05")],
)
def test_format_duration_short(seconds: object, expected: str) -> None:
    assert format_duration_short(seconds) == expected
