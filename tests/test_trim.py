"""The trim model: OUT-exclusive arithmetic and the two meanings of ":30"."""

from __future__ import annotations

from fractions import Fraction

import pytest

from framecheck.app.models.media_time import FrameRate, MediaTime
from framecheck.app.models.trim import (
    STANDARD_TARGET_SECONDS,
    ExactCut,
    Fit,
    TargetDuration,
    TargetMode,
    TrimRange,
)

R_2997 = FrameRate(Fraction(30000, 1001))
R_25 = FrameRate(Fraction(25, 1))
UNDER = TargetMode.AT_OR_UNDER


def at(frames: int, rate: FrameRate = R_2997) -> MediaTime:
    return MediaTime(frames, rate)


# --------------------------------------------------------------------------
# OUT-exclusive arithmetic
# --------------------------------------------------------------------------


def test_frame_count_excludes_the_out_point() -> None:
    trim = TrimRange(at(0), at(900))
    assert trim.frame_count == 900
    assert trim.duration.frames == 900
    # The last frame in the export is 899; OUT names the first one dropped.
    assert trim.out_point.frames - 1 == 899


def test_a_range_of_one_frame_is_not_empty() -> None:
    trim = TrimRange(at(10), at(11))
    assert trim.frame_count == 1
    assert not trim.is_empty


def test_equal_points_are_an_empty_range() -> None:
    trim = TrimRange(at(10), at(10))
    assert trim.frame_count == 0
    assert trim.is_empty


def test_out_before_in_is_rejected() -> None:
    with pytest.raises(ValueError):
        TrimRange(at(10), at(9))


def test_mixed_rates_are_rejected() -> None:
    with pytest.raises(ValueError):
        TrimRange(at(0, R_2997), at(100, R_25))


# --------------------------------------------------------------------------
# for_target
# --------------------------------------------------------------------------


def test_thirty_seconds_at_2997_stays_under_thirty_at_or_under() -> None:
    """AT_OR_UNDER (the fallback when a destination refuses the whole rate)
    never overruns the slot.

    A platform policing a :30 measures wall-clock seconds, so 30.030 s is
    rejected while 29.997 s passes.
    """
    trim = TrimRange.for_target(at(0), TargetDuration.of(30, UNDER))
    assert trim.frame_count == 899
    assert trim.duration_seconds == Fraction(899 * 1001, 30000)
    assert float(trim.duration_seconds) == pytest.approx(29.9967, abs=1e-4)
    assert trim.duration_seconds < 30


def test_timecode_mode_still_gives_the_broadcast_900_frames() -> None:
    """Traffic systems want 00:00:30;00, which really runs 30.030 s."""
    trim = TrimRange.for_target(at(0), TargetDuration.of(30, TargetMode.TIMECODE))
    assert trim.frame_count == 900
    assert float(trim.duration_seconds) == pytest.approx(30.030, abs=1e-6)
    assert trim.out_point.to_timecode() == "00:00:30;00"


@pytest.mark.parametrize(
    "rate",
    [
        FrameRate(Fraction(24000, 1001)),
        FrameRate(Fraction(24, 1)),
        FrameRate(Fraction(25, 1)),
        FrameRate(Fraction(30000, 1001)),
        FrameRate(Fraction(30, 1)),
        FrameRate(Fraction(60000, 1001)),
    ],
)
@pytest.mark.parametrize("seconds", STANDARD_TARGET_SECONDS)
def test_no_standard_target_ever_runs_over_at_any_rate(rate: FrameRate, seconds: int) -> None:
    """The rule that keeps deliverables from bouncing: never a frame over."""
    frames = TargetDuration.of(seconds, UNDER).frames_at(rate)
    assert Fraction(frames) / rate.value <= seconds
    # ...and it must be the *longest* such cut, not an arbitrary short one.
    assert Fraction(frames + 1) / rate.value > seconds


