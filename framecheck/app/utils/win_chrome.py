"""Windows title-bar theming.

Qt styles everything inside the window but not the caption, which the desktop
window manager draws. Left alone it stays light, so a dark application wears a
white hat. These DWM attributes push the app's own colours into the caption.

Everything here degrades silently: the attributes arrived in different Windows
builds, and an older build simply ignores the ones it does not recognise.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

# DwmSetWindowAttribute constants.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19  # Windows 10 1809-1903
DWMWA_BORDER_COLOR = 34  # Windows 11 22000+
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


APP_USER_MODEL_ID = "Framecheck.Framecheck.App.1"


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """Give the process its own taskbar identity.

    Without this, Windows groups the window under python.exe and shows the
    Python icon in the taskbar and Alt-Tab, because the host executable owns
    the identity by default. Must be called before the first window is created.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        log.debug("could not set the app user model id", exc_info=True)


def _colorref(hex_colour: str) -> int:
    """#RRGGBB -> Windows COLORREF (0x00BBGGRR)."""
    value = hex_colour.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return (b << 16) | (g << 8) | r


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    try:
        dwmapi = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return False
    data = ctypes.c_int(value)
    result = dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(attribute),
        ctypes.byref(data),
        ctypes.sizeof(data),
    )
    return result == 0


# WM_NCHITTEST results. Returning these from a frameless window is what buys
# back every native caption behaviour instead of re-implementing it.
HTCLIENT = 1
HTCAPTION = 2
HTMINBUTTON = 8
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2

SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92


def resize_border_thickness() -> int:
    """Width of the invisible resize border, in physical pixels."""
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(SM_CXSIZEFRAME) + user32.GetSystemMetrics(
            SM_CXPADDEDBORDER
        )
    except (AttributeError, OSError):
        return 8


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def extend_frame_for_shadow(hwnd: int) -> None:
    """Restore the drop shadow a frameless window otherwise loses.

    A one-pixel extension is enough for DWM to draw the shadow and the thin
    outer border, without the caption area coming back.
    """
    if sys.platform != "win32" or not hwnd:
        return
    try:
        margins = MARGINS(0, 0, 1, 0)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(margins)
        )
    except (AttributeError, OSError):
        log.debug("could not extend frame for shadow", exc_info=True)


def apply_dark_titlebar(
    hwnd: int,
    caption: str | None = None,
    text: str | None = None,
    border: str | None = None,
) -> None:
    """Theme the window caption to match the application.

    `hwnd` must belong to a window that has been shown; the attributes are
    applied to an existing caption and are a no-op before one exists.
    """
    if sys.platform != "win32" or not hwnd:
        return

    # Dark mode first: on builds without explicit caption colours this alone
    # turns the title bar dark.
    if not _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1):
        _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, 1)

    # Exact colours: Windows 11 only, ignored elsewhere.
    if caption:
        _set_attribute(hwnd, DWMWA_CAPTION_COLOR, _colorref(caption))
    if text:
        _set_attribute(hwnd, DWMWA_TEXT_COLOR, _colorref(text))
    if border:
        _set_attribute(hwnd, DWMWA_BORDER_COLOR, _colorref(border))

    log.debug("applied dark title bar to hwnd %s", hwnd)
