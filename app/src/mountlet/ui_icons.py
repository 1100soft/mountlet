from __future__ import annotations

from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any


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
    pixmap_type = getattr(qt, "QPixmap", None)
    painter_type = getattr(qt, "QPainter", None)
    color_type = getattr(qt, "QColor", None)
    size_type = getattr(qt, "QSize", None)
    if pixmap_type is None or painter_type is None or color_type is None or size_type is None:
        return icon
    try:
        source = icon.pixmap(size_type(size, size))
        if source.isNull():
            return icon
        pixmap = pixmap_type(source.size())
        pixmap.fill(qt.Qt.GlobalColor.transparent)
        painter = painter_type(pixmap)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(painter_type.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color_type(color))
        painter.end()
        return icon_type(pixmap)
    except Exception:
        return icon


def apply_button_icon(
    qt: Any,
    button: Any,
    name: str,
    *,
    fallback_text: str = "",
    size: int = 22,
    color: str | None = None,
) -> bool:
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
        return True
    except Exception:
        with suppress(Exception):
            button.setText(fallback_text)
        return False


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


__all__ = ["apply_button_icon", "icon_path", "mountlet_icon"]
