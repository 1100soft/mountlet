from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import threading
from typing import Callable

from .config_tools.shared import app_state_dir, apply_permissions

RCLONE_LOG_TAIL_LINES = 240
_LOCK = threading.Lock()
_SUBSCRIBERS: list[Callable[[str], None]] = []


def log_path() -> Path:
    return app_state_dir() / "rclone-output.log"


def append_raw(text: str, *, max_lines: int = RCLONE_LOG_TAIL_LINES, notify: bool = True) -> None:
    if not text:
        return
    callbacks: list[Callable[[str], None]] = []
    with _LOCK:
        path = log_path()
        with suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            incoming = text.splitlines()
            lines = [*existing, *incoming][-max_lines:]
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            apply_permissions(path)
        callbacks = list(_SUBSCRIBERS) if notify else []
    for callback in callbacks:
        with suppress(Exception):
            callback(text)


def tail_text(*, max_lines: int = RCLONE_LOG_TAIL_LINES) -> str:
    path = log_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def subscribe(callback: Callable[[str], None]) -> Callable[[], None]:
    with _LOCK:
        _SUBSCRIBERS.append(callback)

    def unsubscribe() -> None:
        with _LOCK:
            with suppress(ValueError):
                _SUBSCRIBERS.remove(callback)

    return unsubscribe
