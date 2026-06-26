from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from . import core
from .cloud_browser import BrowserEntry, CloudBrowserBackend, TransferItem, format_file_size, parent_browser_path
from .settings import load_app_settings
from .shortcuts import matches_shortcut

MIME_TYPE = "application/x-mountlet-remote-files"
EMBEDDED_BROWSER_MIN_WIDTH = 540
EMBEDDED_BROWSER_MIN_HEIGHT = 340
CHILD_FOLDER_PREFETCH_LIMIT = 24


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
    x = main_right + 8 if right_space >= width + 8 or right_space >= left_space else main_x - width - 8
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
        open_mount: Callable[[core.RemoteInfo, str], None],
        file_manager_label: Callable[[], str],
        open_file: Callable[[Path], bool] | None = None,
        embedded: bool = False,
        layout_changed: Callable[[], None] | None = None,
    ) -> None:
        self.qt = qt
        self.main_window = main_window
        self._remotes = remotes
        self._notify = notify
        self._open_mount = open_mount
        self._open_file = open_file
        self._file_manager_name = file_manager_label
        self._embedded = embedded
        self._layout_changed = layout_changed or (lambda: None)
        self.backend = CloudBrowserBackend()
        self.remote: core.RemoteInfo | None = None
        self.path = ""
        self._side = "right"
        self.entries: list[BrowserEntry] = []
        self.clipboard: tuple[list[TransferItem], bool] | None = None
        self._operation_pending = False
        self._operation_cache_keys: set[tuple[str, str]] = set()
        self._folder_cache: dict[tuple[str, str], list[BrowserEntry]] = {}
        self._loads_pending: set[tuple[str, str]] = set()
        self._load_slots = threading.BoundedSemaphore(4)
        self._bridge = self._make_bridge()
        self._bridge.listing_ready.connect(self._listing_ready)
        self._bridge.operation_finished.connect(self._operation_finished)
        self.window = self._make_window()
        self._build()

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            listing_ready = qt.Signal(str, str, object, str)
            operation_finished = qt.Signal(bool, str)

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
        header.addWidget(self._button("×", self.hide, "Close file browser", square=True))
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
        navigation.addWidget(
            self._button("↗", self._open_current_mount, "Open this folder in the system file manager", square=True)
        )
        self.offline_button = self._button("↓", self.toggle_offline, "Make selected items available offline", square=True)
        navigation.addWidget(self.offline_button)
        layout.addLayout(navigation)

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

            def keyPressEvent(self, event: Any) -> None:
                if outer._handle_key(event):
                    return
                super().keyPressEvent(event)

        self.tree = FileTree()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["", "Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(qt.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(qt.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setDragEnabled(False)
        self.tree.setEditTriggers(qt.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(qt.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        self.tree.itemDoubleClicked.connect(self._open_item)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.setColumnWidth(0, 24)
        self.tree.setColumnWidth(1, 250)
        self.tree.setColumnWidth(2, 72)
        layout.addWidget(self.tree, 1)
        self.status = qt.QLabel("")
        layout.addWidget(self.status)
        self.window.setCentralWidget(root)
        self._update_actions()
        self._update_focus_style()

    def _button(self, text: str, callback: Callable[[], None], tooltip: str, *, square: bool = False) -> Any:
        button = self.qt.QPushButton(text)
        if square:
            button.setFixedSize(28, 26)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def is_visible(self) -> bool:
        return bool(self.root.isVisible()) if self._embedded else bool(self.window.isVisible())

    def hide(self) -> None:
        if self._embedded:
            self.root.hide()
            self._layout_changed()
        else:
            self.window.hide()

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
        if not self._embedded:
            self._position(row)
        if show_browser:
            if self._embedded:
                was_visible = self.root.isVisible()
                self.root.show()
                if not was_visible:
                    self._layout_changed()
            else:
                try:
                    self.window.setAttribute(self.qt.Qt.WidgetAttribute.WA_ShowWithoutActivating, not focus_browser)
                except Exception:
                    pass
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
        try:
            self.window.setAttribute(self.qt.Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        except Exception:
            pass
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

    def _direction_points_to_main(self, key: Any) -> bool:
        if self._side == "left":
            return key == self.qt.Qt.Key.Key_Right
        return key == self.qt.Qt.Key.Key_Left

    def refresh(self, *, force: bool = False) -> None:
        if self.remote is None:
            return
        remote, path = self.remote, self.path
        self.title.setText(remote.display_name)
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
            if self.remote and (self.remote.name, self.path) == key:
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
        self.entries = entries
        self.tree.clear()
        style = self.window.style()
        offline_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_DialogSaveButton)
        directory_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(self.qt.QStyle.StandardPixmap.SP_FileIcon)
        for entry in entries:
            item = self.qt.QTreeWidgetItem(["", entry.name, "" if entry.is_dir else format_file_size(entry.size), entry.modified])
            item.setData(0, self.qt.Qt.ItemDataRole.UserRole, entry)
            item.setIcon(1, directory_icon if entry.is_dir else file_icon)
            if self.remote and self.backend.is_offline(self.remote.name, entry.path, is_dir=entry.is_dir):
                item.setIcon(0, offline_icon)
                item.setToolTip(0, "Available offline as a local read-only copy")
            self.tree.addTopLevelItem(item)
        self.status.setText(f"{len(entries)} item{'s' if len(entries) != 1 else ''}")
        if self.has_focus():
            self._ensure_tree_selection()
        self._update_actions()
        self.qt.QTimer.singleShot(0, lambda visible_entries=list(entries): self._prefetch_child_folders(visible_entries))

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
            flags = (
                self.qt.QItemSelectionModel.SelectionFlag.ClearAndSelect
                | self.qt.QItemSelectionModel.SelectionFlag.Rows
            )
            selection.select(self.tree.currentIndex(), flags)
        elif not self.tree.selectedItems():
            current.setSelected(True)

    def _focus_relative_item(self, delta: int) -> None:
        count = self.tree.topLevelItemCount()
        if count <= 0:
            return
        current = self.tree.currentItem() or self.tree.topLevelItem(0)
        index = self.tree.indexOfTopLevelItem(current) if current is not None else 0
        if index < 0:
            index = 0
        index = min(max(index + delta, 0), count - 1)
        self.tree.setCurrentItem(self.tree.topLevelItem(index))
        self._ensure_tree_selection()

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
        self._menu_action(menu, "Open", lambda selected=item: self._open_item(selected))
        if entry.is_dir:
            self._menu_action(
                menu,
                f"Open in {self._file_manager_label()}",
                lambda path=entry.path: self._open_external_folder(path),
                enabled=bool(self.remote and core.is_mounted(self.remote)),
            )
        menu.addSeparator()
        edits_enabled = self._edits_enabled()
        self._menu_action(menu, "Copy", self.copy_selected, enabled=edits_enabled)
        self._menu_action(menu, "Cut", self.cut_selected, enabled=edits_enabled)
        self._menu_action(menu, "Make available offline", self.toggle_offline, enabled=False)
        self._menu_action(menu, "Delete", self.delete_selected, enabled=edits_enabled)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _show_folder_menu(self, point: Any, *, source: Any | None = None) -> None:
        menu = self.qt.QMenu(self.window)
        self._menu_action(
            menu,
            f"Open in {self._file_manager_label()}",
            lambda: self._open_external_folder(self.path),
            enabled=bool(self.remote and core.is_mounted(self.remote)),
        )
        edits_enabled = self._edits_enabled()
        self._menu_action(
            menu,
            "Paste",
            self.paste,
            enabled=edits_enabled and self.clipboard is not None and not self._operation_pending,
        )
        self._menu_action(menu, "New folder", self.create_folder, enabled=edits_enabled and not self._operation_pending)
        origin = source or self.path_field
        menu.exec(origin.mapToGlobal(point))

    def _menu_action(self, menu: Any, label: str, callback: Callable[[], None], *, enabled: bool = True) -> Any:
        action = menu.addAction(label)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _checked=False: callback())
        return action

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
        all_offline = all(self.backend.is_offline(remote.name, entry.path, is_dir=entry.is_dir) for entry in entries)
        if all_offline:
            def action() -> None:
                for entry in entries:
                    self.backend.remove_offline(remote.name, entry.path)

            message = "Removing local copies…"
        else:
            def action() -> None:
                for entry in entries:
                    self.backend.make_offline(remote, entry)

            message = "Downloading for offline use…"
        self._run_operation(message, action)

    def _run_operation(
        self,
        message: str,
        action: Callable[[], object],
        *,
        clear_clipboard: bool = False,
        invalidate_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        self._operation_pending = True
        current_key = (self.remote.name, self.path) if self.remote is not None else None
        self._operation_cache_keys = set(invalidate_keys or ())
        if current_key is not None:
            self._operation_cache_keys.add(current_key)
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
        changed_keys = self._operation_cache_keys
        self._operation_cache_keys = set()
        for changed_key in changed_keys:
            self._folder_cache.pop(changed_key, None)
        if not success:
            self._notify("File operation", message or "The operation failed.", False)
        current_key = (self.remote.name, self.path) if self.remote is not None else None
        self.refresh(force=current_key in changed_keys)

    def _selection_changed(self) -> None:
        entries = self._selected_entries()
        if self.remote and entries:
            self.offline_button.setToolTip("Offline sync is not available yet")
        self._update_actions()

    def _update_actions(self) -> None:
        selected = bool(self._selected_entries()) if hasattr(self, "tree") else False
        edits_enabled = self._edits_enabled()
        self.tree.setDragEnabled(edits_enabled and selected)
        self.offline_button.setEnabled(False)
        self.offline_button.setProperty("hasSelection", selected)

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
            self._open_local_file(offline)
        elif core.is_mounted(self.remote):
            local = Path(self.remote.mount_path).joinpath(*entry.path.split("/"))
            self._open_local_file(local)
        else:
            self._notify("Open file", "Mount the remote or make this file available offline first.", False)

    def _open_local_file(self, path: Path) -> None:
        if self._open_file and self._open_file(path):
            return
        if self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))):
            return
        self._notify("Open file", "Could not open this file.", False)

    def go_up(self) -> None:
        if self.remote is None:
            return
        self.path = parent_browser_path(self.path)
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
            self._notify("Open folder", "Mount this remote before opening it in the system file manager.", False)
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


__all__ = ["CompactCloudBrowser", "MIME_TYPE", "cascade_position"]
