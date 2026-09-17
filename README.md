# Framecheck

**Video, to spec.**

Framecheck is a Windows desktop app for checking that a video file actually
matches the technical specification of the place it is going. You open a file or
a folder, Framecheck inspects it with ffprobe, plays it back frame-accurately,
checks it against a delivery profile, and exports a conforming version — then
re-reads that export and validates it too. Everything runs locally. Your source
file is never modified.

## Why it exists

Anyone who receives video creative from someone else eventually receives video
creative that is technically wrong for its destination: the wrong container, a
codec the destination will not ingest, the wrong resolution or frame rate,
loudness nowhere near the required target, a duration that is one frame too long
for a 30-second slot, or a ProRes or MXF master that will not open in Windows
Media Player so nobody can even watch it to check. Finding these problems
normally means a command-line tool, a spreadsheet of requirements, and someone
who knows what to look for. Framecheck is that person, as an app.

## What it does today

The full loop works: **load → inspect → watch → validate → trim → conform →
export → validate the output**.

- Open a single file, open a folder, or drag and drop either onto the window.
- Browse a folder's video files in a list.
- Inspect a file with **ffprobe**: container, streams, colour metadata, frame
  rate, CFR/VFR, scan type.
- Play back in an embedded **libmpv** player with scrubbing and frame stepping.
- Trim with draggable IN/OUT markers, numeric timecode entry, duration presets
  (:06 :15 :30 :60 :90), preview cut and loop cut.
- Validate against JSON delivery profiles, with distinct PASS / WARNING /
  FAIL / MANUAL REVIEW results and a "fix on export" affordance.
- Select several destinations at once and be told when one master cannot
  reasonably serve them all.
- See exactly what conform will change before it runs, including measured
  loudness and the dB change normalisation would apply.
- Export with FFmpeg: frame-accurate trim, scale-and-pad (never crop), CFR
  normalisation, audio conversion, optional two-pass loudness normalisation.
- **Re-probe and re-validate the exported file**, because FFmpeg exiting zero
  is not evidence of compliance.
- **Batch export**: tick several files in the sidebar and conform them all to
  one destination in a single run, each one re-validated after it is written.

Every file Framecheck writes is named `FC_<DESTINATION>-<source name>`, so a
conformed deliverable is obvious next to the original it came from *and* says
which destination it was cut for:

```
CIT26-31-100-V-30H.mov          <- the master you were sent
FC_CTV-CIT26-31-100-V-30H.mp4   <- conformed for Streaming / CTV
FC_YT-CIT26-31-100-V-30H.mp4    <- conformed for YouTube
FC_OLV-CIT26-31-100-V-30H.mp4   <- conformed for Online Video
```

Re-conforming an export for a second destination swaps the tag rather than
stacking it, and a filename you type yourself is left exactly as typed. Each
profile sets its own code via `filename_tag` in its JSON.

Not built yet: OCR/speech assistance for manual-review items.

### Batch export

Tick files in the sidebar (shift-click to tick a range, or **Select all**),
choose a destination in VALIDATE, then open EXPORT. Encodes run one at a time —
x264 already uses every core, so running them in parallel finishes no sooner and
makes progress meaningless — and each output is re-probed and re-validated just
like a single export.

**Batch does not trim.** Ten files have ten different durations, and applying one
IN/OUT across them would cut creative nobody reviewed. Batch conforms each file
in full; trimming stays a single-file operation.

## Roadmap

| Milestone | Scope | Status |
| --- | --- | --- |
| M1 | Open / browse / inspect / play | Done |
| M2 | Trimming | Done |
| M3 | Delivery profile engine (JSON specs) | Done |
| M4 | Conformance, loudness, export | Done |
| M5 | More destinations and profiles | Done — 9 profiles ship |
| M6 | Batch export, output re-validation, packaging | Done |

## Frame-accurate durations

Framecheck never models timing as floating-point seconds. Frame rates are exact
rationals (`30000/1001`, not `29.97`) and positions are integer frame counts, so
the distinction that matters in delivery survives end to end:

| | Frames @ 29.97 | Real duration | Timecode |
| --- | --- | --- | --- |
| A :30 spot | 900 | 30.030 s | `00:00:30;00` |
| One frame over | 901 | 30.063 s | `00:00:30;01` |
| Exactly 30.000 s | 899.1 — not a frame | — | — |

That last row is why the trim panel tells you when a requested duration is not
on a frame boundary, and what the nearest legal cuts are, instead of rounding
silently.

**Duration targets never overrun.** When a target does not land on a frame
boundary, Framecheck takes the longest cut that stays *at or under* it. A
platform policing a :30 slot measures wall-clock seconds, so 30.030 s is
rejected while 29.997 s is accepted — a frame under costs nothing, a frame over
fails ingest.

| Rate | `:30` preset | Real duration |
| --- | --- | --- |
| 29.97 | 899 frames | 29.9967 s |
| 23.976 | 719 frames | 29.9883 s |
| 25 | 750 frames | 30.0000 s |
| 30 | 900 frames | 30.0000 s |

Broadcast delivery that genuinely wants 900 frames at 29.97 (timecode
`00:00:30;00`, running 30.030 s) is still available as `TargetMode.TIMECODE`.

## Local-only processing

No cloud, no upload, no account, no login, no telemetry, no analytics. Framecheck
runs FFmpeg and libmpv on your machine against files on your disk, and that is
all it does. The only network access in the project is `tools/fetch_binaries.py`,
which downloads FFmpeg and libmpv when you set the project up.

**Your source file is never modified.** Inspection is read-only, and every export
is written as a new file.

## Supported source formats

Framecheck offers these extensions in its file dialogs and matches them when
scanning a folder:

```
.mp4  .m4v  .mov  .avi  .mkv  .webm .mxf  .mpg
.mpeg .m2v  .ts   .m2ts .mts  .vob  .wmv  .flv
.ogv  .3gp  .dv   .gxf  .r3d  .braw
```

Typical codecs inside those: H.264, H.265/HEVC, ProRes, DNxHD/DNxHR, MPEG-2,
DV, VP9, AV1, and the usual audio (AAC, PCM, MP3, AC-3).

The honest caveat: what can actually be read is whatever the bundled FFmpeg and
libmpv builds can decode. Framecheck does not claim to open every proprietary,
camera-raw or encrypted format — camera formats such as R3D and BRAW in
particular may inspect only partially or not at all, and DRM-protected files are
out of scope entirely.

## Delivery profiles

A delivery profile describes what a destination requires: container, codec,
resolution, frame rate, bitrate, loudness, and so on. In Framecheck these are
**plain JSON files in `specs/`**, one per destination. They are data, not code —
you can read one, edit one, or contribute a new one without touching the
application, and a profile for a destination Framecheck has never heard of is
just another file in that folder.

Nine ship today:

| Profile | Frame | Notes |
| --- | --- | --- |
| `streaming_ctv` | 1920x1080 16:9 | CFR required, 15–30 Mb/s, −24 LKFS |
| `youtube` | native | preserves source frame rate and aspect |
| `online_video` | 1080p / 720p 16:9 | warns above 150 MB |
| `meta_feed` | 1080x1350 4:5 | |
| `meta_reels` | 1080x1920 9:16 | |
| `meta_instream` | 1920x1080 16:9 | |
| `snapchat` | 720x1280 9:16 | |
| `reddit` | multi-ratio | no ProRes final delivery |
| `x` | 1200x1200 1:1 | 16:9 and 9:16 also supported |

The schema is documented in [`specs/SCHEMA.md`](specs/SCHEMA.md). Two rules
matter most when writing one: a *preference* must use `severity: "warning"` and
never `"fail"`, and anything a machine cannot honestly determine — a legal
disclaimer, safe zones, creative approval — must use `manual: true`, which
always reports MANUAL REVIEW and can never be auto-passed.