def test_for_target_starts_at_the_given_in_point() -> None:
    trim = TrimRange.for_target(at(100), TargetDuration.of(6, UNDER))
    assert trim.in_point.frames == 100
    assert trim.frame_count == 179  # 6 s at 29.97, floored to stay under


def test_for_target_clamps_to_the_source() -> None:
    trim = TrimRange.for_target(at(0), TargetDuration.of(90), source_duration=at(500))
    assert trim.out_point.frames == 500


def test_every_standard_target_is_a_whole_number_of_frames_in_timecode_mode() -> None:
    for seconds in STANDARD_TARGET_SECONDS:
        target = TargetDuration.of(seconds, TargetMode.TIMECODE)
        assert target.frames_at(R_2997) == seconds * 30
        assert target.frames_at(R_25) == seconds * 25


# --------------------------------------------------------------------------
# compare_to_target
# --------------------------------------------------------------------------


def test_one_frame_long_reports_one_frame_over() -> None:
    trim = TrimRange(at(0), at(901))
    delta = trim.compare_to_target(TargetDuration.of(30, TargetMode.TIMECODE))
    assert delta.frames == 1
    assert delta.is_over
    assert not delta.is_exact
    assert delta.describe() == "1 frame over :30"


def test_at_or_under_calls_900_frames_one_over() -> None:
    """900 frames runs 30.030 s, which the AT_OR_UNDER rule counts as over."""
    delta = TrimRange(at(0), at(900)).compare_to_target(TargetDuration.of(30, UNDER))
    assert delta.frames == 1
    assert delta.is_over


def test_an_exact_range_says_so() -> None:
    trim = TrimRange(at(0), at(900))
    assert (
        trim.compare_to_target(TargetDuration.of(30, TargetMode.TIMECODE)).describe()
        == "Exactly :30"
    )
    # AT_OR_UNDER's cut is 899 frames; the sped-up default's is 900.
    assert TrimRange(at(0), at(899)).compare_to_target(TargetDuration.of(30, UNDER)).describe() == "Exactly :30"
    assert TrimRange(at(0), at(900)).compare_to_target(TargetDuration.of(30)).describe() == "Exactly :30"


def test_short_ranges_report_frames_under() -> None:
    trim = TrimRange(at(0), at(898))
    delta = trim.compare_to_target(TargetDuration.of(30, TargetMode.TIMECODE))
    assert delta.frames == -2
    assert not delta.is_over
    assert delta.describe() == "2 frames under :30"
    assert delta.seconds == Fraction(-2 * 1001, 30000)


# --------------------------------------------------------------------------
# TIMECODE vs WALL_CLOCK
# --------------------------------------------------------------------------


def test_the_two_modes_disagree_at_2997() -> None:
    timecode = TargetDuration.of(30, TargetMode.TIMECODE)
    wall_clock = TargetDuration.of(30, TargetMode.WALL_CLOCK)
    # 900 frames reads 00:00:30;00 but runs 30.030 s; 899 frames is the closest
    # the grid gets to a real 30.000 s.
    assert timecode.frames_at(R_2997) == 900
    assert wall_clock.frames_at(R_2997) == 899


def test_the_two_modes_agree_at_an_integer_rate() -> None:
    timecode = TargetDuration.of(30, TargetMode.TIMECODE)
    wall_clock = TargetDuration.of(30, TargetMode.WALL_CLOCK)
    assert timecode.frames_at(R_25) == wall_clock.frames_at(R_25) == 750


def test_frame_alignment_is_a_property_of_the_rate() -> None:
    target = TargetDuration.of(30)
    assert target.is_frame_aligned(R_2997) is False
    assert target.is_frame_aligned(R_25) is True


def test_alignment_error_is_signed_and_exact() -> None:
    timecode = TargetDuration.of(30, TargetMode.TIMECODE)
    wall_clock = TargetDuration.of(30, TargetMode.WALL_CLOCK)
    # 900 frames overshoots a real 30 s by exactly 30 ms.
    assert timecode.alignment_error(R_2997) == Fraction(3, 100)
    # 899 frames undershoots it.
    assert wall_clock.alignment_error(R_2997) == Fraction(-101, 30000)
    assert TargetDuration.of(30).alignment_error(R_25) == 0


