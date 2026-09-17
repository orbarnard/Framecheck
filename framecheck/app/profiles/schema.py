"""Turning a profile JSON document into a `Profile`.

Profiles are contributed by people who should never have to read Python to add
a destination, so this module is strict and its error messages are specific:
every rejection names the file and the key that caused it. A vague
"invalid profile" would push the contributor into guessing.

Frame rates are written as decimals in JSON (23.976) because that is how specs
are published. They are parsed into exact rationals here and never stored as
floats -- 23.976 and 24000/1001 are different numbers, and only one of them is
a frame rate.

The canonical field keys live in `validator.FIELDS`: one table, so a key the
validator cannot read is a load error instead of a check that silently never
runs.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..models.media_time import FrameRate
from ..models.profile import (
    AudioTarget,
    FrameRateBehavior,
    Profile,
    Rule,
    Severity,
    TargetSpec,
)
from .validator import FIELDS, is_known_field

_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")

# Keys a requirement entry may carry. Anything else is a typo worth catching.
_RULE_KEYS = frozenset(
    {
        "equals",
        "allowed",
        "min",
        "max",
        "preferred",
        "tolerance",
        "severity",
        "fixable",
        "manual",
        "label",
        "guidance",
        "unit",
    }
)

_TARGET_KEYS = frozenset(
    {
        "container",
        "output_extension",
        "video_codec",
        "video_profile",
        "pixel_format",
        "width",
        "height",
        "aspect_ratio",
        "frame_rate_behavior",
        "allowed_frame_rates",
        "preferred_frame_rate",
        "constant_frame_rate",
        "video_bitrate_mbps",
        "video_bitrate_max_mbps",
        "audio",
        "faststart",
    }
)

_AUDIO_TARGET_KEYS = frozenset(
    {"codec", "channels", "sample_rate_hz", "bitrate_kbps", "loudness_lkfs", "true_peak_db"}
)

# Fields whose numbers are frame rates and must become exact rationals.
_RATE_FIELDS = frozenset({"video.frame_rate"})

# Trailing unit markers stripped when deriving a label; the unit comes from the
# field table instead, so "audio.sample_rate_hz" reads as "Audio sample rate".
_UNIT_SUFFIXES = ("_hz", "_kbps", "_mbps", "_lkfs", "_mb", "_seconds", "_db")


class ProfileSchemaError(ValueError):
    """A profile document is not usable. The message names what and where."""


def derive_label(field: str) -> str:
    """"video.codec" -> "Video codec"; "audio.sample_rate_hz" -> "Audio sample rate"."""
    text = field
    for suffix in _UNIT_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = text.replace(".", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else field


def parse_profile(data: dict, source_path: Path | None = None) -> Profile:
    """Build a `Profile` from a parsed JSON document.

    Raises `ProfileSchemaError` for anything a contributor needs to fix.
    """
    where = source_path.name if source_path else "<profile>"
    if not isinstance(data, dict):
        raise ProfileSchemaError(f"{where}: profile must be a JSON object")

    profile_id = data.get("id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileSchemaError(f"{where}: 'id' is required and must be a string")
    if not _ID_PATTERN.match(profile_id):
        raise ProfileSchemaError(
            f"{where}: 'id' must be a slug (lowercase letters, digits, '-' or '_'), "
            f"got {profile_id!r}"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProfileSchemaError(f"{where}: 'name' is required and must be a non-empty string")

    unknown = set(data) - {
        "id",
        "name",
        "category",
        "description",
        "filename_tag",
        "source_url",
        "notes",
        "requirements",
        "target",
    }
    if unknown:
        raise ProfileSchemaError(f"{where}: unknown top-level key(s): {_listed(unknown)}")

    requirements = data.get("requirements") or {}
    if not isinstance(requirements, dict):
        raise ProfileSchemaError(f"{where}: 'requirements' must be an object")

    rules = tuple(_parse_rule(where, key, spec) for key, spec in requirements.items())

    notes = data.get("notes") or []
    if not isinstance(notes, list) or any(not isinstance(n, str) for n in notes):
        raise ProfileSchemaError(f"{where}: 'notes' must be a list of strings")

    return Profile(
        id=profile_id,
        name=name.strip(),
        category=_string(where, data, "category", default="video"),
        description=_string(where, data, "description", default=""),
        rules=rules,
        target=_parse_target(where, data.get("target") or {}),
        tag=_string(where, data, "filename_tag", default="") or "",
        source_url=_string(where, data, "source_url", default=None),
        notes=tuple(notes),
        source_path=source_path,
    )


# --------------------------------------------------------------------------
# Requirements


def _parse_rule(where: str, field: str, spec: Any) -> Rule:
    if not is_known_field(field):
        raise ProfileSchemaError(
            f"{where}: unknown requirement key {field!r}. "
            f"Known keys: {_listed(FIELDS)}, or any 'review.*' manual check."
        )
    if not isinstance(spec, dict):
        raise ProfileSchemaError(f"{where}: requirement {field!r} must be an object")

    unknown = set(spec) - _RULE_KEYS
    if unknown:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} has unknown key(s): {_listed(unknown)}"
        )

    manual = _bool(where, spec, "manual", field, default=False)
    # A review key is manual by definition; nothing else would make sense.
    if field.startswith("review."):
        manual = True

    severity = _parse_severity(where, field, spec.get("severity"), manual)
    equals = _comparable(where, field, spec.get("equals"), "equals")
    allowed = _parse_allowed(where, field, spec.get("allowed"))
    minimum = _number(where, spec, "min", field)
    maximum = _number(where, spec, "max", field)
    preferred = _comparable(where, field, spec.get("preferred"), "preferred")
    tolerance = _number(where, spec, "tolerance", field)

    _reject_nonsense(where, field, equals, allowed, minimum, maximum, preferred, tolerance, manual)

    if field in _RATE_FIELDS:
        equals = _rate(where, field, equals)
        allowed = tuple(_rate(where, field, value) for value in allowed) if allowed else None
        preferred = _rate(where, field, preferred)

    label = spec.get("label")
    if label is not None and not isinstance(label, str):
        raise ProfileSchemaError(f"{where}: requirement {field!r} 'label' must be a string")
    guidance = spec.get("guidance")
    if guidance is not None and not isinstance(guidance, str):
        raise ProfileSchemaError(f"{where}: requirement {field!r} 'guidance' must be a string")

    field_spec = FIELDS.get(field)
    unit = spec.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise ProfileSchemaError(f"{where}: requirement {field!r} 'unit' must be a string")

    return Rule(
        field=field,
        label=label or derive_label(field),
        severity=severity,
        equals=equals,
        allowed=allowed,
        minimum=minimum,
        maximum=maximum,
        preferred=preferred,
        tolerance=tolerance,
        unit=unit if unit is not None else (field_spec.unit if field_spec else ""),
        guidance=guidance,
        manual=manual,
        # Nothing about a manual check is machine-fixable.
        fixable=False if manual else _bool(where, spec, "fixable", field, default=False),
    )


def _parse_severity(where: str, field: str, raw: Any, manual: bool) -> Severity:
    if manual:
        # Manual checks always report MANUAL_REVIEW; any other severity would
        # be a promise the validator deliberately will not keep.
        return Severity.MANUAL
    if raw is None:
        return Severity.FAIL
    if not isinstance(raw, str):
        raise ProfileSchemaError(f"{where}: requirement {field!r} 'severity' must be a string")
    try:
        return Severity(raw.strip().lower())
    except ValueError:
        legal = ", ".join(s.value for s in Severity)
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} has invalid severity {raw!r}; expected one of {legal}"
        ) from None


def _parse_allowed(where: str, field: str, raw: Any) -> tuple[Any, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} 'allowed' must be a non-empty list"
        )
    return tuple(_comparable(where, field, value, "allowed") for value in raw)


def _reject_nonsense(
    where: str,
    field: str,
    equals: Any,
    allowed: Any,
    minimum: Any,
    maximum: Any,
    preferred: Any,
    tolerance: Any,
    manual: bool,
) -> None:
    bounded = minimum is not None or maximum is not None
    if equals is not None and allowed is not None:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} combines 'equals' and 'allowed'; use one"
        )
    if equals is not None and bounded:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} combines 'equals' with 'min'/'max'; use one"
        )
    if allowed is not None and bounded:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} combines 'allowed' with 'min'/'max'; use one"
        )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ProfileSchemaError(f"{where}: requirement {field!r} has 'min' greater than 'max'")
    if tolerance is not None and preferred is None:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} has 'tolerance' without 'preferred'"
        )
    if tolerance is not None and tolerance < 0:
        raise ProfileSchemaError(f"{where}: requirement {field!r} has a negative 'tolerance'")
    if not manual and (equals, allowed, minimum, maximum, preferred) == (None,) * 5:
        raise ProfileSchemaError(
            f"{where}: requirement {field!r} states no comparison; add one, "
            f"or mark it \"manual\": true"
        )


def _comparable(where: str, field: str, value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    raise ProfileSchemaError(
        f"{where}: requirement {field!r} '{key}' must be a string or number, got {value!r}"
    )


def _rate(where: str, field: str, value: Any) -> FrameRate | None:
    if value is None:
        return None
    rate = FrameRate.parse(value)
    if rate is None:
        raise ProfileSchemaError(f"{where}: requirement {field!r} has invalid frame rate {value!r}")
    return rate


# --------------------------------------------------------------------------
# Target


def _parse_target(where: str, raw: Any) -> TargetSpec:
    if not isinstance(raw, dict):
        raise ProfileSchemaError(f"{where}: 'target' must be an object")
    unknown = set(raw) - _TARGET_KEYS
    if unknown:
        raise ProfileSchemaError(f"{where}: 'target' has unknown key(s): {_listed(unknown)}")

    defaults = TargetSpec()
    behavior_raw = raw.get("frame_rate_behavior")
    if behavior_raw is None:
        behavior = defaults.frame_rate_behavior
    else:
        try:
            behavior = FrameRateBehavior(str(behavior_raw))
        except ValueError:
            legal = ", ".join(b.value for b in FrameRateBehavior)
            raise ProfileSchemaError(
                f"{where}: 'target.frame_rate_behavior' is {behavior_raw!r}; expected {legal}"
            ) from None

    allowed_rates = raw.get("allowed_frame_rates") or []
    if not isinstance(allowed_rates, list):
        raise ProfileSchemaError(f"{where}: 'target.allowed_frame_rates' must be a list")

    return TargetSpec(
        container=_string(where, raw, "container", default=defaults.container, prefix="target."),
        output_extension=_string(
            where, raw, "output_extension", default=defaults.output_extension, prefix="target."
        ),
        video_codec=_string(
            where, raw, "video_codec", default=defaults.video_codec, prefix="target."
        ),
        video_profile=_string(
            where, raw, "video_profile", default=defaults.video_profile, prefix="target."
        ),
        pixel_format=_string(
            where, raw, "pixel_format", default=defaults.pixel_format, prefix="target."
        ),
        width=_int(where, raw, "width", prefix="target."),
        height=_int(where, raw, "height", prefix="target."),
        aspect_ratio=_string(where, raw, "aspect_ratio", default=None, prefix="target."),
        frame_rate_behavior=behavior,
        allowed_frame_rates=tuple(
            _target_rate(where, "allowed_frame_rates", value) for value in allowed_rates
        ),
        preferred_frame_rate=_target_rate(
            where, "preferred_frame_rate", raw.get("preferred_frame_rate")
        ),
        constant_frame_rate=_bool(
            where, raw, "constant_frame_rate", "target", default=defaults.constant_frame_rate
        ),
        video_bitrate_mbps=_number(where, raw, "video_bitrate_mbps", "target"),
        video_bitrate_max_mbps=_number(where, raw, "video_bitrate_max_mbps", "target"),
        audio=_parse_audio_target(where, raw.get("audio") or {}),
        faststart=_bool(where, raw, "faststart", "target", default=defaults.faststart),
    )


def _target_rate(where: str, key: str, value: Any) -> Fraction | None:
    if value is None:
        return None
    rate = FrameRate.parse(value)
    if rate is None:
        raise ProfileSchemaError(f"{where}: 'target.{key}' has invalid frame rate {value!r}")
    return rate.value


def _parse_audio_target(where: str, raw: Any) -> AudioTarget:
    if not isinstance(raw, dict):
        raise ProfileSchemaError(f"{where}: 'target.audio' must be an object")
    unknown = set(raw) - _AUDIO_TARGET_KEYS
    if unknown:
        raise ProfileSchemaError(f"{where}: 'target.audio' has unknown key(s): {_listed(unknown)}")
    defaults = AudioTarget()
    return AudioTarget(
        codec=_string(where, raw, "codec", default=defaults.codec, prefix="target.audio."),
        channels=_int(where, raw, "channels", prefix="target.audio.") or defaults.channels,
        sample_rate_hz=(
            _int(where, raw, "sample_rate_hz", prefix="target.audio.") or defaults.sample_rate_hz
        ),
        bitrate_kbps=(
            _int(where, raw, "bitrate_kbps", prefix="target.audio.") or defaults.bitrate_kbps
        ),
        loudness_lkfs=_number(where, raw, "loudness_lkfs", "target.audio"),
        true_peak_db=_number(where, raw, "true_peak_db", "target.audio"),
    )


# --------------------------------------------------------------------------
# Small typed readers


def _listed(values: Any) -> str:
    return ", ".join(repr(v) for v in sorted(values))


def _string(where: str, data: dict, key: str, *, default: str | None, prefix: str = "") -> Any:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ProfileSchemaError(f"{where}: '{prefix}{key}' must be a string, got {value!r}")
    return value


def _number(where: str, data: dict, key: str, field: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileSchemaError(f"{where}: {field!r} '{key}' must be a number, got {value!r}")
    return float(value)


def _int(where: str, data: dict, key: str, *, prefix: str = "") -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileSchemaError(f"{where}: '{prefix}{key}' must be an integer, got {value!r}")
    return value


def _bool(where: str, data: dict, key: str, field: str, *, default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProfileSchemaError(f"{where}: {field!r} '{key}' must be true or false, got {value!r}")
    return value
