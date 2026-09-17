"""Visual system: colour tokens, type scale, and the application stylesheet.

Dark-first, restrained, dense. Every colour used anywhere in the UI is named
here -- no literals scattered through widget code, so the palette can be
adjusted in one place.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


class Color:
    """Colour tokens. Named by role, not by hue."""

    # Surfaces, darkest to lightest.
    CANVAS = "#0a0b0d"        # window background, behind everything
    SURFACE = "#101114"       # panels
    SURFACE_RAISED = "#16181c"  # inputs, list rows on hover
    SURFACE_ACTIVE = "#1d2025"  # pressed / selected rows
    VIDEO_BACKDROP = "#000000"  # true black behind the picture

    # Lines. Two weights only: a hairline separator and a visible border.
    SEPARATOR = "#1b1d22"
    BORDER = "#24272e"
    BORDER_STRONG = "#31353d"

    # Text, in descending prominence.
    TEXT = "#e6e8eb"
    TEXT_SECONDARY = "#9096a0"
    TEXT_TERTIARY = "#5f656e"
    TEXT_DISABLED = "#44484f"

    # One accent, used sparingly: focus, selection, the primary action.
    ACCENT = "#5b63d3"
    ACCENT_HOVER = "#6b73e0"
    ACCENT_PRESSED = "#4d55bd"
    ACCENT_SUBTLE = "#1e2036"
    ACCENT_TEXT = "#ffffff"

    # Validation states. Reserved for status, never decoration.
    PASS = "#3fb950"
    WARNING = "#d29922"
    FAIL = "#f85149"
    MANUAL = "#58a6ff"
    NEUTRAL = "#5f656e"

    # Timeline.
    TIMELINE_TRACK = "#1b1d22"
    TIMELINE_RANGE = "#2a2e37"
    TIMELINE_PLAYHEAD = "#e6e8eb"
    TIMELINE_EXCLUDED = "#0d0e11"


class Metrics:
    """Spacing and sizing constants. Compact by intent."""

    GUTTER = 12
    GUTTER_SM = 8
    GUTTER_XS = 4
    RADIUS = 6
    RADIUS_SM = 4
    ROW_HEIGHT = 26
    CONTROL_HEIGHT = 28
    TOPBAR_HEIGHT = 44
    SIDEBAR_WIDTH = 260
    INSPECTOR_WIDTH = 330


def _pick_font(candidates: list[str], fallback: str) -> str:
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return fallback


def ui_font_family() -> str:
    return _pick_font(["Inter", "Segoe UI Variable Text", "Segoe UI"], "Segoe UI")


def mono_font_family() -> str:
    """Tabular font for timecode and technical values.

    Timecode readouts must not reflow as digits change -- that jitter is what
    makes a scrubbing UI feel cheap.
    """
    return _pick_font(["Cascadia Mono", "Consolas", "Segoe UI Mono"], "Consolas")


def apply_theme(app: QApplication) -> None:
    """Install the palette, base font, and stylesheet."""
    app.setStyle("Fusion")

    base_font = QFont(ui_font_family(), 9)
    base_font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(base_font)

    app.setStyleSheet(build_stylesheet())


def status_color(status: str) -> QColor:
    """Colour for a PASS/WARNING/FAIL/MANUAL token."""
    return QColor(
        {
            "pass": Color.PASS,
            "warning": Color.WARNING,
            "warn": Color.WARNING,
            "fail": Color.FAIL,
            "manual": Color.MANUAL,
            "manual_review": Color.MANUAL,
        }.get(status.lower(), Color.NEUTRAL)
    )


def build_stylesheet() -> str:
    """Compose the stylesheet.

    A function rather than a module constant: it resolves font families through
    QFontDatabase, which requires a live QGuiApplication. Building it at import
    time would make merely importing any UI module crash.
    """
    return _STYLESHEET_TEMPLATE.replace("__MONO_FAMILY__", mono_font_family())


# Colour tokens interpolate eagerly at import; only the font family is deferred,
# via a plain token rather than a format placeholder so the CSS braces in this
# f-string need no second round of escaping.
_STYLESHEET_TEMPLATE = f"""
/* Plain containers paint nothing. Layout rows are bare QWidgets, and giving
   every one of them the canvas colour banded them against the lighter panel
   they sit on -- which read as uneven spacing rather than as stray fills.
   Backgrounds belong to named surfaces only; the window paints the canvas
   behind everything else. */
