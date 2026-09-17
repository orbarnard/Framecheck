"""Validation output: per-check results and the report that rolls them up.

Deliberately dumb data. The validator produces these, panels render them, and
the export pipeline reads `fixable` to decide what it can correct. None of them
contain logic beyond the rollup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import CheckStatus, Profile, Severity


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one Rule against one file."""

    label: str
    status: CheckStatus
    actual: str  # what the file has, already formatted for display
    expected: str  # what the profile wants
    field: str = ""
    guidance: str | None = None
    # True when export can correct this. Drives the "FIX ON EXPORT" affordance.
    fixable: bool = False
    fix_description: str | None = None

    @property
    def is_problem(self) -> bool:
        return self.status in (CheckStatus.FAIL, CheckStatus.WARNING)


@dataclass(frozen=True)
class ValidationReport:
    """Every check for one profile against one file."""

    profile_id: str
    profile_name: str
    checks: tuple[CheckResult, ...] = ()
    # Set when the report could not be produced at all (no probe data yet).
    unavailable_reason: str | None = None

    @property
    def status(self) -> CheckStatus:
        """Worst status across all checks.

        MANUAL_REVIEW ranks below WARNING: an unreviewed disclaimer is not a
        technical defect, but it must still stop anyone calling the file clean.
        """
        if not self.checks:
            return CheckStatus.NOT_APPLICABLE
        return max((c.status for c in self.checks), key=lambda s: s.rank)

    @property
    def passed(self) -> bool:
        """True only when nothing failed and nothing awaits human review."""
        return self.status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE)

    def by_status(self, status: CheckStatus) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.status is status)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return self.by_status(CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        return self.by_status(CheckStatus.WARNING)

    @property
    def manual_reviews(self) -> tuple[CheckResult, ...]:
        return self.by_status(CheckStatus.MANUAL_REVIEW)

    @property
    def fixable(self) -> tuple[CheckResult, ...]:
        """Problems export can correct."""
        return tuple(c for c in self.checks if c.is_problem and c.fixable)

    @property
    def unfixable(self) -> tuple[CheckResult, ...]:
        """Problems that need a new master or a creative decision."""
        return tuple(c for c in self.checks if c.is_problem and not c.fixable)

    def summary_line(self) -> str:
        counts = {
            CheckStatus.FAIL: len(self.failures),
            CheckStatus.WARNING: len(self.warnings),
            CheckStatus.MANUAL_REVIEW: len(self.manual_reviews),
        }
        parts = [f"{n} {s.value.replace('_', ' ')}" for s, n in counts.items() if n]
        return ", ".join(parts) if parts else "All checks pass"


@dataclass(frozen=True)
class OutputGroup:
    """A set of profiles that one exported file can satisfy together."""

    profiles: tuple[Profile, ...]
    label: str
    reason: str = ""

    @property
    def primary(self) -> Profile:
        return self.profiles[0]


@dataclass(frozen=True)
class CompatibilityReport:
    """Whether several selected destinations can share one output file.

    When they cannot -- normally because the aspect ratios differ -- this says
    so and proposes a grouping, rather than cropping landscape creative into a
    vertical frame. Reframing is a creative act, not a transcode.
    """

    groups: tuple[OutputGroup, ...] = ()
    single_master_possible: bool = True
    reason: str = ""
    conflicts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def output_count(self) -> int:
        return len(self.groups)
