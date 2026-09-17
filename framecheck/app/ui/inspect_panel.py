"""Inspector panel: the parsed MediaInfo rendered as dense key/value rows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.media_info import MediaInfo
from ..models.media_time import format_seconds
from ..utils.paths import format_size, shorten_path
from .theme import Color, Metrics

_KEY_WIDTH = 112
_ROW_HEIGHT = 18
_DASH = "--"


def _clear(layout: QLayout) -> None:
    """Remove and destroy every widget in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _mbps(bitrate_bps: int | None) -> str | None:
    return f"{bitrate_bps / 1_000_000:.2f} Mb/s" if bitrate_bps else None


class InspectPanel(QWidget):
    """Right-hand inspector. States: empty, loading, error, populated."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER, Metrics.GUTTER)
        outer.setSpacing(Metrics.GUTTER_SM)

        # No in-panel header: the inspector tab bar already names this panel,
        # and repeating it wastes a row and reads as a duplicate.
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_empty_page())
        self._stack.addWidget(self._build_content_page())

        self.show_empty()

    # -- construction ----------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_XS)
        layout.addStretch(1)

        message = QLabel("No file loaded")
        message.setObjectName("MetaValueMuted")
        message.setAlignment(Qt.AlignCenter)
        layout.addWidget(message)

        hint = QLabel("Open a file or drop one here.")
        hint.setObjectName("MetaKey")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)
        return page

    def _build_content_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("Inspector")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, Metrics.GUTTER_SM, 0)
        column.setSpacing(Metrics.GUTTER)

        self._file_rows = self._add_section(column, "FILE")
        self._video_rows = self._add_section(column, "VIDEO")
        self._audio_rows = self._add_section(column, "AUDIO")
        column.addStretch(1)

        scroll.setWidget(body)
        return scroll

    def _add_section(self, column: QVBoxLayout, title: str) -> QVBoxLayout:
        """Heading + hairline + an empty rows layout the states refill."""
        label = QLabel(title)
        label.setObjectName("SectionLabel")
        column.addWidget(label)

        separator = QFrame()
        separator.setObjectName("Separator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedHeight(1)
        column.addWidget(separator)

        rows = QVBoxLayout()
        rows.setContentsMargins(0, Metrics.GUTTER_XS, 0, 0)
        rows.setSpacing(Metrics.GUTTER_XS)
        column.addLayout(rows)
        return rows

    def _add_row(
        self,
        section_layout: QVBoxLayout,
        key: str,
        value: str | None,
        mono: bool = False,
        color: str | None = None,
    ) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        key_label = QLabel(key)
        key_label.setObjectName("MetaKey")
        key_label.setFixedWidth(_KEY_WIDTH)
        key_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(key_label)

        missing = value is None or value == ""
        value_label = QLabel(_DASH if missing else value)
        if missing:
            value_label.setObjectName("MetaValueMuted")
        else:
            value_label.setObjectName("MetaValueMono" if mono else "MetaValue")
        value_label.setWordWrap(True)
        value_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        if color is not None and not missing:
            value_label.setStyleSheet(f"color: {color};")
        layout.addWidget(value_label, 1)

        row.setMinimumHeight(_ROW_HEIGHT)
        section_layout.addWidget(row)

    def _reset(self) -> None:
        for rows in (self._file_rows, self._video_rows, self._audio_rows):
            _clear(rows)
        self._stack.setCurrentIndex(1)

    # -- states ----------------------------------------------------------

    def show_empty(self) -> None:
        self._stack.setCurrentIndex(0)

    def show_loading(self, path: Path) -> None:
        self._reset()
        self._add_row(self._file_rows, "Name", path.name)
        for key in ("Location", "Size", "Container", "Duration", "Frames", "Overall bitrate"):
            self._add_row(self._file_rows, key, "Inspecting…")

    def show_error(self, path: Path, message: str) -> None:
        self._reset()
        self._add_row(self._file_rows, "Name", path.name)
        self._add_row(self._file_rows, "Error", message or "Unknown error", color=Color.FAIL)

    def show_info(self, info: MediaInfo) -> None:
        self._reset()
        self._fill_file(info)
        self._fill_video(info)
        self._fill_audio(info)

    # -- sections --------------------------------------------------------

    def _fill_file(self, info: MediaInfo) -> None:
        rows = self._file_rows
        self._add_row(rows, "Name", info.path.name)

        location = str(info.path.parent)
        self._add_row(rows, "Location", shorten_path(location, 44))
        # Tooltip carries the full path the elided label drops.
        rows.itemAt(rows.count() - 1).widget().setToolTip(str(info.path))

        # format_size returns "--" itself; pass None so the row picks the muted style.
        size = format_size(info.size_bytes) if info.size_bytes else None
        self._add_row(rows, "Size", size, mono=True)

        self._add_row(rows, "Container", info.container_format)
        if info.container_long_name:
            rows.itemAt(rows.count() - 1).widget().setToolTip(info.container_long_name)

        if info.duration is not None:
            duration = info.duration.to_clock()
        elif info.duration_seconds is not None:
            duration = format_seconds(info.duration_seconds)
        else:
            duration = None
        self._add_row(rows, "Duration", duration, mono=True)

        frames = info.frame_count
        self._add_row(rows, "Frames", f"{frames:,}" if frames else None, mono=True)
        self._add_row(rows, "Overall bitrate", _mbps(info.bitrate_bps), mono=True)

    def _fill_video(self, info: MediaInfo) -> None:
        rows = self._video_rows
        video = info.video
        if video is None:
            self._add_row(rows, "Stream", "No video stream", color=Color.WARNING)
            return

        codec = video.codec.upper() if video.codec else None
        if codec and video.profile:
            codec = f"{codec} ({video.profile})"
        self._add_row(rows, "Codec", codec)
        if video.codec_long:
            rows.itemAt(rows.count() - 1).widget().setToolTip(video.codec_long)

        level = video.level
        if level is None or level < 0:
            # Codecs without a level concept (ProRes, DNx) report -99.
            level_text = None
        elif level >= 10:
            # ffprobe reports H.264/HEVC levels scaled by ten: 40 -> 4.0.
            level_text = f"{level / 10:.1f}"
        else:
            level_text = str(level)
        self._add_row(rows, "Level", level_text, mono=True)

        self._add_row(rows, "Resolution", video.resolution, mono=True)
        self._add_row(
            rows,
            "Display aspect",
            video.display_aspect_ratio,
            mono=True,
        )
        self._add_row(rows, "Sample aspect", video.sample_aspect_ratio, mono=True)
        self._add_row(rows, "Pixel format", video.pixel_format, mono=True)

        rate = video.effective_frame_rate
        self._add_row(rows, "Frame rate", f"{rate.label()} fps" if rate else None, mono=True)

        vfr = video.likely_variable_frame_rate
        self._add_row(
            rows,
            "Rate type",
            "VFR?" if vfr else "CFR",
            color=Color.WARNING if vfr else Color.TEXT,
        )

        order = video.field_order
        progressive = order is None or order == "progressive"
        self._add_row(
            rows,
            "Scan type",
            "Progressive" if progressive else order,
            color=None if progressive else Color.WARNING,
        )

        self._add_row(rows, "Bitrate", _mbps(video.bitrate_bps), mono=True)

        colour_fields = (
            ("Colour space", video.color_space),
            ("Colour range", video.color_range),
            ("Transfer", video.color_transfer),
            ("Primaries", video.color_primaries),
        )
        if any(value for _, value in colour_fields):
            for key, value in colour_fields:
                self._add_row(rows, key, value, mono=True)

    def _fill_audio(self, info: MediaInfo) -> None:
        rows = self._audio_rows
        if not info.has_audio:
            # A delivery master with no audio is nearly always a mistake.
            self._add_row(rows, "Stream", "No audio stream", color=Color.WARNING)
            return

        audio = info.audio
        assert audio is not None  # has_audio guarantees this
        codec = audio.codec.upper() if audio.codec else None
        if codec and audio.profile:
            codec = f"{codec} ({audio.profile})"
        self._add_row(rows, "Codec", codec)

        sample_rate = audio.sample_rate_hz
        # Thin space groups the digits without reading as a thousands comma.
        rate_text = f"{sample_rate:,}".replace(",", " ") + " Hz" if sample_rate else None
        self._add_row(rows, "Sample rate", rate_text, mono=True)

        if audio.channels:
            channels = str(audio.channels)
            if audio.channel_layout:
                channels = f"{channels} ({audio.channel_layout})"
        else:
            channels = audio.channel_layout
        self._add_row(rows, "Channels", channels)

        kbps = audio.bitrate_kbps
        self._add_row(rows, "Bitrate", f"{kbps} kb/s" if kbps else None, mono=True)
        self._add_row(rows, "Sample format", audio.sample_format, mono=True)
