"""The application window: top bar, sidebar, player, trim, inspector.

The only module that wires the panels together. Panels stay ignorant of each
other so they can be tested and replaced independently.

Async discipline: every ffprobe, loudness analysis, folder scan and export runs
on the shared thread pool. Results carry a generation token; anything stamped
with an old generation is dropped, so a slow job for a file the user has since
left cannot overwrite the panel they are now looking at.

The export job is rebuilt from scratch whenever anything it depends on changes
(selection, trim, loudness, profile, filename, destination). Rebuilding is
cheap and pure, and it guarantees the CONFORM panel and the FFmpeg command are
always derived from the same object.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QThreadPool, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..media.conform import build_job
from ..models.batch import BatchItem, BatchPlan
from ..models.export_job import ExportJob, ExportResult, ExportState, LoudnessResult
from ..models.media_file import MediaFile, ProbeState, is_supported_media, media_dialog_filter
from ..models.media_info import MediaInfo
from ..models.profile import Profile
from ..models.trim import TrimRange
from ..profiles import compatibility
from ..profiles.loader import ProfileLoader
from ..profiles.validator import validate
from ..services import logging_service
from ..services.binaries import missing_binaries
from ..services.settings import Settings
from ..utils.paths import OutputDestination, shorten_path
from ..utils.win_chrome import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTCAPTION,
    HTCLIENT,
    HTLEFT,
    HTMAXBUTTON,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    WM_NCHITTEST,
    WM_NCLBUTTONDOWN,
    WM_NCLBUTTONUP,
    apply_dark_titlebar,
    extend_frame_for_shadow,
    resize_border_thickness,
)
from ..workers.batch_runner import BatchRunner
from ..workers.folder_scan_worker import FolderScanWorker
from ..workers.loudness_worker import LoudnessWorker
from ..workers.probe_worker import ProbeWorker
from ..workers.transcode_worker import TranscodeWorker
from .batch_panel import BatchPanel
from .conform_panel import ConformPanel
from .export_panel import ExportPanel
from .inspect_panel import InspectPanel
from .player_widget import PlayerWidget
from .source_browser import SourceBrowser
from .theme import Color, Metrics
from .title_bar import TitleBar
from .trim_panel import TrimPanel
from .validation_panel import ValidationPanel

log = logging.getLogger(__name__)

APP_TITLE = "Framecheck"
TAGLINE = "Video, to spec."

TAB_INSPECT, TAB_CONFORM, TAB_VALIDATE, TAB_EXPORT = range(4)

# Measure at most this much audio for the on-screen reading. Ads run well under
# it and are measured whole; long-form gets a labelled estimate instead of a
# multi-minute wait. Export always re-measures in full before normalising.
LOUDNESS_SAMPLE_SECONDS = 180.0


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.pool = QThreadPool.globalInstance()

        self._files: list[MediaFile] = []
        self._current: MediaFile | None = None
        self._output = settings.output_destination
        self._generation = 0
        self._scan_worker: FolderScanWorker | None = None
        self._export_worker: TranscodeWorker | None = None

        # Per-file state, keyed by MediaFile.key so it survives reselection.
        self._loudness: dict[str, LoudnessResult] = {}
        self._loudness_running: set[str] = set()
        # Strong references to in-flight workers. See _run_worker.
        self._active_workers: set = set()
        self._trims: dict[str, TrimRange] = {}

        self._profiles: list[Profile] = []
        self._profile_errors: list[str] = []
        self._selected_ids: list[str] = []
        self._normalize = False
        self._filename_override: str | None = None
        self._job: ExportJob | None = None
        self._exporting = False

        # Batch state: the files ticked in the sidebar, and the plan built from
        # them. Independent of `_current`, which is the one file in the player.
        self._checked: list[MediaFile] = []
        self._plan: BatchPlan | None = None
        self._batch_running = False

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1180, 720)
        self.setAcceptDrops(True)
        # Frameless so the menus can share the caption strip. Every native
        # caption behaviour is handed back to Windows in nativeEvent().
        if sys.platform == "win32":
            self.setWindowFlag(Qt.FramelessWindowHint, True)

        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self._load_profiles()
        self._restore_window_state()
        self._refresh_output_bar()

        QTimer.singleShot(0, self._attach_player)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The caption only exists once the window is shown, and re-applying on
        # every show is harmless -- Windows resets it when the theme changes.
        hwnd = int(self.winId())
        apply_dark_titlebar(
            hwnd, caption=Color.SURFACE, text=Color.TEXT, border=Color.SEPARATOR
        )
        # A frameless window loses the drop shadow; DWM gives it back.
        extend_frame_for_shadow(hwnd)
        self.title_bar.set_maximized(self.isMaximized())

    # -- construction -----------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_title_bar())
        layout.addWidget(self._build_top_bar())

        self.splitter = QSplitter(Qt.Horizontal, central)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)

        self.source_browser = SourceBrowser(self.splitter)
        self.source_browser.file_activated.connect(self._on_file_activated)
        self.source_browser.selection_changed.connect(self._on_batch_selection_changed)
        self.source_browser.hide()

        self.splitter.addWidget(self.source_browser)
        self.splitter.addWidget(self._build_centre())
        self.splitter.addWidget(self._build_inspector())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([Metrics.SIDEBAR_WIDTH, 720, Metrics.INSPECTOR_WIDTH])

        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        self._set_status("Ready")

    def _build_centre(self) -> QWidget:
        centre = QWidget(self.splitter)
        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.player = PlayerWidget(centre)
        self.player.error.connect(self._on_player_error)
        self.player.trim_changed.connect(self._on_trim_changed_from_player)

        self.trim_panel = TrimPanel(centre)
        self.trim_panel.trim_changed.connect(self._on_trim_changed_from_panel)
        self.trim_panel.set_in_requested.connect(self.player.set_in)
        self.trim_panel.set_out_requested.connect(self.player.set_out)
        self.trim_panel.preview_cut_requested.connect(self.player.preview_cut)
        self.trim_panel.loop_cut_toggled.connect(self.player.set_loop_cut)

        layout.addWidget(self.player, 1)
        layout.addWidget(self.trim_panel)
        layout.addWidget(self._build_output_bar())
        return centre

    def _build_inspector(self) -> QWidget:
        panel = QWidget(self.splitter)
        panel.setObjectName("Inspector")
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # A segmented control rather than QTabWidget: these tabs drive a plain
        # stack, and QTabWidget insists on drawing a frame around the pane.
        tabs = QWidget(panel)
        tabs.setObjectName("InspectorTabs")
        tab_layout = QHBoxLayout(tabs)
        tab_layout.setContentsMargins(Metrics.GUTTER, 0, Metrics.GUTTER, 0)
        tab_layout.setSpacing(Metrics.GUTTER)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for index, title in enumerate(("INSPECT", "CONFORM", "VALIDATE", "EXPORT")):
            button = QPushButton(title, tabs)
            button.setObjectName("InspectorTab")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            self.tab_group.addButton(button, index)
            tab_layout.addWidget(button)
        tab_layout.addStretch(1)
        self.tab_group.idClicked.connect(self._show_tab)

        self.inspector_stack = QStackedWidget(panel)
        self.inspect_panel = InspectPanel(self.inspector_stack)
        self.conform_panel = ConformPanel(self.inspector_stack)
        self.validation_panel = ValidationPanel(self.inspector_stack)

        # The EXPORT tab holds two panels and shows whichever matches what the
        # user has selected: one file, or several ticked for batch.
        self.export_stack = QStackedWidget(self.inspector_stack)
        self.export_panel = ExportPanel(self.export_stack)
        self.batch_panel = BatchPanel(self.export_stack)
        self.export_stack.addWidget(self.export_panel)
        self.export_stack.addWidget(self.batch_panel)

        for widget in (
            self.inspect_panel,
            self.conform_panel,
            self.validation_panel,
            self.export_stack,
        ):
            self.inspector_stack.addWidget(widget)

        self.batch_panel.export_requested.connect(self.start_batch_export)
        self.batch_panel.cancel_requested.connect(self.cancel_batch_export)
        self.batch_panel.output_folder_change_requested.connect(self.choose_output_folder)
        self.batch_panel.open_folder_requested.connect(self.reveal_output_folder)

        self.batch_runner = BatchRunner(self.pool, self)
        self.batch_runner.item_started.connect(self._on_batch_item_changed)
        self.batch_runner.item_progress.connect(self._on_batch_item_progress)
        self.batch_runner.item_finished.connect(self._on_batch_item_changed)
        self.batch_runner.finished.connect(self._on_batch_finished)

        self.conform_panel.normalize_toggled.connect(self._on_normalize_toggled)
        self.validation_panel.profile_selection_changed.connect(self._on_profiles_selected)
        self.validation_panel.fix_requested.connect(self._on_fix_requested)
        self.export_panel.export_requested.connect(self.start_export)
        self.export_panel.cancel_requested.connect(self.cancel_export)
        self.export_panel.output_folder_change_requested.connect(self.choose_output_folder)
        self.export_panel.filename_edited.connect(self._on_filename_edited)
        self.export_panel.open_folder_requested.connect(self.reveal_output_folder)
        self.export_panel.open_file_requested.connect(self._open_output_file)

        layout.addWidget(tabs)
        layout.addWidget(self.inspector_stack, 1)
        self._show_tab(TAB_INSPECT)
        return panel

    def _build_title_bar(self) -> QWidget:
        """The caption strip: icon, title, menus and window buttons."""
        from ..main import application_icon

        self.title_bar = TitleBar(application_icon(), self)
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self.toggle_maximized)
        self.title_bar.close_requested.connect(self.close)
        return self.title_bar

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.set_maximized(self.isMaximized())
            # A maximised frameless window would otherwise hang the resize
            # border off every edge of the screen.
            margin = resize_border_thickness() if self.isMaximized() else 0
            self.centralWidget().setContentsMargins(margin, margin, margin, margin)

    def nativeEvent(self, event_type, message):  # noqa: ANN001 - Qt signature
        """Answer WM_NCHITTEST so Windows drives the caption behaviours.

        Returning HTCAPTION over the bar gives drag, double-click-to-maximise
        and Aero Snap; the edge codes give native resizing; HTMAXBUTTON is what
        makes the Windows 11 Snap Layouts flyout appear on hover.
        """
        if sys.platform != "win32" or event_type not in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            return super().nativeEvent(event_type, message)

        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents

        if msg.message == WM_NCHITTEST:
            # lParam packs signed screen coordinates.
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            local = self.mapFromGlobal(QPoint(x, y) / self.devicePixelRatioF())

            if not self.isMaximized():
                border = max(4, int(resize_border_thickness() / self.devicePixelRatioF()))
                width, height = self.width(), self.height()
                left, right = local.x() < border, local.x() > width - border
                top, bottom = local.y() < border, local.y() > height - border
                corner = {
                    (True, False, True, False): HTTOPLEFT,
                    (False, True, True, False): HTTOPRIGHT,
                    (True, False, False, True): HTBOTTOMLEFT,
                    (False, True, False, True): HTBOTTOMRIGHT,
                }.get((left, right, top, bottom))
                if corner is not None:
                    return True, corner
                for hit, code in ((left, HTLEFT), (right, HTRIGHT), (top, HTTOP), (bottom, HTBOTTOM)):
                    if hit:
                        return True, code

            bar_pos = self.title_bar.mapFrom(self, local)
            if self.title_bar.rect().contains(bar_pos):
                if self.title_bar.maximize_button_geometry().contains(bar_pos):
                    return True, HTMAXBUTTON
                if not self.title_bar.is_interactive_at(bar_pos):
                    return True, HTCAPTION
            return True, HTCLIENT

        if msg.message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONUP) and msg.wParam == HTMAXBUTTON:
            # Windows sends these to the maximise button instead of Qt once we
            # claim HTMAXBUTTON, so the click has to be handled here.
            if msg.message == WM_NCLBUTTONUP:
                self.toggle_maximized()
            return True, 0

        return super().nativeEvent(event_type, message)

    def _build_top_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("TopBar")
        bar.setFixedHeight(Metrics.TOPBAR_HEIGHT)

        layout = QHBoxLayout(bar)
        # Small left margin: menu items carry their own padding, so a full
        # gutter here would push "File" noticeably inboard of everything below.
        layout.setContentsMargins(Metrics.GUTTER_XS, 0, Metrics.GUTTER, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        # The menu bar lives in the title bar, not here. QMainWindow is never
        # asked for its own menu bar, so it reserves no space for one.
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName("AppMenuBar")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.title_bar.set_menu_bar(self.menu_bar)

        self.current_file_label = QLabel("", bar)
        self.current_file_label.setObjectName("CurrentFileLabel")
        layout.addWidget(self.current_file_label, 1)

        open_file = QPushButton("Open File", bar)
        open_file.clicked.connect(self.open_file_dialog)
        open_folder = QPushButton("Open Folder", bar)
        open_folder.clicked.connect(self.open_folder_dialog)
        layout.addWidget(open_file)
        layout.addWidget(open_folder)
        return bar

    def _build_output_bar(self) -> QWidget:
        """The export destination, always visible -- never buried in settings."""
        bar = QWidget(self)
        bar.setObjectName("OutputBar")
        bar.setFixedHeight(34)
        bar.setStyleSheet(
            f"#OutputBar {{ background-color: {Color.SURFACE}; "
            f"border-top: 1px solid {Color.SEPARATOR}; }}"
        )

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Metrics.GUTTER, 0, Metrics.GUTTER, 0)
        layout.setSpacing(Metrics.GUTTER_SM)

        caption = QLabel("OUTPUT FOLDER", bar)
        caption.setObjectName("SectionLabel")
        layout.addWidget(caption)

        self.output_label = QLabel("", bar)
        self.output_label.setObjectName("MetaValueMono")
        layout.addWidget(self.output_label, 1)

        change = QPushButton("Change", bar)
        change.setObjectName("Ghost")
        change.clicked.connect(self.choose_output_folder)
        layout.addWidget(change)

        self.same_as_source_button = QPushButton("Use Source Folder", bar)
        self.same_as_source_button.setObjectName("Ghost")
        self.same_as_source_button.clicked.connect(self.use_source_folder)
        layout.addWidget(self.same_as_source_button)
        return bar

    def _build_menus(self) -> None:
        menubar = self.menu_bar

        file_menu = menubar.addMenu("&File")
        self._add_action(file_menu, "Open File…", QKeySequence.Open, self.open_file_dialog)
        self._add_action(file_menu, "Open Folder…", QKeySequence("Ctrl+Shift+O"), self.open_folder_dialog)
        file_menu.addSeparator()
        self.recent_files_menu = file_menu.addMenu("Recent Files")
        self.recent_folders_menu = file_menu.addMenu("Recent Folders")
        file_menu.addSeparator()
        self._add_action(file_menu, "Choose Output Folder…", QKeySequence("Ctrl+Shift+E"), self.choose_output_folder)
        self._add_action(file_menu, "Open Output Folder", None, self.reveal_output_folder)
        file_menu.addSeparator()
        self._add_action(file_menu, "Conform && Export…", QKeySequence("Ctrl+E"), self.start_export)
        file_menu.addSeparator()
        self._add_action(file_menu, "Close File", QKeySequence("Ctrl+W"), self.close_current_file)
        self._add_action(file_menu, "Exit", QKeySequence("Alt+F4"), self.close)

        playback_menu = menubar.addMenu("&Playback")
        self._add_action(playback_menu, "Play / Pause", QKeySequence(Qt.Key_Space), self.player.toggle_play)
        self._add_action(playback_menu, "Previous Frame", QKeySequence(Qt.Key_Comma), lambda: self.player.step_frames(-1))
        self._add_action(playback_menu, "Next Frame", QKeySequence(Qt.Key_Period), lambda: self.player.step_frames(1))
        playback_menu.addSeparator()
        self._add_action(playback_menu, "Back 1 Second", QKeySequence(Qt.Key_Left), lambda: self.player.jump(-1.0))
        self._add_action(playback_menu, "Forward 1 Second", QKeySequence(Qt.Key_Right), lambda: self.player.jump(1.0))
        self._add_action(playback_menu, "Go to Start", QKeySequence(Qt.Key_Home), self.player.go_to_start)
        self._add_action(playback_menu, "Last 5 Seconds", QKeySequence(Qt.Key_End), self.player.go_to_tail)
        playback_menu.addSeparator()
        self._add_action(playback_menu, "Mute", QKeySequence("M"), self.player.toggle_mute)

        trim_menu = menubar.addMenu("&Trim")
        self._add_action(trim_menu, "Set In Point", QKeySequence("I"), self.player.set_in)
        self._add_action(trim_menu, "Set Out Point", QKeySequence("O"), self.player.set_out)
        trim_menu.addSeparator()
        self._add_action(trim_menu, "Preview Cut", QKeySequence("P"), self.player.preview_cut)
        self._add_action(trim_menu, "Loop Cut", QKeySequence("L"), self.player.toggle_loop_cut)
        trim_menu.addSeparator()
        self._add_action(trim_menu, "Reset Trim", QKeySequence("Ctrl+R"), self._reset_trim)

        view_menu = menubar.addMenu("&View")
        for index, (title, key) in enumerate(
            (("Inspect", "Ctrl+1"), ("Conform", "Ctrl+2"), ("Validate", "Ctrl+3"), ("Export", "Ctrl+4"))
        ):
            self._add_action(view_menu, title, QKeySequence(key), lambda _=False, i=index: self._show_tab(i))
        view_menu.addSeparator()
        self.recursive_action = QAction("Scan Folders Recursively", self)
        self.recursive_action.setCheckable(True)
        self.recursive_action.setChecked(self.settings.recursive_scan)
        self.recursive_action.toggled.connect(self._on_recursive_toggled)
        view_menu.addAction(self.recursive_action)

        help_menu = menubar.addMenu("&Help")
        self._add_action(help_menu, "Open Log", None, self.open_log)
        self._add_action(help_menu, "Technical Details…", None, self.show_technical_details)
        self._add_action(help_menu, "Reload Profiles", None, self._reload_profiles)
        help_menu.addSeparator()
        self._add_action(help_menu, f"About {APP_TITLE}", None, self.show_about)

        self._rebuild_recent_menus()

    def _add_action(self, menu, text: str, shortcut, handler) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(handler)
        menu.addAction(action)
        return action

    def _build_shortcuts(self) -> None:
        """Menu actions carry the shortcuts; this widens their context.

        Application context means the keys work wherever focus sits, which is
        what a scrub-step-mark workflow needs. Text fields are the exception
        and are handled by Qt itself -- a QLineEdit with focus consumes the
        character keys before the shortcut sees them.
        """
        for action in self.findChildren(QAction):
            action.setShortcutContext(Qt.ApplicationShortcut)

    def _run_worker(self, worker, *terminal_signals) -> None:
        """Start `worker` on the pool, holding it alive until it finishes.

        QThreadPool takes ownership of the C++ QRunnable, but nothing keeps the
        Python wrapper alive. Once it is collected its `signals` QObject goes
        with it, and results emitted afterwards are delivered to nothing -- the
        job runs to completion and the UI simply never hears about it.

        The race is won by whichever finishes first, so fast workers appear to
        work and slow ones silently vanish. Holding a reference until a
        terminal signal fires removes the race entirely.
        """
        self._active_workers.add(worker)

        def release(*_args, _worker=worker) -> None:
            self._active_workers.discard(_worker)

        for signal in terminal_signals:
            signal.connect(release)
        self.pool.start(worker)

    # -- profiles ---------------------------------------------------------

    def _load_profiles(self) -> None:
        loader = ProfileLoader()
        self._loader = loader
        self._profiles = loader.load_all()
        self._profile_errors = loader.errors

        remembered = self.settings.default_profile_id
        if remembered and any(p.id == remembered for p in self._profiles):
            self._selected_ids = [remembered]
        self.validation_panel.set_profiles(self._profiles, self._selected_ids)

        log.info("loaded %d delivery profiles", len(self._profiles))
        if self._profile_errors:
            for message in self._profile_errors:
                log.warning("profile error: %s", message)
            self._set_status(f"{len(self._profile_errors)} profile(s) could not be loaded — see log")

    def _reload_profiles(self) -> None:
        """Re-read specs/ without restarting -- profiles are user-editable."""
        self._load_profiles()
        self._revalidate()
        self._set_status(f"Reloaded {len(self._profiles)} profiles")

    @property
    def _primary_profile(self) -> Profile | None:
        for profile_id in self._selected_ids:
            profile = self._loader.get(profile_id)
            if profile is not None:
                return profile
        return None

    @property
    def _selected_profiles(self) -> list[Profile]:
        found = [self._loader.get(pid) for pid in self._selected_ids]
        return [p for p in found if p is not None]

    @Slot(list)
    def _on_profiles_selected(self, profile_ids: list) -> None:
        self._selected_ids = [str(pid) for pid in profile_ids]
        if self._selected_ids:
            self.settings.default_profile_id = self._selected_ids[0]
        # A new destination invalidates any filename the user has not edited.
        self._filename_override = None
        # A destination that specifies a loudness target is what makes the
        # analysis pass worth paying for; start it now rather than on load.
        if self._current is not None and self._current.info is not None:
            self._start_loudness(self._current, self._current.info)
        self._revalidate()
        self._rebuild_job()
        self._refresh_batch_panel()

    # -- window state -----------------------------------------------------

    def _restore_window_state(self) -> None:
        geometry = self.settings.window_geometry
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1440, 880)
        self.player.set_volume(self.settings.volume)
        self.player.set_muted(self.settings.muted)

    def closeEvent(self, event) -> None:
        self.settings.window_geometry = self.saveGeometry()
        self.settings.volume = self.player.volume
        self.settings.muted = self.player.is_muted
        self.settings.output_destination = self._output
        self.settings.sync()
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        if self._export_worker is not None:
            self._export_worker.cancel()
        self.player.shutdown()
        log.info("Framecheck closing")
        super().closeEvent(event)

    def _attach_player(self) -> None:
        if not self.player.attach_engine():
            missing = missing_binaries()
            if missing:
                self._set_status(f"Missing runtime binaries: {', '.join(missing)}")
        self._warn_about_missing_binaries()

    def _warn_about_missing_binaries(self) -> None:
        missing = missing_binaries()
        if not missing:
            return
        QMessageBox.warning(
            self,
            "Runtime binaries missing",
            "Framecheck could not find:\n\n"
            + "\n".join(f"  • {name}" for name in missing)
            + "\n\nRun this from the project folder to download them:\n"
            "    python tools/fetch_binaries.py",
        )

    def _show_tab(self, index: int) -> None:
        self.inspector_stack.setCurrentIndex(index)
        button = self.tab_group.button(index)
        if button is not None:
            button.setChecked(True)

    # -- opening ----------------------------------------------------------

    @Slot()
    def open_file_dialog(self) -> None:
        start_dir = self.settings.last_input_dir
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Open video file", str(start_dir) if start_dir else "", media_dialog_filter()
        )
        if path_str:
            self.open_path(Path(path_str))

    @Slot()
    def open_folder_dialog(self) -> None:
        start_dir = self.settings.last_input_dir
        folder = QFileDialog.getExistingDirectory(
            self, "Open folder", str(start_dir) if start_dir else ""
        )
        if folder:
            self.open_folder(Path(folder))

    def open_path(self, path: Path) -> None:
        path = Path(path)
        if path.is_dir():
            self.open_folder(path)
            return
        if not path.is_file():
            self._set_status(f"Not found: {path}")
            return

        self.settings.last_input_dir = path.parent
        self.settings.push_recent_file(path)
        self._rebuild_recent_menus()

        media_file = self._find_loaded(path)
        if media_file is None:
            media_file = MediaFile(path)
            self._files = [media_file]
            self.source_browser.set_files(self._files, path.parent)
            self.source_browser.hide()
        self._select(media_file)

    def open_folder(self, folder: Path) -> None:
        folder = Path(folder)
        if not folder.is_dir():
            self._set_status(f"Not a folder: {folder}")
            return

        self.settings.last_input_dir = folder
        self.settings.push_recent_folder(folder)
        self._rebuild_recent_menus()

        if self._scan_worker is not None:
            self._scan_worker.cancel()

        self.source_browser.show()
        self.source_browser.set_scanning(True)
        self._set_status(f"Scanning {folder}…")

        worker = FolderScanWorker(folder, self.settings.recursive_scan)
        worker.signals.finished.connect(self._on_folder_scanned)
        worker.signals.failed.connect(self._on_folder_scan_failed)
        self._scan_worker = worker
        self._run_worker(worker, worker.signals.finished, worker.signals.failed)

    @Slot(Path, list, bool)
    def _on_folder_scanned(self, folder: Path, paths: list, hit_limit: bool) -> None:
        self._scan_worker = None
        self._files = [MediaFile(p) for p in paths]
        self.source_browser.set_scanning(False)
        self.source_browser.set_files(self._files, folder)

        if not self._files:
            self._set_status(f"No supported video files in {folder}")
            self.close_current_file()
            return

        message = f"{len(self._files)} file{'s' if len(self._files) != 1 else ''} in {folder.name}"
        if hit_limit:
            message += " (scan limit reached — showing partial results)"
        self._set_status(message)

        self._select(self._files[0])
        for media_file in self._files[1:]:
            self._start_probe(media_file, load_into_player=False)

    @Slot(Path, str)
    def _on_folder_scan_failed(self, folder: Path, message: str) -> None:
        self._scan_worker = None
        self.source_browser.set_scanning(False)
        self._set_status(message)
        QMessageBox.warning(self, "Could not open folder", f"{folder}\n\n{message}")

    # -- selection --------------------------------------------------------

    @Slot(object)
    def _on_file_activated(self, media_file: MediaFile) -> None:
        if media_file is not None and media_file is not self._current:
            self._select(media_file)

    def _select(self, media_file: MediaFile) -> None:
        self._generation += 1
        self._current = media_file
        self._filename_override = None

        self.current_file_label.setText(shorten_path(media_file.path, 72))
        self.current_file_label.setToolTip(str(media_file.path))
        self.setWindowTitle(f"{media_file.name} — {APP_TITLE}")
        self.source_browser.select_path(media_file.path)
        self.export_panel.set_result(None)
        self._refresh_output_bar()

        if not media_file.exists:
            media_file.state = ProbeState.ERROR
            media_file.error = "File is no longer available"
            self.inspect_panel.show_error(media_file.path, media_file.error)
            self.source_browser.update_file(media_file)
            self.player.clear()
            self._clear_downstream_panels()
            return

        rate = media_file.info.frame_rate if media_file.info else None
        self.player.load(media_file.path, rate)

        if media_file.state is ProbeState.READY and media_file.info is not None:
            self._apply_info(media_file, media_file.info)
        else:
            self.inspect_panel.show_loading(media_file.path)
            self._clear_downstream_panels()
            self._start_probe(media_file, load_into_player=True)

    def _clear_downstream_panels(self) -> None:
        self.conform_panel.show_empty()
        self.export_panel.show_empty()
        self.validation_panel.show_pending() if self._selected_ids else self.validation_panel.show_empty()
        self._job = None

    def _start_probe(self, media_file: MediaFile, load_into_player: bool) -> None:
        if media_file.state is ProbeState.PROBING:
            return
        media_file.state = ProbeState.PROBING
        self.source_browser.update_file(media_file)

        generation = self._generation if load_into_player else -1
        worker = ProbeWorker(media_file.path, generation)
        worker.signals.finished.connect(self._on_probe_finished)
        worker.signals.failed.connect(self._on_probe_failed)
        self._run_worker(worker, worker.signals.finished, worker.signals.failed)

    @Slot(Path, object, int)
    def _on_probe_finished(self, path: Path, info: MediaInfo, generation: int) -> None:
        media_file = self._find_loaded(path)
        if media_file is None:
            return
        media_file.state = ProbeState.READY
        media_file.info = info
        media_file.error = None
        self.source_browser.update_file(media_file)

        # A ticked file becoming readable changes what the batch can encode.
        if any(f.key == media_file.key for f in self._checked):
            self._refresh_batch_panel()

        if media_file is self._current and generation == self._generation:
            self._apply_info(media_file, info)

    def _apply_info(self, media_file: MediaFile, info: MediaInfo) -> None:
        """Everything that follows from having probe data for the current file."""
        self.inspect_panel.show_info(info)

        rate = info.frame_rate
        if rate is not None:
            self.player.set_frame_rate(rate)
            duration = info.duration
            self.trim_panel.set_source(duration, rate)
            trim = self._trims.get(media_file.key)
            if trim is not None:
                self.trim_panel.set_trim(trim)
                self.player.set_trim(trim)
        if not info.has_video:
            self.player.show_message("No video stream — audio only")

        self._start_loudness(media_file, info)
        self._revalidate()
        self._rebuild_job()

    @Slot(Path, str, int)
    def _on_probe_failed(self, path: Path, message: str, generation: int) -> None:
        media_file = self._find_loaded(path)
        if media_file is None:
            return
        media_file.state = ProbeState.ERROR
        media_file.error = message
        self.source_browser.update_file(media_file)

        if media_file is self._current and generation == self._generation:
            self.inspect_panel.show_error(path, message)
            self._clear_downstream_panels()
            self._set_status(f"Could not inspect {path.name}: {message}")

    def close_current_file(self) -> None:
        self._generation += 1
        self._current = None
        self._job = None
        self.player.clear()
        self.inspect_panel.show_empty()
        self.conform_panel.show_empty()
        self.export_panel.show_empty()
        self.validation_panel.show_empty()
        self.trim_panel.set_source(None, self.player.engine.rate)
        self.current_file_label.setText("")
        self.setWindowTitle(APP_TITLE)
        self._refresh_output_bar()

    def _find_loaded(self, path: Path) -> MediaFile | None:
        key = MediaFile(path).key
        for media_file in self._files:
            if media_file.key == key:
                return media_file
        return None

    # -- loudness ---------------------------------------------------------

    def _start_loudness(self, media_file: MediaFile, info: MediaInfo) -> None:
        """Measure integrated loudness in the background.

        Never blocks playback: the file is already on screen and audible by the
        time this runs, and the panels show 'Measuring...' until it lands.
        """
        target = self._target_loudness()
        if not info.has_audio:
            self.conform_panel.set_loudness(None, target)
            return
        cached = self._loudness.get(media_file.key)
        if cached is not None:
            self.conform_panel.set_loudness(cached, target)
            return
        if target is None:
            # No destination cares about loudness yet. Reading a multi-gigabyte
            # master to measure something nothing is asking for is the single
            # most expensive thing this app can do -- especially on a
            # cloud-synced folder, where it forces a full file hydration.
            # Selecting a profile with a loudness target starts it.
            return
        if media_file.key in self._loudness_running:
            return
        self._loudness_running.add(media_file.key)

        duration = float(info.duration_seconds) if info.duration_seconds else None
        worker = LoudnessWorker(
            media_file.path,
            None,
            generation=self._generation,
            max_seconds=LOUDNESS_SAMPLE_SECONDS,
            source_seconds=duration,
        )
        worker.signals.finished.connect(self._on_loudness_finished)
        worker.signals.failed.connect(self._on_loudness_failed)
        self._run_worker(worker, worker.signals.finished, worker.signals.failed)

    @Slot(Path, object, int)
    def _on_loudness_finished(self, path: Path, result: LoudnessResult, generation: int) -> None:
        media_file = self._find_loaded(path)
        if media_file is not None:
            self._loudness[media_file.key] = result
            self._loudness_running.discard(media_file.key)
        if self._current is media_file and generation == self._generation:
            self.conform_panel.set_loudness(result, self._target_loudness())
            self._revalidate()
            self._rebuild_job()
            log.info("loudness for %s: %.1f LUFS", path.name, result.integrated_lufs)

    @Slot(Path, str, int)
    def _on_loudness_failed(self, path: Path, message: str, generation: int) -> None:
        media_file = self._find_loaded(path)
        if media_file is not None:
            self._loudness_running.discard(media_file.key)
        log.warning("loudness analysis failed for %s: %s", path, message)
        if self._current is not None and self._current.path == path:
            self.conform_panel.set_loudness(None, self._target_loudness())

    def _target_loudness(self) -> float | None:
        profile = self._primary_profile
        return profile.target.audio.loudness_lkfs if profile else None

    def _current_loudness(self) -> LoudnessResult | None:
        return self._loudness.get(self._current.key) if self._current else None

    # -- trimming ---------------------------------------------------------

    @Slot(object)
    def _on_trim_changed_from_player(self, trim: TrimRange | None) -> None:
        self.trim_panel.set_trim(trim)
        self._store_trim(trim)

    @Slot(object)
    def _on_trim_changed_from_panel(self, trim: TrimRange | None) -> None:
        self.player.set_trim(trim)
        self._store_trim(trim)

    def _store_trim(self, trim: TrimRange | None) -> None:
        if self._current is None:
            return
        if trim is None:
            self._trims.pop(self._current.key, None)
        else:
            self._trims[self._current.key] = trim
        self._rebuild_job()

    def _reset_trim(self) -> None:
        self.trim_panel.reset()

    def _current_trim(self) -> TrimRange | None:
        if self._current is None:
            return None
        trim = self._trims.get(self._current.key)
        if trim is None:
            return None
        info = self._current.info
        duration = info.duration if info else None
        # A range covering the whole file is not a trim; sending it to FFmpeg
        # would add a needless seek and risk a one-frame difference.
        return None if duration is not None and trim.is_full(duration) else trim

    # -- validation -------------------------------------------------------

    def _revalidate(self) -> None:
        profiles = self._selected_profiles
        if not profiles:
            self.validation_panel.show_empty()
            self.validation_panel.set_compatibility(None)
            return

        report = compatibility.analyze(profiles)
        self.validation_panel.set_compatibility(report)

        info = self._current.info if self._current else None
        if info is None:
            self.validation_panel.show_pending()
            return

        loudness = self._current_loudness()
        reports = [validate(info, profile, loudness) for profile in profiles]
        self.validation_panel.set_reports(reports)

        worst = max((r.status for r in reports), key=lambda s: s.rank)
        log.info(
            "validated %s against %s: %s",
            self._current.name if self._current else "?",
            ", ".join(p.id for p in profiles),
            worst.value,
        )

    @Slot()
    def _on_fix_requested(self) -> None:
        """A 'Fix on export' affordance was clicked.

        Everything fixable is already applied by the conform step, so this
        turns on loudness normalisation (the one fix that is opt-in, because it
        alters the mix) and shows the user what will change.
        """
        if self._target_loudness() is not None:
            self._normalize = True
        self._rebuild_job()
        self._show_tab(TAB_CONFORM)

    @Slot(bool)
    def _on_normalize_toggled(self, enabled: bool) -> None:
        self._normalize = bool(enabled)
        self._rebuild_job()

    # -- the export job ---------------------------------------------------

    def _rebuild_job(self) -> None:
        """Recompute the pending export from current state. Cheap and pure."""
        info = self._current.info if self._current else None
        profile = self._primary_profile
        if info is None or profile is None:
            self._job = None
            self.conform_panel.show_empty()
            self.export_panel.show_empty()
            return

        others = tuple(p for p in self._selected_profiles if p.id != profile.id)
        # Only advertise profiles a single output can actually satisfy.
        report = compatibility.analyze([profile, *others])
        if not report.single_master_possible:
            others = ()

        job = build_job(
            info,
            profile,
            destination=self._output,
            trim=self._current_trim(),
            loudness=self._current_loudness(),
            normalize=self._normalize,
            overwrite=False,
            filename=self._filename_override,
            additional_profiles=others,
        )
        self._job = job

        self.conform_panel.set_job(job)
        self.conform_panel.set_actions(list(job.actions))
        self.conform_panel.set_loudness(self._current_loudness(), self._target_loudness())

        self.export_panel.set_job(job)
        self.export_panel.set_output(
            job.output_path.parent, job.output_path.name, job.output_path.exists()
        )

    @Slot(str)
    def _on_filename_edited(self, filename: str) -> None:
        self._filename_override = filename or None
        self._rebuild_job()

    # -- export -----------------------------------------------------------

    @Slot()
    def start_export(self) -> None:
        if self._exporting:
            return
        if self._job is None:
            self._set_status("Select a destination profile before exporting")
            self._show_tab(TAB_VALIDATE)
            return

        job = self._job
        if job.output_path.exists():
            answer = QMessageBox.warning(
                self,
                "File already exists",
                f"{job.output_path.name} already exists in\n{job.output_path.parent}\n\n"
                "Overwrite it?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
            from dataclasses import replace

            job = replace(job, overwrite=True)

        self._exporting = True
        self.export_panel.set_result(None)
        self.export_panel.set_state(ExportState.ENCODING, 0.0, "Starting…")
        self._show_tab(TAB_EXPORT)
        self._set_status(f"Exporting {job.output_path.name}…")
        log.info("export start: %s -> %s", job.source_path, job.output_path)

        worker = TranscodeWorker(job, self._generation)
        worker.signals.progress.connect(self._on_export_progress)
        worker.signals.finished.connect(self._on_export_finished)
        worker.signals.failed.connect(self._on_export_failed)
        self._export_worker = worker
        self._run_worker(worker, worker.signals.finished, worker.signals.failed)

    @Slot()
    def cancel_export(self) -> None:
        if self._export_worker is not None:
            self._export_worker.cancel()
            self._set_status("Cancelling export…")

    @Slot(float, str)
    def _on_export_progress(self, fraction: float, detail: str) -> None:
        state = ExportState.VERIFYING if fraction >= 1.0 else ExportState.ENCODING
        self.export_panel.set_state(state, fraction, detail)

    @Slot(object)
    def _on_export_finished(self, result: ExportResult) -> None:
        self._exporting = False
        self._export_worker = None
        self.export_panel.set_state(result.state, 1.0, "")
        self.export_panel.set_result(result)

        if result.succeeded:
            worst = (
                max((r.status for r in result.reports), key=lambda s: s.rank)
                if result.reports
                else None
            )
            suffix = f" — output validates {worst.value}" if worst else ""
            self._set_status(f"Export complete: {result.output_path.name}{suffix}")
            log.info("export complete: %s%s", result.output_path, suffix)
        elif result.state == ExportState.CANCELLED:
            self._set_status("Export cancelled")
        else:
            self._set_status(f"Export failed: {result.error or 'unknown error'}")
        self._rebuild_job()  # the output now exists; refresh the collision state

    @Slot(str)
    def _on_export_failed(self, message: str) -> None:
        self._exporting = False
        self._export_worker = None
        self.export_panel.set_state(ExportState.FAILED, 0.0, message)
        self._set_status(f"Export failed: {message}")

    # -- batch export -----------------------------------------------------

    @Slot(list)
    def _on_batch_selection_changed(self, files: list) -> None:
        """Ticking files in the sidebar switches EXPORT into batch mode."""
        self._checked = [f for f in files if f is not None]
        self._refresh_batch_panel()
        # Probe anything not yet inspected: a batch cannot encode what it has
        # not read, and doing it now means the list is ready when they hit go.
        for media_file in self._checked:
            if media_file.state is ProbeState.PENDING:
                self._start_probe(media_file, load_into_player=False)

    def _refresh_batch_panel(self) -> None:
        if self._batch_running:
            return
        if not self._checked:
            self._plan = None
            self.batch_panel.show_empty()
            self.export_stack.setCurrentWidget(self.export_panel)
            return

        self._plan = BatchPlan(
            items=[BatchItem(media_file=f) for f in self._checked],
            profile=self._primary_profile,
            normalize_loudness=self._normalize,
        )
        self.batch_panel.set_plan(self._plan)
        self.batch_panel.set_output_folder(
            self._output.resolve_dir(self._checked[0].path)
        )
        self.export_stack.setCurrentWidget(self.batch_panel)

    @Slot()
    def start_batch_export(self) -> None:
        if self._batch_running or self._plan is None:
            return
        if self._plan.profile is None:
            self._set_status("Select a destination profile in VALIDATE first")
            self._show_tab(TAB_VALIDATE)
            return

        existing = [
            item.media_file.name
            for item in self._plan.items
            if item.media_file.info is not None
        ]
        if not existing:
            self._set_status("None of the selected files could be inspected")
            return

        self._batch_running = True
        self.batch_panel.set_running(True)
        self._show_tab(TAB_EXPORT)
        self._set_status(f"Exporting {len(existing)} files to {self._plan.profile.name}…")
        self.batch_runner.start(self._plan, self._output)

    @Slot()
    def cancel_batch_export(self) -> None:
        self.batch_runner.cancel()
        self._set_status("Cancelling batch…")

    @Slot(str)
    def _on_batch_item_changed(self, key: str) -> None:
        self.batch_panel.update_item(key)

    @Slot(str, float)
    def _on_batch_item_progress(self, key: str, _fraction: float) -> None:
        self.batch_panel.update_item(key)

    @Slot()
    def _on_batch_finished(self) -> None:
        self._batch_running = False
        self.batch_panel.set_running(False)
        if self._plan is None:
            return
        self.batch_panel.set_plan(self._plan)
        done, failed = self._plan.completed, self._plan.failed
        message = f"Batch complete: {done} exported"
        if failed:
            message += f", {failed} failed"
        self._set_status(message)
        log.info(message)

    def _open_output_file(self) -> None:
        if self._job is not None and self._job.output_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._job.output_path)))

    # -- output destination -----------------------------------------------

    @Slot()
    def choose_output_folder(self) -> None:
        start = self._output.resolve_dir(self._current.path if self._current else None)
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose output folder", str(start) if start else ""
        )
        if not chosen:
            return
        self._output = OutputDestination.custom(Path(chosen))
        self.settings.output_destination = self._output
        self.settings.last_output_dir = Path(chosen)
        self._refresh_output_bar()
        self._rebuild_job()
        self._refresh_batch_panel()
        log.info("output folder set to %s", chosen)

    @Slot()
    def use_source_folder(self) -> None:
        self._output = OutputDestination.same_as_source()
        self.settings.output_destination = self._output
        self._refresh_output_bar()
        self._rebuild_job()
        self._refresh_batch_panel()

    @Slot()
    def reveal_output_folder(self) -> None:
        directory = self._output.resolve_dir(self._current.path if self._current else None)
        if directory is None or not directory.is_dir():
            self._set_status("No output folder to open yet")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _refresh_output_bar(self) -> None:
        source = self._current.path if self._current else None
        text = self._output.display_text(source)
        self.output_label.setText(shorten_path(text, 70))
        self.output_label.setToolTip(text)
        self.same_as_source_button.setVisible(self._output.mode.name == "CUSTOM")

    # -- recents ----------------------------------------------------------

    def _rebuild_recent_menus(self) -> None:
        self.recent_files_menu.clear()
        files = self.settings.recent_files
        if not files:
            self.recent_files_menu.addAction("No recent files").setEnabled(False)
        for path in files:
            action = self.recent_files_menu.addAction(shorten_path(path, 60))
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: self.open_path(p))

        self.recent_folders_menu.clear()
        folders = self.settings.recent_folders
        if not folders:
            self.recent_folders_menu.addAction("No recent folders").setEnabled(False)
        for path in folders:
            action = self.recent_folders_menu.addAction(shorten_path(path, 60))
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: self.open_folder(p))

    def _on_recursive_toggled(self, checked: bool) -> None:
        self.settings.recursive_scan = checked

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir() or is_supported_media(path):
                event.acceptProposedAction()
                return

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        folders = [p for p in paths if p.is_dir()]
        files = [p for p in paths if p.is_file() and is_supported_media(p)]

        if folders:
            self.open_folder(folders[0])
        elif len(files) > 1:
            self._files = [MediaFile(p) for p in files]
            self.source_browser.show()
            self.source_browser.set_files(self._files, files[0].parent)
            self._select(self._files[0])
            for media_file in self._files[1:]:
                self._start_probe(media_file, load_into_player=False)
            self.settings.last_input_dir = files[0].parent
        elif files:
            self.open_path(files[0])
        else:
            self._set_status("Nothing supported in that drop")
            return
        event.acceptProposedAction()

    # -- misc -------------------------------------------------------------

    @Slot(str)
    def _on_player_error(self, message: str) -> None:
        if self._current is not None:
            self._current.playback_failed = True
        self.player.show_message(message)
        self._set_status("Playback error — see Help ▸ Open Log")

    def open_log(self) -> None:
        path = logging_service.log_file()
        if path is None or not path.exists():
            self._set_status("No log file yet")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_technical_details(self) -> None:
        from ..media.ffmpeg_builder import build_command_text, build_export_args
        from ..services.binaries import ffmpeg_path, ffprobe_path, libmpv_dir

        lines = [
            f"{APP_TITLE} — technical details",
            "",
            f"ffprobe:  {ffprobe_path() or 'not found'}",
            f"ffmpeg:   {ffmpeg_path() or 'not found'}",
            f"libmpv:   {libmpv_dir() or 'not found'}",
            f"specs:    {len(self._profiles)} profiles loaded",
            f"log:      {logging_service.log_file()}",
            "",
        ]
        if self._current is not None:
            lines += [f"source:   {self._current.path}", f"state:    {self._current.state.value}"]
            if self._current.error:
                lines.append(f"error:    {self._current.error}")
        else:
            lines.append("No file loaded.")

        if self._job is not None:
            try:
                lines += ["", "Pending export command:", build_command_text(build_export_args(self._job))]
            except Exception as exc:  # a half-specified job should not break the dialog
                lines += ["", f"Could not build the export command: {exc}"]

        box = QMessageBox(self)
        box.setWindowTitle("Technical details")
        box.setText("\n".join(lines))
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_TITLE}",
            f"<b>{APP_TITLE}</b><br>{TAGLINE}<br><br>"
            "Local-only inspection, trimming, validation and delivery conformance "
            "for video files.<br>No cloud processing. Your source files are never "
            "modified.<br><br>Licensed GPL-3.0-or-later. Bundles FFmpeg and libmpv — "
            "see THIRD_PARTY_NOTICES.md.",
        )

    def _set_status(self, message: str) -> None:
        self.status.showMessage(message)
        log.debug("status: %s", message)
