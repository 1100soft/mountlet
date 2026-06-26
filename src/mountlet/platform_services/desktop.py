from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .processes import external_process_environment


class DesktopServices:
    """Desktop-shell capabilities used by the tray UI.

    Every capability has a generic Qt fallback. Optional adapters can supply
    richer behavior for a particular file manager, workspace protocol, or
    window manager without leaking those details into the application window.
    """

    def __init__(
        self,
        qt: Any,
        *,
        folder_opener: Callable[[Any, str, str], bool] | None = None,
        text_opener: Callable[[Path], bool] | None = None,
        file_manager_name: Callable[[], str] | None = None,
        window_workspace_check: Callable[[Any], bool | None] | None = None,
        window_workspace_mover: Callable[[Any], bool] | None = None,
        keep_above_setter: Callable[[Any, bool], bool] | None = None,
    ) -> None:
        self.qt = qt
        self._folder_opener = folder_opener
        self._text_opener = text_opener
        self._file_manager_name = file_manager_name
        self._window_workspace_check = window_workspace_check
        self._window_workspace_mover = window_workspace_mover
        self._keep_above_setter = keep_above_setter

    def file_manager_label(self) -> str:
        if self._file_manager_name:
            return self._file_manager_name()
        return "the file manager"

    def open_folder(self, path: str, strategy: str = "default") -> bool:
        if self._folder_opener and self._folder_opener(self.qt, path, strategy):
            return True
        return bool(self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(path)))

    def open_text_file(self, path: Path) -> bool:
        if self._text_opener and self._text_opener(path):
            return True
        return bool(self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))))

    def open_file(self, path: Path) -> bool:
        if platform.system() == "Windows" and hasattr(os, "startfile"):
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except OSError:
                return False
            return True
        command = _file_opener_command()
        if command:
            try:
                subprocess.Popen(
                    [*command, str(path)],
                    env=external_process_environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                pass
            else:
                return True
        return bool(self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))))

    def window_is_on_current_workspace(self, window: Any) -> bool | None:
        if not self._window_workspace_check:
            return None
        return self._window_workspace_check(window)

    def move_window_to_current_workspace(self, window: Any) -> bool:
        if not self._window_workspace_mover:
            return False
        return self._window_workspace_mover(window)

    def set_keep_above(self, window: Any, enabled: bool) -> bool:
        if not self._keep_above_setter:
            return False
        return self._keep_above_setter(window, enabled)


def _file_opener_command() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        command = shutil.which("open")
        return [command] if command else []
    if system == "Linux":
        command = shutil.which("xdg-open")
        if command:
            return [command]
        command = shutil.which("gio")
        return [command, "open"] if command else []
    return []