QWidget {{
    background-color: transparent;
    color: {Color.TEXT};
    font-size: 12px;
}}

QMainWindow, QDialog {{
    background-color: {Color.CANVAS};
}}

/* Labels must not paint their own background, or every label inside a raised
   surface punches a canvas-coloured hole in it. */
QLabel {{
    background: transparent;
}}

/* ---- Title bar (the app draws its own caption) ---------------------- */

#TitleBar {{
    background-color: {Color.SURFACE};
}}

#CaptionTitle {{
    color: {Color.TEXT};
    font-size: 12px;
    font-weight: 600;
}}

#CaptionSubtitle {{
    color: {Color.TEXT_TERTIARY};
    font-size: 11px;
}}

QPushButton#CaptionButton, QPushButton#CaptionClose {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    color: {Color.TEXT_SECONDARY};
    padding: 0px;
}}

QPushButton#CaptionButton:hover {{
    background-color: {Color.SURFACE_ACTIVE};
    color: {Color.TEXT};
}}

QPushButton#CaptionButton:pressed {{
    background-color: {Color.SURFACE_RAISED};
}}

/* Windows' own close-button colours, so the control behaves as users expect. */
QPushButton#CaptionClose:hover {{
    background-color: #c42b1c;
    color: #ffffff;
}}

QPushButton#CaptionClose:pressed {{
    background-color: #b0271a;
    color: #ffffff;
}}

/* ---- Top bar ------------------------------------------------------- */

#TopBar {{
    background-color: {Color.SURFACE};
    border-bottom: 1px solid {Color.SEPARATOR};
}}

#AppMark {{
    color: {Color.TEXT};
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.2px;
}}

#AppTagline {{
    color: {Color.TEXT_TERTIARY};
    font-size: 11px;
}}

#CurrentFileLabel {{
    color: {Color.TEXT_SECONDARY};
    font-size: 12px;
}}

/* ---- Panels -------------------------------------------------------- */

#Sidebar, #Inspector {{
    background-color: {Color.SURFACE};
}}

/* No side borders here: the splitter already paints a 1px handle between the
   columns, and a border on top of it reads as a doubled 2px rule. */

#PanelHeader {{
    color: {Color.TEXT_TERTIARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    padding: 0px;
}}

#SectionLabel {{
    color: {Color.TEXT_TERTIARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.7px;
}}

#MetaKey {{
    color: {Color.TEXT_TERTIARY};
    font-size: 11px;
}}

#MetaValue {{
    color: {Color.TEXT};
    font-size: 11px;
}}

#MetaValueMono {{
    color: {Color.TEXT};
    font-family: "__MONO_FAMILY__";
    font-size: 11px;
}}

#MetaValueMuted {{
    color: {Color.TEXT_DISABLED};
    font-size: 11px;
}}

#Separator {{
    background-color: {Color.SEPARATOR};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ---- Buttons ------------------------------------------------------- */

QPushButton {{
    background-color: {Color.SURFACE_RAISED};
    color: {Color.TEXT};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    padding: 5px 11px;
    font-size: 12px;
}}

QPushButton:hover {{
    background-color: {Color.SURFACE_ACTIVE};
    border-color: {Color.BORDER_STRONG};
}}

QPushButton:pressed {{
    background-color: {Color.SURFACE};
}}

QPushButton:disabled {{
    color: {Color.TEXT_DISABLED};
    border-color: {Color.SEPARATOR};
    background-color: transparent;
}}

QPushButton#Primary {{
    background-color: {Color.ACCENT};
    color: {Color.ACCENT_TEXT};
    border: 1px solid {Color.ACCENT};
    font-weight: 500;
}}

QPushButton#Primary:hover {{
    background-color: {Color.ACCENT_HOVER};
    border-color: {Color.ACCENT_HOVER};
}}

QPushButton#Primary:pressed {{
    background-color: {Color.ACCENT_PRESSED};
}}

QPushButton#Primary:disabled {{
    background-color: {Color.SURFACE_RAISED};
    color: {Color.TEXT_DISABLED};
    border-color: {Color.BORDER};
}}

QPushButton#Ghost {{
    background-color: transparent;
    border-color: transparent;
    color: {Color.TEXT_SECONDARY};
    padding: 4px 8px;
}}