def test_target_labels_read_as_slots() -> None:
    assert TargetDuration.of(6).label() == ":06"
    assert TargetDuration.of(90).label() == ":90"
    assert TargetDuration.of(Fraction(305, 10)).label() == "30.500s"


# --------------------------------------------------------------------------
# Moving the points
# --------------------------------------------------------------------------


def test_with_in_cannot_reach_out() -> None:
    trim = TrimRange(at(100), at(200)).with_in(at(200))
    assert trim.in_point.frames == 200
    assert trim.out_point.frames == 201
    assert trim.frame_count == 1


def test_with_in_past_out_pushes_out_along() -> None:
    trim = TrimRange(at(100), at(200)).with_in(at(500))
    assert (trim.in_point.frames, trim.out_point.frames) == (500, 501)


def test_with_out_cannot_reach_in() -> None:
    trim = TrimRange(at(100), at(200)).with_out(at(100))
    assert (trim.in_point.frames, trim.out_point.frames) == (99, 100)


def test_with_out_before_in_pulls_in_back() -> None:
    trim = TrimRange(at(100), at(200)).with_out(at(50))
    assert (trim.in_point.frames, trim.out_point.frames) == (49, 50)


def test_with_out_at_frame_zero_cannot_go_negative() -> None:
    trim = TrimRange(at(0), at(200)).with_out(at(0))
    assert (trim.in_point.frames, trim.out_point.frames) == (0, 0)


def test_normal_moves_leave_the_other_point_alone() -> None:
    base = TrimRange(at(100), at(200))
    assert base.with_in(at(50)).out_point.frames == 200
    assert base.with_out(at(300)).in_point.frames == 100


# --------------------------------------------------------------------------
# Clamping and coverage
# --------------------------------------------------------------------------


def test_clamped_pulls_both_points_inside_the_source() -> None:
    trim = TrimRange(at(50), at(400)).clamped(at(100))
    assert (trim.in_point.frames, trim.out_point.frames) == (50, 100)


def test_clamped_collapses_a_range_entirely_past_the_end() -> None:
    trim = TrimRange(at(150), at(400)).clamped(at(100))
    assert (trim.in_point.frames, trim.out_point.frames) == (100, 100)
    assert trim.is_empty


def test_clamped_without_a_source_is_a_no_op() -> None:
    trim = TrimRange(at(50), at(400))
    assert trim.clamped(None) == trim


def test_is_full_only_when_the_whole_source_is_kept() -> None:
    duration = at(900)
    assert TrimRange.full(duration).is_full(duration)
    assert not TrimRange(at(0), at(899)).is_full(duration)
    assert not TrimRange(at(1), at(900)).is_full(duration)
    # With no known duration, only the IN point can be judged.
    assert TrimRange(at(0), at(10)).is_full(None)
    assert not TrimRange(at(1), at(10)).is_full(None)


def test_at_rate_preserves_wall_clock_position() -> None:
    trim = TrimRange(at(0), at(900)).at_rate(R_25)
    assert trim.out_point.rate == R_25
    assert trim.out_point.frames == 751  # 30.030 s at 25 fps


# --------------------------------------------------------------------------
# parse_time_input (lives in the panel, kept Qt-free so it can be tested here)
# --------------------------------------------------------------------------


def _parser():
    pytest.importorskip("PySide6")
    from framecheck.app.ui.trim_panel import parse_time_input

    return parse_time_input


