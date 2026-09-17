"""The left sidebar: the list of source files in the open folder.

Shows one dense row per MediaFile -- checkbox, name, technical summary,
duration and a probe-status dot -- and emits `file_activated` when the user
picks one. Rows are painted by a delegate rather than built from per-row
widgets so a folder of a few thousand files costs a few thousand dataclasses,
not a few thousand QWidgets.

Two independent selections live here: the *current* file (one, drives the
player, `file_activated`) and the *checked* files (many, drive batch export,
`selection_changed`). Ticking a checkbox never changes the current file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models.media_file import MediaFile, ProbeState
from ..models.media_time import format_duration_short
from .theme import Color, Metrics, mono_font_family

# Checked state lives in a private role, not Qt.CheckStateRole: the stock role
# makes the style paint its own indicator on top of the one we draw.
CHECKED_ROLE = Qt.UserRole + 1

ROW_HEIGHT = 38          # floor; the delegate grows it if the fonts need it
ROW_INSET_X = 3          # keeps the selected-row pill off the panel edge
ROW_INSET_Y = 1
LINE_GAP = 1

CHECK_SIZE = 13
CHECK_LEFT = 6
# Click zone for the checkbox, measured from the left edge of the row rect.
CHECK_COLUMN_WIDTH = ROW_INSET_X + CHECK_LEFT + CHECK_SIZE + 6

DOT_DIAMETER = 6
DOT_LEFT_MARGIN = CHECK_LEFT + CHECK_SIZE + 6
TEXT_LEFT_MARGIN = DOT_LEFT_MARGIN + DOT_DIAMETER + 7
RIGHT_MARGIN = 8
DURATION_WIDTH = 46


def _status_color(media_file: MediaFile) -> str:
    """Colour of the row's status dot.

    Milestone 3 swaps this for profile validation status; keeping it a single
    free function means the delegate does not need to change.
    """
    return {
        ProbeState.PENDING: Color.NEUTRAL,
        ProbeState.PROBING: Color.MANUAL,
        ProbeState.READY: Color.PASS,
        ProbeState.ERROR: Color.FAIL,
    }.get(media_file.state, Color.NEUTRAL)


class _FileRowDelegate(QStyledItemDelegate):
    """Paints a two-line file row with a checkbox, status dot and duration."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name_font = QFont()
        self._name_font.setPixelSize(12)
        self._detail_font = QFont()
        self._detail_font.setPixelSize(10)
        self._duration_font = QFont(mono_font_family())
        self._duration_font.setPixelSize(11)

    def row_height(self) -> int:
        """Row height that fits both lines at the current DPI scaling.

        Measured rather than assumed: at 125% Windows scaling the two lines
        want more room than the nominal pixel sizes suggest, and a fixed 38
        would clip the descenders.
        """
        text = (
            QFontMetrics(self._name_font).height()
            + QFontMetrics(self._detail_font).height()
            + LINE_GAP
        )
        return max(ROW_HEIGHT, text + 2 * (ROW_INSET_Y + 2))

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        # Width 0, not option.rect.width(): echoing the viewport width back at
        # the view feeds its own layout, and after a relayout the rows grow
        # past the viewport and raise a horizontal scrollbar. Rows stretch to
        # the viewport on their own in list mode.
        return QSize(0, self.row_height())

    def _paint_checkbox(self, painter: QPainter, rect: QRect, checked: bool) -> None:
        box = QRect(rect.left() + CHECK_LEFT, rect.center().y() - CHECK_SIZE // 2,
                    CHECK_SIZE, CHECK_SIZE)
        if checked:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(Color.ACCENT))
            painter.drawRoundedRect(box, 3, 3)
            painter.setBrush(Qt.NoBrush)
            pen = QPen(QColor(Color.ACCENT_TEXT))
            pen.setWidth(2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            left = QPoint(box.left() + 3, box.center().y())
            mid = QPoint(box.center().x() - 1, box.bottom() - 4)
            right = QPoint(box.right() - 2, box.top() + 4)
            painter.drawPolyline([left, mid, right])
        else:
            painter.setBrush(QColor(Color.SURFACE_RAISED))
            painter.setPen(QPen(QColor(Color.BORDER_STRONG), 1))
            # Half-pixel offset so the 1px border lands on a device pixel.
            painter.drawRoundedRect(
                QRect(box.left(), box.top(), box.width() - 1, box.height() - 1), 3, 3
            )

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        media_file: MediaFile | None = index.data(Qt.UserRole)
        if media_file is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect.adjusted(ROW_INSET_X, ROW_INSET_Y, -ROW_INSET_X, -ROW_INSET_Y)
        if option.state & QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(Color.SURFACE_ACTIVE))
            painter.drawRoundedRect(rect, Metrics.RADIUS_SM, Metrics.RADIUS_SM)
        elif option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(Color.SURFACE_RAISED))
            painter.drawRoundedRect(rect, Metrics.RADIUS_SM, Metrics.RADIUS_SM)

        self._paint_checkbox(painter, rect, bool(index.data(CHECKED_ROLE)))

        painter.setBrush(QColor(_status_color(media_file)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            rect.left() + DOT_LEFT_MARGIN,
            rect.center().y() - DOT_DIAMETER // 2,
            DOT_DIAMETER,
            DOT_DIAMETER,
        )

        duration = (
            format_duration_short(media_file.info.duration_seconds)
            if media_file.info is not None
            else "--:--"
        )
        duration_rect = QRect(
            rect.right() - RIGHT_MARGIN - DURATION_WIDTH,
            rect.top(),
            DURATION_WIDTH,
            rect.height(),
        )
        painter.setFont(self._duration_font)
        painter.setPen(QColor(Color.TEXT_SECONDARY))
        painter.drawText(duration_rect, Qt.AlignRight | Qt.AlignVCenter, duration)

        text_left = rect.left() + TEXT_LEFT_MARGIN
        text_width = max(0, duration_rect.left() - Metrics.GUTTER_XS - text_left)

        # Centre the two lines as a block, so there is no dead strip under them.
        name_h = QFontMetrics(self._name_font).height()
        detail_h = QFontMetrics(self._detail_font).height()
        top = rect.top() + (rect.height() - (name_h + detail_h + LINE_GAP)) // 2

        painter.setFont(self._name_font)
        painter.setPen(QColor(Color.TEXT))
        name = QFontMetrics(self._name_font).elidedText(
            media_file.name, Qt.ElideRight, text_width
        )
        painter.drawText(
            QRect(text_left, top, text_width, name_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            name,
        )

        painter.setFont(self._detail_font)
        painter.setPen(QColor(Color.TEXT_TERTIARY))
        detail = QFontMetrics(self._detail_font).elidedText(
            media_file.summary_line(), Qt.ElideRight, text_width
        )
        painter.drawText(
            QRect(text_left, top + name_h + LINE_GAP, text_width, detail_h),
            Qt.AlignLeft | Qt.AlignVCenter,
            detail,
        )

        painter.restore()


class _FileList(QListWidget):
    """List view that routes clicks in the checkbox column away from selection.

    Swallowing the press (rather than letting the view handle it and undoing
    the damage afterwards) is what keeps ticking a box from loading the file.
    """

    checkbox_clicked = Signal(int, bool)  # row, shift held

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._swallowed = False

    def _checkbox_row(self, event) -> int:
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if not index.isValid():
            return -1
        rect = self.visualRect(index)
        if pos.x() - rect.left() <= CHECK_COLUMN_WIDTH:
            return index.row()
        return -1

    def mousePressEvent(self, event) -> None:
        row = self._checkbox_row(event) if event.button() == Qt.LeftButton else -1
        if row >= 0:
            self._swallowed = True
            self.checkbox_clicked.emit(row, bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return
        self._swallowed = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._swallowed:
            self._swallowed = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._checkbox_row(event) >= 0:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SourceBrowser(QWidget):
    """Sidebar listing the source files of the open folder."""

    file_activated = Signal(object)
    selection_changed = Signal(list)  # list[MediaFile] -- the ticked files

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        # Range, not a fixed width: the splitter sizes this column, and a fixed
        # width leaves the difference showing the window canvas as a vertical
        # band beside the panel.
        self.setMinimumWidth(200)
        self.setMaximumWidth(460)

        self._files: list[MediaFile] = []
        self._emitted_key: str | None = None
        self._last_toggled_row: int | None = None

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(Metrics.GUTTER_SM)
        # Literal caps: Qt stylesheets ignore text-transform.
        title = QLabel("SOURCE FILES")
        title.setObjectName("PanelHeader")
        self._count_label = QLabel("")
        self._count_label.setObjectName("SectionLabel")
        self._select_toggle = QPushButton("Select all")
        self._select_toggle.setObjectName("Ghost")
        # No programmatic font here: the stylesheet sizes button text, and a
        # competing QFont makes sizeHint under-measure, clipping the label to
        # "elect non".
        self._select_toggle.setCursor(Qt.PointingHandCursor)
        self._select_toggle.setFocusPolicy(Qt.NoFocus)
        self._select_toggle.setVisible(False)
        self._select_toggle.clicked.connect(self._on_select_toggle)
        # The toggle must never be the element that loses the width fight, so
        # the title is allowed to shrink and the toggle keeps its natural size.
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._count_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._select_toggle.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        header.addWidget(title, 1)
        header.addStretch(0)
        header.addWidget(self._count_label, 0)
        header.addWidget(self._select_toggle, 0)

        self._folder_label = QLabel("")
        self._folder_label.setObjectName("MetaValueMuted")
        self._folder_label.setVisible(False)

        self._delegate = _FileRowDelegate(self)
        self._list = _FileList()
        self._list.setItemDelegate(self._delegate)
        self._list.setMouseTracking(True)
        self._list.setUniformItemSizes(True)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.setFrameShape(QListWidget.NoFrame)
        self._list.currentItemChanged.connect(self._on_current_changed)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.checkbox_clicked.connect(self._on_checkbox_clicked)

        self._placeholder = QLabel("")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setContentsMargins(Metrics.GUTTER, 0, Metrics.GUTTER, 0)

        self._stack = QStackedLayout()
        self._stack.addWidget(self._list)
        self._stack.addWidget(self._placeholder)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER_SM
        )
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addLayout(header)
        layout.addWidget(self._folder_label)
        layout.addLayout(self._stack, 1)

        self._show_placeholder(
            "No folder open", "Open a folder to see its video files here."
        )

    # ---- files ---------------------------------------------------------

    def set_files(self, files: list[MediaFile], folder: Path | None = None) -> None:
        self._files = list(files)
        self._emitted_key = None
        self._last_toggled_row = None
        had_checks = bool(self.checked_files())

        row_height = self._delegate.row_height()
        self._list.blockSignals(True)
        self._list.clear()
        for media_file in self._files:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, media_file)
            item.setData(CHECKED_ROLE, False)
            item.setSizeHint(QSize(0, row_height))
            item.setToolTip(str(media_file.path))
            self._list.addItem(item)
        self._list.blockSignals(False)

        if folder is not None:
            self._folder_label.setToolTip(str(folder))
            self._folder_label.setText(
                self._folder_label.fontMetrics().elidedText(
                    folder.name or str(folder),
                    Qt.ElideMiddle,
                    # Elide against the real width, which the splitter controls.
                    max(80, self.width() - 2 * Metrics.GUTTER),
                )
            )
            self._folder_label.setVisible(True)
        else:
            self._folder_label.setVisible(False)

        self._refresh_header()
        if had_checks:
            self.selection_changed.emit([])

        if self._files:
            self._stack.setCurrentWidget(self._list)
        elif folder is not None:
            self._show_placeholder("No video files", "This folder has no supported video files.")
        else:
            self._show_placeholder("No folder open", "Open a folder to see its video files here.")

    def update_file(self, media_file: MediaFile) -> None:
        key = media_file.key
        for row in range(self._list.count()):
            item = self._list.item(row)
            existing: MediaFile | None = item.data(Qt.UserRole)
            if existing is not None and existing.key == key:
                # CHECKED_ROLE is deliberately left alone: a probe finishing
                # must not untick a row the user has queued for export.
                item.setData(Qt.UserRole, media_file)
                item.setToolTip(str(media_file.path))
                if row < len(self._files):
                    self._files[row] = media_file
                self._list.update(self._list.indexFromItem(item))
                return

    def select_path(self, path: Path) -> None:
        key = MediaFile(path).key
        for row in range(self._list.count()):
            media_file: MediaFile | None = self._list.item(row).data(Qt.UserRole)
            if media_file is not None and media_file.key == key:
                self._list.setCurrentRow(row)
                return

    def current_file(self) -> MediaFile | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def clear(self) -> None:
        self.set_files([])

    def set_scanning(self, scanning: bool) -> None:
        if scanning:
            self._show_placeholder("Scanning folder…", "")
        elif self._files:
            self._stack.setCurrentWidget(self._list)
        else:
            self._show_placeholder("No folder open", "Open a folder to see its video files here.")

    # ---- checked set ---------------------------------------------------

    def checked_files(self) -> list[MediaFile]:
        """The ticked files, in row order."""
        out: list[MediaFile] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(CHECKED_ROLE):
                media_file: MediaFile | None = item.data(Qt.UserRole)
                if media_file is not None:
                    out.append(media_file)
        return out

    def set_checked(self, files: list[MediaFile]) -> None:
        keys = {f.key for f in files}
        states: dict[int, bool] = {}
        for row in range(self._list.count()):
            media_file: MediaFile | None = self._list.item(row).data(Qt.UserRole)
            states[row] = media_file is not None and media_file.key in keys
        self._apply(states)

    def clear_checks(self) -> None:
        self._apply({row: False for row in range(self._list.count())})

    def check_all(self) -> None:
        self._apply({row: True for row in range(self._list.count())})

    def _apply(self, states: dict[int, bool]) -> None:
        changed = False
        for row, checked in states.items():
            item = self._list.item(row)
            if item is None or bool(item.data(CHECKED_ROLE)) == checked:
                continue
            item.setData(CHECKED_ROLE, checked)
            self._list.update(self._list.indexFromItem(item))
            changed = True
        self._refresh_header()
        if changed:
            self.selection_changed.emit(self.checked_files())

    def _on_checkbox_clicked(self, row: int, shift: bool) -> None:
        item = self._list.item(row)
        if item is None:
            return
        checked = not bool(item.data(CHECKED_ROLE))
        rows = [row]
        if shift and self._last_toggled_row is not None:
            lo, hi = sorted((self._last_toggled_row, row))
            rows = list(range(lo, hi + 1))
        self._last_toggled_row = row
        self._apply({r: checked for r in rows})

    def _on_select_toggle(self) -> None:
        if self._all_checked():
            self.clear_checks()
        else:
            self.check_all()

    def _all_checked(self) -> bool:
        count = self._list.count()
        return count > 0 and len(self.checked_files()) == count

    # ---- header --------------------------------------------------------

    def _refresh_header(self) -> None:
        total = self._list.count()
        checked = len(self.checked_files())
        self._select_toggle.setVisible(total > 0)
        self._select_toggle.setText("Select none" if self._all_checked() else "Select all")
        # Reserve the widest of the two labels so the control does not resize
        # as it toggles, and never clips whichever word is longer.
        metrics = self._select_toggle.fontMetrics()
        widest = max(
            metrics.horizontalAdvance("Select none"),
            metrics.horizontalAdvance("Select all"),
        )
        self._select_toggle.setMinimumWidth(widest + 20)  # + stylesheet padding
        if checked:
            # "7 of 7", not "7 of 7 selected": the title, the count and the
            # toggle share a 260px sidebar, and the longer form clips the
            # toggle right when the user most needs it.
            self._count_label.setText(f"{checked} of {total}")
            self._count_label.setStyleSheet(f"color: {Color.ACCENT};")
        else:
            self._count_label.setText(
                f"{total} file{'' if total == 1 else 's'}" if total else ""
            )
            self._count_label.setStyleSheet("")

    def _show_placeholder(self, title: str, hint: str) -> None:
        hint_html = (
            f'<div style="color:{Color.TEXT_TERTIARY};font-size:11px;margin-top:4px">{hint}</div>'
            if hint
            else ""
        )
        self._placeholder.setText(
            f'<div style="color:{Color.TEXT_SECONDARY};font-size:12px">{title}</div>{hint_html}'
        )
        self._stack.setCurrentWidget(self._placeholder)

    # ---- current file --------------------------------------------------

    def _on_current_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        self._emit(current)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self._emit(item)

    def _emit(self, item: QListWidgetItem | None) -> None:
        # Both signals fire for a single mouse selection; the key guard keeps
        # that to one activation.
        if item is None:
            return
        media_file: MediaFile | None = item.data(Qt.UserRole)
        if media_file is None or media_file.key == self._emitted_key:
            return
        self._emitted_key = media_file.key
        self.file_activated.emit(media_file)