Edit a file in `specs/` and use **Help ▸ Reload Profiles** to pick it up without
restarting.

## Getting started

Requires Windows and Python 3.11 or newer (developed on 3.14).

```powershell
git clone <repository-url> framecheck
cd framecheck
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python tools/fetch_binaries.py
python -m framecheck.app.main
```

`tools/fetch_binaries.py` downloads FFmpeg, ffprobe and libmpv into `vendor/`
and records exactly what it fetched in `vendor/PROVENANCE.json`. These binaries
are not committed to the repository, so this step is required. Extracting the
libmpv archive needs 7-Zip, or the `tar.exe` that ships with Windows 10 and
later.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Space` | Play / pause |
| `Left` / `Right` | Seek back / forward |
| `,` / `.` | Previous / next frame |
| `Home` / `End` | Jump to start / last 5 seconds |
| `I` / `O` | Set IN / set OUT |
| `P` | Preview cut (2 s before OUT, through OUT) |
| `L` | Loop around the cut |
| `Ctrl+R` | Reset trim |
| `Ctrl+1`…`Ctrl+4` | Inspect / Conform / Validate / Export |
| `Ctrl+O` | Open file |
| `Ctrl+Shift+O` | Open folder |
| `Ctrl+Shift+E` | Choose output folder |
| `Ctrl+E` | Conform and export |

## Building a Windows package

```
python tools/fetch_binaries.py     # once, to populate vendor/
pip install pyinstaller
python tools/build_exe.py          # add --zip for the release archive
```

The result is `dist/Framecheck/` (~423 MB) containing `Framecheck.exe` alongside
the Qt DLLs, `vendor/ffmpeg/`, `vendor/playback/libmpv-2.dll`, `specs/`,
`assets/`, `LICENSE` and `THIRD_PARTY_NOTICES.md`. `--zip` additionally produces
`dist/Framecheck-0.1.0-win64.zip` (~166 MB), ready to attach to a release.

`tools/build_exe.py` refuses to build if `vendor/` has not been populated, so a
missing `fetch_binaries.py` run fails loudly rather than producing an app that
cannot inspect or play anything.

The build is **PyInstaller onedir** — not onefile. That is a licensing
requirement, not a preference: the bundled Qt is LGPL-3.0, which obliges us to
let a recipient replace those libraries and relink, and onedir keeps them as
ordinary `.dll` files that can be swapped in place. It also avoids unpacking
~400 MB on every launch. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

The recipe is [`build/framecheck.spec`](build/framecheck.spec). Two things in it
are load-bearing and worth knowing before you change it:

- `contents_directory="."` keeps the payload flat in `dist/Framecheck` instead of
  a `_internal` subfolder, so `sys._MEIPASS` *is* the distribution folder and
  `vendor/`, `specs/` and `assets/` resolve exactly as they do in a checkout.
- `libmpv-2.dll` is bundled as a **data** file, not a binary. python-mpv loads it
  through `ctypes` after `os.add_dll_directory(vendor/playback)`, so it has to
  remain a real file at that exact relative path.

Because it is a GUI build with no console, the proof that it works is the log at
`%LOCALAPPDATA%\Framecheck\logs\framecheck.log`: it should show the startup
line, `loaded 9 delivery profiles` (bundled specs found) and `mpv attached`
(bundled libmpv loaded).

## Licence

Framecheck is licensed under the **GNU General Public License v3.0 or later**.
The full text is in [`LICENSE`](LICENSE).

GPLv3 is required, not chosen: the bundled FFmpeg build includes libx264 and is
therefore GPL (an LGPL FFmpeg cannot encode H.264, which Framecheck needs); the
bundled libmpv links that GPL FFmpeg; and Qt/PySide6 is LGPL-3.0, which combines
into GPL-3.0 but not GPL-2.0. GPLv3 is the only licence that covers all three.

Per-dependency notices, upstream URLs and redistribution obligations are in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
