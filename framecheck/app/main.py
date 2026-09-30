"""Framecheck entry point.

Order matters here: logging, then QApplication, then the theme (which needs a
live QGuiApplication to resolve fonts), then the window.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from .media.playback import EMBEDS_BY_WINDOW_ID
from .services.logging_service import configure_logging
from .services.settings import Settings
from .utils.win_chrome import set_app_user_model_id

log = logging.getLogger(__name__)


def application_icon() -> "QIcon | None":
    """The app icon from assets/, or None when it has not been generated.

    Regenerate with `python tools/make_icon.py`.
    """
    from PySide6.QtGui import QIcon

    from .services.binaries import resource_root

    assets = resource_root() / "assets"
    for name in ("framecheck.ico", "framecheck.png"):
        path = assets / name
        if path.is_file():
            return QIcon(str(path))
    log.warning("application icon missing in %s", assets)
    return None


def configure_surface_format() -> None:
    """Ask for the OpenGL context libmpv's renderer needs. Before QApplication.

    libmpv renders through a QOpenGLWidget on macOS and Linux, and wants a
    modern context: macOS in particular offers only OpenGL 2.1 unless a core
    profile is requested, and it has to be requested before Qt starts.
    """
    if EMBEDS_BY_WINDOW_ID:
        return
    surface_format = QSurfaceFormat()
    surface_format.setVersion(3, 3)
    surface_format.setProfile(QSurfaceFormat.CoreProfile)
    surface_format.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface_format)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="framecheck", description="Video, to spec.")
    parser.add_argument("path", nargs="?", help="video file or folder to open on launch")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    configure_logging(verbose=args.verbose)

    # Before any window exists: this is what makes the taskbar show Framecheck
    # and its icon rather than grouping the app under python.exe.
    set_app_user_model_id()

    configure_surface_format()

    QApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Framecheck")
    app.setOrganizationName("Framecheck")
    app.setApplicationDisplayName("Framecheck")

    icon = application_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    # Imported after QApplication exists: the theme resolves font families
    # through QFontDatabase, and the UI modules import the theme.
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    apply_theme(app)

    window = MainWindow(Settings())
    window.show()

    if args.path:
        window.open_path(Path(args.path))

    try:
        return app.exec()
    except Exception:  # pragma: no cover - last-resort diagnostics
        log.exception("unhandled exception in event loop")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
