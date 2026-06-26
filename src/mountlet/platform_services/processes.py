from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

from .base import PlatformServices


def external_process_environment() -> dict[str, str]:
    """Return an environment safe for non-bundled desktop applications.

    PyInstaller prepends its private libraries on Unix. External Qt programs
    such as Dolphin must not inherit those paths.
    """
    environment = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return environment
    for variable in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = environment.pop(f"{variable}_ORIG", None)
        if original is None:
            environment.pop(variable, None)
        else:
            environment[variable] = original
    return environment


def process_group_options(platform: PlatformServices) -> dict[str, Any]:
    """Return detached process options suitable for the active platform."""
    return platform.mount_process_options()


def signal_process_tree(
    process: subprocess.Popen[str],
    sig: int,
    platform: PlatformServices,
    *,
    force: bool = False,
) -> None:
    if platform.system_name == "Windows":
        try:
            if force:
                process.kill()
            else:
                process.terminate()
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, sig)
    except OSError:
        try:
            process.send_signal(sig)
        except OSError:
            pass


def terminate_process(
    process: subprocess.Popen[str],
    platform: PlatformServices,
    *,
    timeout: float = 2,
) -> None:
    if process.poll() is not None:
        return
    signal_process_tree(process, signal.SIGTERM, platform)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        signal_process_tree(process, force_signal, platform, force=True)
        process.wait(timeout=timeout)
