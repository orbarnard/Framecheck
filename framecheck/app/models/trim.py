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

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from .media_time import FrameRate, MediaTime, Rounding

# Durations a delivery slot is normally cut to, in seconds.
STANDARD_TARGET_SECONDS: tuple[int, ...] = (6, 15, 30, 60, 90)


class TargetMode(Enum):
    """What the user meant by a target duration."""

    # The delivery default. The longest cut that does not EXCEED the target in
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


@dataclass(frozen=True)
class TargetDuration:
    """A requested output duration, plus how it was meant."""

    seconds: Fraction
    mode: TargetMode = TargetMode.AT_OR_UNDER

    @classmethod
    def of(
        cls,
        seconds: float | int | Fraction,
        mode: TargetMode = TargetMode.AT_OR_UNDER,
    ) -> "TargetDuration":
        return cls(Fraction(seconds).limit_denominator(1_000_000), mode)

    def frames_at(self, rate: FrameRate) -> int:
        """Frame count this target resolves to at `rate`.

        AT_OR_UNDER floors: the result is the longest cut whose real duration is
        <= the target, so the output can never overrun the slot it is cut for.
        At 29.97 a :30 is 899 frames / 29.9967 s.

        TIMECODE multiplies by the *nominal* rate: a :30 at 29.97 is 30 x 30 =
        900 frames, which is what an edit suite and a traffic system mean, and
        which runs 30.030 s.

        WALL_CLOCK rounds to the nearest frame, which may land just over.
        """
        if self.mode is TargetMode.TIMECODE:
            return int(self.seconds * rate.nominal)
        rounding = (
            Rounding.FLOOR if self.mode is TargetMode.AT_OR_UNDER else Rounding.NEAREST
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
