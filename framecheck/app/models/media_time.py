"""Exact frame/time arithmetic.

Framecheck never models frame timing with floating-point seconds. Frame rates
are rational (24000/1001, not 23.976) and positions are integer frame counts
against a known rate. Floats appear only where a display string or a player
seek target is produced.

No Qt dependency and no I/O, so this module is cheap to test.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

# Frame rates that broadcast/digital delivery actually uses. Used to snap
# slightly-off probe values (e.g. 23.98) onto the exact rational rate.
COMMON_RATES: tuple[Fraction, ...] = (
    Fraction(24000, 1001),  # 23.976
    Fraction(24, 1),
    Fraction(25, 1),
    Fraction(30000, 1001),  # 29.97
    Fraction(30, 1),
    Fraction(48000, 1001),
    Fraction(50, 1),
    Fraction(60000, 1001),  # 59.94
    Fraction(60, 1),
)

_SNAP_TOLERANCE = Fraction(1, 1000)


class Rounding(Enum):
    """How a seconds value converts to a frame index.

    Always passed explicitly at a seconds -> frames boundary. An implicit
    default here is how trims end up one frame out.
    """

    FLOOR = "floor"
    CEIL = "ceil"
    NEAREST = "nearest"


@dataclass(frozen=True, order=True)
class FrameRate:
    """A rational frame rate."""

    value: Fraction

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError(f"frame rate must be positive, got {self.value}")

    @classmethod
    def parse(cls, raw: object) -> "FrameRate | None":
        """Parse an ffprobe rate string such as ``"30000/1001"``.

        Returns None for absent or degenerate values (ffprobe reports ``0/0``
        for streams with no meaningful rate).
        """
        if raw is None:
            return None
        if isinstance(raw, FrameRate):
            return raw
        if isinstance(raw, Fraction):
            return cls(cls._snap(raw)) if raw > 0 else None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            if raw <= 0 or not math.isfinite(raw):
                return None
            return cls(cls._snap(Fraction(raw).limit_denominator(100000)))

        text = str(raw).strip()
        if not text:
            return None
        m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
        if m:
            num, den = int(m.group(1)), int(m.group(2))
            if num <= 0 or den <= 0:
                return None
            return cls(cls._snap(Fraction(num, den)))
        try:
            parsed = Fraction(text).limit_denominator(100000)
        except (ValueError, ZeroDivisionError):
            return None
        if parsed <= 0:
            return None
        return cls(cls._snap(parsed))

    @staticmethod
    def _snap(value: Fraction) -> Fraction:
        """Snap near-miss decimal rates onto the exact rational equivalent."""
        for candidate in COMMON_RATES:
            if abs(value - candidate) <= _SNAP_TOLERANCE:
                return candidate
        return value

    @property
    def is_drop_frame(self) -> bool:
        """True for 29.97 / 59.94, where SMPTE drop-frame timecode applies.

        Describes the *rate*. Whether a given file actually carries drop-frame
        timecode is a separate question this does not answer.
        """
        return self.value in (Fraction(30000, 1001), Fraction(60000, 1001))

    @property
    def nominal(self) -> int:
        """Nearest integer rate, e.g. 30 for 29.97. Used by timecode math."""
        return round(float(self.value))

    def __float__(self) -> float:
        return float(self.value)

    def label(self) -> str:
        """Human label: '29.97' for rational rates, '25' for integer ones."""
        if self.value.denominator == 1:
            return str(self.value.numerator)
        return f"{float(self.value):.3f}".rstrip("0").rstrip(".")

    def __str__(self) -> str:
        return self.label()


@dataclass(frozen=True, order=True)
class MediaTime:
    """A position or duration, stored as an exact frame count at a rate."""

    frames: int
    rate: FrameRate

    def __post_init__(self) -> None:
        if self.frames < 0:
            raise ValueError(f"frame index must be >= 0, got {self.frames}")

    @classmethod
    def from_seconds(
        cls,
        seconds: float | Fraction | int,
        rate: FrameRate,
        rounding: Rounding = Rounding.NEAREST,
    ) -> "MediaTime":
        exact = (
            seconds
            if isinstance(seconds, Fraction)
            else Fraction(seconds).limit_denominator(1_000_000)
        )
        if exact < 0:
            exact = Fraction(0)
        frames_exact = exact * rate.value
        if rounding is Rounding.FLOOR:
            frames = math.floor(frames_exact)
        elif rounding is Rounding.CEIL:
            frames = math.ceil(frames_exact)
        else:
            # Fraction rounds half-to-even; for frame snapping, half-up reads
            # more predictably to someone dragging a marker.
            frames = math.floor(frames_exact + Fraction(1, 2))
        return cls(max(0, frames), rate)

    @classmethod
    def zero(cls, rate: FrameRate) -> "MediaTime":
        return cls(0, rate)

    @property
    def seconds(self) -> Fraction:
        """Exact position in seconds. Never collapse this to float for math."""
        return Fraction(self.frames) / self.rate.value

    @property
    def seconds_float(self) -> float:
        """Float seconds, for display and for player seek targets only."""
        return float(self.seconds)

    def at_rate(self, rate: FrameRate, rounding: Rounding = Rounding.NEAREST) -> "MediaTime":
        """Re-express this instant against a different frame rate."""
        if rate == self.rate:
            return self
        return MediaTime.from_seconds(self.seconds, rate, rounding)

    def offset_frames(self, delta: int) -> "MediaTime":
        return MediaTime(max(0, self.frames + delta), self.rate)

    def offset_seconds(self, delta: float | Fraction) -> "MediaTime":
        shifted = self.seconds + Fraction(delta).limit_denominator(1_000_000)
        return MediaTime.from_seconds(max(Fraction(0), shifted), self.rate)

    def clamped(self, lo: "MediaTime | None" = None, hi: "MediaTime | None" = None) -> "MediaTime":
        frames = self.frames
        if lo is not None:
            frames = max(frames, lo.at_rate(self.rate).frames)
        if hi is not None:
            frames = min(frames, hi.at_rate(self.rate).frames)
        return MediaTime(max(0, frames), self.rate)

    def __sub__(self, other: "MediaTime") -> "MediaTime":
        return MediaTime(max(0, self.frames - other.at_rate(self.rate).frames), self.rate)

    def __add__(self, other: "MediaTime") -> "MediaTime":
        return MediaTime(self.frames + other.at_rate(self.rate).frames, self.rate)

    def to_clock(self, decimals: int = 3) -> str:
        """HH:MM:SS.mmm -- the readout users compare against a spec."""
        return format_seconds(self.seconds, decimals)

    def to_timecode(self) -> str:
        """SMPTE timecode, drop-frame aware (HH:MM:SS;FF when drop-frame)."""
        fps = self.rate.nominal
        if fps <= 0:
            return self.to_clock()
        if self.rate.is_drop_frame:
            drop = 2 if fps == 30 else 4
            frames_per_10min = fps * 60 * 10 - 9 * drop
            frames_per_min = fps * 60 - drop
            d, m = divmod(self.frames, frames_per_10min)
            if m >= drop:
                adjusted = self.frames + 9 * drop * d + drop * ((m - drop) // frames_per_min)
            else:
                adjusted = self.frames + 9 * drop * d
            ff = adjusted % fps
            ss = (adjusted // fps) % 60
            mm = (adjusted // (fps * 60)) % 60
            hh = adjusted // (fps * 3600)
            return f"{hh:02d}:{mm:02d}:{ss:02d};{ff:02d}"
        ff = self.frames % fps
        ss = (self.frames // fps) % 60
        mm = (self.frames // (fps * 60)) % 60
        hh = self.frames // (fps * 3600)
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def __str__(self) -> str:
        return self.to_clock()


def format_seconds(seconds: float | Fraction | int | None, decimals: int = 3) -> str:
    """Clock string for a duration that may have no known frame rate."""
    if seconds is None:
        return "--:--:--.---" if decimals == 3 else "--:--:--"
    total = (
        seconds if isinstance(seconds, Fraction) else Fraction(seconds).limit_denominator(1_000_000)
    )
    if total < 0:
        total = Fraction(0)
    whole = int(total)
    frac = total - whole
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if decimals <= 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    scale = 10**decimals
    # Truncate rather than round: a readout must never show a time the
    # playhead has not actually reached.
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{int(frac * scale):0{decimals}d}"


def format_duration_short(seconds: float | Fraction | int | None) -> str:
    """Compact M:SS used in dense lists."""
    if seconds is None:
        return "--:--"
    total = int(Fraction(seconds).limit_denominator(1_000_000))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
