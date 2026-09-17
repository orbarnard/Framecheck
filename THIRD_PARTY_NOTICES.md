# Third-party notices

Framecheck is distributed under the **GNU General Public License v3.0 or later**
(see [`LICENSE`](LICENSE)). That licence is not a preference — it is the only
licence that can cover the combined work, for the reasons set out at the bottom
of this file.

**No third-party binaries are committed to this repository.** FFmpeg, ffprobe
and libmpv are downloaded at build time by `python tools/fetch_binaries.py`,
which records the exact URL and recorded licence of every download in
`vendor/PROVENANCE.json`. Python dependencies are installed from PyPI by pip.
If you redistribute a Framecheck build, the obligations below attach to *your*
distribution, because it is your build that contains the binaries.

---

## FFmpeg (including libx264)

| | |
| --- | --- |
| **Used for** | Media inspection (`ffprobe`) and, from Milestone 2 onward, trimming and transcoding (`ffmpeg`). |
| **Upstream** | <https://ffmpeg.org/> — Windows build from <https://www.gyan.dev/ffmpeg/builds/> (`ffmpeg-release-essentials.zip`) |
| **Licence** | **GPL-2.0-or-later**; the build fetched here is a **GPL build** recorded in `vendor/PROVENANCE.json` as `GPL-3.0-or-later (build includes libx264)`, because it is configured with `--enable-gpl` and links **libx264** (GPL-2.0-or-later, <https://www.videolan.org/developers/x264.html>). |
| **Obligation** | The whole distribution must be GPL-licensed. Retain FFmpeg's copyright notices and licence files (`fetch_binaries.py` extracts them alongside the executables into `vendor/ffmpeg/`), and make the complete corresponding source of the FFmpeg build available to anyone who receives a binary — either by shipping it, or by a written offer valid for three years, or by pointing at the build's own published sources. |

An LGPL FFmpeg build cannot encode H.264. H.264 output is a core requirement of
Framecheck, so the GPL build is not optional.

## libmpv

| | |
| --- | --- |
| **Used for** | Embedded video playback — decode, scrubbing and frame stepping (`vendor/playback/libmpv-2.dll`). |
| **Upstream** | <https://mpv.io/> — Windows build from <https://github.com/shinchiro/mpv-winbuild-cmake> (release `20260903`, asset `mpv-dev-x86_64-20260903-git-69e63f425a.7z`) |
| **Licence** | mpv itself is LGPL-2.1-or-later in its default configuration, but this build links the **GPL FFmpeg** above, so it must be treated as **GPL-2.0-or-later**. `vendor/PROVENANCE.json` records it as `GPL-2.0-or-later (build links GPL FFmpeg)`. |
| **Obligation** | Same as FFmpeg: GPL terms for the distribution, notices retained, and complete corresponding source of the libmpv build available to recipients. The exact release tag and asset URL are recorded in `vendor/PROVENANCE.json` precisely so that source can be identified. |

`fetch_binaries.py` resolves the *latest* release at run time, so the tag above
is the one recorded in the checked-in `vendor/PROVENANCE.json`. Whatever your
build actually downloaded is what your `vendor/PROVENANCE.json` says — cite that
file, not this one, when you redistribute.

## Qt / PySide6 (and shiboken6)

| | |
| --- | --- |
| **Used for** | The entire desktop user interface (PySide6 6.11.2), and the C++/Python bindings runtime it depends on (shiboken6, same version). |
| **Upstream** | <https://www.qt.io/qt-for-python> — <https://pypi.org/project/PySide6/>, <https://pypi.org/project/shiboken6/> |
| **Licence** | **LGPL-3.0** (open-source edition; a commercial Qt licence is the alternative and is not used here). The underlying Qt libraries are LGPL-3.0 with the Qt LGPL exceptions; some Qt modules are GPL-3.0-only and are not used by Framecheck. |
| **Obligation** | LGPL-3.0 requires that recipients can replace the Qt libraries with their own modified versions and relink. Framecheck satisfies this by shipping as a **PyInstaller onedir** bundle (Milestone 6): the Qt DLLs sit as ordinary, separate `.dll` files in the distribution directory and can be swapped out in place. A onefile bundle would make relinking materially harder and must not be used. Retain the Qt/PySide6 copyright and licence notices in the distribution, and provide the LGPL source (or a link to the exact PySide6/Qt version) on request. |

LGPL-3.0 is compatible with GPL-3.0, and only upward: it can be combined into a
GPL-3.0 work, but **not** into a GPL-2.0-only work. This is why the combined
distribution is GPL-3.0-**or-later** rather than GPL-2.0.

## python-mpv

| | |
| --- | --- |
| **Used for** | The Python ctypes binding that drives `libmpv-2.dll` (version 1.0.8). |
| **Upstream** | <https://github.com/jaseg/python-mpv> |
| **Licence** | Dual: **GPL-2.0-or-later OR LGPL-2.1-or-later**, verified from the installed package metadata (`python_mpv-1.0.8.dist-info/METADATA`: `License: GPLv2+ or LGPLv2.1+`, with both `LICENSE.GPL` and `LICENSE.LGPL` shipped). Framecheck uses it under the **LGPL-2.1-or-later** arm. |
| **Obligation** | Retain the copyright notice and licence text. LGPL-2.1-or-later permits use in a GPL-3.0 work via its "or later" clause (relicensing to LGPL-3.0, which is then GPL-3.0-compatible). Installed from PyPI at build time; not vendored here. |

## Python

CPython 3.14 (development target; the project declares `requires-python >=3.11`)
is used under the PSF License Agreement — <https://docs.python.org/3/license.html>.
Permissive and GPL-compatible; no obligation beyond notice retention.

---

## Why the whole thing is GPL-3.0-or-later

1. The bundled FFmpeg is a GPL build because it includes **libx264**, and an
   LGPL FFmpeg cannot encode H.264 — which is the product's core output format.
2. The bundled **libmpv** links that GPL FFmpeg, so it inherits GPL terms.
3. **Qt/PySide6** is **LGPL-3.0**, which can be combined into a GPL-3.0 work but
   not a GPL-2.0-only one.

GPL-3.0-or-later is the only licence that satisfies all three at once.

---

## Written offer: source for the bundled FFmpeg and libmpv

The GPL requires that anyone who receives these binaries can also obtain their
corresponding source. Framecheck does not modify either component; it downloads
published builds and redistributes them unchanged.

**Exactly which builds** a given copy of Framecheck contains is recorded in
`vendor/PROVENANCE.json`, which ships inside the distribution. At the time of
writing:

| Component | Upstream build | Source |
| --- | --- | --- |
| FFmpeg | gyan.dev release-essentials — <https://www.gyan.dev/ffmpeg/builds/> | <https://git.ffmpeg.org/ffmpeg.git> and the build recipes at <https://github.com/GyanD/codexffmpeg> |
| libmpv | shinchiro mpv-winbuild-cmake, release tag in `PROVENANCE.json` — <https://github.com/shinchiro/mpv-winbuild-cmake/releases> | <https://github.com/mpv-player/mpv> and the build scripts at <https://github.com/shinchiro/mpv-winbuild-cmake> |
| libx264 (inside FFmpeg) | as built by the above | <https://code.videolan.org/videolan/x264> |

**Written offer.** For three years from the date you received a Framecheck
binary, the project will, on request, provide the complete corresponding source
for the bundled FFmpeg and libmpv — matched to the exact build recorded in that
copy's `vendor/PROVENANCE.json` — on a physical medium or via a download link,
for no more than the cost of distribution. Open an issue on the project's
repository to request it.

**Qt / PySide6 (LGPL-3.0).** The distribution ships Qt as loose DLLs under
`PySide6/` rather than a single archive, so a recipient can replace them with
their own build of Qt. That, together with the licence text in `licenses/`, is
what LGPL-3.0 §4(d) asks for.

Licence texts for every bundled component are in the `licenses/` folder of the
distribution, plus `vendor/ffmpeg/FFMPEG_LICENSE` next to the FFmpeg binaries.

---

If you believe a notice here is wrong or incomplete, please open an issue — this
file is meant to be accurate, not decorative.