QPushButton#Ghost:hover {{
    background-color: {Color.SURFACE_RAISED};
    color: {Color.TEXT};
}}

QPushButton#Transport {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {Metrics.RADIUS_SM}px;
    color: {Color.TEXT_SECONDARY};
    padding: 3px;
    min-width: 28px;
    max-width: 28px;
    min-height: 26px;
    max-height: 26px;
    font-size: 13px;
}}

QPushButton#Transport:hover {{
    background-color: {Color.SURFACE_RAISED};
    color: {Color.TEXT};
}}

QPushButton#Transport:checked {{
    background-color: {Color.ACCENT_SUBTLE};
    color: {Color.TEXT};
}}

QPushButton#Transport:disabled {{
    color: {Color.TEXT_DISABLED};
    background-color: transparent;
}}

QPushButton#TransportPrimary {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    color: {Color.TEXT};
    min-width: 34px;
    max-width: 34px;
    min-height: 28px;
    max-height: 28px;
    font-size: 13px;
}}

QPushButton#TransportPrimary:hover {{
    background-color: {Color.SURFACE_ACTIVE};
    border-color: {Color.BORDER_STRONG};
}}

/* ---- Inputs -------------------------------------------------------- */

QLineEdit {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    padding: 5px 8px;
    color: {Color.TEXT};
    selection-background-color: {Color.ACCENT};
    selection-color: {Color.ACCENT_TEXT};
}}

QLineEdit:focus {{
    border-color: {Color.ACCENT};
}}

QLineEdit:read-only {{
    color: {Color.TEXT_SECONDARY};
    background-color: {Color.SURFACE};
}}

QComboBox {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    padding: 4px 8px;
    color: {Color.TEXT};
}}

QComboBox:hover {{
    border-color: {Color.BORDER_STRONG};
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}

QComboBox QAbstractItemView {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    selection-background-color: {Color.ACCENT_SUBTLE};
    selection-color: {Color.TEXT};
    outline: none;
}}

QCheckBox {{
    color: {Color.TEXT_SECONDARY};
    spacing: 7px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {Color.BORDER_STRONG};
    border-radius: 3px;
    background-color: {Color.SURFACE_RAISED};
}}

QCheckBox::indicator:checked {{
    background-color: {Color.ACCENT};
    border-color: {Color.ACCENT};
}}

/* Checkable list items draw their own indicator and ignore the QCheckBox
   rules above, so the destination list needs the same treatment spelled out. */
QListWidget::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {Color.BORDER_STRONG};
    border-radius: 3px;
    background-color: {Color.SURFACE_RAISED};
}}

QListWidget::indicator:hover {{
    border-color: {Color.ACCENT};
}}

QListWidget::indicator:checked {{
    background-color: {Color.ACCENT};
    border-color: {Color.ACCENT};
}}

/* ---- Lists --------------------------------------------------------- */

QListWidget, QListView, QTreeWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}

QListWidget::item {{
    border-radius: {Metrics.RADIUS_SM}px;
    padding: 0px;
    margin: 1px 6px;
}}

QListWidget::item:hover {{
    background-color: {Color.SURFACE_RAISED};
}}

QListWidget::item:selected {{
    background-color: {Color.SURFACE_ACTIVE};
}}

/* ---- Scrollbars ---------------------------------------------------- */

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {Color.BORDER};
    border-radius: 4px;
    min-height: 28px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {Color.BORDER_STRONG};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}

QScrollBar::handle:horizontal {{
    background: {Color.BORDER};
    border-radius: 4px;
    min-width: 28px;
    margin: 2px;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---- Sliders (volume) ---------------------------------------------- */

QSlider::groove:horizontal {{
    height: 3px;
    background: {Color.BORDER};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {Color.TEXT_SECONDARY};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {Color.TEXT};
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border-radius: 5px;
}}

/* ---- Menus --------------------------------------------------------- */

/* The menu bar sits inside the app bar, so it paints no background of its own
   and picks up the bar's surface. */
QMenuBar {{
    background: transparent;
    color: {Color.TEXT_SECONDARY};
    padding: 0px;
}}

QMenuBar::item {{
    padding: 5px 10px;
    margin: 0px 1px;
    border-radius: {Metrics.RADIUS_SM}px;
    background: transparent;
    color: {Color.TEXT_SECONDARY};
}}

QMenuBar::item:selected {{
    background-color: {Color.ACCENT_SUBTLE};
    color: {Color.TEXT};
}}

QMenuBar::item:pressed {{
    background-color: {Color.ACCENT};
    color: {Color.ACCENT_TEXT};
}}

QMenu {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS}px;
    padding: 4px;
}}

