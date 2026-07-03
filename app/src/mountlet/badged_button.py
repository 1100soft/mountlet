from __future__ import annotations

from typing import Any


def create_badged_button(qt: Any, text: str = "") -> Any:
    """Return a QPushButton that can paint a small notification dot."""

    class BadgedButton(qt.QPushButton):
        def __init__(self, label: str = "") -> None:
            super().__init__(label)
            self._mountlet_badge_visible = False
            self._mountlet_badge_color = "#ef4444"
            self._mountlet_badge_size = 8

        def setBadgeVisible(self, visible: bool) -> None:
            visible = bool(visible)
            if self._mountlet_badge_visible == visible:
                return
            self._mountlet_badge_visible = visible
            self.update()

        def badgeVisible(self) -> bool:
            return bool(self._mountlet_badge_visible)

        def setBadgeColor(self, color: str) -> None:
            if self._mountlet_badge_color == color:
                return
            self._mountlet_badge_color = color
            self.update()

        def badgeColor(self) -> str:
            return str(self._mountlet_badge_color)

        def paintEvent(self, event: Any) -> None:
            super().paintEvent(event)
            if not self._mountlet_badge_visible:
                return
            painter = qt.QPainter(self)
            try:
                painter.setRenderHint(qt.QPainter.RenderHint.Antialiasing, True)
            except Exception:
                pass
            painter.setPen(qt.Qt.PenStyle.NoPen)
            painter.setBrush(qt.QColor(self._mountlet_badge_color))
            rect = self.rect()
            size = int(self._mountlet_badge_size)
            margin = max(3, size // 2)
            painter.drawEllipse(rect.right() - size - margin, rect.top() + margin, size, size)
            painter.end()

    return BadgedButton(text)


def set_badge(button: Any, visible: bool, color: str = "#ef4444") -> None:
    """Set a notification badge when the button supports it."""
    setter = getattr(button, "setBadgeColor", None)
    if callable(setter):
        setter(color)
    visibility = getattr(button, "setBadgeVisible", None)
    if callable(visibility):
        visibility(bool(visible))


__all__ = ["create_badged_button", "set_badge"]
