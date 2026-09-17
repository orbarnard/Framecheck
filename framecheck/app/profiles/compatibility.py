"""Whether several destinations can share one exported file.

Container and codec differences are an encoding problem and Framecheck will
simply encode twice. Frame shape is not: a 16:9 master cannot become a 9:16
one by any automatic means that keeps the creative intact. So profiles group
by aspect ratio, and a grouping with more than one member is reported plainly
as "you need more than one master" rather than resolved by cutting picture off
the edges of the frame.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models.profile import Profile
from ..models.validation_result import CompatibilityReport, OutputGroup
from .validator import PRETTY_NAMES, aspect_value, reduced_ratio


def _shape(profile: Profile) -> str | None:
    """The aspect this profile's output must have, or None for "keep source"."""
    target = profile.target
    if target.aspect_ratio:
        return target.aspect_ratio
    if target.width and target.height:
        return reduced_ratio(target.width, target.height)
    return None


def _base_label(ratio: str | None) -> str:
    value = aspect_value(ratio)
    if value is None:
        return "Master"
    if value > 1.05:
        return "Landscape master"
    if value < 0.95:
        return "Vertical master"
    return "Square master"


def _resolution(profiles: Sequence[Profile]) -> str | None:
    """The smallest frame that satisfies every member -- i.e. the largest asked for."""
    sizes = [
        (p.target.width, p.target.height)
        for p in profiles
        if p.target.width and p.target.height
    ]
    if not sizes:
        return None
    return f"{max(w for w, _ in sizes)}x{max(h for _, h in sizes)}"


def analyze(profiles: Sequence[Profile]) -> CompatibilityReport:
    """Group `profiles` into the fewest output files that can serve them."""
    profiles = list(profiles)
    if not profiles:
        return CompatibilityReport(reason="No destinations selected.")

    # Ordered buckets keyed by what an encoder cannot reconcile in one pass.
    buckets: dict[tuple[str, str, float], list[Profile]] = {}
    ratios: dict[tuple[str, str, float], str] = {}
    flexible: list[Profile] = []

    for profile in profiles:
        ratio = _shape(profile)
        value = aspect_value(ratio)
        if value is None:
            # No fixed shape: this destination takes whatever the master is.
            flexible.append(profile)
            continue
        key = (
            profile.target.container.lower(),
            profile.target.video_codec.lower(),
            round(value, 2),
        )
        buckets.setdefault(key, []).append(profile)
        ratios.setdefault(key, ratio or "")

    if buckets:
        # Flexible destinations ride along with the first real master.
        first = next(iter(buckets))
        buckets[first].extend(flexible)
    elif flexible:
        buckets[("", "", 0.0)] = flexible
        ratios[("", "", 0.0)] = ""

    labels = _unique_labels([ratios[key] for key in buckets])
    groups = tuple(
        OutputGroup(
            profiles=tuple(members),
            label=label,
            reason=_group_reason(members, ratios[key]),
        )
        for label, (key, members) in zip(labels, buckets.items())
    )

    if len(groups) == 1:
        return CompatibilityReport(
            groups=groups,
            single_master_possible=True,
            reason=_single_reason(groups[0]),
        )

    return CompatibilityReport(
        groups=groups,
        single_master_possible=False,
        reason=_split_reason(groups),
        conflicts=_conflicts(groups, ratios, buckets),
    )


def _unique_labels(ratios: Sequence[str]) -> list[str]:
    """Group labels, qualified by ratio only when the plain label repeats.

    4:5 and 9:16 are both "Vertical master"; two groups with the same name in
    the UI is worse than a slightly longer one.
    """
    bases = [_base_label(r) for r in ratios]
    return [
        f"{base} ({ratio})" if bases.count(base) > 1 and ratio else base
        for base, ratio in zip(bases, ratios)
    ]


def _describe(profiles: Sequence[Profile]) -> str:
    names = [p.name for p in profiles]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _group_reason(members: Sequence[Profile], ratio: str) -> str:
    target = members[0].target
    resolution = _resolution(members) or "the source resolution"
    shape = f" at {ratio}" if ratio else ""
    codec = PRETTY_NAMES.get(target.video_codec.lower(), target.video_codec.upper())
    container = PRETTY_NAMES.get(target.container.lower(), target.container.upper())
    return (
        f"One {resolution} {codec} {container} file"
        f"{shape} satisfies {_describe(members)}."
    )


def _single_reason(group: OutputGroup) -> str:
    count = len(group.profiles)
    if count == 1:
        return group.reason
    return f"{group.reason} All {count} destinations share one master."


def _split_reason(groups: Sequence[OutputGroup]) -> str:
    named = ", ".join(g.label for g in groups[:-1]) + f" and {groups[-1].label}"
    return (
        f"These destinations need different frame shapes, so they need "
        f"{len(groups)} separate masters: {named}. Framecheck will scale and pad "
        "to fit a frame, but it will not cut picture off the edges to force a "
        "different shape -- recomposing for another aspect ratio is a creative "
        "decision, not a transcode. Supply or grade a version framed for each shape."
    )


def _conflicts(
    groups: Sequence[OutputGroup],
    ratios: dict[tuple[str, str, float], str],
    buckets: dict[tuple[str, str, float], list[Profile]],
) -> tuple[str, ...]:
    keys = list(buckets)
    lines: list[str] = []
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            a, b = groups[i], groups[keys.index(right)]
            lines.append(
                f"{a.primary.name} wants {ratios[left] or 'the source shape'} but "
                f"{b.primary.name} wants {ratios[right] or 'the source shape'}."
            )
    return tuple(lines)
