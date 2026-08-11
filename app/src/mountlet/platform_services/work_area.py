from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any, Callable


Rect = tuple[int, int, int, int]
_CURRENT_DESKTOP_RE = re.compile(r"_NET_CURRENT_DESKTOP[^=]*=\s*(\d+)")
_WORKAREA_RE = re.compile(r"_NET_WORKAREA[^=]*=\s*([-\d, \t]+)")


def rect_tuple(rect: Any) -> Rect:
    x = rect.x() if hasattr(rect, "x") else rect.left()
    y = rect.y() if hasattr(rect, "y") else rect.top()
    return int(x), int(y), int(rect.width()), int(rect.height())


def intersect_rects(left: Rect, right: Rect) -> Rect:
    x = max(left[0], right[0])
    y = max(left[1], right[1])
    far_x = min(left[0] + left[2], right[0] + right[2])
    far_y = min(left[1] + left[3], right[1] + right[3])
    if far_x <= x or far_y <= y:
        return right
    return x, y, far_x - x, far_y - y


def parse_x11_work_area(output: str) -> Rect | None:
    desktop_match = _CURRENT_DESKTOP_RE.search(output)
    workarea_match = _WORKAREA_RE.search(output)
    if desktop_match is None or workarea_match is None:
        return None
    values = [int(value) for value in re.findall(r"-?\d+", workarea_match.group(1))]
    offset = int(desktop_match.group(1)) * 4
    if offset + 4 > len(values):
        return None
    x, y, width, height = values[offset : offset + 4]
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


class WorkAreaResolver:
    """Resolve panel-excluded monitor bounds without rendering probe windows."""

    def __init__(
        self,
        *,
        system_name: str | None = None,
        environment: Mapping[str, str] | None = None,
        executable_finder: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.system_name = system_name or platform.system()
        self.environment = environment if environment is not None else os.environ
        self._executable_finder = executable_finder
        self._command_runner = command_runner
        self._x11_loaded = False
        self._x11_rect: Rect | None = None

    def usable_screen_rect(self, screen: Any) -> Rect:
        """Return the authoritative work area intersected with one monitor."""
        qt_available = rect_tuple(screen.availableGeometry())
        platform_area = self._platform_work_area()
        if platform_area is None:
            return qt_available
        geometry = getattr(screen, "geometry", None)
        monitor = rect_tuple(geometry()) if callable(geometry) else qt_available
        return intersect_rects(intersect_rects(monitor, platform_area), qt_available)

    def _platform_work_area(self) -> Rect | None:
        # Windows QScreen uses GetMonitorInfo(rcWork), macOS uses visibleFrame,
        # and Wayland only exposes compositor-approved geometry to clients.
        # Their authoritative result is therefore the Qt fallback above.
        if self.system_name != "Linux":
            return None
        if not self.environment.get("DISPLAY"):
            return None
        if self.environment.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return None
        if not self._x11_loaded:
            self._x11_loaded = True
            self._x11_rect = self._read_x11_work_area()
        return self._x11_rect

    def _read_x11_work_area(self) -> Rect | None:
        xprop = self._executable_finder("xprop")
        if not xprop:
            return None
        try:
            result = self._command_runner(
                [xprop, "-root", "_NET_CURRENT_DESKTOP", "_NET_WORKAREA"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return parse_x11_work_area(result.stdout)
