from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

from . import __version__


FAST_EXIT_SECONDS = 5.0
_RUNTIME_LOG_HANDLE = None
_FROZEN_LINUX_QT_NOTE = (
    "Frozen Linux build: using bundled-Qt-safe defaults "
    "(QT_IM_MODULE=xim on X11, QT_STYLE_OVERRIDE=Fusion, no host QT_QPA_PLATFORMTHEME/QT_PLUGIN_PATH). "
    "Set MOUNTLET_QT_IM_MODULE, MOUNTLET_QT_STYLE_OVERRIDE, or MOUNTLET_QT_PLATFORMTHEME to override."
)


def _write_startup_log(message: str) -> None:
    try:
        from .config_tools.shared import app_state_dir

        path = app_state_dir() / "startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message, encoding="utf-8")
    except Exception:
        print(message, file=sys.stderr)


def _runtime_log_path() -> Path | None:
    try:
        from .config_tools.shared import app_state_dir

        path = app_state_dir() / "runtime.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def _append_runtime_log(message: str) -> None:
    path = _runtime_log_path()
    if path is None:
        print(message, file=sys.stderr)
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        print(message, file=sys.stderr)


def _install_runtime_logging() -> None:
    global _RUNTIME_LOG_HANDLE
    path = _runtime_log_path()
    if path is not None and _RUNTIME_LOG_HANDLE is None:
        try:
            _RUNTIME_LOG_HANDLE = path.open("a", encoding="utf-8")
            _RUNTIME_LOG_HANDLE.write(f"\nMountlet {__version__} runtime started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            _RUNTIME_LOG_HANDLE.flush()
            faulthandler.enable(file=_RUNTIME_LOG_HANDLE, all_threads=True)
        except Exception:
            _RUNTIME_LOG_HANDLE = None
    previous_excepthook = sys.excepthook
    previous_threading_excepthook = getattr(threading, "excepthook", None)

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        _append_runtime_log("Unhandled exception:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        previous_excepthook(exc_type, exc, tb)

    def threading_excepthook(args: threading.ExceptHookArgs) -> None:
        _append_runtime_log(
            "Unhandled thread exception:\n"
            + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        )
        if previous_threading_excepthook is not None:
            previous_threading_excepthook(args)

    sys.excepthook = excepthook
    if previous_threading_excepthook is not None:
        threading.excepthook = threading_excepthook


def _prepare_frozen_linux_qt_environment() -> None:
    if platform.system() != "Linux" or not getattr(sys, "frozen", False):
        return
    _set_qt_env("QT_IM_MODULE", "MOUNTLET_QT_IM_MODULE", _default_frozen_linux_input_method(), replace=True)
    _set_qt_env("QT_STYLE_OVERRIDE", "MOUNTLET_QT_STYLE_OVERRIDE", "Fusion", replace=False)
    _set_qt_env("QT_QPA_PLATFORMTHEME", "MOUNTLET_QT_PLATFORMTHEME", "", replace=True)
    os.environ.setdefault("QT_ACCESSIBILITY", "0")
    os.environ.setdefault("QT_LINUX_ACCESSIBILITY_ALWAYS_ON", "0")
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QML2_IMPORT_PATH", None)
    _append_runtime_log(_FROZEN_LINUX_QT_NOTE)


def _default_frozen_linux_input_method() -> str:
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return "xim"
    return "compose"


def _set_qt_env(target: str, override: str, default: str, *, replace: bool) -> None:
    if override in os.environ:
        value = os.environ.get(override, "")
        if value:
            os.environ[target] = value
        else:
            os.environ.pop(target, None)
        return
    if not default:
        os.environ.pop(target, None)
        return
    if replace:
        os.environ[target] = default
    else:
        os.environ.setdefault(target, default)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--packaging-smoke-test"]:
        print(f"Mountlet {__version__}")
        return 0
    if args == ["--packaging-startup-import-test"]:
        from . import tray  # noqa: F401

        print(f"Mountlet {__version__} startup imports ok")
        return 0
    if args == ["--packaging-rclone-smoke-test"]:
        from .config_tools.shared import run_rclone_version

        version = run_rclone_version()
        print(version)
        return 0 if version.startswith("rclone v") else 1

    _install_runtime_logging()
    _prepare_frozen_linux_qt_environment()
    started = time.monotonic()
    try:
        from . import tray

        result = int(tray.main([]) or 0)
    except Exception:
        detail = f"Mountlet {__version__} failed during desktop startup.\n\n{traceback.format_exc()}"
        _write_startup_log(detail)
        return 1

    elapsed = time.monotonic() - started
    if result != 0 or elapsed < FAST_EXIT_SECONDS:
        _write_startup_log(
            f"Mountlet {__version__} exited during desktop startup.\n\n"
            f"Exit code: {result}\n"
            f"Elapsed: {elapsed:.2f}s\n"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
