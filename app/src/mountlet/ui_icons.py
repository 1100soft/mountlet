from __future__ import annotations

from contextlib import suppress
from importlib.resources import files
from pathlib import Path
import re
from typing import Any

NEUTRAL_ICON_RE = re.compile(r"#334155\b", re.IGNORECASE)


def icon_path(name: str) -> str | None:
    filename = name if name.endswith(".svg") else f"{name}.svg"
    try:
        asset = files("mountlet").joinpath(f"assets/{filename}")
        if asset.is_file():
            return str(asset)
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent / "assets" / filename
    return str(fallback) if fallback.is_file() else None


def mountlet_icon(qt: Any, name: str, *, size: int = 22, color: str | None = None) -> Any | None:
    path = icon_path(name)
    icon_type = getattr(qt, "QIcon", None)
    if path is None or icon_type is None:
        return None
    try:
        icon = icon_type(path)
    except Exception:
        return None
    if color is None:
        return icon
    recolored = _recolored_svg_icon(qt, path, size=size, color=color)
    if recolored is not None:
        return recolored
    return icon


def _recolored_svg_icon(qt: Any, path: str, *, size: int, color: str) -> Any | None:
    pixmap_type = getattr(qt, "QPixmap", None)
    painter_type = getattr(qt, "QPainter", None)
    icon_type = getattr(qt, "QIcon", None)
    size_type = getattr(qt, "QSize", None)
    if pixmap_type is None or painter_type is None or icon_type is None or size_type is None:
        return None
    try:
        from PySide6.QtCore import QByteArray
        from PySide6.QtSvg import QSvgRenderer

        svg = Path(path).read_text(encoding="utf-8")
        svg = NEUTRAL_ICON_RE.sub(color, svg)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            return None
    except Exception:
        return None
    try:
        pixmap = pixmap_type(size_type(size, size))
        pixmap.fill(qt.Qt.GlobalColor.transparent)
        painter = painter_type(pixmap)
        renderer.render(painter)
        painter.end()
        return icon_type(pixmap)
    except Exception:
        return None


def apply_button_icon(
    qt: Any,
    button: Any,
    name: str,
    *,
    fallback_text: str = "",
    size: int = 22,
    color: str | None = None,
) -> bool:
    explicit_color = color
    if color is None:
        color = _button_text_color(button)
    icon = mountlet_icon(qt, name, size=size, color=color)
    if icon is None:
        with suppress(Exception):
            button.setText(fallback_text)
        return False
    try:
        button.setIcon(icon)
        if hasattr(button, "setIconSize") and hasattr(qt, "QSize"):
            button.setIconSize(qt.QSize(size, size))
        button.setText("")
        with suppress(Exception):
            button.setProperty("mountletIconName", name)
            button.setProperty("mountletIconFallback", fallback_text)
            button.setProperty("mountletIconSize", size)
            button.setProperty("mountletIconColor", explicit_color or "")
        if explicit_color is None:
            _ensure_dynamic_icon_refresh(qt, button)
        return True
    except Exception:
        with suppress(Exception):
            button.setText(fallback_text)
        return False


def refresh_widget_icons(qt: Any, widget: Any | None) -> None:
    if widget is None:
        return
    _refresh_one_widget_icon(qt, widget)
    children = getattr(widget, "children", lambda: [])()
    for child in children:
        refresh_widget_icons(qt, child)


def _refresh_one_widget_icon(qt: Any, widget: Any) -> None:
    property_getter = getattr(widget, "property", None)
    if not callable(property_getter):
        return
    icon_name = property_getter("mountletIconName")
    if not icon_name:
        return
    fallback = property_getter("mountletIconFallback") or ""
    size = property_getter("mountletIconSize") or 22
    stored_color = property_getter("mountletIconColor") or None
    color = stored_color or _button_text_color(widget)
    with suppress(Exception):
        size = int(size)
    apply_button_icon(qt, widget, str(icon_name), fallback_text=str(fallback), size=size, color=color)
    if not stored_color:
        with suppress(Exception):
            widget.setProperty("mountletIconColor", "")


def _button_text_color(button: Any) -> str | None:
    try:
        palette = button.palette()
        role = button.foregroundRole()
        color = palette.color(role)
        if color.isValid():
            return color.name()
    except Exception:
        pass
    return None


def _ensure_dynamic_icon_refresh(qt: Any, widget: Any) -> None:
    if getattr(widget, "_mountlet_dynamic_icon_filter", None) is not None:
        return
    object_type = getattr(qt, "QObject", None)
    event_type = getattr(qt, "QEvent", None)
    timer_type = getattr(qt, "QTimer", None)
    install_filter = getattr(widget, "installEventFilter", None)
    if object_type is None or event_type is None or timer_type is None or not callable(install_filter):
        return
    changed_types = {
        value
        for value in (
            getattr(event_type.Type, "ApplicationPaletteChange", None),
            getattr(event_type.Type, "PaletteChange", None),
            getattr(event_type.Type, "StyleChange", None),
            getattr(event_type.Type, "Show", None),
        )
        if value is not None
    }

    class DynamicIconFilter(object_type):
        def __init__(self) -> None:
            super().__init__(widget)
            self.pending = False

        def eventFilter(self, watched: Any, event: Any) -> bool:
            try:
                changed = event.type() in changed_types
            except Exception:
                changed = False
            if changed and not self.pending:
                self.pending = True
                # Palette-change events reach the filter before the widget has
                # adopted the new application palette. Refresh on the next tick.
                timer_type.singleShot(1, self.refresh)
            return False

        def refresh(self) -> None:
            self.pending = False
            with suppress(Exception):
                _refresh_one_widget_icon(qt, widget)

    try:
        event_filter = DynamicIconFilter()
        install_filter(event_filter)
        widget._mountlet_dynamic_icon_filter = event_filter
    except Exception:
        return


__all__ = ["apply_button_icon", "icon_path", "mountlet_icon", "refresh_widget_icons"]
