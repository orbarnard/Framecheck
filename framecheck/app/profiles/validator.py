"""Checking a probed file against a delivery profile.

The whole point of this module is that it is boring and predictable. A user
looks at the panel it feeds and decides whether to ship; so the rules of
engagement are fixed:

* A preference is never a FAIL. The profile's own `severity` decides.
* `manual` rules never PASS and never FAIL -- a machine does not sign off a
  legal disclaimer.
* An undeterminable value is never a silent PASS. It is NOT_APPLICABLE when
  the rule does not apply to this file at all (no audio stream), and
  MANUAL_REVIEW when it applies but nothing measured it yet.
* Frame rates compare as exact rationals. 29.97 in a spec and 30000/1001 in a
  file are the same rate, and float comparison is how that stops being true.

`FIELDS` is the single table of everything the validator can look at. It is
also what `schema.py` validates contributed profile keys against, so an
unknown key is caught at load time rather than silently never checked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from math import gcd
from typing import Any

from ..models.export_job import LoudnessResult
from ..models.media_info import MediaInfo
from ..models.media_time import FrameRate
from ..models.profile import CheckStatus, Profile, Rule, Severity
from ..models.validation_result import CheckResult, ValidationReport

# --------------------------------------------------------------------------
# Value helpers


def aspect_value(text: str | None) -> float | None:
    """Numeric aspect from "16:9", "1.778" or "1920x1080". None if unusable.

    ffprobe reports "0:1" for streams with no meaningful DAR, which must not
    become a division by zero or a 0.0 that compares against nothing.
    """
    if not text:
        return None
    raw = str(text).strip().replace("x", ":")
    if ":" in raw:
        left, _, right = raw.partition(":")
        try:
            w, h = float(left), float(right)
        except ValueError:
            return None
        return w / h if w > 0 and h > 0 else None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def reduced_ratio(width: int, height: int) -> str:
    """"16:9" for 1920x1080. Used when the container reports no DAR."""
    divisor = gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def _aspect_equal(actual: Any, expected: Any) -> bool:
    """Aspect ratios within 1%. "16:9" and "1.778" describe the same frame."""
    a, b = aspect_value(actual), aspect_value(expected)
    if a is None or b is None:
        return False
    return abs(a - b) <= 0.01 * b


def _container_equal(actual: Any, expected: Any) -> bool:
    """ffprobe names a container by every format it could be.

    An MP4 probes as "mov,mp4,m4a,3gp,3g2,mj2", so membership -- not equality
    -- is the honest test.
    """
    names = {part.strip().lower() for part in str(actual).split(",")}
    return str(expected).strip().lower() in names


def _generic_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, FrameRate) or isinstance(expected, FrameRate):
        a = actual.value if isinstance(actual, FrameRate) else _to_fraction(actual)
        b = expected.value if isinstance(expected, FrameRate) else _to_fraction(expected)
        return a is not None and a == b
    if isinstance(actual, str) or isinstance(expected, str):
        return str(actual).strip().lower() == str(expected).strip().lower()
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return actual == expected


def _to_fraction(value: Any) -> Fraction | None:
    rate = FrameRate.parse(value)
    return rate.value if rate else None


def _numeric(value: Any) -> float | None:
    """The value as a float for min/max/tolerance comparison, or None."""
    if isinstance(value, FrameRate):
        return float(value.value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Fraction)):
        return float(value)
    return None


# --------------------------------------------------------------------------
# The extractor table

# Getters take the loudness result as well as the MediaInfo: integrated
# loudness is the one checked quantity ffprobe cannot report, and threading it
# through the same table keeps every field in one place.
Getter = Callable[[MediaInfo, "LoudnessResult | None"], Any]


@dataclass(frozen=True)
class FieldSpec:
    """How to read, display and compare one canonical field key."""

    get: Getter
    fmt: Callable[[Any], str] = str
    unit: str = ""
    eq: Callable[[Any, Any], bool] = _generic_equal


def _video_aspect(info: MediaInfo, _l: LoudnessResult | None) -> str | None:
    video = info.video
    if video is None:
        return None
    if aspect_value(video.display_aspect_ratio) is not None:
        return video.display_aspect_ratio
    if video.width and video.height:
        return reduced_ratio(video.width, video.height)
    return None


def _frame_rate_mode(info: MediaInfo, _l: LoudnessResult | None) -> str | None:
    video = info.video
    if video is None or video.effective_frame_rate is None:
        return None
    return "vfr" if video.likely_variable_frame_rate else "cfr"


def _scan_type(info: MediaInfo, _l: LoudnessResult | None) -> str | None:
    video = info.video
    if video is None:
        return None
    # Containers omit field_order far more often than they carry "interlaced";
    # absent means progressive in every format Framecheck accepts.
    return "interlaced" if video.is_interlaced else "progressive"


def _video_bitrate_mbps(info: MediaInfo, _l: LoudnessResult | None) -> float | None:
    video = info.video
    if video is None or not video.bitrate_bps:
        return None
    return video.bitrate_bps / 1_000_000


def _plain(value: Any) -> str:
    """Drop a pointless trailing ".0" so specs read the way people write them."""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _fixed(decimals: int) -> Callable[[Any], str]:
    return lambda v: f"{float(v):.{decimals}f}"


FIELDS: dict[str, FieldSpec] = {
    "container": FieldSpec(lambda i, _l: i.container_format, eq=_container_equal),
    "video.codec": FieldSpec(lambda i, _l: i.video.codec if i.video else None),
    "video.profile": FieldSpec(lambda i, _l: i.video.profile if i.video else None),
    "video.width": FieldSpec(lambda i, _l: i.video.width if i.video else None, unit="px"),
    "video.height": FieldSpec(lambda i, _l: i.video.height if i.video else None, unit="px"),
    "video.resolution": FieldSpec(lambda i, _l: i.video.resolution if i.video else None),
    "video.aspect_ratio": FieldSpec(_video_aspect, eq=_aspect_equal),
    "video.frame_rate": FieldSpec(
        lambda i, _l: i.frame_rate, fmt=lambda v: v.label(), unit="fps"
    ),
    "video.frame_rate_mode": FieldSpec(_frame_rate_mode, fmt=lambda v: str(v).upper()),
    "video.scan_type": FieldSpec(_scan_type),
    "video.bitrate_mbps": FieldSpec(_video_bitrate_mbps, fmt=_fixed(1), unit="Mb/s"),
    "video.pixel_format": FieldSpec(lambda i, _l: i.video.pixel_format if i.video else None),
    "audio.codec": FieldSpec(lambda i, _l: i.audio.codec if i.audio else None),
    "audio.channels": FieldSpec(lambda i, _l: i.audio.channels if i.audio else None),
    "audio.sample_rate_hz": FieldSpec(
        lambda i, _l: i.audio.sample_rate_hz if i.audio else None, unit="Hz"
    ),
    "audio.bitrate_kbps": FieldSpec(
        lambda i, _l: i.audio.bitrate_kbps if i.audio else None, unit="kbps"
    ),
    "audio.loudness_lkfs": FieldSpec(
        lambda _i, loudness: loudness.integrated_lufs if loudness else None,
        fmt=_fixed(1),
        unit="LKFS",
    ),
    "file.size_mb": FieldSpec(lambda i, _l: i.size_mb, fmt=_fixed(1), unit="MB"),
    "file.duration_seconds": FieldSpec(
        lambda i, _l: float(i.duration_seconds) if i.duration_seconds else None,
        fmt=_fixed(2),
        unit="s",
    ),
}


def is_known_field(key: str) -> bool:
    """True for a canonical key or any `review.*` manual check."""
    return key in FIELDS or (key.startswith("review.") and len(key) > len("review."))


# --------------------------------------------------------------------------
# Guidance and fix wording

_SEVERITY_STATUS: dict[Severity, CheckStatus] = {
    Severity.FAIL: CheckStatus.FAIL,
    Severity.WARNING: CheckStatus.WARNING,
    Severity.MANUAL: CheckStatus.MANUAL_REVIEW,
    # Context only: an unmet INFO rule must not move the overall verdict, and
    # NOT_APPLICABLE is the only status that ranks below PASS.
    Severity.INFO: CheckStatus.NOT_APPLICABLE,
}

_DEFAULT_GUIDANCE: dict[str, str] = {
    "video.frame_rate_mode": (
        "Variable frame rate is inferred from container metadata, which is a "
        "heuristic; packet analysis during conform will confirm it. Deliver at "
        "a constant frame rate."
    ),
    "video.aspect_ratio": (
        "The source frame shape does not match this destination. A transcode "
        "can scale and pad but it cannot reframe the picture, so a different "
        "composition is recommended for this placement."
    ),
}

_UNMEASURED_GUIDANCE = "The file does not report this value. Confirm it by hand."

_FIX_TEMPLATES: dict[str, str] = {
    "container": "Rewrap to {t}",
    "video.codec": "Re-encode to {t}",
    "video.profile": "Re-encode with the {t} profile",
    "video.width": "Scale to {t} px wide",
    "video.height": "Scale to {t} px tall",
    "video.resolution": "Scale to {t}",
    "video.frame_rate": "Convert frame rate to {t} fps",
    "video.frame_rate_mode": "Re-encode at a constant frame rate",
    "video.scan_type": "Deinterlace to progressive",
    "video.bitrate_mbps": "Re-encode at {t} Mb/s",
    "video.pixel_format": "Convert pixel format to {t}",
    "audio.codec": "Re-encode audio to {t}",
    "audio.channels": "Remix audio to {t} channels",
    "audio.sample_rate_hz": "Resample audio to {t}",
    "audio.bitrate_kbps": "Re-encode audio at {t} kbps",
    "audio.loudness_lkfs": "Normalize to {t} LKFS",
    "file.size_mb": "Re-encode at a lower bitrate to fit under {t} MB",
}

PRETTY_NAMES: dict[str, str] = {
    "h264": "H.264",
    "h265": "H.265",
    "hevc": "H.265",
    "prores": "ProRes",
    "dnxhd": "DNxHD",
    "aac": "AAC",
    "mp3": "MP3",
    "mp4": "MP4",
    "mov": "QuickTime",
    "cfr": "constant frame rate",
}


def _fix_target(rule: Rule) -> str:
    """The value a fix would move the file to, written for a human."""
    value = rule.equals
    if value is None and rule.preferred is not None:
        value = rule.preferred
    if value is None and rule.allowed:
        value = rule.allowed[0]
    if value is None:
        value = rule.minimum if rule.minimum is not None else rule.maximum
    if value is None:
        return "spec"
    if isinstance(value, FrameRate):
        return value.label()
    if rule.field == "audio.sample_rate_hz":
        return f"{float(value) / 1000:g} kHz"
    text = _plain(value)
    return PRETTY_NAMES.get(text.lower(), text)


def _fix_description(rule: Rule) -> str | None:
    template = _FIX_TEMPLATES.get(rule.field)
    if template is None:
        return None
    return template.format(t=_fix_target(rule))


# --------------------------------------------------------------------------
# Evaluation


def _satisfied(rule: Rule, value: Any, eq: Callable[[Any, Any], bool]) -> bool:
    """True when `value` meets every comparison the rule expresses."""
    number = _numeric(value)
    if rule.equals is not None and not eq(value, rule.equals):
        return False
    if rule.allowed and not any(eq(value, option) for option in rule.allowed):
        return False
    if rule.minimum is not None and (number is None or number < rule.minimum):
        return False
    if rule.maximum is not None and (number is None or number > rule.maximum):
        return False
    if rule.preferred is None:
        return True
    if rule.tolerance is not None:
        target = _numeric(rule.preferred)
        return (
            number is not None
            and target is not None
            and abs(number - target) <= rule.tolerance
        )
    # A bare `preferred` with no bounds means "this exact value is wanted";
    # alongside min/max the bounds already decided, and preference is guidance.
    if rule.equals is None and not rule.allowed and rule.minimum is None and rule.maximum is None:
        return eq(value, rule.preferred)
    return True


def _display(spec: FieldSpec | None, value: Any) -> str:
    if value is None:
        return "Not reported"
    if spec is None:
        return str(value)
    return f"{spec.fmt(value)} {spec.unit}".strip()


def _check(info: MediaInfo, rule: Rule, loudness: LoudnessResult | None) -> CheckResult:
    spec = FIELDS.get(rule.field)
    domain = rule.field.split(".", 1)[0]
    expected = rule.expectation_text()

    def result(
        status: CheckStatus,
        actual: str,
        *,
        guidance: str | None = None,
        fixable: bool = False,
        fix: str | None = None,
    ) -> CheckResult:
        return CheckResult(
            label=rule.label,
            status=status,
            actual=actual,
            expected=expected,
            field=rule.field,
            guidance=rule.guidance or guidance,
            fixable=fixable,
            fix_description=fix,
        )

    if rule.manual:
        actual = _display(spec, spec.get(info, loudness)) if spec else "Needs review"
        return result(CheckStatus.MANUAL_REVIEW, actual)

    if spec is None:  # unreachable via the loader; a hand-built Rule could hit it
        return result(CheckStatus.MANUAL_REVIEW, "Unknown field")

    if domain == "video" and not info.has_video:
        return result(CheckStatus.NOT_APPLICABLE, "No video stream")
    if domain == "audio" and not info.has_audio:
        return result(CheckStatus.NOT_APPLICABLE, "No audio stream")

    value = spec.get(info, loudness)
    if value is None:
        return result(CheckStatus.MANUAL_REVIEW, "Not reported", guidance=_UNMEASURED_GUIDANCE)

    actual = _display(spec, value)
    if _satisfied(rule, value, spec.eq):
        return result(CheckStatus.PASS, actual)

    # Reframing is a creative decision, so an aspect mismatch is never
    # advertised as something export will fix, whatever the JSON claims.
    fixable = rule.fixable and rule.field != "video.aspect_ratio"
    return result(
        _SEVERITY_STATUS[rule.severity],
        actual,
        guidance=_DEFAULT_GUIDANCE.get(rule.field),
        fixable=fixable,
        fix=_fix_description(rule) if fixable else None,
    )


def validate(
    info: MediaInfo,
    profile: Profile,
    loudness: LoudnessResult | None = None,
) -> ValidationReport:
    """Check `info` against every rule in `profile`.

    `loudness` comes from a separate analysis pass; without it, loudness rules
    report MANUAL_REVIEW rather than pretending the file is fine.
    """
    return ValidationReport(
        profile_id=profile.id,
        profile_name=profile.name,
        checks=tuple(_check(info, rule, loudness) for rule in profile.rules),
    )
