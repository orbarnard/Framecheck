# Contributing to Framecheck

Contributions are welcome. Framecheck is GPL-3.0-or-later; by contributing you
agree your changes are licensed under the same terms.

## Development setup

Windows, Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python tools/fetch_binaries.py
python -m framecheck.app.main
```

`tools/fetch_binaries.py` downloads FFmpeg, ffprobe and libmpv into `vendor/`
(not committed) and records what it fetched in `vendor/PROVENANCE.json`. If you
change how a binary is obtained, update that script and
`THIRD_PARTY_NOTICES.md` in the same pull request.

## Tests

```powershell
pytest
```

Tests live in `tests/`. Anything with real logic in it — a parser, a branch, a
duration or frame-count calculation — needs a test. Anything that would silently
produce a wrong number needs a test more than most.

## Code style

- **Type hints everywhere**, and `from __future__ import annotations` at the top
  of every module.
- **No Qt imports in `framecheck/app/models/`.** The models are plain Python data
  and must stay testable without a QApplication. Qt belongs in `ui/`, `workers/`
  and the Qt-backed parts of `services/`.
- **All FFmpeg and ffprobe argument construction lives in
  `framecheck/app/media/`.** No other package builds command lines. If the UI
  needs a transcode, it asks `media/` for one.
- **Subprocess calls take argument lists, never shell strings.** No `shell=True`,
  no string interpolation into a command, ever. Paths contain spaces, quotes and
  non-ASCII characters, and users do not control what their files are called.
- **Never modify the user's source file.** Inspection is read-only; every export
  is a new file at a new path. A code path that writes to, renames, moves or
  deletes an input file is a bug, not a feature.
- Keep line length at 100 (`[tool.ruff]` in `pyproject.toml`).
- Prefer the standard library. New runtime dependencies need a reason in the
  pull request.

## Contributing a delivery profile

Delivery profiles are **plain JSON in `specs/`** — one file per destination.
They are data, so adding a destination requires no application code.

- One destination per file, named after the destination.
- Include a **real-world source** for the spec values: a link to the published
  delivery specification, or a dated reference to the document you were given.
  A profile whose numbers cannot be traced to a source will not be merged.
- Record the date you checked it. Destinations change their requirements.

The profile **schema arrives in Milestone 3**. Until then, profile pull requests
will be held rather than merged — open an issue with the spec you want covered
and it will be waiting when the schema lands.

## Results: PASS, WARNING, FAIL, MANUAL REVIEW

These four are distinct and must stay distinct. Collapsing them is the fastest
way to make Framecheck useless.

| Result | Means |
| --- | --- |
| **PASS** | The requirement was checked automatically and is met. |
| **WARNING** | Checked, not met, but the requirement is a preference or recommendation — the file is still deliverable. |
| **FAIL** | Checked, not met, and the requirement is mandatory — the destination will reject the file. |
| **MANUAL REVIEW** | Cannot be determined automatically. A human must look. |

Two rules follow, and neither is negotiable:

1. **A preference is never reported as a hard failure.** If the spec says
   "recommended", "preferred" or "should", it is a WARNING. Crying FAIL over a
   recommendation trains users to ignore real failures.
2. **Anything that cannot be determined automatically is MANUAL REVIEW, and is
   never auto-passed.** Legal disclaimers, on-screen text legibility, title and
   action safe zones, brand and creative approval, whether the audio is the
   right audio — a machine cannot judge these. Returning PASS for an unchecked
   requirement is worse than returning nothing, because it tells someone the
   file is cleared when nobody looked.

When you add a check, decide which of the four it can produce and say so in the
test.
