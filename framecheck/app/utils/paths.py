"""Path handling and the output-destination model.

Everything here is pure: no Qt, no filesystem writes beyond the collision check
it must perform. Output safety rules live here rather than in the UI so that
batch export in a later milestone inherits them for free.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Characters Windows forbids in a filename, plus control characters.
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Device names Windows reserves regardless of extension.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


# Prefix stamped on every file Framecheck writes. Makes conformed deliverables
# obvious next to the originals they were cut from.
OUTPUT_PREFIX = "FC_"


class OutputMode(Enum):
    SAME_AS_SOURCE = "same_as_source"
    CUSTOM = "custom"


@dataclass(frozen=True)
class OutputDestination:
    """Where exports go.

    Stored as a mode plus an optional directory rather than a resolved path, so
    "same folder as source" stays correct when the selected file changes.
    """

    mode: OutputMode = OutputMode.SAME_AS_SOURCE
    custom_dir: Path | None = None

    @classmethod
    def same_as_source(cls) -> "OutputDestination":
        return cls(OutputMode.SAME_AS_SOURCE, None)

    @classmethod
    def custom(cls, directory: Path | str) -> "OutputDestination":
        return cls(OutputMode.CUSTOM, Path(directory))

    def resolve_dir(self, source: Path | None) -> Path | None:
        """The directory an export would land in for this source."""
        if self.mode is OutputMode.CUSTOM and self.custom_dir is not None:
            return self.custom_dir
        if source is None:
            return None
        return source.parent

    def display_text(self, source: Path | None) -> str:
        resolved = self.resolve_dir(source)
        if resolved is None:
            return "Same folder as source"
        if self.mode is OutputMode.SAME_AS_SOURCE:
            return f"{resolved}  (same as source)"
        return str(resolved)


def sanitize_filename(name: str, fallback: str = "output") -> str:
    """Strip characters Windows will not accept in a filename.

    Does not touch the extension separator -- callers pass a bare name.
    """
    # Windows rejects names ending in a space or a dot. Strip both repeatedly:
    # a single pass in either order leaves the other behind on "clip .".
    cleaned = _ILLEGAL_CHARS.sub("_", name).strip()
    while cleaned and cleaned[-1] in " .":
        cleaned = cleaned[:-1]
    if not cleaned:
        return fallback
    stem = cleaned.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:200]


def strip_output_prefix(stem: str, prefix: str = OUTPUT_PREFIX) -> str:
    """Remove a leading ``FC_`` or ``FC_TAG-`` from a filename stem.

    Lets a conformed file be re-conformed for another destination without the
    prefixes accumulating.
    """
    if not prefix or not stem.upper().startswith(prefix.upper()):
        return stem
    remainder = stem[len(prefix) :]
    # A tag runs up to the first hyphen and is short and tag-shaped; anything
    # else is part of the user's own name and must be left alone.
    head, separator, tail = remainder.partition("-")
    if separator and tail and head and len(head) <= 12 and head.upper() == head:
        return tail
    return remainder


def same_file(a: Path, b: Path) -> bool:
    """True when two paths point at the same file.

    Uses os.path.samefile when both exist (handles junctions, substituted
    drives, UNC aliases); falls back to a case-insensitive normalised compare.
    """
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    try:
        return str(a.resolve()).lower() == str(b.resolve()).lower()
    except OSError:
        return str(a).lower() == str(b).lower()


def unique_output_path(path: Path) -> Path:
    """Return `path`, or the first free ``name (2).ext`` variant.

    Used when the user asks Framecheck to avoid a collision rather than
    overwrite. Callers that want to prompt should check `path.exists()` first.
    """
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for counter in range(2, 1000):
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not find a free filename near {path}")


def build_output_path(
    source: Path,
    destination: OutputDestination,
    *,
    suffix_tag: str | None = None,
    extension: str = ".mp4",
    filename: str | None = None,
    prefix: str = OUTPUT_PREFIX,
) -> Path:
    """Compose the full output path for a source file.

    Produces the default ``FC_sourcename_CTV.mp4`` shape: the prefix marks the
    file as Framecheck output at a glance in a folder that also holds the
    originals, and the suffix records which destination it was cut for. An
    explicit `filename` (user-edited) overrides both. The result is guaranteed
    never to be the source file itself.
    """
    directory = destination.resolve_dir(source) or source.parent
    if filename:
        stem = sanitize_filename(Path(filename).stem, fallback=source.stem)
        ext = Path(filename).suffix or extension
    else:
        stem = sanitize_filename(source.stem)
        # Strip a prefix the source already carries, so re-conforming an export
        # for a second destination replaces the tag rather than stacking them:
        # FC_CTV-spot.mp4 -> FC_YT-spot.mp4, never FC_YT-FC_CTV-spot.mp4.
        stem = strip_output_prefix(stem, prefix)
        tag = sanitize_filename(suffix_tag, fallback="") if suffix_tag else ""
        marker = f"{prefix}{tag}-" if tag else prefix
        stem = f"{marker}{stem}"
        ext = extension
    if not ext.startswith("."):
        ext = f".{ext}"

    candidate = directory / f"{stem}{ext}"
    if same_file(candidate, source):
        # Never let an export target resolve to its own source, whatever the
        # naming rules produced.
        candidate = directory / f"{stem}_framecheck{ext}"
    return candidate


def shorten_path(path: Path | str, max_length: int = 64) -> str:
    """Middle-elide a path for display in a fixed-width header."""
    text = str(path)
    if len(text) <= max_length:
        return text
    head = text[: max_length // 2 - 2]
    tail = text[-(max_length // 2 - 1) :]
    return f"{head}...{tail}"


def format_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "--"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            precision = 0 if unit in ("B", "KB") else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
