"""Finding and loading the profile JSON files.

Two folders: the shipped `specs/` inside the install, and the user's own
`%LOCALAPPDATA%\\Framecheck\\specs`. An upgrade replaces the first and never
touches the second, so custom and edited specs live there. A user spec with the
same `id` as a shipped one replaces it -- that is how a built-in is customised.

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
from ..utils.paths import app_data_dir
from .schema import ProfileSchemaError, parse_profile

log = logging.getLogger(__name__)


def default_specs_dir() -> Path:
    """The shipped `specs/` directory, in a checkout or a frozen build."""
    return resource_root() / "specs"


def user_specs_dir() -> Path:
    """The user's own specs, which upgrades never touch. Not created here."""
    return app_data_dir() / "specs"


USER_SPECS_README = """Custom Framecheck delivery specs
================================

Put your own delivery spec JSON files in this folder. Framecheck upgrades never
touch it.

To change a built-in destination, copy its JSON from the "specs" folder in the
Framecheck install folder into this one and edit the copy. A spec here with the
same "id" replaces the built-in one.

After editing, use Help > Reload Profiles in Framecheck.
"""


class ProfileLoader:
    """Loads every `*.json` in the shipped and user specs folders, caching the result.

    Given an explicit `specs_dir`, only that folder is read unless `user_dir`
    is also given -- tests stay independent of whatever is on the machine.
    """

    def __init__(self, specs_dir: Path | None = None, user_dir: Path | None = None) -> None:
        if specs_dir is None:
            specs_dir, user_dir = default_specs_dir(), user_dir or user_specs_dir()
        self.specs_dir = Path(specs_dir)
        self.user_dir = Path(user_dir) if user_dir is not None else None
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
        errors: list[str] = []
        if not self.specs_dir.is_dir():
            errors.append(f"Profile directory not found: {self.specs_dir}")
            shipped: dict[str, Profile] = {}
        else:
            shipped = self._read_dir(self.specs_dir, errors)
        user = self._read_dir(self.user_dir, errors) if self.user_dir else {}
        for profile_id in shipped.keys() & user.keys():
            log.info("custom spec %s replaces the built-in one", user[profile_id].source_path)
        merged = {**shipped, **user}
        return sorted(merged.values(), key=lambda p: p.name.lower()), errors

    @staticmethod
    def _read_dir(directory: Path, errors: list[str]) -> dict[str, Profile]:
        """Profiles in one folder by id. Duplicates *within* a folder are errors."""
        profiles: dict[str, Profile] = {}
        if not directory.is_dir():
            return profiles

        seen: dict[str, str] = {}
        for path in sorted(directory.glob("*.json")):
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
            profiles[profile.id] = profile
        return profiles