QMenu::item {{
    padding: 5px 24px 5px 12px;
    border-radius: {Metrics.RADIUS_SM}px;
    color: {Color.TEXT_SECONDARY};
}}

QMenu::item:selected {{
    background-color: {Color.SURFACE_ACTIVE};
    color: {Color.TEXT};
}}

QMenu::item:disabled {{
    color: {Color.TEXT_DISABLED};
}}

QMenu::separator {{
    height: 1px;
    background-color: {Color.SEPARATOR};
    margin: 4px 8px;
}}

/* ---- Inspector tabs ------------------------------------------------ */

/* A segmented control rather than QTabWidget: tabs here switch a panel stack,
   and Qt's tab frame draws a border box we do not want. */
#InspectorTabs {{
    background-color: {Color.SURFACE};
    border-bottom: 1px solid {Color.SEPARATOR};
}}

QPushButton#InspectorTab {{
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    color: {Color.TEXT_TERTIARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.7px;
    padding: 7px 4px 6px 4px;
}}

QPushButton#InspectorTab:hover {{
    color: {Color.TEXT_SECONDARY};
}}

QPushButton#InspectorTab:checked {{
    color: {Color.TEXT};
    border-bottom: 2px solid {Color.ACCENT};
}}

/* ---- Trim / conform / validate / export panels ---------------------- */

#TrimPanel {{
    background-color: {Color.SURFACE};
    border-top: 1px solid {Color.SEPARATOR};
}}

#ValidationPanel, #ConformPanel, #ExportPanel, #BatchPanel {{
    background-color: {Color.SURFACE};
}}

QPushButton#Preset {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    color: {Color.TEXT_SECONDARY};
    font-family: "__MONO_FAMILY__";
    font-size: 11px;
    padding: 3px 8px;
    min-width: 34px;
}}

QPushButton#Preset:hover {{
    background-color: {Color.SURFACE_ACTIVE};
    color: {Color.TEXT};
    border-color: {Color.BORDER_STRONG};
}}

QPushButton#Preset:checked {{
    background-color: {Color.ACCENT_SUBTLE};
    border-color: {Color.ACCENT};
    color: {Color.TEXT};
}}

/* Inline "Fix on export" affordance next to a failing check. */
QPushButton#FixAction {{
    background-color: transparent;
    border: 1px solid {Color.BORDER_STRONG};
    border-radius: {Metrics.RADIUS_SM}px;
    color: {Color.TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.4px;
    padding: 2px 7px;
}}

QPushButton#FixAction:hover {{
    border-color: {Color.ACCENT};
    color: {Color.TEXT};
    background-color: {Color.ACCENT_SUBTLE};
}}

/* Timecode entry fields in the trim panel. */
QLineEdit#TimecodeEdit {{
    font-family: "__MONO_FAMILY__";
    font-size: 12px;
    padding: 4px 6px;
}}

#NoticeBlock {{
    background-color: {Color.SURFACE_RAISED};
    border: 1px solid {Color.BORDER};
    border-left: 2px solid {Color.WARNING};
    border-radius: {Metrics.RADIUS_SM}px;
}}

/* ---- Misc ---------------------------------------------------------- */

QToolTip {{
    background-color: {Color.SURFACE_RAISED};
    color: {Color.TEXT};
    border: 1px solid {Color.BORDER};
    border-radius: {Metrics.RADIUS_SM}px;
    padding: 4px 7px;
}}

QStatusBar {{
    background-color: {Color.SURFACE};
    border-top: 1px solid {Color.SEPARATOR};
    color: {Color.TEXT_TERTIARY};
}}

QStatusBar::item {{
    border: none;
}}

QSplitter::handle {{
    background-color: {Color.SEPARATOR};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QProgressBar {{
    background-color: {Color.SURFACE_RAISED};
    border: none;
    border-radius: 2px;
    height: 3px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {Color.ACCENT};
    border-radius: 2px;
}}
"""
