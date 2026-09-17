"""Delivery profiles: load them, validate against them, group them.

No Qt here. A profile is data (`specs/*.json`), the validator is a pure
function of (MediaInfo, Profile), and compatibility is a pure function of the
selected profiles.
"""

from __future__ import annotations

from .compatibility import analyze
from .loader import ProfileLoader, default_specs_dir
from .schema import ProfileSchemaError, parse_profile
from .validator import FIELDS, validate

__all__ = [
    "FIELDS",
    "ProfileLoader",
    "ProfileSchemaError",
    "analyze",
    "default_specs_dir",
    "parse_profile",
    "validate",
]
