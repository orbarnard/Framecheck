"""The trim range: an IN point, an OUT point, and honest duration arithmetic.

Two conventions collide in delivery, and Framecheck refuses to hide the
difference:

* A **:30 spot** at 29.97 is 900 frames. Its real duration is 30.030 s, and its
  drop-frame timecode reads 00:00:30;00.
* **Exactly 30.000 s** at 29.97 is 899.1 frames -- not a whole number. The
  closest legal cuts are 899 frames (29.997 s) and 900 frames (30.030 s).

`TargetDuration` carries which of those the user asked for, and
`TrimRange.for_target` reports what the frame grid can actually deliver so the
UI can say so out loud rather than rounding in silence.

OUT is **exclusive**: duration in frames is `out.frames - in.frames`, so a clip
starting at frame 0 with 900 frames has OUT at frame 900.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from fractions import Fraction

from .media_time import FrameRate, MediaTime, Rounding

# Durations a delivery slot is normally cut to, in seconds.
STANDARD_TARGET_SECONDS: tuple[int, ...] = (6, 15, 30, 60, 90)


class TargetMode(Enum):
    """What the user meant by a target duration."""

    # The delivery default. Exactly the target in real seconds, which platforms
    # enforcing a slot demand: 14.982 s gets flagged as a :14. Where the source
    # rate cannot land it (29.97, 59.94, 23.976) the export moves to the whole
    # rate -- see `ExactCut` and `Fit`.
    EXACT = "exact"
    # The longest cut that does not EXCEED the target in
    # real seconds. A platform that enforces a :30 slot measures wall-clock
    # seconds, so 30.030 s is rejected while 29.997 s is accepted -- being a
    # frame under is free, being a frame over fails ingest.
    AT_OR_UNDER = "at_or_under"
    # "30 seconds" as broadcast means it: a whole number of frames whose
    # timecode reads 00:00:30;00. At 29.97 that is 900 frames / 30.030 s.
    # Correct for traffic systems, wrong for most digital platforms.
    TIMECODE = "timecode"
    # "30.000 seconds" of real elapsed time, rounded to the nearest frame --
    # which may land a frame over.
    WALL_CLOCK = "wall_clock"


class Fit(Enum):
    """How an EXACT cut fills a slot the source rate cannot land on.

    At 29.97 a :15 is 449.55 frames, so no cut of source frames is 15.000 s.
    Either way the export runs at the whole rate (30) and every source frame
    is shown exactly once -- nothing is duplicated or dropped mid-spot.
    """

    # The default. 450 source frames (15.015 s) play 0.1 % faster, picture and
    # sound together: exactly 15.000 s, in sync from first frame to last. The
    # sound rises 1.7 cents, well under what anyone can hear.
    SPEED = "speed"
    # 449 source frames, re-timed, then the last (or first) frame repeated to
    # fill. Audio keeps its speed, so picture drifts ahead of sound by up to
    # 1 ms per second of cut.
    HOLD_END = "hold_end"
    HOLD_START = "hold_start"
    # Not a user choice: the destination converts the rate anyway (59.94 ->
    # 30), and its rate lands the slot. The source is cut at exactly the target
    # in real time and the conversion makes the frames; nothing is sped up.
    CONVERT = "convert"


@dataclass(frozen=True)
class ExactCut:
    """An output of exactly the target length, on a rate that can hit it."""

    source_rate: FrameRate
    rate: FrameRate  # output rate
    frames: int  # output frame count
    source_frames: int  # source frames used, each exactly once
    fit: Fit = Fit.SPEED

    @property
    def held_frames(self) -> int:
        return self.frames - self.source_frames

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.frames) / self.rate.value

    @property
    def speed(self) -> Fraction:
        """Playback speed-up factor: 1001/1000 from 29.97 to 30, 1 when holding."""
        return self.rate.value / self.source_rate.value if self.fit is Fit.SPEED else Fraction(1)

    @property
    def source_seconds(self) -> Fraction:
        """Real source time the output draws on: the frames when sped up, the
        whole slot of audio when holding."""
        if self.fit is Fit.SPEED:
            return Fraction(self.source_frames) / self.source_rate.value
        return self.seconds


@dataclass(frozen=True)
class TargetDuration:
    """A requested output duration, plus how it was meant."""

    seconds: Fraction
    mode: TargetMode = TargetMode.EXACT
    fit: Fit = Fit.SPEED  # EXACT only

    @classmethod
    def of(
        cls,
        seconds: float | int | Fraction,
        mode: TargetMode = TargetMode.EXACT,
        fit: Fit = Fit.SPEED,
    ) -> "TargetDuration":
        return cls(Fraction(seconds).limit_denominator(1_000_000), mode, fit)

    def frames_at(self, rate: FrameRate) -> int:
        """Frame count this target resolves to at `rate`.

        AT_OR_UNDER floors: the result is the longest cut whose real duration is
        <= the target, so the output can never overrun the slot it is cut for.
        At 29.97 a :30 is 899 frames / 29.9967 s.

        TIMECODE multiplies by the *nominal* rate: a :30 at 29.97 is 30 x 30 =
        900 frames, which is what an edit suite and a traffic system mean, and
        which runs 30.030 s.

        WALL_CLOCK rounds to the nearest frame, which may land just over.

        EXACT counts *source* frames, as `exact_cut` decides; where no exact cut
        exists it floors like AT_OR_UNDER.
        """
        cut = self.exact_cut(rate)
        if cut is not None:
            return cut.source_frames
        if self.mode is TargetMode.TIMECODE:
            return int(self.seconds * rate.nominal)
        rounding = (
            Rounding.NEAREST if self.mode is TargetMode.WALL_CLOCK else Rounding.FLOOR
        )
        frames = MediaTime.from_seconds(self.seconds, rate, rounding).frames
        # Never floor a positive target down to nothing on an absurd rate.
        return max(1, frames) if self.seconds > 0 else 0

    def is_frame_aligned(self, rate: FrameRate) -> bool:
        """True when the target lands exactly on a frame boundary.

        False means any cut is approximate, and the UI must say by how much.
        """
        exact = self.seconds * rate.value
        return exact.denominator == 1

    def exact_cut(self, rate: FrameRate) -> ExactCut | None:
        """How an EXACT target is delivered from a source at `rate`.

        None when no re-time is needed (the target is already a frame boundary),
        when the mode is not EXACT, or when the whole rate cannot land it either.
        """
        if self.mode is not TargetMode.EXACT or self.is_frame_aligned(rate):
            return None
        out = FrameRate(Fraction(rate.nominal))
        frames = self.seconds * out.value
        under = math.floor(self.seconds * rate.value)
        if frames.denominator != 1 or under > frames:
            return None
        source_frames = int(frames) if self.fit is Fit.SPEED else under
        return ExactCut(rate, out, int(frames), source_frames, self.fit)

    def alignment_error(self, rate: FrameRate) -> Fraction:
        """Signed seconds by which the achievable cut misses the request.

        Positive means the delivered clip is longer than asked for.
        """
        frames = self.frames_at(rate)
        return Fraction(frames) / rate.value - self.seconds

    def label(self) -> str:
        whole = int(self.seconds)
        if self.seconds == whole:
            return f":{whole:02d}" if whole < 100 else f"{whole}s"
        return f"{float(self.seconds):.3f}s"


@dataclass(frozen=True)
class TrimRange:
    """An inclusive IN point and an exclusive OUT point."""

    in_point: MediaTime
    out_point: MediaTime

    def __post_init__(self) -> None:
        if self.in_point.rate != self.out_point.rate:
            raise ValueError("IN and OUT must share a frame rate")
        if self.out_point.frames < self.in_point.frames:
            raise ValueError(
                f"OUT ({self.out_point.frames}) is before IN ({self.in_point.frames})"
            )

    @classmethod
    def full(cls, duration: MediaTime) -> "TrimRange":
        """The whole source, untrimmed."""
        return cls(MediaTime.zero(duration.rate), duration)

    @classmethod
    def for_target(
        cls,
        in_point: MediaTime,
        target: TargetDuration,
        source_duration: MediaTime | None = None,
    ) -> "TrimRange":
        """A range of `target` length starting at `in_point`, clamped to source."""
        rate = in_point.rate
        frames = target.frames_at(rate)
        out = MediaTime(in_point.frames + max(0, frames), rate)
        if source_duration is not None:
            out = out.clamped(hi=source_duration.at_rate(rate))
        return cls(in_point, out)

    @property
    def rate(self) -> FrameRate:
        return self.in_point.rate

    @property
    def frame_count(self) -> int:
        """Number of frames the export will contain."""
        return self.out_point.frames - self.in_point.frames

    @property
    def duration(self) -> MediaTime:
        return MediaTime(self.frame_count, self.rate)

    @property
    def duration_seconds(self) -> Fraction:
        """Exact real-time length of the output."""
        return self.duration.seconds

    @property
    def is_empty(self) -> bool:
        return self.frame_count <= 0

    def is_full(self, source_duration: MediaTime | None) -> bool:
        """True when this range covers the entire source."""
        if source_duration is None:
            return self.in_point.frames == 0
        return (
            self.in_point.frames == 0
            and self.out_point.frames >= source_duration.at_rate(self.rate).frames
        )

    def with_in(self, point: MediaTime) -> "TrimRange":
        """Move IN, pushing OUT along if IN would pass it."""
        point = point.at_rate(self.rate)
        out = self.out_point if self.out_point.frames > point.frames else point.offset_frames(1)
        return TrimRange(point, out)

    def with_out(self, point: MediaTime) -> "TrimRange":
        """Move OUT, pulling IN back if OUT would pass it."""
        point = point.at_rate(self.rate)
        in_point = self.in_point if self.in_point.frames < point.frames else MediaTime(max(0, point.frames - 1), self.rate)
        return TrimRange(in_point, point)

    def clamped(self, source_duration: MediaTime | None) -> "TrimRange":
        if source_duration is None:
            return self
        limit = source_duration.at_rate(self.rate)
        in_point = self.in_point.clamped(hi=limit)
        out_point = self.out_point.clamped(lo=in_point, hi=limit)
        return TrimRange(in_point, out_point)

    def at_rate(self, rate: FrameRate) -> "TrimRange":
        return TrimRange(self.in_point.at_rate(rate), self.out_point.at_rate(rate))

    def compare_to_target(self, target: TargetDuration) -> "DurationDelta":
        """How this range measures against a requested duration."""
        wanted = target.frames_at(self.rate)
        return DurationDelta(
            frames=self.frame_count - wanted,
            rate=self.rate,
            target=target,
        )

    def __str__(self) -> str:
        return f"{self.in_point.to_clock()} -> {self.out_point.to_clock()} ({self.frame_count}f)"


# The order the trim panel offers fits in, and falls back through when the
# preferred one cannot be made from the footage there is.
FIT_ORDER: tuple[Fit, ...] = (Fit.SPEED, Fit.HOLD_END, Fit.HOLD_START)


@dataclass(frozen=True)
class FitOption:
    """One way to land a target exactly, and whether this footage allows it."""

    fit: Fit
    cut: ExactCut
    available: bool
    reason: str | None = None  # why not, in the user's words


def fit_options(
    target: TargetDuration, rate: FrameRate, available_frames: int | None
) -> list[FitOption]:
    """Every fit for `target` at `rate`, marked usable or not.

    Empty when the target is not EXACT or already lands on a frame boundary --
    there is then nothing to choose. `available_frames` is the footage from IN
    to the end of the source; None means unknown, and assumes enough.
    """
    options: list[FitOption] = []
    for fit in FIT_ORDER:
        cut = replace(target, fit=fit).exact_cut(rate)
        if cut is None:
            return []
        ok = available_frames is None or cut.source_frames <= available_frames
        reason = None
        if not ok:
            needs = Fraction(cut.source_frames) / rate.value
            has = Fraction(available_frames or 0) / rate.value
            reason = f"File too short: needs {float(needs):.3f} s of footage, has {float(has):.3f} s."
        options.append(FitOption(fit, cut, ok, reason))
    return options


def resolve_fit(
    target: TargetDuration, rate: FrameRate, available_frames: int | None
) -> tuple[TargetDuration, bool]:
    """The target with a fit this footage can make, and whether it was switched.

    A master already cut a frame under (29.988 s at 23.976) cannot be sped up
    to a :30 -- that needs 30.030 s of footage -- but holding its last frame
    lands exactly. Keeps the preferred fit whenever it works.
    """
    options = fit_options(target, rate, available_frames)
    if not options or any(o.fit is target.fit and o.available for o in options):
        return target, False
    for option in options:
        if option.available:
            return replace(target, fit=option.fit), True
    return target, False


def suggest_target(
    seconds: Fraction, rate: FrameRate, available_frames: int | None
) -> TargetDuration | None:
    """The standard length nearest `seconds` that this footage can make exactly."""
    for whole in sorted(STANDARD_TARGET_SECONDS, key=lambda s: (abs(s - seconds), s)):
        target = TargetDuration.of(whole)
        options = fit_options(target, rate, available_frames)
        if options:
            if any(o.available for o in options):
                return target
        elif target.is_frame_aligned(rate) and (
            available_frames is None or target.frames_at(rate) <= available_frames
        ):
            return target
    return None


@dataclass(frozen=True)
class DurationDelta:
    """The gap between an actual duration and a requested one.

    Expressed in frames, because "one frame over" is the statement a user needs
    and "0.033 seconds over" is the statement that gets ignored.
    """

    frames: int
    rate: FrameRate
    target: TargetDuration

    @property
    def is_exact(self) -> bool:
        return self.frames == 0

    @property
    def is_over(self) -> bool:
        return self.frames > 0

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.frames) / self.rate.value

    def describe(self) -> str:
        """Plain-language summary, e.g. '1 frame over :30'."""
        if self.frames == 0:
            return f"Exactly {self.target.label()}"
        count = abs(self.frames)
        unit = "frame" if count == 1 else "frames"
        direction = "over" if self.frames > 0 else "under"
        return f"{count} {unit} {direction} {self.target.label()}"
