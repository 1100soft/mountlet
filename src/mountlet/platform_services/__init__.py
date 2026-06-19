"""Operating-system integration for Mountlet.

Application code should depend on :func:`get_platform` instead of branching on
`platform.system()`.  Desktop-environment integrations live separately because
they are optional capabilities, not properties of an operating system.
"""

from __future__ import annotations

import platform as _platform

from .base import PlatformServices
from .linux import LinuxPlatformServices
from .macos import MacOSPlatformServices
from .windows import WindowsPlatformServices


def get_platform(system: str | None = None) -> PlatformServices:
    name = system or _platform.system()
    if name == "Windows":
        return WindowsPlatformServices()
    if name == "Darwin":
        return MacOSPlatformServices()
    return LinuxPlatformServices()


__all__ = ["PlatformServices", "get_platform"]
