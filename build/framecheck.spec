# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Framecheck.

onedir, not onefile. That is a licensing requirement, not a preference: the
bundled Qt libraries are LGPL-3.0, which obliges us to let a recipient replace
them and relink. In onedir they are ordinary .dll files in the distribution
folder that can be swapped in place; in onefile they are opaque blobs inside the
executable. See THIRD_PARTY_NOTICES.md.

`contents_directory='.'` keeps the payload flat in dist/Framecheck rather than
hiding it in `_internal`, so `sys._MEIPASS` is the distribution folder itself and
`vendor/`, `specs/` and `assets/` resolve exactly as they do in a checkout. The
Qt DLLs being visible at the top level is the point, not a side effect.

Build with `python tools/build_exe.py`, not by invoking pyinstaller directly.
"""

from pathlib import Path

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
VERSION = "0.1.0"

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

# libmpv is a *data* file, never a binary. python-mpv loads it through ctypes at
# import time after os.add_dll_directory(vendor/playback), so the DLL has to stay
# a real file at that exact relative path. As a binary PyInstaller would rewrite
# its location and the search-path registration would find nothing.
datas = [
    (str(ROOT / "vendor" / "ffmpeg" / "ffmpeg.exe"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "ffmpeg" / "ffprobe.exe"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "ffmpeg" / "FFMPEG_LICENSE"), "vendor/ffmpeg"),
    (str(ROOT / "vendor" / "playback" / "libmpv-2.dll"), "vendor/playback"),
    (str(ROOT / "vendor" / "PROVENANCE.json"), "vendor"),
    (str(ROOT / "specs"), "specs"),
    (str(ROOT / "assets" / "framecheck.ico"), "assets"),
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
    binaries=[],
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

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(0, 1, 0, 0),
        prodvers=(0, 1, 0, 0),
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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "framecheck.ico"),
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
