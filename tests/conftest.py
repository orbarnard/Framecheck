"""Shared fixtures.

Pure-logic suite: nothing here imports Qt, and nothing here imports the
application package at module scope beyond making the repo root importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # The package is used from a checkout rather than installed.
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixtures_available() -> bool:
    """True when the generated media fixtures are present.

    A fresh clone has none: they are binaries, produced by
    ``python tools/make_fixtures.py``.
    """
    if not FIXTURES_DIR.is_dir():
        return False
    return any(p.is_file() and p.stat().st_size > 0 for p in FIXTURES_DIR.iterdir())


def _ffprobe_available() -> bool:
    from framecheck.app.services.binaries import ffprobe_path

    return ffprobe_path() is not None


requires_fixtures = pytest.mark.skipif(
    not (_fixtures_available() and _ffprobe_available()),
    reason="needs tests/fixtures/* and a bundled ffprobe "
    "(run tools/fetch_binaries.py then tools/make_fixtures.py)",
)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """An empty directory standing in for a user-chosen export folder."""
    destination = tmp_path / "output"
    destination.mkdir()
    return destination
