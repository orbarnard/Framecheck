# Bundled licence texts

Framecheck is distributed under GPL-3.0-or-later (see `LICENSE` at the repository
root). It bundles third-party components under their own licences, and those
licences require their full text to travel with the binary. This folder holds
them, and the PyInstaller build copies it into the distribution.

| File | Applies to |
| --- | --- |
| `LGPL-3.0.txt` | Qt 6 and PySide6/shiboken6 |
| `LGPL-2.1.txt` | libmpv's LGPL parts, python-mpv |
| `GPL-2.0.txt` | the bundled FFmpeg and libmpv builds (GPL-2.0-or-later) |
| `python-mpv-LICENSE.LGPL` | python-mpv, as shipped by its author |

`vendor/ffmpeg/FFMPEG_LICENSE` ships alongside the FFmpeg binaries themselves.

See `THIRD_PARTY_NOTICES.md` for what each component is used for, where its
binaries came from, and how to obtain the corresponding source.
