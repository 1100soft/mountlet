from __future__ import annotations

from typing import Any


# All dimensions in this module are unscaled, 100% reference dimensions.
FILE_BROWSER_REFERENCE_WIDTH = 540
FILE_BROWSER_REFERENCE_HEIGHT = 390
FILE_BROWSER_EMBEDDED_MIN_HEIGHT = 340
FILE_BROWSER_WINDOW_MIN_HEIGHT = 240
FILE_BROWSER_CHROME_ROW_HEIGHT = 28
FILE_BROWSER_CHROME_ROW_COUNT = 5
FILE_BROWSER_LAYOUT_MARGIN = 8
FILE_BROWSER_LAYOUT_SPACING = 5
FILE_BROWSER_LAYOUT_GAP_COUNT = 5
FILE_BROWSER_EMBEDDED_MAX_HEIGHT = 460
FILE_LIST_ROW_HEIGHT = 36
FILE_LIST_HEADER_HEIGHT = 28
# The file tree has no native frame.  Its total height is therefore exactly
# the fixed header plus the fixed rows, independent of the desktop style.
FILE_LIST_FRAME_PADDING = 0
FILE_NAME_COLUMN_MIN_WIDTH = 80
FILE_SIZE_COLUMN_WIDTH = 76
FILE_MODIFIED_COLUMN_WIDTH = 124
FILE_ICON_SOURCE_SIZE = 48
PANEL_ICON_MARGIN = 8


def rect_tuple(rect: Any) -> tuple[int, int, int, int]:
    x = rect.x() if hasattr(rect, "x") else rect.left()
    y = rect.y() if hasattr(rect, "y") else rect.top()
    return x, y, rect.width(), rect.height()


def intersect_rects(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x = max(left[0], right[0])
    y = max(left[1], right[1])
    far_x = min(left[0] + left[2], right[0] + right[2])
    far_y = min(left[1] + left[3], right[1] + right[3])
    if far_x <= x or far_y <= y:
        return right
    return x, y, far_x - x, far_y - y


def exclude_tray_panel(
    work_area: tuple[int, int, int, int],
    tray_rect: tuple[int, int, int, int],
    edge: str | None,
) -> tuple[int, int, int, int]:
    """Infer a panel boundary from the tray icon's panel-facing edge."""
    left, top, width, height = work_area
    right, bottom = left + width, top + height
    tray_left, tray_top, tray_width, tray_height = tray_rect
    center_x = tray_left + tray_width // 2
    center_y = tray_top + tray_height // 2
    # If Qt already excluded the panel, its tray icon lies outside the work
    # area and no further reduction is needed.
    if not (left <= center_x < right and top <= center_y < bottom):
        return work_area
    if edge == "right":
        right = min(right, tray_left - PANEL_ICON_MARGIN)
    elif edge == "left":
        left = max(left, tray_left + tray_width + PANEL_ICON_MARGIN)
    elif edge == "top":
        top = max(top, tray_top + tray_height + PANEL_ICON_MARGIN)
    elif edge == "bottom":
        bottom = min(bottom, tray_top - PANEL_ICON_MARGIN)
    return left, top, max(1, right - left), max(1, bottom - top)
