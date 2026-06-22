from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from . import core
from .cloud_browser import (
    BrowserEntry,
    CloudBrowserBackend,
    TransferItem,
    format_file_size,
    parent_browser_path,
)


MIME_TYPE = "application/x-mountlet-remote-files"


def cascade_position(
    main_rect: tuple[int, int, int, int],
    row_y: int,
    available: tuple[int, int, int, int],
    browser_size: tuple[int, int],
) -> tuple[int, int]:
    main_x, _main_y, main_width, _main_height = main_rect
    left, top, available_width, available_height = available
    width, height = browser_size
    right_edge = left + available_width
    main_right = main_x + main_width
    right_space = right_edge - main_right
    left_space = main_x - left
    if right_space >= width + 8 or right_space >= left_space:
        x = main_right + 8
    else:
        x = main_x - width - 8
    x = min(max(x, left), max(left, right_edge - width))
    y = min(max(row_y, top), max(top, top + available_height - height))
    return x, y


class CompactCloudBrowser:
    def __init__(
        self,
        qt: Any,
        main_window: Any,
        *,
        remotes: Callable[[], list[core.RemoteInfo]],
        notify: Callable[[str, str, bool], None],
        open_mount: Callable[[core.RemoteInfo], None],
    ) -> None:
        self.qt = qt
        self.main_window = main_window
        self._remotes = remotes
        self._notify = notify
        self._open_mount = open_mount
        self.backend = CloudBrowserBackend()
        self.remote: core.RemoteInfo | None = None
        self.path = ""
        self.entries: list[BrowserEntry] = []
        self.clipboard: tuple[list[TransferItem], bool] | None = None
        self._listing_token = 0
        self._operation_pending = False
        self._bridge = self._make_bridge()
        self._bridge.listing_ready.connect(self._listing_ready)
        self._bridge.operation_finished.connect(self._operation_finished)
        self.window = self._make_window()
        self._build()

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            listing_ready = qt.Signal(int, object, object)
            operation_finished = qt.Signal(bool, str)

        return Bridge()

    def _make_window(self) -> Any:
        flags = self.qt.Qt.WindowType.Tool | self.qt.Qt.WindowType.FramelessWindowHint
        try:
            window = self.qt.QMainWindow(None, flags)
        except Exception:
            window = self.qt.QMainWindow()
            window.setWindowFlags(flags)
        window.setWindowTitle("Mountlet Files")
        window.resize(520, 390)
        return window

    def _build(self) -> None:
        qt = self.qt
        root = qt.QWidget()
        layout = qt.QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        header = qt.QHBoxLayout()
        self.title = qt.QLabel("Files")
        title_font = self.title.font()
        title_font.setBold(True)
        self.title.setFont(title_font)
        header.addWidget(self.title)
        header.addStretch(1)
        close = self._button("×", self.hide, "Close file browser", square=True)
        header.addWidget(close)
        layout.addLayout(header)

        navigation = qt.QHBoxLayout()
        self.up_button = self._button("↑", self.go_up, "Parent folder", square=True)
        self.path_field = qt.QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Remote root")
        self.path_field.returnPressed.connect(self._path_entered)
        refresh = self._button("↻", self.refresh, "Refresh folder", square=True)
        external = self._button("↗", self._open_current_mount, "Open this folder in the system file manager", square=True)
        navigation.addWidget(self.up_button)
        navigation.addWidget(self.path_field, 1)
        navigation.addWidget(refresh)
        navigation.addWidget(external)
        layout.addLayout(navigation)

        actions = qt.QHBoxLayout()
        self.copy_button = self._button("Copy", self.copy_selected, "Copy selected files")
        self.cut_button = self._button("Cut", self.cut_selected, "Move selected files on paste")
        self.paste_button = self._button("Paste", self.paste, "Paste into this folder")
        self.offline_button = self._button("↓", self.toggle_offline, "Keep selected files available offline", square=True)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.cut_button)
        actions.addWidget(self.paste_button)
        actions.addStretch(1)
        actions.addWidget(self.offline_button)
        layout.addLayout(actions)

        outer = self

        class FileTree(qt.QTreeWidget):
            def startDrag(self, supported_actions: Any) -> None:
                payload = outer.selected_transfer_items()
                if not payload:
                    return
                mime = qt.QMimeData()
                mime.setData(MIME_TYPE, json.dumps([item.__dict__ for item in payload]).encode("utf-8"))
                drag = qt.QDrag(self)
                drag.setMimeData(mime)
                drag.exec(qt.Qt.DropAction.CopyAction | qt.Qt.DropAction.MoveAction, qt.Qt.DropAction.CopyAction)

        self.tree = FileTree()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["", "Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(qt.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setEditTriggers(qt.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(self._open_item)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.setColumnWidth(0, 24)
        self.tree.setColumnWidth(1, 250)
        self.tree.setColumnWidth(2, 72)
        layout.addWidget(self.tree, 1)

        self.status = qt.QLabel("")
        layout.addWidget(self.status)
        self.window.setCentralWidget(root)

        self._shortcut("Ctrl+C", self.copy_selected)
        self._shortcut("Ctrl+X", self.cut_selected)
        self._shortcut("Ctrl+V", self.paste)
        self._update_actions()

    def _button(self, text: str, callback: Callable[[], None], tooltip: str, *, square: bool = False) -> Any:
        button = self.qt.QPushButton(text)
        if square:
            button.setFixedSize(28, 26)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def _shortcut(self, sequence: str, callback: Callable[[], None]) -> None:
        shortcut = self.qt.QShortcut(self.qt.QKeySequence(sequence), self.window)
        shortcut.activated.connect(callback)

    def is_visible(self) -> bool:
        return bool(self.window.isVisible())

    def hide(self) -> None:
        self.window.hide()

    def show_remote(self, remote: core.RemoteInfo, row: Any, *, open_browser: bool) -> None:
        changed = self.remote is None or self.remote.name != remote.name
        self.remote = remote
        self.path = self.backend.current_path(remote.name)
        self._position(row)
        if open_browser:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        if changed or open_browser:
            self.refresh()

    def refresh(self) -> None:
        if self.remote is None:
            return
        self._listing_token += 1
        token = self._listing_token
        remote = self.remote
        path = self.path
        self.title.setText(remote.display_name)
        self.path_field.setText(path)
        self.path_field.setToolTip(path or "Remote root")
        self.up_button.setEnabled(bool(path))
        self.status.setText("Loading…")

        def worker() -> None:
            try:
                entries = self.backend.list_entries(remote, path)
            except Exception as exc:
                self._bridge.listing_ready.emit(token, None, str(exc))
                return
            self._bridge.listing_ready.emit(token, entries, "")

        threading.Thread(target=worker, daemon=True).start()

    def _listing_ready(self, token: int, entries: object, error: object) -> None:
        if token != self._listing_token:
            return
        if not isinstance(entries, list):
            self.status.setText(str(error) or "Could not load this folder")
            return
        self.entries = entries
        self.tree.clear()
        offline_icon = self.window.style().standardIcon(self.qt.QStyle.StandardPixmap.SP_DialogSaveButton)
        directory_icon = self.window.style().standardIcon(self.qt.QStyle.StandardPixmap.SP_DirIcon)
        file_icon = self.window.style().standardIcon(self.qt.QStyle.StandardPixmap.SP_FileIcon)
        for entry in entries:
            item = self.qt.QTreeWidgetItem(["", entry.name, "" if entry.is_dir else format_file_size(entry.size), entry.modified])
            item.setData(0, self.qt.Qt.ItemDataRole.UserRole, entry)
            item.setIcon(1, directory_icon if entry.is_dir else file_icon)
            if self.remote and self.backend.is_offline(self.remote.name, entry.path, is_dir=entry.is_dir):
                item.setIcon(0, offline_icon)
                item.setToolTip(0, "Available offline")
            self.tree.addTopLevelItem(item)
        self.status.setText(f"{len(entries)} item{'s' if len(entries) != 1 else ''}")
        self._update_actions()

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
        items = self.selected_transfer_items()
        if items:
            self.clipboard = (items, False)
            self.status.setText(f"Copied {len(items)} item{'s' if len(items) != 1 else ''}")
            self._update_actions()

    def cut_selected(self) -> None:
        items = self.selected_transfer_items()
        if items:
            self.clipboard = (items, True)
            self.status.setText(f"Cut {len(items)} item{'s' if len(items) != 1 else ''}")
            self._update_actions()

    def paste(self) -> None:
        if self.clipboard is None:
            return
        items, move = self.clipboard
        self._transfer(items, move=move)

    def accept_drop(self, payload: bytes, *, move: bool = False) -> None:
        try:
            values = json.loads(bytes(payload).decode("utf-8"))
            items = [TransferItem(**value) for value in values]
        except (TypeError, ValueError, json.JSONDecodeError):
            self._notify("File transfer", "The dragged files could not be read.", False)
            return
        self._transfer(items, move=move)

    def _transfer(self, items: list[TransferItem], *, move: bool) -> None:
        if not items or self.remote is None or self._operation_pending:
            return
        destination = self.remote
        destination_path = self.path
        remotes = {remote.name: remote for remote in self._remotes()}
        verb = "Moving" if move else "Copying"
        self._run_operation(
            f"{verb} {len(items)} item{'s' if len(items) != 1 else ''}…",
            lambda: self.backend.transfer(items, remotes, destination, destination_path, move=move),
            clear_clipboard=move,
        )

    def toggle_offline(self) -> None:
        entries = self._selected_entries()
        if not entries or self.remote is None or self._operation_pending:
            return
        remote = self.remote
        all_offline = all(self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir) for entry in entries)
        if all_offline:
            action = lambda: [self.backend.remove_offline(remote.name, entry.path) for entry in entries]
            message = "Removing local copies…"
        else:
            action = lambda: [self.backend.make_offline(remote, entry) for entry in entries]
            message = "Downloading for offline use…"
        self._run_operation(message, action)

    def _run_operation(
        self,
        message: str,
        action: Callable[[], object],
        *,
        clear_clipboard: bool = False,
    ) -> None:
        self._operation_pending = True
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

    def _operation_finished(self, success: bool, message: str) -> None:
        self._operation_pending = False
        if not success:
            self._notify("File operation", message or "The operation failed.", False)
        self.refresh()

    def _selection_changed(self) -> None:
        entries = self._selected_entries()
        if self.remote and entries:
            all_offline = all(self.backend.is_offline(self.remote.name, entry.path, is_dir=entry.is_dir) for entry in entries)
            self.offline_button.setToolTip(
                "Remove local offline copies" if all_offline else "Keep selected files available offline"
            )
        self._update_actions()

    def _update_actions(self) -> None:
        selected = bool(self._selected_entries()) if hasattr(self, "tree") else False
        enabled = not self._operation_pending
        self.copy_button.setEnabled(selected and enabled)
        self.cut_button.setEnabled(selected and enabled)
        self.paste_button.setEnabled(self.clipboard is not None and enabled)
        self.offline_button.setEnabled(selected and enabled)

    def _open_item(self, item: Any, _column: int = 0) -> None:
        entry = item.data(0, self.qt.Qt.ItemDataRole.UserRole)
        if not isinstance(entry, BrowserEntry) or self.remote is None:
            return
        if entry.is_dir:
            self.path = entry.path
            self.backend.remember_path(self.remote.name, self.path)
            self.refresh()
            return
        offline = self.backend.offline_path(self.remote.name, entry.path)
        if offline.is_file():
            self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(offline)))
            return
        if core.is_mounted(self.remote):
            local = Path(self.remote.mount_path).joinpath(*entry.path.split("/"))
            self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(local)))
            return
        self._notify("Open file", "Mount the remote or make this file available offline first.", False)

    def go_up(self) -> None:
        if self.remote is None:
            return
        self.path = parent_browser_path(self.path)
        self.backend.remember_path(self.remote.name, self.path)
        self.refresh()

    def _path_entered(self) -> None:
        return

    def _open_current_mount(self) -> None:
        if self.remote is None:
            return
        if not core.is_mounted(self.remote):
            self._notify("Open folder", "Mount this remote before opening it in the system file manager.", False)
            return
        self._open_mount(self.remote)

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
            self.window.move(*position)
        except Exception:
            return


__all__ = ["CompactCloudBrowser", "MIME_TYPE", "cascade_position"]
