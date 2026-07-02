from __future__ import annotations

from contextlib import suppress
import json
import threading
from pathlib import Path
from typing import Any, Callable

from . import core
from .badged_button import create_badged_button, set_badge
from .cloud_browser import (
    BrowserEntry,
    CloudBrowserBackend,
    TransferItem,
    format_file_size,
    normalize_browser_path,
    parent_browser_path,
)
from .settings import load_app_settings
from .shortcuts import matches_shortcut

MIME_TYPE = "application/x-mountlet-remote-files"
EMBEDDED_BROWSER_MIN_WIDTH = 540
EMBEDDED_BROWSER_MIN_HEIGHT = 340
FILE_BROWSER_MIN_HEIGHT = 240
FILE_BROWSER_MAX_VISIBLE_ROWS = 14
FILE_BROWSER_CONTEXT_ROWS = 1
RCLONE_OUTPUT_TAIL_LINES = 10
RCLONE_OUTPUT_MIN_LINES = 8
RCLONE_OUTPUT_MAX_LINES = 16
CHILD_FOLDER_PREFETCH_LIMIT = 24
OFFLINE_SAVED_BADGE_COLOR = "#22c55e"
ENTRY_ICON_SIZE = 30
OFFLINE_JOB_CONCURRENCY = 3


def cascade_position(
    main_rect: tuple[int, int, int, int],
    row_y: int,
    available: tuple[int, int, int, int],
    browser_size: tuple[int, int],
) -> tuple[int, int]:
    main_x, _main_y, main_width, _main_height = main_rect
    left, top, available_width, available_height = available
    width = browser_size[0] if browser_size[0] > 0 else EMBEDDED_BROWSER_MIN_WIDTH
    height = browser_size[1] if browser_size[1] > 0 else EMBEDDED_BROWSER_MIN_HEIGHT
    right_edge = left + available_width
    main_right = main_x + main_width
    right_space = right_edge - main_right
    left_space = main_x - left
    x = main_right + 8 if right_space >= width + 8 or right_space >= left_space else main_x - width - 8
    x = min(max(x, left), max(left, right_edge - width))
    y = min(max(row_y, top), max(top, top + available_height - height))
    return x, y


def _paths_overlap(left_paths: list[str], right_paths: list[str]) -> bool:
    for left in left_paths:
        normalized_left = normalize_browser_path(left)
        for right in right_paths:
            normalized_right = normalize_browser_path(right)
            if not normalized_left or not normalized_right:
                return True
            if normalized_left == normalized_right:
                return True
            if normalized_left and normalized_right.startswith(f"{normalized_left}/"):
                return True
            if normalized_right and normalized_left.startswith(f"{normalized_right}/"):
                return True
    return False


