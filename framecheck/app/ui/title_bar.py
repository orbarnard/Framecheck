"""Custom window title bar.

The app owns its caption so the menus sit on the same strip as the icon, the
title and the window buttons -- the arrangement Explorer, VS Code and Office
all use.

Windows still does the hard parts. The window is frameless, but MainWindow
answers WM_NCHITTEST with HTCAPTION over this bar, so dragging, double-click to
maximise, Aero Snap, Win+arrow and the Snap Layouts flyout all behave natively
rather than being re-implemented badly.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from .theme import Color, Metrics

TITLE_BAR_HEIGHT = 34
BUTTON_WIDTH = 46

# Segoe's icon fonts carry the standard caption glyphs. Windows 11 ships
# "Segoe Fluent Icons"; Windows 10 has "Segoe MDL2 Assets". The code points are
# the same in both.
GLYPH_MINIMIZE = ""
GLYPH_MAXIMIZE = ""
GLYPH_RESTORE = ""
GLYPH_CLOSE = ""

# Windows' own close-button hover colours.
CLOSE_HOVER = "#c42b1c"
CLOSE_PRESSED = "#b0271a"


def caption_icon_font() -> QFont:
    families = set(QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in families:
            font = QFont(name)
            font.setPixelSize(10)
            return font
    # No icon font: the buttons fall back to drawn shapes.
    font = QFont()
    font.setPixelSize(10)
    return font


class CaptionButton(QPushButton):
    """A minimise / maximise / close button sized like the native ones."""

    def __init__(self, glyph: str, tooltip: str, close: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._close = close
        self._has_icon_font = bool(
            {"Segoe Fluent Icons", "Segoe MDL2 Assets"} & set(QFontDatabase.families())
        )
        self.setToolTip(tooltip)
        self.setFixedSize(QSize(BUTTON_WIDTH, TITLE_BAR_HEIGHT))
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName("CaptionClose" if close else "CaptionButton")
        if self._has_icon_font:
            self.setFont(caption_icon_font())
            self.setText(glyph)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        if self._has_icon_font:
            self.setText(glyph)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._has_icon_font:
            return
        # Fallback shapes, so the window is still operable without the font.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Color.TEXT)
        cx, cy = self.width() / 2, self.height() / 2
        if self._glyph == GLYPH_MINIMIZE:
            painter.drawLine(int(cx - 5), int(cy), int(cx + 5), int(cy))
        elif self._glyph == GLYPH_CLOSE:
            painter.drawLine(int(cx - 5), int(cy - 5), int(cx + 5), int(cy + 5))
            painter.drawLine(int(cx - 5), int(cy + 5), int(cx + 5), int(cy - 5))
        elif self._glyph == GLYPH_RESTORE:
            painter.drawRect(int(cx - 6), int(cy - 3), 9, 9)
            painter.drawLine(int(cx - 3), int(cy - 3), int(cx - 3), int(cy - 6))
        else:
            painter.drawRect(int(cx - 5), int(cy - 5), 10, 10)
        painter.end()


class TitleBar(QWidget):
    """Icon, title, menus and window controls on one strip."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, icon: QIcon | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Metrics.GUTTER_SM, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("CaptionIcon")
        self.icon_label.setFixedSize(16, 16)
        if icon is not None and not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(16, 16))
        layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        self.title_label = QLabel("Framecheck", self)
        self.title_label.setObjectName("CaptionTitle")
        layout.addWidget(self.title_label, 0, Qt.AlignVCenter)
        layout.addSpacing(Metrics.GUTTER_SM)

        # Filled in by MainWindow, which owns the actions.
        self.menu_host = QWidget(self)
        self.menu_host.setObjectName("CaptionMenuHost")
        self.menu_layout = QHBoxLayout(self.menu_host)
        self.menu_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_layout.setSpacing(0)
        self.menu_host.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        layout.addWidget(self.menu_host, 0, Qt.AlignVCenter)

        # The stretch is the draggable region.
        layout.addStretch(1)

        self.subtitle_label = QLabel("", self)
        self.subtitle_label.setObjectName("CaptionSubtitle")
        layout.addWidget(self.subtitle_label, 0, Qt.AlignVCenter)
        layout.addStretch(1)

        self.btn_minimize = CaptionButton(GLYPH_MINIMIZE, "Minimize", parent=self)
        self.btn_maximize = CaptionButton(GLYPH_MAXIMIZE, "Maximize", parent=self)
        self.btn_close = CaptionButton(GLYPH_CLOSE, "Close", close=True, parent=self)
        self.btn_minimize.clicked.connect(self.minimize_requested)
        self.btn_maximize.clicked.connect(self.maximize_requested)
        self.btn_close.clicked.connect(self.close_requested)
        for button in (self.btn_minimize, self.btn_maximize, self.btn_close):
            layout.addWidget(button, 0, Qt.AlignVCenter)

    def set_menu_bar(self, menu_bar: QWidget) -> None:
        menu_bar.setParent(self.menu_host)
        self.menu_layout.addWidget(menu_bar)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_subtitle(self, text: str, tooltip: str = "") -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setToolTip(tooltip or text)

    def set_maximized(self, maximized: bool) -> None:
        self.btn_maximize.set_glyph(GLYPH_RESTORE if maximized else GLYPH_MAXIMIZE)
        self.btn_maximize.setToolTip("Restore" if maximized else "Maximize")

    def maximize_button_geometry(self):
        """Screen-independent rect of the maximise button, in bar coordinates.

        MainWindow needs this to answer HTMAXBUTTON, which is what makes the
        Windows 11 Snap Layouts flyout appear on hover.
        """
        return self.btn_maximize.geometry()

    def is_interactive_at(self, pos) -> bool:
        """True when `pos` (bar coordinates) is over a control, not the drag area."""
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, (QPushButton,)) or child is self.menu_host:
                return True
            if child.parent() is self.menu_host or child is self.menu_host:
                return True
            child = child.parent()
        return False
