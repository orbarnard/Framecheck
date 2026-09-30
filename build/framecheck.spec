# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Framecheck. One recipe, two platforms.

Windows produces dist/Framecheck/ (Framecheck.exe with everything beside it);
macOS produces dist/Framecheck.app on top of that. Where the two differ, the
difference is marked below.

onedir, not onefile. That is a licensing requirement, not a preference: the
bundled Qt libraries are LGPL-3.0, which obliges us to let a recipient replace
them and relink. In onedir they are ordinary library files in the distribution
folder (or in Contents/Frameworks of the .app) that can be swapped in place; in
onefile they are opaque blobs inside the executable. See THIRD_PARTY_NOTICES.md.

`contents_directory='.'` keeps the payload flat in dist/Framecheck rather than
hiding it in `_internal`, so `sys._MEIPASS` is the distribution folder itself and
`vendor/`, `specs/` and `assets/` resolve exactly as they do in a checkout. The
Qt DLLs being visible at the top level is the point, not a side effect. (The
.app bundle has its own fixed layout, Contents/Frameworks plus
Contents/Resources cross-linked, and `sys._MEIPASS` is Contents/Frameworks.)

Build with `python tools/build_exe.py` (Windows) or `python tools/build_mac.py`
(macOS), not by invoking pyinstaller directly.
"""

import os
import re
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

if WINDOWS:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

ROOT = Path(SPECPATH).parent
# Read, not imported: the spec runs inside PyInstaller, not the app's environment.
VERSION = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "framecheck" / "__init__.py").read_text(encoding="utf-8")
).group(1)
VERSION_TUPLE = (*(int(part) for part in VERSION.split(".")), 0)

# framecheck/app/main.py uses relative imports, so it cannot be the entry script
# directly -- PyInstaller runs the entry as __main__, with no parent package.
# Generate a bootstrap into the work directory that imports it as a module, the
# same way `python -m framecheck.app.main` does.
ENTRY = Path(workpath) / "framecheck_main.py"
ENTRY.parent.mkdir(parents=True, exist_ok=True)
ENTRY.write_text(
    "import sys\nfrom framecheck.app.main import main\nsys.exit(main())\n",
    encoding="utf-8",
)

VENDOR = ROOT / "vendor"

# Windows: libmpv is a *data* file, never a binary. python-mpv loads it through
# ctypes at import time after os.add_dll_directory(vendor/playback), so the DLL
# has to stay a real file at that exact relative path. As a binary PyInstaller
# would rewrite its location and the search-path registration would find
# nothing. The shinchiro DLL and the gyan.dev executables are static, so there
# are no dependencies to collect anyway.
#
# macOS: the opposite. The Homebrew ffmpeg, ffprobe and libmpv.dylib each pull
# in dozens of shared libraries by absolute /opt/homebrew path, so they go in
# as *binaries*: PyInstaller walks those dependencies, collects every one into
# the bundle, and rewrites the load paths to @rpath so the app is self-contained.
# They keep their vendor/ paths inside the bundle; only the linkage changes.
binaries = []
if WINDOWS:
    datas = [
        (str(VENDOR / "ffmpeg" / "ffmpeg.exe"), "vendor/ffmpeg"),
        (str(VENDOR / "ffmpeg" / "ffprobe.exe"), "vendor/ffmpeg"),
        (str(VENDOR / "playback" / "libmpv-2.dll"), "vendor/playback"),
    ]
else:
    binaries = [
        (str(VENDOR / "ffmpeg" / "ffmpeg"), "vendor/ffmpeg"),
        (str(VENDOR / "ffmpeg" / "ffprobe"), "vendor/ffmpeg"),
        (str(VENDOR / "playback" / "libmpv.dylib"), "vendor/playback"),
    ]
    datas = []
datas += [
    (str(p), "vendor/ffmpeg") for p in (VENDOR / "ffmpeg").glob("FFMPEG_*")
]
datas += [
    (str(VENDOR / "PROVENANCE.json"), "vendor"),
    (str(ROOT / "specs"), "specs"),
    (str(ROOT / "assets" / "framecheck.ico"), "assets"),
    (str(ROOT / "assets" / "framecheck.png"), "assets"),
    # A GPL binary distribution must carry its licence text and notices.
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    # A GPL/LGPL binary distribution must carry the full licence text of every
    # bundled component, not just name them. PyInstaller drops .dist-info, so
    # these are kept in the repository and copied in explicitly.
    (str(ROOT / "licenses" / "LGPL-3.0.txt"), "licenses"),
    (str(ROOT / "licenses" / "LGPL-2.1.txt"), "licenses"),
    (str(ROOT / "licenses" / "GPL-2.0.txt"), "licenses"),
    (str(ROOT / "licenses" / "python-mpv-LICENSE.LGPL"), "licenses"),
    (str(ROOT / "licenses" / "README.md"), "licenses"),
]
# FFMPEG_LICENSE and PROVENANCE.json depend on how vendor/ was populated; drop
# them rather than failing the build. tools/build_exe.py checks the files that
# actually matter.
datas = [(src, dest) for src, dest in datas if Path(src).exists()]

# The app imports only QtCore, QtGui and QtWidgets (verified by grep). Everything
# below is pulled in by the PySide6 hook otherwise and costs hundreds of MB.
excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["mpv"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

def windows_version_info():
    """The VERSIONINFO resource Explorer shows in the exe's Properties."""
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=VERSION_TUPLE,
            prodvers=VERSION_TUPLE,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Framecheck"),
                            StringStruct("FileDescription", "Video, to spec."),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("InternalName", "Framecheck"),
                            StringStruct("OriginalFilename", "Framecheck.exe"),
                            StringStruct("ProductName", "Framecheck"),
                            StringStruct("ProductVersion", VERSION),
                            StringStruct(
                                "LegalCopyright",
                                "Licensed under the GNU General Public License v3.0 "
                                "or later (GPL-3.0-or-later). See LICENSE.",
                            ),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