class CompactCloudBrowser:
    def __init__(
        self,
        qt: Any,
        main_window: Any,
        *,
        remotes: Callable[[], list[core.RemoteInfo]],
        notify: Callable[[str, str, bool], None],
        open_mount: Callable[[core.RemoteInfo, str], None],
        file_manager_label: Callable[[], str],
        open_file: Callable[[Path], bool] | None = None,
        open_local_folder: Callable[[Path], bool] | None = None,
        toggle_mount: Callable[[str, bool], None] | None = None,
        sync_paths: Callable[[core.RemoteInfo, list[tuple[str, bool]]], None] | None = None,
        embedded: bool = False,
        layout_changed: Callable[[], None] | None = None,
        local_files_changed: Callable[[], None] | None = None,
    ) -> None:
        self.qt = qt
        self.main_window = main_window
        self._remotes = remotes
        self._notify = notify
        self._open_mount = open_mount
        self._open_file = open_file
        self._open_local_folder = open_local_folder
        self._toggle_mount = toggle_mount
        self._sync_paths = sync_paths
        self._file_manager_name = file_manager_label
        self._embedded = embedded
        self._layout_changed = layout_changed or (lambda: None)
        self._local_files_changed = local_files_changed or (lambda: None)
        self.backend = CloudBrowserBackend()
        self.remote: core.RemoteInfo | None = None
        self.path = ""
        self._side = "right"
        self.entries: list[BrowserEntry] = []
        self.clipboard: tuple[list[TransferItem], bool] | None = None
        self._operation_pending = False
        self._zoom_steps = 0
        self._operation_cache_keys: set[tuple[str, str]] = set()
        self._folder_cache: dict[tuple[str, str], list[BrowserEntry]] = {}
        self._loads_pending: set[tuple[str, str]] = set()
        self._working_paths: dict[tuple[str, str], str] = {}
        self._working_phase = 0
        self._working_timer: Any | None = None
        self._rclone_output_dialog: Any | None = None
        self._rclone_output_text: Any | None = None
        self._rclone_output_lines: list[str] = []
        self._rclone_progress_block: list[str] = []
        self._offline_jobs_running = 0
        self._offline_job_queue: list[
            tuple[str, str, Callable[[], object], list[str], str, Callable[[], list[BrowserEntry]] | None]
        ] = []
        self._pending_select_path = ""
        self._closed_until_selected = False
        self._load_slots = threading.BoundedSemaphore(4)
        self._bridge = self._make_bridge()
        self._bridge.listing_ready.connect(self._listing_ready)
        self._bridge.operation_finished.connect(self._operation_finished)
        self._bridge.cached_file_ready.connect(self._cached_file_ready)
        self._bridge.offline_job_paths_ready.connect(self._offline_job_paths_ready)
        self._bridge.offline_job_finished.connect(self._offline_job_finished)
        self._bridge.rclone_output_ready.connect(self._append_rclone_output)
        self.backend.operation_output_callback = lambda text: self._bridge.rclone_output_ready.emit(text)
        self.window = self._make_window()
        self._file_icon_provider = self._make_file_icon_provider()
        self._build()
        self._setup_working_animation()

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            listing_ready = qt.Signal(str, str, object, str)
            operation_finished = qt.Signal(bool, str)
            cached_file_ready = qt.Signal(str, str, object, str)
            offline_job_paths_ready = qt.Signal(str, object, str)
            offline_job_finished = qt.Signal(str, object, str, bool, str)
            rclone_output_ready = qt.Signal(str)

        return Bridge()

    def _make_window(self) -> Any:
        outer = self
        flags = self.qt.Qt.WindowType.Tool | self.qt.Qt.WindowType.FramelessWindowHint

        class BrowserWindow(self.qt.QMainWindow):
            def keyPressEvent(self, event: Any) -> None:
                if outer._handle_key(event):
                    return
                super().keyPressEvent(event)

            def focusInEvent(self, event: Any) -> None:
                super().focusInEvent(event)
                outer._ensure_tree_selection()
                outer._update_focus_style()
                outer._update_main_focus_style()

            def changeEvent(self, event: Any) -> None:
                super().changeEvent(event)
                if event.type() in {
                    outer.qt.QEvent.Type.ActivationChange,
                    outer.qt.QEvent.Type.WindowActivate,
                    outer.qt.QEvent.Type.WindowDeactivate,
                }:
                    outer._update_focus_style()
                    outer._position_rclone_output()

            def moveEvent(self, event: Any) -> None:
                super().moveEvent(event)
                outer._position_rclone_output()

            def resizeEvent(self, event: Any) -> None:
                super().resizeEvent(event)
                outer._position_rclone_output()

        try:
            window = BrowserWindow(None, flags)
        except Exception:
            window = BrowserWindow()
            window.setWindowFlags(flags)
        window.setWindowTitle("Mountlet Files")
        window.resize(520, 390)
        return window

    def _build(self) -> None:
        qt = self.qt
        root = qt.QWidget()
        root.setObjectName("fileBrowserSurface")
        root.setMinimumSize(EMBEDDED_BROWSER_MIN_WIDTH, EMBEDDED_BROWSER_MIN_HEIGHT)
        self.root = root
        layout = qt.QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        header = qt.QHBoxLayout()
        self.title = qt.QLabel("Files")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        header.addWidget(self.title)
        header.addStretch(1)
        self.mount_switch = self._mount_switch()
        header.addWidget(self.mount_switch)
        self.remote_sync_button = self._button("⇄", self.sync_remote, "Sync cached files for this remote", square=True)
        header.addWidget(self.remote_sync_button)
        self.rclone_output_button = self._button("▤", self._show_rclone_output, "Show rclone output", square=True)
        header.addWidget(self.rclone_output_button)
        header.addWidget(self._button("×", self.hide_until_selected, "Close file browser", square=True))
        layout.addLayout(header)

        navigation = qt.QHBoxLayout()
        self.up_button = self._button("↑", self.go_up, "Parent folder", square=True)
        self.root_button = self._button("⌂", self.go_root, "Remote root", square=True)
        self.path_field = qt.QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Remote root")
        self.path_field.setContextMenuPolicy(qt.Qt.ContextMenuPolicy.CustomContextMenu)
        self.path_field.customContextMenuRequested.connect(self._show_folder_menu)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.root_button)
        navigation.addWidget(self.path_field, 1)
        navigation.addWidget(self._button("↻", lambda: self.refresh(force=True), "Refresh folder", square=True))
        self.open_folder_button = self._button(
            "↗",
            self._open_current_mount,
            "Open this folder in the system file manager",
            square=True,
        )
        navigation.addWidget(self.open_folder_button)
        layout.addLayout(navigation)

        item_actions = qt.QHBoxLayout()
        self.copy_button = self._button("⧉", self.copy_selected, "Copy selected items", square=True)
        self.cut_button = self._button("✂", self.cut_selected, "Cut selected items", square=True)
        self.paste_button = self._button("▣", self.paste, "Paste into this folder", square=True)
        self.delete_button = self._button("⌫", self.delete_selected, "Delete selected items", square=True)
        self.offline_button = self._button("", self.toggle_offline, "Make selected items available offline", square=True)
        self.selection_sync_button = self._button("⇄", self.sync_selected, "Sync selected local copies", square=True)
        save_icon = self._offline_icon()
        self._offline_base_icon = save_icon
        if save_icon is not None:
            self.offline_button.setIcon(save_icon)
        item_actions.addWidget(self.copy_button)
        item_actions.addWidget(self.cut_button)
        item_actions.addWidget(self.paste_button)
        item_actions.addWidget(self.delete_button)
        item_actions.addWidget(self.offline_button)
        item_actions.addWidget(self.selection_sync_button)
        item_actions.addStretch(1)
        layout.addLayout(item_actions)

        outer = self

        class FileTree(qt.QTreeWidget):
            def startDrag(self, _supported_actions: Any) -> None:
                if not outer._edits_enabled():
                    return
                items = outer.selected_transfer_items()
                if not items:
                    return
                mime = qt.QMimeData()
                mime.setData(MIME_TYPE, json.dumps([item.__dict__ for item in items]).encode())
                drag = qt.QDrag(self)
                drag.setMimeData(mime)
                drag.exec(qt.Qt.DropAction.CopyAction | qt.Qt.DropAction.MoveAction, qt.Qt.DropAction.CopyAction)

            def dragEnterEvent(self, event: Any) -> None:
                if outer._drop_event_supported(event):
                    event.acceptProposedAction()
                    return
                super().dragEnterEvent(event)

            def dragMoveEvent(self, event: Any) -> None:
                if outer._drop_event_supported(event):
                    event.acceptProposedAction()
                    return
                super().dragMoveEvent(event)

            def dropEvent(self, event: Any) -> None:
                if outer._handle_drop_event(event):
                    event.acceptProposedAction()
                    return
                super().dropEvent(event)

            def keyPressEvent(self, event: Any) -> None:
                if outer._handle_key(event):
                    return
                super().keyPressEvent(event)

            def wheelEvent(self, event: Any) -> None:
                try:
                    modifiers = event.modifiers()
                except Exception:
                    modifiers = qt.Qt.KeyboardModifier.NoModifier
                if modifiers & qt.Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        outer.zoom_in()
                    elif delta < 0:
                        outer.zoom_out()
                    event.accept()
                    return
                super().wheelEvent(event)

        self.tree = FileTree()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(qt.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(qt.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setDragEnabled(False)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setEditTriggers(qt.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(qt.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        self.tree.itemDoubleClicked.connect(self._open_item)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        with suppress(Exception):
            self.tree.setIconSize(qt.QSize(ENTRY_ICON_SIZE, ENTRY_ICON_SIZE))
        self.tree.setColumnWidth(0, 282)
        self.tree.setColumnWidth(1, 72)
        layout.addWidget(self.tree, 1)
        self.status = qt.QLabel("")
        layout.addWidget(self.status)
        self.window.setCentralWidget(root)
        self._update_actions()
        self._update_focus_style()

    def _show_rclone_output(self) -> None:
        dialog = self._rclone_output_dialog
        if dialog is None:
            dialog = self.qt.QDialog(self.window)
            dialog.setWindowTitle("rclone output")
            layout = self.qt.QVBoxLayout(dialog)
            text = self.qt.QPlainTextEdit()
            text.setReadOnly(True)
            text.setMinimumWidth(520)
            layout.addWidget(text)
            buttons = self.qt.QDialogButtonBox(self.qt.QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.hide)
            layout.addWidget(buttons)
            self._rclone_output_dialog = dialog
            self._rclone_output_text = text
            text.setPlainText(self._rclone_output_text_block())
            self._resize_rclone_output_text()
            self._scroll_rclone_output_to_end()
        dialog.show()
        self._position_rclone_output()
        dialog.raise_()
        dialog.activateWindow()

    def _append_rclone_output(self, text: str) -> None:
        if not text:
            return
        lines = self._split_rclone_output_lines(text)
        if not lines:
            return
        for line in lines:
            self._record_rclone_output_line(line)
        if len(self._rclone_output_lines) > RCLONE_OUTPUT_TAIL_LINES:
            self._rclone_output_lines = self._rclone_output_lines[-RCLONE_OUTPUT_TAIL_LINES:]
        editor = self._rclone_output_text
        if editor is None:
            return
        editor.setPlainText(self._rclone_output_text_block())
        self._resize_rclone_output_text()
        self._scroll_rclone_output_to_end()
        self._position_rclone_output()

    def _record_rclone_output_line(self, line: str) -> None:
        if line.startswith("[rclone exited with code"):
            if not getattr(self, "_rclone_progress_block", []):
                self._rclone_output_lines.append(line)
            return
        if self._is_rclone_progress_header(line):
            self._rclone_progress_block = [line]
            self._rclone_output_lines = [line]
            return
        if getattr(self, "_rclone_progress_block", []):
            self._rclone_progress_block.append(line)
            self._rclone_output_lines = list(self._rclone_progress_block)
            return
        self._rclone_output_lines.append(line)

    def _is_rclone_progress_header(self, line: str) -> bool:
        if not line.startswith("Transferred:"):
            return False
        value = line.split(":", 1)[1]
        return any(unit in value for unit in (" B", "KiB", "MiB", "GiB", "TiB", "PiB"))

    def _split_rclone_output_lines(self, text: str) -> list[str]:
        normalized = text.replace("\r", "\n")
        normalized = normalized.replace("Transferred:\t", "\nTransferred:\t")
        normalized = normalized.replace("Transferred:   ", "\nTransferred:   ")
        normalized = normalized.replace("Checks:", "\nChecks:")
        normalized = normalized.replace("Elapsed time:", "\nElapsed time:")
        normalized = normalized.replace("Transferring:", "\nTransferring:")
        normalized = normalized.replace("[rclone exited with code", "\n[rclone exited with code")
        return [line for line in normalized.splitlines() if line.strip()]

    def _rclone_output_text_block(self) -> str:
        return "\n".join(self._rclone_output_lines)

    def _resize_rclone_output_text(self) -> None:
        editor = self._rclone_output_text
        if editor is None:
            return
        line_count = max(RCLONE_OUTPUT_MIN_LINES, min(RCLONE_OUTPUT_MAX_LINES, len(self._rclone_output_lines) or 1))
        try:
            line_height = editor.fontMetrics().lineSpacing()
        except Exception:
            line_height = 18
        height = int(line_height * line_count + 18)
        with suppress(Exception):
            editor.setMinimumHeight(height)
            editor.setMaximumHeight(height)

    def _scroll_rclone_output_to_end(self) -> None:
        editor = self._rclone_output_text
        if editor is None:
            return
        with suppress(Exception):
            self._move_rclone_output_cursor_to_end()

    def _position_rclone_output(self) -> None:
        dialog = getattr(self, "_rclone_output_dialog", None)
        if dialog is None:
            return
        with suppress(Exception):
            if not dialog.isVisible():
                return
        try:
            frame = self.window.frameGeometry()
            screen = self.window.screen() or self.qt.QApplication.primaryScreen()
            available = screen.availableGeometry()
            width = max(dialog.width(), 420)
            height = max(dialog.height(), 120)
            gap = 8
            if self._side == "left":
                x = frame.x() - width - gap
                if x < available.x():
                    x = frame.x() + frame.width() + gap
            else:
                x = frame.x() + frame.width() + gap
                if x + width > available.x() + available.width():
                    x = frame.x() - width - gap
            y = frame.y()
            x = min(max(x, available.x()), max(available.x(), available.x() + available.width() - width))
            y = min(max(y, available.y()), max(available.y(), available.y() + available.height() - height))
            dialog.move(x, y)
        except Exception:
            return

    def _move_rclone_output_cursor_to_end(self) -> None:
        editor = self._rclone_output_text
        if editor is None:
            return
        text_cursor = getattr(self.qt, "QTextCursor", None)
        move_operation = getattr(text_cursor, "MoveOperation", None)
        end = getattr(move_operation, "End", None)
        if end is not None:
            with suppress(Exception):
                editor.moveCursor(end)

    def _button(self, text: str, callback: Callable[[], None], tooltip: str, *, square: bool = False) -> Any:
        button = create_badged_button(self.qt, text)
        if square:
            button.setFixedSize(30, 28)
            self._enlarge_button_text(button)
            with suppress(Exception):
                button.setIconSize(self.qt.QSize(22, 22))
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _mount_switch(self) -> Any:
        qt = self.qt
        outer = self

        class Switch(qt.QCheckBox):
            def __init__(self) -> None:
                super().__init__()
                self.setText("")
                self.setFixedSize(42, 22)
                self.setCursor(qt.QCursor(qt.Qt.CursorShape.PointingHandCursor))

            def paintEvent(self, event: Any) -> None:
                painter = qt.QPainter(self)
                painter.setRenderHint(qt.QPainter.RenderHint.Antialiasing)
                painter.setPen(qt.Qt.PenStyle.NoPen)
                track = qt.QColor("#16a34a" if self.isChecked() else "#9ca3af")
                if not self.isEnabled():
                    track = qt.QColor("#6b7280")
                painter.setBrush(track)
                painter.drawRoundedRect(1, 2, 40, 18, 9, 9)
                painter.setBrush(qt.QColor("#ffffff"))
                painter.drawEllipse(22 if self.isChecked() else 4, 4, 14, 14)

            def hitButton(self, position: Any) -> bool:
                return bool(self.rect().contains(position))

        switch = Switch()
        switch.stateChanged.connect(lambda state: outer._mount_switch_changed(bool(state)))
        return switch

    def _mount_switch_changed(self, want_mounted: bool) -> None:
        if self.remote is None or self._toggle_mount is None:
            return
        self._toggle_mount(self.remote.name, want_mounted)

    def _update_mount_switch(self) -> None:
        switch = getattr(self, "mount_switch", None)
        if switch is None:
            return
        remote = getattr(self, "remote", None)
        mounted = bool(remote and core.is_mounted(remote))
        try:
            switch.blockSignals(True)
            switch.setChecked(mounted)
            switch.blockSignals(False)
        except Exception:
            pass
        switch.setEnabled(remote is not None)
        if remote is None:
            switch.setToolTip("Select a remote")
            return
        switch.setToolTip(f"{'Unmount' if mounted else 'Mount'} {remote.display_name}")

    def zoom_in(self) -> None:
        self._set_zoom(self._zoom_steps + 1)

    def zoom_out(self) -> None:
        self._set_zoom(self._zoom_steps - 1)

    def _set_zoom(self, steps: int) -> None:
        steps = min(max(steps, -4), 6)
        if steps == self._zoom_steps:
            return
        self._zoom_steps = steps
        for widget in (getattr(self, "tree", None), getattr(self, "path_field", None), getattr(self, "status", None)):
            if widget is None:
                continue
            try:
                font = widget.font()
                base = getattr(widget, "_mountlet_base_point_size", None)
                if base is None:
                    base = font.pointSizeF()
                    if base <= 0:
                        base = max(font.pointSize(), 10)
                    setattr(widget, "_mountlet_base_point_size", base)
                font.setPointSizeF(max(7.0, float(base) + steps))
                widget.setFont(font)
            except Exception:
                continue
        with suppress(Exception):
            self.tree.resizeColumnToContents(0)
            self.tree.resizeColumnToContents(1)
        self._resize_to_rendered_items()
        self._layout_changed()

    def _offline_icon(self) -> Any | None:
        try:
            return self.window.style().standardIcon(self.qt.QStyle.StandardPixmap.SP_DialogSaveButton)
        except Exception:
            return None

    def _make_file_icon_provider(self) -> Any | None:
        provider_type = getattr(self.qt, "QFileIconProvider", None)
        if provider_type is None:
            return None
        try:
            return provider_type()
        except Exception:
            return None

    def _base_entry_icon(self, entry: BrowserEntry, *, directory_icon: Any, file_icon: Any) -> Any:
        provider = getattr(self, "_file_icon_provider", None)
        file_info_type = getattr(self.qt, "QFileInfo", None)
        if provider is None or file_info_type is None:
            return directory_icon if entry.is_dir else file_icon
        try:
            if entry.is_dir:
                return directory_icon
            if self.remote is not None:
                local = self.backend.offline_path(self.remote.name, entry.path)
                if local.exists():
                    return provider.icon(file_info_type(str(local)))
            return provider.icon(file_info_type(entry.name)) or file_icon
        except Exception:
            return directory_icon if entry.is_dir else file_icon

    def _entry_icon(
        self,
        entry: BrowserEntry,
        *,
        directory_icon: Any,
        file_icon: Any,
        temporary_cached: bool = False,
        protected_cached: bool = False,
        cache_partial: bool = False,
        changed: bool = False,
        working: str = "",
    ) -> Any:
        base_icon = self._base_entry_icon(entry, directory_icon=directory_icon, file_icon=file_icon)
        if not any((temporary_cached, protected_cached, changed, working)):
            return base_icon
        return self._composite_entry_icon(
            base_icon,
            temporary_cached=temporary_cached,
            protected_cached=protected_cached,
            cache_partial=cache_partial,
            changed=changed,
            working=working,
        )

    def _composite_entry_icon(
        self,
        base_icon: Any,
        *,
        temporary_cached: bool,
        protected_cached: bool,
        cache_partial: bool = False,
        changed: bool = False,
        working: str = "",
    ) -> Any:
        pixmap_type = getattr(self.qt, "QPixmap", None)
        painter_type = getattr(self.qt, "QPainter", None)
        icon_type = getattr(self.qt, "QIcon", None)
        if pixmap_type is None or painter_type is None or icon_type is None:
            return base_icon
        try:
            size = self.qt.QSize(ENTRY_ICON_SIZE, ENTRY_ICON_SIZE)
            pixmap = pixmap_type(size)
            pixmap.fill(self.qt.Qt.GlobalColor.transparent)
            painter = painter_type(pixmap)
            painter.setRenderHint(painter_type.RenderHint.Antialiasing, True)
            base_pixmap = base_icon.pixmap(size)
            painter.drawPixmap(0, 0, base_pixmap)
            if temporary_cached or protected_cached:
                color = "#a855f7" if temporary_cached and protected_cached else "#38bdf8" if protected_cached else "#ef4444"
                painter.setOpacity(0.5 if cache_partial else 1.0)
                pen = self.qt.QPen(self.qt.QColor(0, 0, 0, 170))
                pen.setWidth(5)
                painter.setPen(pen)
                painter.setBrush(self.qt.Qt.BrushStyle.NoBrush)
                painter.drawLine(20, 12, 20, 24)
                painter.drawLine(15, 19, 20, 24)
                painter.drawLine(25, 19, 20, 24)
                painter.drawLine(14, 27, 26, 27)
                pen = self.qt.QPen(self.qt.QColor(color))
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawLine(20, 12, 20, 24)
                painter.drawLine(15, 19, 20, 24)
                painter.drawLine(25, 19, 20, 24)
                painter.drawLine(14, 27, 26, 27)
            if changed:
                painter.setOpacity(1.0)
                painter.setPen(self.qt.Qt.PenStyle.NoPen)
                painter.setBrush(self.qt.QColor(0, 0, 0, 180))
                painter.drawEllipse(19, -1, 12, 12)
                painter.setBrush(self.qt.QColor("#ef4444"))
                painter.drawEllipse(21, 1, 8, 8)
            if working:
                opacity = 0.45 if self._working_phase % 2 else 1.0
                color = "#38bdf8" if working == "download" else "#f59e0b"
                painter.setOpacity(opacity)
                painter.setPen(self.qt.Qt.PenStyle.NoPen)
                painter.setBrush(self.qt.QColor(0, 0, 0, 165))
                painter.drawEllipse(1, 1, 14, 14)
                pen = self.qt.QPen(self.qt.QColor(color))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(self.qt.Qt.BrushStyle.NoBrush)
                if working == "download":
                    painter.drawLine(8, 3, 8, 10)
                    painter.drawLine(5, 7, 8, 10)
                    painter.drawLine(11, 7, 8, 10)
                    painter.drawLine(4, 13, 12, 13)
                else:
                    painter.drawArc(4, 4, 9, 9, 40 * 16, 260 * 16)
                    painter.drawLine(12, 5, 12, 9)
                    painter.drawLine(12, 5, 8, 5)
            painter.end()
            return icon_type(pixmap)
        except Exception:
            return base_icon

    def _setup_working_animation(self) -> None:
        timer_type = getattr(self.qt, "QTimer", None)
        if timer_type is None:
            return
        try:
            timer = timer_type(self.window)
            timer.setInterval(450)
            timer.timeout.connect(self._advance_working_animation)
        except Exception:
            return
        self._working_timer = timer

    def _advance_working_animation(self) -> None:
        working_paths = getattr(self, "_working_paths", {})
        if not working_paths:
            timer = getattr(self, "_working_timer", None)
            if timer is not None:
                with suppress(Exception):
                    timer.stop()
            return
        self._refresh_download_working_paths()
        self._working_phase += 1
        self._refresh_entry_icons()

    def _start_working_paths(self, remote_name: str, paths: list[str], kind: str) -> None:
        if not paths:
            return
        if not hasattr(self, "_working_paths"):
            self._working_paths = {}
        for path in paths:
            self._working_paths[(remote_name, normalize_browser_path(path))] = kind
        timer = getattr(self, "_working_timer", None)
        if timer is not None:
            with suppress(Exception):
                if not timer.isActive():
                    timer.start()
        self._refresh_entry_icons()

    def _finish_working_paths(self, remote_name: str, paths: list[str], kind: str = "") -> None:
        if not hasattr(self, "_working_paths"):
            return
        for path in paths:
            key = (remote_name, normalize_browser_path(path))
            if not kind or self._working_paths.get(key) == kind:
                self._working_paths.pop(key, None)
        if not self._working_paths:
            timer = getattr(self, "_working_timer", None)
            if timer is not None:
                with suppress(Exception):
                    timer.stop()
        self._refresh_entry_icons()

    def _working_kind_for_entry(self, remote_name: str, path: str, *, is_dir: bool) -> str:
        normalized = normalize_browser_path(path)
        working_paths = getattr(self, "_working_paths", {})
        exact = working_paths.get((remote_name, normalized))
        if exact:
            return exact
        prefix = f"{normalized}/" if normalized else ""
        for key, kind in working_paths.items():
            if key[0] != remote_name:
                continue
            working_path = str(key[1])
            if is_dir and prefix and working_path.startswith(prefix):
                return kind
        return ""

    def _refresh_visible_download_state(self, remote_name: str, entries: list[BrowserEntry]) -> None:
        if not hasattr(self, "_working_paths"):
            self._working_paths = {}
        for entry in entries:
            if entry.is_dir:
                continue
            normalized = normalize_browser_path(entry.path)
            key = (remote_name, normalized)
            if self._working_paths.get(key) == "download" and self.backend.is_cached(
                remote_name,
                normalized,
                is_dir=False,
            ):
                self._working_paths.pop(key, None)

    def _refresh_download_working_paths(self, remote_name: str | None = None) -> None:
        if not hasattr(self, "_working_paths"):
            return
        for key, kind in list(self._working_paths.items()):
            if kind != "download":
                continue
            if remote_name is not None and key[0] != remote_name:
                continue
            if self.backend.is_cached(key[0], str(key[1]), is_dir=False):
                self._working_paths.pop(key, None)

    def _refresh_entry_icons(self) -> None:
        remote = getattr(self, "remote", None)
        tree = getattr(self, "tree", None)
        window = getattr(self, "window", None)
        if remote is None or tree is None or window is None:
            return
        style = window.style()
        directory_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_FileIcon)
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item is None:
                continue
            entry = item.data(0, self.qt.Qt.ItemDataRole.UserRole)
            if isinstance(entry, BrowserEntry):
                self._apply_entry_state(item, entry, remote, directory_icon, file_icon)

    def _entry_has_operation(self, remote_name: str, path: str, *, is_dir: bool, kind: str) -> bool:
        return self._working_kind_for_entry(remote_name, path, is_dir=is_dir) == kind

    def _operation_paths_overlap(self, remote_name: str, paths: list[str], kind: str) -> bool:
        if not paths:
            return False
        for path in paths:
            normalized = normalize_browser_path(path)
            for key, active_kind in getattr(self, "_working_paths", {}).items():
                if key[0] != remote_name or active_kind != kind:
                    continue
                if _paths_overlap([normalized], [str(key[1])]):
                    return True
        return False

    def _queued_offline_job_overlaps(self, remote_name: str, paths: list[str], kind: str) -> bool:
        normalized_paths = [normalize_browser_path(path) for path in paths]
        for queued_remote, _message, _action, queued_paths, queued_kind, _discover_paths in getattr(
            self,
            "_offline_job_queue",
            [],
        ):
            if queued_remote != remote_name or queued_kind != kind:
                continue
            if _paths_overlap(normalized_paths, queued_paths):
                return True
        return False

    def _offline_remove_pending(self, remote_name: str, paths: list[str]) -> bool:
        return self._operation_paths_overlap(remote_name, paths, "remove") or self._queued_offline_job_overlaps(
            remote_name,
            paths,
            "remove",
        )

    def _offline_download_pending(self, remote_name: str, paths: list[str]) -> bool:
        return self._operation_paths_overlap(remote_name, paths, "download") or self._queued_offline_job_overlaps(
            remote_name,
            paths,
            "download",
        )

    def _remote_has_operation(self, remote_name: str, kind: str) -> bool:
        return any(
            key[0] == remote_name and active_kind == kind
            for key, active_kind in getattr(self, "_working_paths", {}).items()
        )

    def _enlarge_button_text(self, button: Any) -> None:
        try:
            font = button.font()
            font.setPointSize(max(font.pointSize() + 3, 13))
            button.setFont(font)
        except Exception:
            return

    def is_visible(self) -> bool:
        return bool(self.root.isVisible()) if self._embedded else bool(self.window.isVisible())

    def hide(self) -> None:
        if self._embedded:
            self.root.hide()
            self._layout_changed()
        else:
            self.window.hide()

    def hide_until_selected(self) -> None:
        self._closed_until_selected = True
        self.hide()

    def close(self) -> None:
        self._closed_until_selected = True
        if self._embedded:
            self.root.hide()
            self._layout_changed()
        else:
            self.window.close()

    def embed_into(self, layout: Any) -> None:
        if not self._embedded:
            return
        if self.window.centralWidget() is self.root:
            self.window.takeCentralWidget()
        self.root.setParent(layout.parentWidget())
        layout.addWidget(self.root)
        self.root.hide()

    def preload(self, remotes: list[core.RemoteInfo]) -> None:
        for remote in remotes:
            path = self.backend.current_path(remote.name)
            key = (remote.name, path)
            if key not in self._folder_cache and key not in self._loads_pending:
                self._load_folder(remote, path)

    def invalidate(self, remote_name: str | None = None) -> None:
        if remote_name is None:
            self._folder_cache.clear()
            self._loads_pending.clear()
            if self.remote is not None:
                self.refresh(force=True)
            return
        self._folder_cache = {
            key: entries for key, entries in self._folder_cache.items() if key[0] != remote_name
        }
        self._loads_pending = {key for key in self._loads_pending if key[0] != remote_name}
        if self.remote is not None and self.remote.name == remote_name:
            self.refresh(force=True)

    def show_remote(
        self,
        remote: core.RemoteInfo,
        row: Any,
        *,
        show_browser: bool,
        focus_browser: bool = False,
    ) -> None:
        changed = self.remote is None or self.remote.name != remote.name
        self.remote = remote
        self.path = self.backend.current_path(remote.name)
        if focus_browser:
            self._closed_until_selected = False
        elif show_browser and getattr(self, "_closed_until_selected", False):
            show_browser = False
        if not self._embedded:
            self._position(row)
        if show_browser:
            if self._embedded:
                was_visible = self.root.isVisible()
                self.root.show()
                if not was_visible:
                    self._layout_changed()
            else:
                with suppress(Exception):
                    self.window.setAttribute(self.qt.Qt.WidgetAttribute.WA_ShowWithoutActivating, not focus_browser)
                self.window.show()
                self.window.raise_()
            if focus_browser:
                self.focus()
        if changed or show_browser:
            self.refresh(force=False)

    def focus(self) -> None:
        if self._embedded:
            self.root.show()
            self.main_window.raise_()
            self.main_window.activateWindow()
            self.tree.setFocus(self.qt.Qt.FocusReason.ShortcutFocusReason)
            self._ensure_tree_selection()
            self._update_focus_style()
            self._update_main_focus_style()
            self._layout_changed()
            return
        with suppress(Exception):
            self.window.setAttribute(self.qt.Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.tree.setFocus(self.qt.Qt.FocusReason.ShortcutFocusReason)
        self._ensure_tree_selection()

    def focus_main_window(self) -> None:
        self.main_window.raise_()
        self.main_window.activateWindow()
        callback = getattr(self.main_window, "focus_remote_row", None)
        if callable(callback):
            callback()
        self._update_focus_style()
        self._update_main_focus_style()

    def has_focus(self) -> bool:
        if not self._embedded:
            try:
                return bool(self.window.isActiveWindow())
            except Exception:
                return False
        focus = self.qt.QApplication.focusWidget()
        return bool(focus is not None and (self.root.isAncestorOf(focus) or focus is self.root))

    def _update_main_focus_style(self) -> None:
        callback = getattr(self.main_window, "update_focus_style", None)
        if callable(callback):
            callback()

    def _update_focus_style(self) -> None:
        root = getattr(self, "root", None)
        if root is None:
            return
        active = self.has_focus() if self._embedded else bool(self.window.isActiveWindow())
        color = "#2563eb" if active else "rgba(107, 114, 128, 110)"
        root.setStyleSheet(f"QWidget#fileBrowserSurface {{ border: 2px solid {color}; border-radius: 4px; }}")

    def _handle_key(self, event: Any) -> bool:
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & self.qt.Qt.KeyboardModifier.ControlModifier)
        if control and key == self.qt.Qt.Key.Key_C:
            if not self._edits_enabled():
                return self._edit_disabled()
            self.copy_selected()
        elif control and key == self.qt.Qt.Key.Key_X:
            if not self._edits_enabled():
                return self._edit_disabled()
            self.cut_selected()
        elif control and key == self.qt.Qt.Key.Key_V:
            if not self._edits_enabled():
                return self._edit_disabled()
            self.paste()
        elif key == self.qt.Qt.Key.Key_Delete:
            if not self._edits_enabled():
                return self._edit_disabled()
            self.delete_selected()
        elif key == self.qt.Qt.Key.Key_Escape:
            self.focus_main_window()
        elif key in {self.qt.Qt.Key.Key_Left, self.qt.Qt.Key.Key_Right}:
            if self._direction_points_to_main(key):
                self.focus_main_window()
            else:
                pass
        elif matches_shortcut(self.qt, event, "browser_copy"):
            if not self._edits_enabled():
                return self._edit_disabled()
            self.copy_selected()
        elif matches_shortcut(self.qt, event, "browser_cut"):
            if not self._edits_enabled():
                return self._edit_disabled()
            self.cut_selected()
        elif matches_shortcut(self.qt, event, "browser_paste"):
            if not self._edits_enabled():
                return self._edit_disabled()
            self.paste()
        elif matches_shortcut(self.qt, event, "browser_delete"):
            if not self._edits_enabled():
                return self._edit_disabled()
            self.delete_selected()
        elif matches_shortcut(self.qt, event, "common_previous"):
            self._focus_relative_item(-1)
        elif matches_shortcut(self.qt, event, "common_next"):
            self._focus_relative_item(1)
        elif matches_shortcut(self.qt, event, "browser_parent"):
            self.go_up()
        elif matches_shortcut(self.qt, event, "browser_root"):
            self.go_root()
        elif matches_shortcut(self.qt, event, "browser_refresh"):
            self.refresh(force=True)
        elif self._is_fixed_zoom_in(key, control) or matches_shortcut(self.qt, event, "browser_zoom_in"):
            self.zoom_in()
        elif self._is_fixed_zoom_out(key, control) or matches_shortcut(self.qt, event, "browser_zoom_out"):
            self.zoom_out()
        elif matches_shortcut(self.qt, event, "browser_open_folder"):
            self._open_current_mount()
        elif matches_shortcut(self.qt, event, "browser_new_folder"):
            if not self._edits_enabled():
                return self._edit_disabled()
            self.create_folder()
        elif key in {self.qt.Qt.Key.Key_Return, self.qt.Qt.Key.Key_Enter} or matches_shortcut(
            self.qt, event, "browser_open"
        ):
            item = self.tree.currentItem()
            if item is None:
                return False
            self._open_item(item)
        else:
            return False
        event.accept()
        return True

    def _is_fixed_zoom_in(self, key: Any, control: bool) -> bool:
        return bool(
            control
            and key
            in {
                getattr(self.qt.Qt.Key, "Key_Plus", None),
                getattr(self.qt.Qt.Key, "Key_Equal", None),
            }
        )

    def _is_fixed_zoom_out(self, key: Any, control: bool) -> bool:
        return bool(control and key == getattr(self.qt.Qt.Key, "Key_Minus", None))

    def _direction_points_to_main(self, key: Any) -> bool:
        if self._side == "left":
            return key == self.qt.Qt.Key.Key_Right
        return key == self.qt.Qt.Key.Key_Left

    def refresh(self, *, force: bool = False) -> None:
        if self.remote is None:
            return
        remote, path = self.remote, self.path
        self.title.setText(remote.display_name)
        self._update_mount_switch()
        self.path_field.setText(path)
        self.path_field.setToolTip(path or "Remote root")
        self.up_button.setEnabled(bool(path))
        self.root_button.setEnabled(bool(path))
        key = (remote.name, path)
        cached = self._folder_cache.get(key)
        if cached is not None and not force:
            self._display_entries(cached)
            return
        if key in self._loads_pending:
            if cached is not None:
                self._display_entries(cached)
            else:
                self.status.setText("Loading…")
            return
        self.status.setText("Loading…")
        self._load_folder(remote, path)

    def _load_folder(self, remote: core.RemoteInfo, path: str) -> None:
        key = (remote.name, path)
        if key in self._loads_pending:
            return
        self._loads_pending.add(key)

        def worker() -> None:
            with self._load_slots:
                try:
                    entries = self.backend.list_entries(remote, path)
                except Exception as exc:
                    self._bridge.listing_ready.emit(remote.name, path, None, str(exc))
                    return
            self._bridge.listing_ready.emit(remote.name, path, entries, "")

        threading.Thread(target=worker, daemon=True).start()

    def _listing_ready(self, remote_name: str, path: str, entries: object, error: str) -> None:
        key = (remote_name, path)
        self._loads_pending.discard(key)
        if not isinstance(entries, list):
            cached = getattr(self, "_folder_cache", {}).get(key)
            if self.remote and (self.remote.name, self.path) == key:
                if cached is not None:
                    self._display_entries(cached)
                    self.status.setText("Showing cached folder contents")
                else:
                    self.entries = []
                    self.tree.clear()
                    self.status.setText(error or "Could not load this folder")
                    self._update_actions()
            return
        self._folder_cache[key] = entries
        if self.remote is None or (self.remote.name, self.path) != key:
            return
        self._display_entries(entries)

    def _display_entries(self, entries: list[BrowserEntry]) -> None:
        previous_path = ""
        previous_index = 0
        previous_scroll = 0
        pending_select_path = getattr(self, "_pending_select_path", "")
        selected_paths: set[str] = set()
        with suppress(Exception):
            previous_scroll = int(self.tree.verticalScrollBar().value())
        current_item = self.tree.currentItem()
        if current_item is not None:
            previous = current_item.data(0, self.qt.Qt.ItemDataRole.UserRole)
            if isinstance(previous, BrowserEntry):
                previous_path = previous.path
            with suppress(Exception):
                previous_index = max(self.tree.indexOfTopLevelItem(current_item), 0)
        for selected_item in self.tree.selectedItems():
            selected = selected_item.data(0, self.qt.Qt.ItemDataRole.UserRole)
            if isinstance(selected, BrowserEntry):
                selected_paths.add(selected.path)
        remote = self.remote
        self.entries = entries
        if remote is not None:
            self._refresh_visible_download_state(remote.name, entries)
        self.tree.clear()
        style = self.window.style()
        directory_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_FileIcon)
        current_target = None
        fallback_target = None
        for entry in entries:
            item = self.qt.QTreeWidgetItem([entry.name, "" if entry.is_dir else format_file_size(entry.size), entry.modified])
            item.setData(0, self.qt.Qt.ItemDataRole.UserRole, entry)
            if entry.path in selected_paths:
                item.setSelected(True)
            if pending_select_path and entry.path == pending_select_path:
                current_target = item
            if current_target is None and previous_path and entry.path == previous_path:
                current_target = item
            if remote is not None:
                self._apply_entry_state(item, entry, remote, directory_icon, file_icon)
            self.tree.addTopLevelItem(item)
            if fallback_target is None and self.tree.topLevelItemCount() - 1 >= previous_index:
                fallback_target = item
        target = current_target or fallback_target
        if target is not None:
            self.tree.setCurrentItem(target)
            if target not in self.tree.selectedItems():
                target.setSelected(True)
            if pending_select_path:
                with suppress(Exception):
                    self.tree.scrollToItem(target)
        if pending_select_path:
            self._pending_select_path = ""
        self.status.setText(f"{len(entries)} item{'s' if len(entries) != 1 else ''}")
        if self.has_focus():
            self._ensure_tree_selection()
        self._update_actions()
        self._update_open_folder_button()
        if not pending_select_path:
            with suppress(Exception):
                self.tree.verticalScrollBar().setValue(previous_scroll)
        self._resize_to_rendered_items()
        getattr(self, "_layout_changed", lambda: None)()
        self.qt.QTimer.singleShot(0, lambda visible_entries=list(entries): self._prefetch_related_folders(visible_entries))

    def _resize_to_rendered_items(self) -> None:
        tree = getattr(self, "tree", None)
        root = getattr(self, "root", None)
        if tree is None or root is None:
            return
        entries = getattr(self, "entries", [])
        count = len(entries) if entries else tree.topLevelItemCount()
        count = max(1, int(count))
        visible_rows = min(count + FILE_BROWSER_CONTEXT_ROWS, FILE_BROWSER_MAX_VISIBLE_ROWS + FILE_BROWSER_CONTEXT_ROWS)
        try:
            row_height = tree.sizeHintForRow(0)
        except Exception:
            row_height = 0
        if row_height <= 0:
            try:
                row_height = tree.fontMetrics().height() + 8
            except Exception:
                row_height = 24 + max(0, self._zoom_steps)
        try:
            header_height = tree.header().sizeHint().height()
        except Exception:
            header_height = 28
        tree_height = int(header_height + row_height * visible_rows + 8)
        with suppress(Exception):
            tree.setMinimumHeight(tree_height)
            tree.setMaximumHeight(tree_height)
        try:
            root.setMinimumHeight(FILE_BROWSER_MIN_HEIGHT)
            if not self._embedded:
                with suppress(Exception):
                    self.window.setMinimumHeight(FILE_BROWSER_MIN_HEIGHT)
            hint = root.sizeHint()
            desired_height = max(FILE_BROWSER_MIN_HEIGHT, hint.height())
            root.setMinimumHeight(desired_height)
            if not self._embedded:
                self.window.resize(max(self.window.width(), hint.width()), desired_height)
                self._position_rclone_output()
        except Exception:
            pass

    def _apply_entry_state(
        self,
        item: Any,
        entry: BrowserEntry,
        remote: core.RemoteInfo,
        directory_icon: Any,
        file_icon: Any,
    ) -> None:
        state = self.backend.offline_content_state(remote.name, entry.path, is_dir=entry.is_dir)
        offline = bool(state and state.offline)
        protected_content = bool(state and state.protected)
        temporary_content = bool(state and state.temporary)
        partial_cache = bool(state and state.partial)
        changed = bool(self.backend.offline_changed(remote.name, entry.path, is_dir=entry.is_dir))
        working = self._working_kind_for_entry(remote.name, entry.path, is_dir=entry.is_dir)
        item.setIcon(0, self._entry_icon(
            entry,
            directory_icon=directory_icon,
            file_icon=file_icon,
            temporary_cached=temporary_content,
            protected_cached=protected_content,
            cache_partial=partial_cache,
            changed=changed,
            working=working,
        ))
        tooltip = ""
        if working == "download":
            tooltip = "Downloading local copy"
        elif working == "sync":
            tooltip = "Syncing local copy"
        elif changed:
            tooltip = "Local copy has unresolved changes"
        elif offline:
            tooltip = "Available offline as a local snapshot"
        elif protected_content and temporary_content:
            tooltip = "Contains saved offline files and temporary cached files"
        elif protected_content:
            tooltip = "Contains files saved for offline access"
        elif temporary_content:
            tooltip = "Cached local copy"
        item.setToolTip(0, tooltip)

    def _set_item_foreground(self, item: Any, color: str) -> None:
        qt_color = self.qt.QColor(color)
        brush_factory = getattr(self.qt, "QBrush", None)
        brush = brush_factory(qt_color) if brush_factory is not None else qt_color
        for column in range(self.tree.columnCount()):
            item.setForeground(column, brush)

    def _ensure_tree_selection(self) -> None:
        if self.tree.topLevelItemCount() <= 0:
            return
        current = self.tree.currentItem() or self.tree.topLevelItem(0)
        if current is None:
            return
        self.tree.setCurrentItem(current)
        selection_model = getattr(self.tree, "selectionModel", None)
        selection = selection_model() if callable(selection_model) else None
        if selection is not None:
            selection_model = getattr(self.qt, "QItemSelectionModel", None)
            selection_flags = getattr(selection_model, "SelectionFlag", None)
            if selection_flags is not None:
                flags = selection_flags.ClearAndSelect | selection_flags.Rows
                selection.select(self.tree.currentIndex(), flags)
            elif not self.tree.selectedItems():
                current.setSelected(True)
        elif not self.tree.selectedItems():
            current.setSelected(True)

    def _focus_relative_item(self, delta: int) -> None:
        count = self.tree.topLevelItemCount()
        if count <= 0:
            return
        current = self.tree.currentItem() or self.tree.topLevelItem(0)
        index = self.tree.indexOfTopLevelItem(current) if current is not None else 0
        index = max(index, 0)
        index = min(max(index + delta, 0), count - 1)
        self.tree.setCurrentItem(self.tree.topLevelItem(index))
        self._ensure_tree_selection()

    def _prefetch_related_folders(self, entries: list[BrowserEntry]) -> None:
        self._prefetch_parent_folder()
        self._prefetch_child_folders(entries)

    def _prefetch_parent_folder(self) -> None:
        remote = self.remote
        if remote is None or not self.path:
            return
        parent = parent_browser_path(self.path)
        key = (remote.name, parent)
        if key in self._folder_cache or key in self._loads_pending:
            return
        self._load_folder(remote, parent)

    def _prefetch_child_folders(self, entries: list[BrowserEntry]) -> None:
        remote = self.remote
        if remote is None:
            return
        scheduled = 0
        for entry in entries:
            if not entry.is_dir:
                continue
            key = (remote.name, entry.path)
            if key in self._folder_cache or key in self._loads_pending:
                continue
            self._load_folder(remote, entry.path)
            scheduled += 1
            if scheduled >= CHILD_FOLDER_PREFETCH_LIMIT:
                return

    def _selected_entries(self) -> list[BrowserEntry]:
        result: list[BrowserEntry] = []
        for item in self.tree.selectedItems():
            entry = item.data(0, self.qt.Qt.ItemDataRole.UserRole)
            if isinstance(entry, BrowserEntry):
                result.append(entry)
        return result

    def selected_transfer_items(self) -> list[TransferItem]:
        if self.remote is None:
            return []
        return [
            TransferItem(self.remote.name, entry.path, entry.name, entry.is_dir)
            for entry in self._selected_entries()
        ]

    def copy_selected(self) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        items = self.selected_transfer_items()
        if items:
            self.clipboard = (items, False)
            self.status.setText(f"Copied {len(items)} item{'s' if len(items) != 1 else ''}")
            self._update_actions()

    def cut_selected(self) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        items = self.selected_transfer_items()
        if items:
            self.clipboard = (items, True)
            self.status.setText(f"Cut {len(items)} item{'s' if len(items) != 1 else ''}")
            self._update_actions()

    def paste(self) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        if self.clipboard is not None:
            items, move = self.clipboard
            self._transfer(items, move=move)

    def delete_selected(self) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        entries = self._selected_entries()
        if not entries or self.remote is None or self._operation_pending:
            return
        names = ", ".join(entry.name for entry in entries[:3])
        if len(entries) > 3:
            names += f", and {len(entries) - 3} more"
        reply = self.qt.QMessageBox.question(
            self.window,
            "Delete from cloud storage?",
            f"Permanently delete {names}?\n\nThis cannot be undone by Mountlet.",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.No,
        )
        if reply != self.qt.QMessageBox.StandardButton.Yes:
            return
        remote = self.remote
        self._run_operation("Deleting…", lambda: self.backend.delete_entries(remote, entries))

    def create_folder(self) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        if self.remote is None or self._operation_pending:
            return
        name, accepted = self.qt.QInputDialog.getText(self.window, "New folder", "Folder name")
        if not accepted or not name.strip():
            return
        remote, parent = self.remote, self.path
        self._run_operation("Creating folder…", lambda: self.backend.create_folder(remote, parent, name.strip()))

    def _show_tree_menu(self, point: Any) -> None:
        item = self.tree.itemAt(point)
        if item is None:
            self._show_folder_menu(point, source=self.tree.viewport())
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        entry = item.data(0, self.qt.Qt.ItemDataRole.UserRole)
        if not isinstance(entry, BrowserEntry):
            return
        menu = self.qt.QMenu(self.window)
        self._menu_action(menu, "Open", lambda selected=entry: self._open_entry(selected))
        if self._can_replace_original_with_copy(entry):
            self._menu_action(
                menu,
                "Replace original with this copy",
                lambda selected=entry: self._replace_original_with_copy(selected),
            )
        if entry.is_dir:
            can_open_folder = bool(
                self.remote
                and (core.is_mounted(self.remote) or self.backend.has_cached_content(self.remote.name, entry.path, is_dir=True))
            )
            self._menu_action(
                menu,
                f"Open in {self._file_manager_label()}",
                lambda path=entry.path: self._open_external_folder(path),
                enabled=can_open_folder,
            )
        menu.addSeparator()
        edits_enabled = self._edits_enabled()
        self._menu_action(menu, "Copy", self.copy_selected, enabled=edits_enabled)
        self._menu_action(menu, "Cut", self.cut_selected, enabled=edits_enabled)
        self._add_offline_menu_actions(menu, entry)
        self._menu_action(
            menu,
            "Sync now",
            lambda selected=entry: self._sync_cached_path(selected.path, selected.is_dir),
            enabled=self._can_sync_cache(entry),
        )
        if self._can_free_cache(entry):
            self._menu_action(menu, "Free cached copy", lambda selected=entry: self._free_cache(selected.path))
        self._menu_action(menu, "Delete", self.delete_selected, enabled=edits_enabled)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _show_folder_menu(self, point: Any, *, source: Any | None = None) -> None:
        menu = self.qt.QMenu(self.window)
        self._menu_action(
            menu,
            f"Open in {self._file_manager_label()}",
            lambda: self._open_external_folder(self.path),
            enabled=bool(self.remote and (core.is_mounted(self.remote) or self._current_cached_folder_available())),
        )
        edits_enabled = self._edits_enabled()
        self._menu_action(
            menu,
            "Paste",
            self.paste,
            enabled=edits_enabled and self.clipboard is not None and not self._operation_pending,
        )
        self._menu_action(menu, "New folder", self.create_folder, enabled=edits_enabled and not self._operation_pending)
        menu.addSeparator()
        self._menu_action(menu, "Sync now", lambda: self._sync_cached_path(self.path, True), enabled=self._can_sync_folder())
        self._menu_action(menu, "Free resolved cache in this folder", lambda: self._free_cache(self.path), enabled=bool(self.remote))
        self._menu_action(menu, "Free all resolved cache", self._free_all_resolved_cache)
        origin = source or self.path_field
        menu.exec(origin.mapToGlobal(point))

    def _menu_action(self, menu: Any, label: str, callback: Callable[[], None], *, enabled: bool = True) -> Any:
        action = menu.addAction(label)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _checked=False: callback())
        return action

    def _can_replace_original_with_copy(self, entry: BrowserEntry) -> bool:
        return bool(
            self.remote
            and not entry.is_dir
            and core.is_mounted(self.remote)
            and self.backend.original_path_for_conflict_copy(entry.path)
        )

    def _can_free_cache(self, entry: BrowserEntry) -> bool:
        remote = getattr(self, "remote", None)
        return bool(
            remote
            and self.backend.is_cached(remote.name, entry.path, is_dir=entry.is_dir)
            and not self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir)
            and not self.backend.has_offline_content(remote.name, entry.path, is_dir=entry.is_dir)
            and not self.backend.offline_changed(remote.name, entry.path, is_dir=entry.is_dir)
        )

    def _can_sync_cache(self, entry: BrowserEntry) -> bool:
        remote = getattr(self, "remote", None)
        return bool(
            remote
            and self._sync_paths is not None
            and not self._operation_pending
            and not self._entry_has_operation(remote.name, entry.path, is_dir=entry.is_dir, kind="sync")
            and self.backend.has_cached_content(remote.name, entry.path, is_dir=entry.is_dir)
        )

    def _can_sync_folder(self) -> bool:
        remote = getattr(self, "remote", None)
        return bool(
            remote
            and self._sync_paths is not None
            and not self._operation_pending
            and not self._entry_has_operation(remote.name, self.path, is_dir=True, kind="sync")
            and self.backend.managed_file_paths_under(remote.name, self.path)
        )

    def _sync_cached_path(self, path: str, is_dir: bool) -> None:
        if self.remote is None or self._sync_paths is None:
            return
        affected = self.backend.managed_record_paths_under(self.remote.name, path)
        if not affected:
            return
        self._start_working_paths(self.remote.name, affected, "sync")
        self._sync_paths(self.remote, [(path, is_dir)])

    def start_sync(self, remote_name: str, paths: list[str]) -> None:
        self._start_working_paths(remote_name, paths, "sync")

    def sync_selected(self) -> None:
        remote = getattr(self, "remote", None)
        entries = self._selected_entries()
        if remote is None or self._sync_paths is None or not entries or self._operation_pending:
            return
        paths = [(entry.path, entry.is_dir) for entry in entries]
        affected = self._managed_record_paths_for_items(remote.name, paths)
        if not affected:
            return
        self._start_working_paths(remote.name, affected, "sync")
        self._sync_paths(remote, paths)

    def sync_remote(self) -> None:
        remote = getattr(self, "remote", None)
        if remote is None or self._sync_paths is None or self._operation_pending:
            return
        affected = self.backend.managed_record_paths(remote.name)
        if not affected:
            return
        self._start_working_paths(remote.name, affected, "sync")
        self._sync_paths(remote, [("", True)])

    def _managed_record_paths_for_items(self, remote_name: str, items: list[tuple[str, bool]]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for path, _is_dir in items:
            for record_path in self.backend.managed_record_paths_under(remote_name, path):
                if record_path in seen:
                    continue
                seen.add(record_path)
                paths.append(record_path)
        return paths

    def _add_offline_menu_actions(self, menu: Any, entry: BrowserEntry) -> None:
        remote = getattr(self, "remote", None)
        if remote is None:
            return
        partial = self.backend.is_partially_offline(remote.name, entry.path, is_dir=entry.is_dir)
        offline = self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir)
        downloading = self._entry_has_operation(
            remote.name,
            entry.path,
            is_dir=entry.is_dir,
            kind="download",
        )
        remove_pending = self._offline_remove_pending(remote.name, [entry.path])
        available = not self._operation_pending and not downloading
        has_local_content = self.backend.has_cached_content(remote.name, entry.path, is_dir=entry.is_dir)
        if partial:
            self._menu_action(
                menu,
                "Make available offline",
                self.toggle_offline,
                enabled=available,
            )
            self._menu_action(
                menu,
                "Remove offline copies",
                lambda selected=entry: self._remove_offline_copy(selected.path),
                enabled=not self._operation_pending and not remove_pending,
            )
            return
        if downloading:
            self._menu_action(
                menu,
                "Remove offline copies",
                lambda selected=entry: self._remove_offline_copy(selected.path),
                enabled=not self._operation_pending and not remove_pending,
            )
            return
        self._menu_action(
            menu,
            "Remove offline copy" if offline else "Make available offline",
            self.toggle_offline,
            enabled=(available or (offline and has_local_content and not self._operation_pending)) and not remove_pending,
        )

    def _remove_offline_copy(self, path: str) -> None:
        if self.remote is None:
            return
        remote_name = self.remote.name
        self._queue_remove_offline_job(remote_name, path)

    def _queue_remove_offline_job(self, remote_name: str, path: str) -> None:
        normalized = normalize_browser_path(path)
        paths = [normalized]
        if self._offline_remove_pending(remote_name, paths):
            return

        def action() -> None:
            self.backend.remove_offline(remote_name, normalized)

        if self._offline_download_pending(remote_name, paths):
            self._offline_job_queue.append((remote_name, "Removing local copies…", action, paths, "remove", None))
            self.status.setText("Queued removal after download…")
            self._update_actions()
            return
        self._start_local_remove_job(remote_name, paths, action)

    def _start_local_remove_job(self, remote_name: str, paths: list[str], action: Callable[[], object]) -> None:
        self._start_working_paths(remote_name, paths, "remove")
        self.status.setText("Removing local copies…")
        self._update_actions()

        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self._bridge.offline_job_finished.emit(remote_name, paths, "remove", False, str(exc))
                return
            self._bridge.offline_job_finished.emit(remote_name, paths, "remove", True, "")

        threading.Thread(target=worker, daemon=True).start()

    def _free_cache(self, path: str) -> None:
        if self.remote is None:
            return
        remote_name = self.remote.name
        self._run_operation("Freeing cache…", lambda: self.backend.free_cache(remote_name, path))

    def _free_all_resolved_cache(self) -> None:
        self._run_operation("Freeing resolved cache…", self.backend.free_all_resolved_cache)

    def _replace_original_with_copy(self, entry: BrowserEntry) -> None:
        if self.remote is None or self._operation_pending:
            return
        remote = self.remote
        parent = parent_browser_path(entry.path)
        self._run_operation(
            "Replacing original…",
            lambda: self.backend.replace_original_with_conflict_copy(remote, entry.path),
            invalidate_keys={(remote.name, parent)},
        )

    def _file_manager_label(self) -> str:
        try:
            return self._file_manager_name()
        except Exception:
            return "file manager"

    def accept_drop(self, payload: bytes, *, move: bool = False) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        try:
            values = json.loads(payload.decode("utf-8"))
            items = [TransferItem(**value) for value in values]
        except (TypeError, ValueError, json.JSONDecodeError):
            self._notify("File transfer", "The dragged files could not be read.", False)
            return
        self._transfer(items, move=move)

    def accept_local_paths(self, paths: list[Path]) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        if not paths or self.remote is None or self._operation_pending:
            return
        remote, destination_path = self.remote, self.path
        invalidate = {(remote.name, destination_path)}
        self._run_operation(
            f"Uploading {len(paths)} item{'s' if len(paths) != 1 else ''}…",
            lambda: self.backend.copy_local_paths(paths, remote, destination_path),
            invalidate_keys=invalidate,
        )

    def _drop_event_supported(self, event: Any) -> bool:
        if not self._edits_enabled() or self.remote is None or self._operation_pending:
            return False
        mime = event.mimeData()
        return bool(mime.hasFormat(MIME_TYPE) or self._local_paths_from_mime(mime))

    def _handle_drop_event(self, event: Any) -> bool:
        if not self._drop_event_supported(event):
            return False
        mime = event.mimeData()
        if mime.hasFormat(MIME_TYPE):
            move = event.proposedAction() == self.qt.Qt.DropAction.MoveAction
            self.accept_drop(bytes(mime.data(MIME_TYPE)), move=move)
            return True
        local_paths = self._local_paths_from_mime(mime)
        if not local_paths:
            return False
        self.accept_local_paths(local_paths)
        return True

    def _local_paths_from_mime(self, mime: Any) -> list[Path]:
        if not mime.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime.urls():
            with suppress(Exception):
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.exists():
                    paths.append(path)
        return paths

    def _transfer(self, items: list[TransferItem], *, move: bool) -> None:
        if not self._edits_enabled():
            self._edit_disabled()
            return
        if not items or self.remote is None or self._operation_pending:
            return
        destination, destination_path = self.remote, self.path
        remotes = {remote.name: remote for remote in self._remotes()}
        verb = "Moving" if move else "Copying"
        invalidate = {(destination.name, destination_path)}
        if move:
            invalidate.update((item.remote_name, parent_browser_path(item.path)) for item in items)
        self._run_operation(
            f"{verb} {len(items)} item{'s' if len(items) != 1 else ''}…",
            lambda: self.backend.transfer(items, remotes, destination, destination_path, move=move),
            clear_clipboard=move,
            invalidate_keys=invalidate,
        )

    def toggle_offline(self) -> None:
        entries = self._selected_entries()
        if not entries or self.remote is None or self._operation_pending:
            return
        remote = self.remote
        if any(self._entry_has_operation(remote.name, entry.path, is_dir=entry.is_dir, kind="download") for entry in entries):
            for entry in entries:
                self._queue_remove_offline_job(remote.name, entry.path)
            return
        all_offline = all(self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir) for entry in entries)
        if all_offline:
            def action() -> None:
                for entry in entries:
                    self.backend.remove_offline(remote.name, entry.path)

            message = "Removing local copies…"
            working_paths = None
            working_kind = ""
        else:
            def action() -> None:
                for entry in entries:
                    self.backend.make_offline(remote, entry)

            def discover_paths() -> list[BrowserEntry]:
                discovered: list[BrowserEntry] = []
                seen: set[str] = set()
                for entry in entries:
                    path = normalize_browser_path(entry.path)
                    if path and path not in seen:
                        seen.add(path)
                        discovered.append(entry)
                    for discovered_entry in self.backend.list_entries_recursive(remote, entry):
                        path = normalize_browser_path(discovered_entry.path)
                        if path and path not in seen:
                            seen.add(path)
                            discovered.append(discovered_entry)
                return discovered

            message = "Downloading for offline use…"
            working_paths = []
            working_kind = "download"
        self._queue_offline_job(
            message,
            action,
            working_paths=working_paths,
            working_kind=working_kind,
            discover_paths=discover_paths if not all_offline else None,
        )

    def _offline_action_label(self) -> str:
        entries = self._selected_entries()
        remote = getattr(self, "remote", None)
        if not entries or remote is None:
            return "Make available offline"
        if any(self._entry_has_operation(remote.name, entry.path, is_dir=entry.is_dir, kind="download") for entry in entries):
            return "Remove offline copy"
        all_offline = all(self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir) for entry in entries)
        return "Remove offline copy" if all_offline else "Make available offline"

    def _run_operation(
        self,
        message: str,
        action: Callable[[], object],
        *,
        clear_clipboard: bool = False,
        invalidate_keys: set[tuple[str, str]] | None = None,
        working_paths: list[str] | None = None,
        working_kind: str = "",
    ) -> None:
        self._operation_pending = True
        current_key = (self.remote.name, self.path) if self.remote is not None else None
        self._operation_cache_keys = set(invalidate_keys or ())
        if current_key is not None:
            self._operation_cache_keys.add(current_key)
        working_remote = self.remote.name if self.remote is not None else ""
        if working_remote and working_paths and working_kind:
            self._start_working_paths(working_remote, working_paths, working_kind)
        self.status.setText(message)
        self._update_actions()

        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self._bridge.operation_finished.emit(False, str(exc))
                return
            if clear_clipboard:
                self.clipboard = None
            self._bridge.operation_finished.emit(True, "")

        threading.Thread(target=worker, daemon=True).start()

    def _queue_offline_job(
        self,
        message: str,
        action: Callable[[], object],
        *,
        working_paths: list[str] | None = None,
        working_kind: str = "",
        discover_paths: Callable[[], list[BrowserEntry]] | None = None,
    ) -> None:
        remote_name = self.remote.name if self.remote is not None else ""
        normalized_paths = [normalize_browser_path(path) for path in (working_paths or [])]
        mark_immediately = working_kind != "remove"
        if mark_immediately and remote_name and normalized_paths and working_kind:
            self._start_working_paths(remote_name, normalized_paths, working_kind)
        self._offline_job_queue.append((remote_name, message, action, normalized_paths, working_kind, discover_paths))
        if self._offline_jobs_running >= OFFLINE_JOB_CONCURRENCY:
            self.status.setText("Queued offline file work…")
            self._update_actions()
        self._start_offline_jobs()

    def _start_offline_jobs(self) -> None:
        while self._offline_job_queue:
            next_index = self._next_runnable_offline_job_index(
                allow_download=self._offline_jobs_running < OFFLINE_JOB_CONCURRENCY
            )
            if next_index is None:
                return
            remote_name, message, action, working_paths, working_kind, discover_paths = self._offline_job_queue.pop(next_index)
            if working_kind == "remove":
                self._start_local_remove_job(remote_name, working_paths, action)
                continue
            self._offline_jobs_running += 1
            self.status.setText(message)
            self._update_actions()

            def worker(
                job_remote_name: str = remote_name,
                job_paths: list[str] = list(working_paths),
                job_kind: str = working_kind,
                job_discover_paths: Callable[[], list[BrowserEntry]] | None = discover_paths,
                job_action: Callable[[], object] = action,
            ) -> None:
                discovered_paths: list[str] = list(job_paths)
                discovery_error = ""

                def discover() -> None:
                    nonlocal discovered_paths, discovery_error
                    if job_discover_paths is None:
                        return
                    try:
                        discovered_entries = job_discover_paths()
                    except Exception as exc:
                        discovery_error = str(exc)
                        return
                    discovered_paths = [
                        normalize_browser_path(entry.path)
                        for entry in discovered_entries
                        if not entry.is_dir and normalize_browser_path(entry.path)
                    ]
                    self._bridge.offline_job_paths_ready.emit(job_remote_name, discovered_entries, job_kind)

                discovery_thread: threading.Thread | None = None
                if job_discover_paths is not None:
                    discovery_thread = threading.Thread(target=discover, daemon=True)
                    discovery_thread.start()
                try:
                    job_action()
                except Exception as exc:
                    if discovery_thread is not None:
                        discovery_thread.join()
                    self._bridge.offline_job_finished.emit(job_remote_name, discovered_paths, job_kind, False, str(exc))
                    return
                if discovery_thread is not None:
                    discovery_thread.join()
                if discovery_error and not discovered_paths:
                    self._bridge.offline_job_finished.emit(job_remote_name, discovered_paths, job_kind, False, discovery_error)
                    return
                self._bridge.offline_job_finished.emit(job_remote_name, discovered_paths, job_kind, True, "")

            threading.Thread(target=worker, daemon=True).start()

    def _next_runnable_offline_job_index(self, *, allow_download: bool = True) -> int | None:
        for index, (remote_name, _message, _action, paths, kind, _discover_paths) in enumerate(self._offline_job_queue):
            if kind != "remove" and not allow_download:
                continue
            if kind == "remove" and self._operation_paths_overlap(remote_name, paths, "download"):
                continue
            return index
        return None

    def _offline_job_paths_ready(self, remote_name: str, paths: object, kind: str) -> None:
        if not isinstance(paths, list):
            return
        if all(isinstance(path, BrowserEntry) for path in paths):
            entries = [path for path in paths if isinstance(path, BrowserEntry)]
            self._cache_recursive_entries(remote_name, entries)
            normalized_paths = [normalize_browser_path(entry.path) for entry in entries if not entry.is_dir]
        else:
            normalized_paths = [normalize_browser_path(str(path)) for path in paths if normalize_browser_path(str(path))]
        if remote_name and normalized_paths and kind:
            self._start_working_paths(remote_name, normalized_paths, kind)
            self._refresh_download_working_paths(remote_name)
            self._refresh_entry_icons()

    def _cache_recursive_entries(self, remote_name: str, entries: list[BrowserEntry]) -> None:
        by_parent: dict[str, dict[str, BrowserEntry]] = {}
        directory_paths = {
            normalize_browser_path(entry.path)
            for entry in entries
            if entry.is_dir and normalize_browser_path(entry.path)
        }
        for directory_path in directory_paths:
            by_parent.setdefault(directory_path, {})
        for entry in entries:
            normalized = normalize_browser_path(entry.path)
            if not normalized:
                continue
            parent = parent_browser_path(normalized)
            by_parent.setdefault(parent, {})[normalized] = BrowserEntry(
                name=entry.name,
                path=normalized,
                is_dir=entry.is_dir,
                size=entry.size,
                modified=entry.modified,
            )
            current_parent = parent
            while current_parent:
                ancestor_parent = parent_browser_path(current_parent)
                by_parent.setdefault(ancestor_parent, {}).setdefault(
                    current_parent,
                    BrowserEntry(
                        name=current_parent.rsplit("/", 1)[-1],
                        path=current_parent,
                        is_dir=True,
                    ),
                )
                current_parent = ancestor_parent
        for parent, children in by_parent.items():
            existing = self._folder_cache.get((remote_name, parent))
            if existing is not None:
                merged = {entry.path: entry for entry in existing}
                merged.update(children)
                children = merged
            elif parent not in directory_paths:
                continue
            self._folder_cache[(remote_name, parent)] = sorted(
                children.values(),
                key=lambda entry: (not entry.is_dir, entry.name.casefold()),
            )

    def _offline_job_finished(
        self,
        remote_name: str,
        paths: object,
        kind: str,
        success: bool,
        message: str,
    ) -> None:
        if kind != "remove":
            self._offline_jobs_running = max(0, self._offline_jobs_running - 1)
        if kind == "download" and remote_name:
            self._refresh_download_working_paths(remote_name)
        if remote_name and isinstance(paths, list):
            self._finish_working_paths(remote_name, [str(path) for path in paths], kind)
        if kind == "remove":
            self._folder_cache = {
                key: entries for key, entries in getattr(self, "_folder_cache", {}).items() if key[0] != remote_name
            }
        if not success:
            self._notify("Offline files", message or "The operation failed.", False)
        self._local_files_changed()
        self.refresh(force=True)
        self._start_offline_jobs()

    def _operation_finished(self, success: bool, message: str) -> None:
        self._operation_pending = False
        remote_name = self.remote.name if self.remote is not None else ""
        if remote_name:
            self._finish_working_remote(remote_name, "download")
        changed_keys = self._operation_cache_keys
        self._operation_cache_keys = set()
        for changed_key in changed_keys:
            self._folder_cache.pop(changed_key, None)
        if not success:
            self._notify("File operation", message or "The operation failed.", False)
        self._local_files_changed()
        current_key = (self.remote.name, self.path) if self.remote is not None else None
        self.refresh(force=current_key in changed_keys)

    def _selection_changed(self) -> None:
        self._update_actions()

    def _update_actions(self) -> None:
        selected = bool(self._selected_entries()) if hasattr(self, "tree") else False
        edits_enabled = self._edits_enabled()
        operation_pending = bool(getattr(self, "_operation_pending", False))
        self.tree.setDragEnabled(edits_enabled and selected)
        edit_action_enabled = selected and edits_enabled and not operation_pending
        edit_disabled_reason = self._edit_action_disabled_reason(
            selected=selected,
            edits_enabled=edits_enabled,
            operation_pending=operation_pending,
        )
        for button, enabled, tooltip in (
            (getattr(self, "copy_button", None), edit_action_enabled, "Copy selected items"),
            (getattr(self, "cut_button", None), edit_action_enabled, "Cut selected items"),
            (getattr(self, "delete_button", None), edit_action_enabled, "Delete selected items"),
        ):
            if button is None:
                continue
            self._set_action_button_state(button, enabled, tooltip if edit_disabled_reason is None else edit_disabled_reason)
        paste_button = getattr(self, "paste_button", None)
        if paste_button is not None:
            paste_enabled = edits_enabled and self.clipboard is not None and not operation_pending
            paste_disabled_reason = self._paste_action_disabled_reason(
                edits_enabled=edits_enabled,
                operation_pending=operation_pending,
            )
            self._set_action_button_state(
                paste_button,
                paste_enabled,
                "Paste into this folder" if paste_disabled_reason is None else paste_disabled_reason,
            )
        remote_sync_button = getattr(self, "remote_sync_button", None)
        if remote_sync_button is not None:
            remote_sync_enabled = bool(
                self.remote
                and self._sync_paths is not None
                and not operation_pending
                and not self._remote_has_operation(self.remote.name, "sync")
                and self.backend.managed_record_paths(self.remote.name)
            )
            self._set_action_button_state(
                remote_sync_button,
                remote_sync_enabled,
                "Sync cached files for this remote" if remote_sync_enabled else "No cached or offline files to sync",
            )
        selection_sync_button = getattr(self, "selection_sync_button", None)
        if selection_sync_button is not None:
            selected_entries = self._selected_entries()
            selection_sync_enabled = bool(
                self.remote
                and self._sync_paths is not None
                and selected_entries
                and not operation_pending
                and any(
                    self.backend.has_cached_content(self.remote.name, entry.path, is_dir=entry.is_dir)
                    for entry in selected_entries
                )
                and not any(
                    self._entry_has_operation(self.remote.name, entry.path, is_dir=entry.is_dir, kind="sync")
                    for entry in selected_entries
                )
            )
            self._set_action_button_state(
                selection_sync_button,
                selection_sync_enabled,
                "Sync selected local copies" if selection_sync_enabled else "Select cached or offline files first",
            )
        offline_enabled = selected and not operation_pending
        remote = getattr(self, "remote", None)
        selected_entries = self._selected_entries() if selected and remote is not None else []
        selected_downloading = False
        selected_remove_pending = False
        if remote is not None:
            selected_downloading = any(
                self._entry_has_operation(remote.name, entry.path, is_dir=entry.is_dir, kind="download")
                for entry in selected_entries
            )
            selected_remove_pending = any(
                self._offline_remove_pending(remote.name, [entry.path])
                for entry in selected_entries
            )
        if offline_enabled and selected_remove_pending:
            offline_enabled = False
        if offline_enabled and remote is not None and not selected_downloading:
            offline_enabled = not any(
                self._entry_has_operation(remote.name, entry.path, is_dir=entry.is_dir, kind="download")
                for entry in selected_entries
            )
        self._set_action_button_state(
            self.offline_button,
            offline_enabled,
            "Select files or folders to make them available offline",
        )
        self._update_snapshot_button_icon(offline_enabled)
        remove_offline = selected and self._offline_action_label() == "Remove offline copy"
        selected_changed = self._selected_offline_changed()
        self.offline_button.setText("")
        set_badge(self.offline_button, remove_offline, OFFLINE_SAVED_BADGE_COLOR)
        if operation_pending:
            self.offline_button.setToolTip("Wait for the current file operation to finish")
        elif selected_remove_pending:
            self.offline_button.setToolTip("Removal is already queued")
        elif selected:
            self.offline_button.setToolTip(
                "Queue removal after the current download finishes"
                if selected_downloading
                else
                "Saved offline with local changes; click to remove the local snapshot"
                if selected_changed
                else "Saved offline; click to remove the local snapshot"
                if remove_offline
                else "Save a local snapshot for offline access"
            )
        else:
            self.offline_button.setToolTip("Select files or folders to make them available offline")
        self._update_open_folder_button()

    def _set_action_button_state(self, button: Any, enabled: bool, tooltip: str) -> None:
        button.setEnabled(enabled)
        button.setToolTip(tooltip)
        with suppress(Exception):
            button.setStyleSheet("")

    def _edit_action_disabled_reason(
        self,
        *,
        selected: bool,
        edits_enabled: bool,
        operation_pending: bool,
    ) -> str | None:
        if not selected:
            return "Select files or folders first"
        if not edits_enabled:
            return "Enable integrated file edits in App settings first"
        if operation_pending:
            return "Wait for the current file operation to finish"
        return None

    def _paste_action_disabled_reason(
        self,
        *,
        edits_enabled: bool,
        operation_pending: bool,
    ) -> str | None:
        if not edits_enabled:
            return "Enable integrated file edits in App settings first"
        if self.clipboard is None:
            return "Copy or cut files first"
        if operation_pending:
            return "Wait for the current file operation to finish"
        return None

    def _update_snapshot_button_icon(self, enabled: bool) -> None:
        icon = getattr(self, "_offline_base_icon", None)
        if icon is None:
            return
        if enabled:
            self.offline_button.setIcon(icon)
            return
        self.offline_button.setIcon(self._dimmed_icon(icon))

    def _dimmed_icon(self, icon: Any) -> Any:
        try:
            size = self.qt.QSize(22, 22)
            source = icon.pixmap(size)
            pixmap_type = getattr(self.qt, "QPixmap", None)
            painter_type = getattr(self.qt, "QPainter", None)
            global_color = getattr(getattr(self.qt, "Qt", object), "GlobalColor", object)
            transparent = getattr(global_color, "transparent", None)
            if transparent is None:
                return self.qt.QIcon(icon.pixmap(size, self.qt.QIcon.Mode.Disabled))
            if pixmap_type is None or painter_type is None:
                return self.qt.QIcon(icon.pixmap(size, self.qt.QIcon.Mode.Disabled))
            dimmed = pixmap_type(size)
            dimmed.fill(transparent)
            painter = painter_type(dimmed)
            painter.setOpacity(0.28)
            painter.drawPixmap(0, 0, source)
            painter.end()
            return self.qt.QIcon(dimmed)
        except Exception:
            return icon

    def _selected_offline_changed(self) -> bool:
        remote = getattr(self, "remote", None)
        if remote is None:
            return False
        return any(
            self.backend.offline_changed(remote.name, entry.path, is_dir=entry.is_dir)
            for entry in self._selected_entries()
        )

    def refresh_mount_state(self, remote_name: str) -> None:
        remote = getattr(self, "remote", None)
        if remote is None or remote.name != remote_name:
            return
        self._update_mount_switch()
        self._display_entries(list(getattr(self, "entries", [])))

    def finish_sync(self, remote_name: str) -> None:
        self._finish_working_remote(remote_name, "sync")

    def _finish_working_remote(self, remote_name: str, kind: str) -> None:
        if not hasattr(self, "_working_paths"):
            return
        changed = False
        for key in list(self._working_paths):
            if key[0] == remote_name and self._working_paths.get(key) == kind:
                self._working_paths.pop(key, None)
                changed = True
        if changed:
            self._refresh_entry_icons()

    def _update_open_folder_button(self) -> None:
        button = getattr(self, "open_folder_button", None)
        if button is None:
            return
        remote = getattr(self, "remote", None)
        cached_available = self._current_cached_folder_available()
        if remote is not None and not core.is_mounted(remote) and cached_available:
            button.setStyleSheet("")
            if self.backend.has_offline_content(remote.name, self.path, is_dir=True):
                button.setToolTip(f"Open the offline snapshot folder in {self._file_manager_label()}")
            else:
                button.setToolTip(f"Open the local cache folder in {self._file_manager_label()}")
        else:
            button.setStyleSheet("")
            button.setToolTip(f"Open this folder in {self._file_manager_label()}")

    def _current_cached_folder_available(self) -> bool:
        remote = getattr(self, "remote", None)
        if remote is None:
            return False
        offline = self.backend.offline_path(remote.name, self.path)
        return offline.is_dir() or self.backend.has_cached_content(remote.name, self.path, is_dir=True)

    def _edits_enabled(self) -> bool:
        try:
            return bool(load_app_settings().integrated_file_edits)
        except Exception:
            return False

    def _edit_disabled(self) -> bool:
        self._notify(
            "Mountlet Files",
            "Integrated file edits are disabled. Enable them in App settings, or use the system file manager.",
            False,
        )
        return True

    def _open_item(self, item: Any, _column: int = 0) -> None:
        try:
            entry = item.data(0, self.qt.Qt.ItemDataRole.UserRole)
        except RuntimeError:
            return
        self._open_entry(entry)

    def _open_entry(self, entry: object) -> None:
        if not isinstance(entry, BrowserEntry) or self.remote is None:
            return
        if entry.is_dir:
            self.path = entry.path
            self.backend.remember_path(self.remote.name, self.path)
            self.refresh()
            return
        if core.is_mounted(self.remote):
            local = Path(self.remote.mount_path).joinpath(*entry.path.split("/"))
            self._open_local_file(local)
            return
        self._open_cached_file(entry)

    def _open_cached_file(self, entry: BrowserEntry) -> None:
        remote = self.remote
        if remote is None:
            return
        cached = self.backend.offline_path(remote.name, entry.path)
        if cached.is_file():
            self._open_local_file(self.backend.prepare_offline_open(remote.name, entry.path))
            return
        self._start_working_paths(remote.name, [entry.path], "download")
        self.status.setText("Downloading cached copy…")
        self._update_actions()

        def worker() -> None:
            try:
                local = self.backend.cache_file(remote, entry)
            except Exception as exc:
                self._bridge.cached_file_ready.emit(remote.name, entry.path, None, str(exc))
                return
            self._bridge.cached_file_ready.emit(remote.name, entry.path, local, "")

        threading.Thread(target=worker, daemon=True).start()

    def _cached_file_ready(self, remote_name: str, path: str, local: object, error: str) -> None:
        self._finish_working_paths(remote_name, [path])
        if error:
            self._notify("Open file", error, False)
            self.status.setText(error)
            self._update_actions()
            return
        if self.remote is None or self.remote.name != remote_name:
            return
        self._folder_cache.pop((remote_name, parent_browser_path(path)), None)
        if isinstance(local, Path):
            self._open_local_file(local)
        self._local_files_changed()
        self.refresh(force=True)

    def _open_local_file(self, path: Path) -> None:
        if self._open_file and self._open_file(path):
            return
        if self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))):
            return
        self._notify("Open file", "Could not open this file.", False)

    def go_up(self) -> None:
        if self.remote is None:
            return
        previous_path = self.path
        self.path = parent_browser_path(self.path)
        self._pending_select_path = previous_path
        self.backend.remember_path(self.remote.name, self.path)
        self.refresh()

    def go_root(self) -> None:
        if self.remote is None:
            return
        self.path = ""
        self.backend.remember_path(self.remote.name, self.path)
        self.refresh()

    def _open_current_mount(self) -> None:
        self._open_external_folder(self.path)

    def _open_external_folder(self, path: str) -> None:
        if self.remote is None:
            return
        if not core.is_mounted(self.remote):
            offline = self.backend.offline_path(self.remote.name, path)
            if offline.is_dir():
                local_folder = self.backend.prepare_offline_open(self.remote.name, path)
                if self._open_local_folder and self._open_local_folder(local_folder):
                    return
                if self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(local_folder))):
                    return
            self._notify(
                "Open folder",
                "Open or cache a file in this folder before opening it in the system file manager.",
                False,
            )
            return
        self._open_mount(self.remote, path)

    def _position(self, row: Any) -> None:
        try:
            main = self.main_window.frameGeometry()
            row_top = row.mapToGlobal(self.qt.QPoint(0, 0)).y()
            screen = self.main_window.screen() or self.qt.QApplication.primaryScreen()
            available = screen.availableGeometry()
            position = cascade_position(
                (main.x(), main.y(), main.width(), main.height()),
                row_top,
                (available.x(), available.y(), available.width(), available.height()),
                (self.window.width(), self.window.height()),
            )
            self._side = "left" if position[0] < main.x() else "right"
            self.window.move(*position)
        except Exception:
            return

    def side(self) -> str:
        return self._side


__all__ = ["MIME_TYPE", "CompactCloudBrowser", "cascade_position"]
