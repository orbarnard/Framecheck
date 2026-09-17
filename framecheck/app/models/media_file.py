"""The unit of work: one source file and what we know about it.

Even when a single file is open, Framecheck keeps a list of MediaFile. That
keeps the batch work in later milestones a UI change rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .media_info import MediaInfo

# Extensions we offer in file dialogs and match when scanning a folder.
# Deliberately not "everything FFmpeg can open" -- a folder scan that drags in
# every .bin is worse than one that misses an exotic container the user can
# still open explicitly via Open File.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".mxf",
        ".mpg",
        ".mpeg",
        ".m2v",
        ".ts",
        ".m2ts",
        ".mts",
        ".vob",
        ".wmv",
        ".flv",
        ".ogv",
        ".3gp",
        ".dv",
        ".gxf",
        ".r3d",
        ".braw",
    }
)


class ProbeState(Enum):
    """Where a file is in the inspection lifecycle."""

    PENDING = "pending"
    PROBING = "probing"
    READY = "ready"
    ERROR = "error"


@dataclass
class MediaFile:
    """A source file in the workspace.

    Mutable by design: workers fill in `info` as results arrive. Identity is
    the resolved path.
    """

    path: Path
    state: ProbeState = ProbeState.PENDING
    info: MediaInfo | None = None
    error: str | None = None
    # Set when the embedded player could not open the source directly and a
    # preview proxy was substituted. Milestone 1 records this; proxy creation
    # itself is Milestone 6.
    playback_failed: bool = False
    tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False

    @property
    def key(self) -> str:
        """Stable identity for lookups. Case-insensitive, as Windows is."""
        try:
            return str(self.path.resolve()).lower()
        except OSError:
            return str(self.path).lower()

    def summary_line(self) -> str:
        """One-line technical summary for dense list rows."""
        if self.state is ProbeState.ERROR:
            return "Unreadable"
        if self.state is not ProbeState.READY or self.info is None:
            return "Not analyzed"
        info = self.info
        parts: list[str] = []
        if info.video:
            if info.video.resolution:
                parts.append(info.video.resolution)
            if info.video.codec:
                parts.append(info.video.codec.upper())
            rate = info.frame_rate
            if rate:
                parts.append(f"{rate.label()} fps")
        elif info.audio:
            parts.append("Audio only")
            if info.audio.codec:
                parts.append(info.audio.codec.upper())
        return "  ".join(parts) if parts else "No streams"


def is_supported_media(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def media_dialog_filter() -> str:
    """Qt file-dialog filter string covering supported extensions."""
    patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
    return f"Video files ({patterns});;All files (*)"
