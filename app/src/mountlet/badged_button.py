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
            self._mountlet_check_visible = False
            self._mountlet_check_color = "#22c55e"
            self._mountlet_disabled_opacity_effect = None
            self._update_mountlet_disabled_opacity()

        def setEnabled(self, enabled: bool) -> None:
            super().setEnabled(enabled)
            self._update_mountlet_disabled_opacity()

        def changeEvent(self, event: Any) -> None:
            super().changeEvent(event)
            try:
                if event.type() == qt.QEvent.Type.EnabledChange:
                    self._update_mountlet_disabled_opacity()
            except Exception:
                return

        def _update_mountlet_disabled_opacity(self) -> None:
            effect_type = getattr(qt, "QGraphicsOpacityEffect", None)
            if effect_type is None:
                return
            try:
                effect = self._mountlet_disabled_opacity_effect
                if effect is None:
                    effect = effect_type(self)
                    self._mountlet_disabled_opacity_effect = effect
                    self.setGraphicsEffect(effect)
                effect.setOpacity(1.0 if self.isEnabled() else 0.38)
            except Exception:
                return

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

        def setCheckVisible(self, visible: bool) -> None:
            visible = bool(visible)
            if self._mountlet_check_visible == visible:
                return
            self._mountlet_check_visible = visible
            self.update()

        def setCheckColor(self, color: str) -> None:
            if self._mountlet_check_color == color:
                return
            self._mountlet_check_color = color
            self.update()

        def paintEvent(self, event: Any) -> None:
            super().paintEvent(event)
            if not self._mountlet_badge_visible and not self._mountlet_check_visible:
                return
            painter = qt.QPainter(self)
            try:
                painter.setRenderHint(qt.QPainter.RenderHint.Antialiasing, True)
            except Exception:
                pass
            rect = self.rect()
            if self._mountlet_badge_visible:
                painter.setPen(qt.Qt.PenStyle.NoPen)
                painter.setBrush(qt.QColor(self._mountlet_badge_color))
                size = int(self._mountlet_badge_size)
                margin = max(3, size // 2)
                painter.drawEllipse(rect.right() - size - margin, rect.top() + margin, size, size)
            if self._mountlet_check_visible:
                size = max(12, min(rect.width(), rect.height()) // 2)
                margin = max(2, size // 5)
                left = rect.right() - size - margin
                top = rect.top() + margin
                painter.setPen(qt.Qt.PenStyle.NoPen)
                painter.setBrush(qt.QColor(self._mountlet_check_color))
                painter.drawEllipse(left, top, size, size)
                pen = qt.QPen(qt.QColor("#ffffff"))
                pen.setWidth(max(2, size // 6))
                pen.setCapStyle(qt.Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(qt.Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                path = qt.QPainterPath()
                path.moveTo(left + size * 0.28, top + size * 0.54)
                path.lineTo(left + size * 0.44, top + size * 0.70)
                path.lineTo(left + size * 0.74, top + size * 0.34)
                painter.drawPath(path)
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


def set_checkmark(button: Any, visible: bool, color: str = "#22c55e") -> None:
    """Set a small checkmark badge when the button supports it."""
    setter = getattr(button, "setCheckColor", None)
    if callable(setter):
        setter(color)
    visibility = getattr(button, "setCheckVisible", None)
    if callable(visibility):
        visibility(bool(visible))


__all__ = ["create_badged_button", "set_badge", "set_checkmark"]