@pytest.mark.parametrize(
    ("text", "rate", "expected_frames"),
    [
        ("00:00:30.000", R_25, 750),
        ("00:00:30.000", R_2997, 899),  # nearest frame to a real 30 s
        ("00:01:00.500", R_25, 1513),  # 60.5 s x 25 = 1512.5, snapped half-up
        ("  00:00:02.000  ", R_25, 50),
        ("1:30", R_25, 2250),  # MM:SS
        ("0:02", R_25, 50),
        ("30", R_25, 750),  # bare seconds
        ("30.5", R_25, 763),
        ("0", R_25, 0),
        ("00:00:30:00", R_25, 750),  # HH:MM:SS:FF
        ("00:00:30:12", R_25, 762),
        ("00:00:30;00", R_2997, 900),  # drop-frame reads 900, not 899
        ("00:01:00;02", R_2997, 1800),  # frame 02 is the first legal one there
        ("01:00:00;00", R_2997, 107892),
    ],
)
def test_parse_time_input_accepts_the_formats_users_paste(
    text: str, rate: FrameRate, expected_frames: int
) -> None:
    parsed = _parser()(text, rate)
    assert parsed is not None
    assert parsed.frames == expected_frames
    assert parsed.rate == rate


def test_parsed_drop_frame_timecode_round_trips() -> None:
    parse = _parser()
    for frames in (0, 900, 1800, 17982, 107892):
        point = MediaTime(frames, R_2997)
        assert parse(point.to_timecode(), R_2997) == point


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "garbage",
        "12:ab",
        "-5",
        "1:2:3:4:5",
        "00:99:00.000",  # minutes out of range
        "00:00:75.000",  # seconds out of range
        "00:00:30:30",  # frame number >= the nominal rate
        "30 s",
        "::",
    ],
)
def test_parse_time_input_rejects_nonsense(text: str) -> None:
    assert _parser()(text, R_25) is None


# --------------------------------------------------------------------------
# EXACT: whole seconds on a 1000/1001 rate
# --------------------------------------------------------------------------

R_30 = FrameRate(Fraction(30))


@pytest.mark.parametrize("seconds", STANDARD_TARGET_SECONDS)
def test_exact_speed_uses_a_whole_slot_of_frames_and_holds_none(seconds) -> None:
    cut = TargetDuration.of(seconds).exact_cut(R_2997)
    assert cut == ExactCut(R_2997, R_30, seconds * 30, seconds * 30, Fit.SPEED)
    assert cut.held_frames == 0
    assert cut.seconds == seconds
    assert cut.speed == Fraction(1001, 1000)
    assert cut.source_seconds == Fraction(seconds * 1001, 1000)


@pytest.mark.parametrize(
    "seconds, source_frames, held",
    [(6, 179, 1), (15, 449, 1), (30, 899, 1), (60, 1798, 2), (90, 2697, 3)],
)
def test_exact_hold_cuts_under_and_fills_at_the_edge(seconds, source_frames, held) -> None:
    cut = TargetDuration.of(seconds, fit=Fit.HOLD_END).exact_cut(R_2997)
    assert cut == ExactCut(R_2997, R_30, seconds * 30, source_frames, Fit.HOLD_END)
    assert cut.held_frames == held
    assert cut.speed == 1
    assert cut.source_seconds == seconds  # the whole slot of real audio


def test_exact_speed_is_the_preset_default() -> None:
    target = TargetDuration.of(15)
    assert (target.mode, target.fit) == (TargetMode.EXACT, Fit.SPEED)


def test_exact_trims_the_source_frames_the_cut_uses() -> None:
    assert TargetDuration.of(15).frames_at(R_2997) == 450
    assert TargetDuration.of(15, fit=Fit.HOLD_START).frames_at(R_2997) == 449


def test_exact_needs_no_retime_on_an_aligned_rate() -> None:
    assert TargetDuration.of(15).exact_cut(R_25) is None
    assert TargetDuration.of(15).frames_at(R_25) == 375


def test_other_modes_never_retime() -> None:
    assert TargetDuration.of(15, TargetMode.AT_OR_UNDER).exact_cut(R_2997) is None


def test_exact_gives_up_when_the_whole_rate_cannot_land_it_either() -> None:
    assert TargetDuration.of(Fraction(1, 7)).exact_cut(R_2997) is None
