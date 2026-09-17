"""The PASS / WARNING / FAIL / MANUAL pill shared by the inspector panels.

One widget, painted rather than styled, so a chip costs a dot and a word and
lines up identically wherever it appears.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..models.profile import CheckStatus
from .theme import status_color

_DOT = 6
_GAP = 6
_TEXT_PX = 10
_TRACKING = 0.7

_LABELS = {
    CheckStatus.PASS: "PASS",
    CheckStatus.WARNING: "WARNING",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.MANUAL_REVIEW: "MANUAL REVIEW",
    CheckStatus.NOT_APPLICABLE: "N/A",
}


def status_label(status: CheckStatus | None) -> str:
    """Uppercase word for a status. Empty string for no status at all."""
    if status is None:
        return ""
    return _LABELS.get(status, status.value.replace("_", " ").upper())


def status_qcolor(status: CheckStatus | None) -> QColor:
    """Theme colour for a status."""
    return status_color(status.value if status is not None else "")


class StatusChip(QWidget):
    """A coloured dot with an uppercase label.

    In `compact` mode the word is dropped for PASS and NOT_APPLICABLE: a row of
    green dots reads as "fine" faster than a column of the word PASS.
    """

    def __init__(
        self,
        status: CheckStatus = CheckStatus.NOT_APPLICABLE,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._status = status
        self._compact = compact
        self._font = QFont()
        self._font.setPixelSize(_TEXT_PX)
        self._font.setWeight(QFont.Weight.DemiBold)
        self._font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, _TRACKING)
        # Purely informational: never eat a click meant for the row behind it.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    # -- api -------------------------------------------------------------

    def set_status(self, status: CheckStatus) -> None:
        if status is self._status:
            return
        self._status = status
        self.updateGeometry()
        self.update()

    def status(self) -> CheckStatus:
        return self._status

    def text(self) -> str:
        if not self._compact:
            return status_label(self._status)
        if self._status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE):
            return ""
        # "MANUAL REVIEW" is too wide for a dense row; the dot carries the colour.
        if self._status is CheckStatus.MANUAL_REVIEW:
            return "MANUAL"
        return status_label(self._status)

    # -- painting --------------------------------------------------------

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._font)
        text = self.text()
        # QFontMetrics does not reliably include absolute letter spacing, so add
        # it back per character plus a pixel of slack. Under-measuring here
        # clips the label to "WARNIN(", which looks like a rendering bug.
        tracking = int(_TRACKING * len(text)) + 2 if text else 0
        width = _DOT + (_GAP + metrics.horizontalAdvance(text) + tracking if text else 0)
        return QSize(width, max(_DOT, metrics.height()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        color = status_qcolor(self._status)
        top = (self.height() - _DOT) / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(0.0, top, float(_DOT), float(_DOT)))

        text = self.text()
        if text:
            painter.setFont(self._font)
            painter.setPen(color)
            painter.drawText(
                QRectF(_DOT + _GAP, 0.0, float(self.width() - _DOT - _GAP), float(self.height())),
                int(Qt.AlignLeft | Qt.AlignVCenter),
                text,
            )
        painter.end()
