"""Finding and loading the profile JSON files in `specs/`.

One broken contributed profile must never stop Framecheck starting. A file
that will not parse is skipped and recorded in `errors`, which the UI can show
as "3 of 4 profiles loaded" rather than a crash on launch.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models.profile import Profile
from ..services.binaries import resource_root
from .schema import ProfileSchemaError, parse_profile

log = logging.getLogger(__name__)


def default_specs_dir() -> Path:
    """The shipped `specs/` directory, in a checkout or a frozen build."""
    return resource_root() / "specs"


class ProfileLoader:
    """Loads every `*.json` in a specs directory, caching the result."""

    def __init__(self, specs_dir: Path | None = None) -> None:
        self.specs_dir = Path(specs_dir) if specs_dir is not None else default_specs_dir()
        self._profiles: list[Profile] | None = None
        self._errors: list[str] = []

    def load_all(self) -> list[Profile]:
        """Every profile that parsed, sorted by name."""
        if self._profiles is None:
            self._profiles, self._errors = self._read()
        return list(self._profiles)

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.load_all() if p.id == profile_id), None)

    @property
    def errors(self) -> list[str]:
        """One line per file that could not be used, with the reason."""
        self.load_all()
        return list(self._errors)

    def reload(self) -> list[Profile]:
        """Re-read from disk, for someone editing a profile with the app open."""
        self._profiles = None
        self._errors = []
        return self.load_all()

    def _read(self) -> tuple[list[Profile], list[str]]:
        profiles: list[Profile] = []
        errors: list[str] = []
        if not self.specs_dir.is_dir():
            return profiles, [f"Profile directory not found: {self.specs_dir}"]

        seen: dict[str, str] = {}
        for path in sorted(self.specs_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"{path.name}: could not be read as JSON ({exc})")
                log.warning("skipping unreadable profile %s: %s", path, exc)
                continue
            try:
                profile = parse_profile(data, path)
            except ProfileSchemaError as exc:
                errors.append(str(exc))
                log.warning("skipping invalid profile %s: %s", path, exc)
                continue
            if profile.id in seen:
                errors.append(
                    f"{path.name}: duplicate profile id {profile.id!r} "
                    f"(already defined by {seen[profile.id]})"
                )
                continue
            seen[profile.id] = path.name
            profiles.append(profile)

        profiles.sort(key=lambda p: p.name.lower())
        return profiles, errors
