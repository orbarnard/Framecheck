"""The export job: everything one output file needs, decided before encoding.

An ExportJob is assembled and shown to the user *before* FFmpeg runs, so the
"what will change" list in the CONFORM panel and the command that actually runs
are built from the same object. They cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .media_info import MediaInfo
from .profile import Profile, TargetSpec
from .trim import TrimRange


@dataclass(frozen=True)
class LoudnessResult:
    """Output of an FFmpeg loudnorm analysis pass."""

    integrated_lufs: float
    true_peak_db: float | None = None
    loudness_range: float | None = None
    threshold: float | None = None
    # Raw loudnorm measurements, needed verbatim for a two-pass normalisation.
    raw: dict = field(default_factory=dict, repr=False)
    # True when only part of the programme was measured, to keep the reading
    # responsive on long or slow-to-read sources. An estimate is fine to show
    # and to validate loosely against, but must never drive a normalisation --
    # export re-measures in full first.
    estimated: bool = False
    # Seconds of audio actually measured, for the "estimated from" caption.
    analyzed_seconds: float | None = None

    def delta_to(self, target_lkfs: float) -> float:
        """Gain in dB needed to reach `target_lkfs`."""
        return target_lkfs - self.integrated_lufs


@dataclass(frozen=True)
class ConformAction:
    """One change the export will make, in the user's language."""

    label: str  # "Frame rate"
    from_value: str  # "variable"
    to_value: str  # "29.97 CFR"
    reason: str = ""  # why it is happening
    # True when this alters the creative rather than the container -- scaling,
    # padding, loudness. Surfaced more prominently.
    affects_picture_or_sound: bool = False

    def describe(self) -> str:
        return f"{self.label}: {self.from_value} -> {self.to_value}"


@dataclass(frozen=True)
class ExportJob:
    """A fully-specified single output."""

    source_path: Path
    source_info: MediaInfo
    output_path: Path
    target: TargetSpec
    profile: Profile | None = None
    # Also-satisfied profiles when one master serves several destinations.
    additional_profiles: tuple[Profile, ...] = ()
    trim: TrimRange | None = None
    normalize_loudness: bool = False
    source_loudness: LoudnessResult | None = None
    actions: tuple[ConformAction, ...] = ()
    overwrite: bool = False

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return ((self.profile,) if self.profile else ()) + self.additional_profiles

    @property
    def is_trimmed(self) -> bool:
        return self.trim is not None and not self.trim.is_empty

    @property
    def expected_duration_seconds(self) -> Fraction | None:
        if self.trim is not None:
            return self.trim.duration_seconds
        return self.source_info.duration_seconds

    @property
    def expected_frame_count(self) -> int | None:
        if self.trim is not None:
            return self.trim.frame_count
        return self.source_info.frame_count

    def changes_picture_or_sound(self) -> tuple[ConformAction, ...]:
        return tuple(a for a in self.actions if a.affects_picture_or_sound)


class ExportState:
    """Progress states an export moves through. Plain constants, not an enum,
    because they are only ever compared and displayed."""

    QUEUED = "queued"
    ANALYZING = "analyzing"  # loudness measurement pass
    ENCODING = "encoding"
    VERIFYING = "verifying"  # re-probing and re-validating the written file
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExportResult:
    """What actually happened, including verification of the written file.

    FFmpeg exiting zero is not evidence of compliance, so `reports` holds the
    validation of the *output* file, re-probed from disk.
    """

    job: ExportJob
    state: str
    output_path: Path | None = None
    output_info: MediaInfo | None = None
    reports: tuple = ()  # tuple[ValidationReport, ...]
    error: str | None = None
    duration_seconds: float | None = None  # wall-clock time the encode took

    @property
    def succeeded(self) -> bool:
        return self.state == ExportState.DONE and self.output_path is not None
