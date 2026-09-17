"""Delivery profiles: what a destination requires, and what to encode for it.

A profile JSON gives two things, and Framecheck keeps them strictly apart:

* **Rules** -- what the validator checks a file against. Rules never build
  commands.
* **TargetSpec** -- what an export should produce. The encoder never reads
  rules.

Keeping these separate is what stops "validation quietly disagrees with what we
actually encoded", which is the failure mode that makes a conformance tool
worthless.

Profile authors write flat, obvious JSON (`"width": 1920`). The loader expands
that into Rules via a field table, so contributing a profile needs no knowledge
of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from typing import Any


class Severity(Enum):
    """How badly a broken rule matters."""

    # A hard requirement. The destination will reject the file.
    FAIL = "fail"
    # Outside preferred guidance but usable. Never report a preference as FAIL.
    WARNING = "warning"
    # Cannot be determined automatically and must never be auto-approved.
    MANUAL = "manual"
    # Reported for context only; never affects the overall verdict.
    INFO = "info"


class CheckStatus(Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    MANUAL_REVIEW = "manual_review"
    # The source has nothing to check against this rule (e.g. no audio stream).
    NOT_APPLICABLE = "not_applicable"

    @property
    def rank(self) -> int:
        """Ordering for "worst status wins" rollups."""
        return {
            CheckStatus.NOT_APPLICABLE: 0,
            CheckStatus.PASS: 1,
            CheckStatus.MANUAL_REVIEW: 2,
            CheckStatus.WARNING: 3,
            CheckStatus.FAIL: 4,
        }[self]


class FrameRateBehavior(Enum):
    """What to do with the source frame rate on export."""

    # Keep the source rate when the destination allows it. The default, and the
    # right answer almost always: resampling frame rate damages motion.
    PRESERVE_IF_ALLOWED = "preserve_native_if_allowed"
    # Always convert to the profile's preferred rate.
    FORCE = "force"


@dataclass(frozen=True)
class Rule:
    """One validation check.

    Exactly one comparison kind is meaningful per rule; `preferred` may
    accompany min/max to express "20 Mb/s preferred, 15-30 acceptable".
    """

    field: str  # key into the extractor table, e.g. "video.codec"
    label: str  # human name shown in the validation panel, e.g. "Video codec"
    severity: Severity = Severity.FAIL
    equals: Any = None
    allowed: tuple[Any, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    preferred: Any = None
    tolerance: float | None = None  # +/- band around `preferred`, same units
    unit: str = ""
    guidance: str | None = None  # shown when the rule is not satisfied
    # True for things a machine must not sign off: disclaimers, safe zones,
    # creative approval. These always report MANUAL_REVIEW, never PASS.
    manual: bool = False
    # True when Framecheck can correct this during export.
    fixable: bool = False

    def expectation_text(self) -> str:
        """Short description of what this rule wants."""
        if self.manual:
            return "Human review required"
        parts: list[str] = []
        if self.equals is not None:
            parts.append(str(self.equals))
        if self.allowed:
            parts.append(" / ".join(str(v) for v in self.allowed))
        if self.minimum is not None and self.maximum is not None:
            parts.append(f"{_num(self.minimum)}-{_num(self.maximum)}")
        elif self.minimum is not None:
            parts.append(f"at least {_num(self.minimum)}")
        elif self.maximum is not None:
            parts.append(f"at most {_num(self.maximum)}")
        if self.preferred is not None and not parts:
            parts.append(f"{_num(self.preferred)} preferred")
        elif self.preferred is not None:
            parts.append(f"({_num(self.preferred)} preferred)")
        text = " ".join(parts) if parts else "--"
        return f"{text} {self.unit}".strip()


def _num(value: Any) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class AudioTarget:
    codec: str = "aac"
    channels: int = 2
    sample_rate_hz: int = 48000
    bitrate_kbps: int = 320
    # Integrated loudness target in LKFS/LUFS. None means "leave levels alone".
    loudness_lkfs: float | None = None
    true_peak_db: float | None = -2.0


@dataclass(frozen=True)
class TargetSpec:
    """What an export for this profile should produce.

    Read only by the encoder. `width`/`height` of None mean "keep the source
    dimensions" -- used by profiles that accept native resolution.
    """

    container: str = "mp4"
    output_extension: str = ".mp4"
    video_codec: str = "h264"
    video_profile: str | None = "high"
    pixel_format: str = "yuv420p"
    width: int | None = None
    height: int | None = None
    # Encoding to a fixed aspect is scaling, not reframing. Framecheck pads to
    # fit rather than cropping, and says so; cropping is a creative decision.
    aspect_ratio: str | None = None
    frame_rate_behavior: FrameRateBehavior = FrameRateBehavior.PRESERVE_IF_ALLOWED
    allowed_frame_rates: tuple[Fraction, ...] = ()
    preferred_frame_rate: Fraction | None = None
    constant_frame_rate: bool = True
    video_bitrate_mbps: float | None = None
    video_bitrate_max_mbps: float | None = None
    audio: AudioTarget = field(default_factory=AudioTarget)
    faststart: bool = True  # move the MP4 index to the front for web delivery


@dataclass(frozen=True)
class Profile:
    """A delivery destination."""

    id: str
    name: str
    category: str = "video"
    description: str = ""
    rules: tuple[Rule, ...] = ()
    target: TargetSpec = field(default_factory=TargetSpec)
    # Short code stamped into output filenames: FC_CTV-spot.mp4. Kept brief
    # because it sits in front of every deliverable's name.
    tag: str = ""
    # Where the numbers came from, so a contributor can check them.
    source_url: str | None = None
    notes: tuple[str, ...] = ()
    # Path the profile was loaded from; None for built-ins defined in code.
    source_path: Any = None

    @property
    def output_extension(self) -> str:
        return self.target.output_extension

    def rule_for(self, field_name: str) -> Rule | None:
        for rule in self.rules:
            if rule.field == field_name:
                return rule
        return None

    def filename_tag(self) -> str:
        """Short token for default output filenames, e.g. 'CTV'.

        Falls back to the id when a profile declares no tag, so a contributed
        profile still produces a usable filename without extra ceremony.
        """
        return (self.tag or self.id).upper().replace("_", "-")