version_info = windows_version_info() if WINDOWS else None

# macOS code signing. Unset: PyInstaller ad-hoc signs, which runs locally but
# is refused by Gatekeeper on other machines until the user opens it by hand.
# Set to a "Developer ID Application" identity (tools/build_mac.py --sign), the
# bundle is signed with the hardened runtime and the entitlements a Python app
# needs, which is what notarisation requires.
CODESIGN_IDENTITY = os.environ.get("FRAMECHECK_CODESIGN_IDENTITY") or None
ENTITLEMENTS = str(ROOT / "build" / "entitlements.plist") if CODESIGN_IDENTITY else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Framecheck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # The host architecture: the Homebrew libraries are single-arch, so an
    # Apple Silicon build and an Intel build are two separate builds.
    target_arch=None,
    codesign_identity=CODESIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
    icon=str(ROOT / "assets" / ("framecheck.icns" if MACOS else "framecheck.ico")),
    version=version_info,
    # "." disables PyInstaller's `_internal` subdirectory, so sys._MEIPASS is the
    # distribution folder itself and vendor/, specs/ and assets/ sit where
    # resource_root() expects them -- and the LGPL Qt DLLs are plainly visible.
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Framecheck",
)

if MACOS:
    app = BUNDLE(
        coll,
        name="Framecheck.app",
        icon=str(ROOT / "assets" / "framecheck.icns"),
        bundle_identifier="app.framecheck.Framecheck",
        version=VERSION,
        info_plist={
            "CFBundleName": "Framecheck",
            "CFBundleDisplayName": "Framecheck",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHumanReadableCopyright": (
                "Licensed under the GNU General Public License v3.0 or later "
                "(GPL-3.0-or-later). See LICENSE."
            ),
            "LSApplicationCategoryType": "public.app-category.video",
            # Without this the whole UI is rendered at 1x and upscaled on a
            # Retina display.
            "NSHighResolutionCapable": True,
            # Qt's dark stylesheet is the whole look; a light system appearance
            # must not leak into native controls (menus, dialogs stay native).
            "NSRequiresAquaSystemAppearance": False,
            # Media files are read with ffprobe and libmpv, never through a
            # macOS media framework, but macOS still asks what the app can
            # open before it offers it in Open With.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Video",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": ["public.movie"],
                }
            ],
        },
    )
