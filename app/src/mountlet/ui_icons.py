from __future__ import annotations

from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

NEUTRAL_ICON_COLORS = ("#334155", "#333333", "#000000")


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
        for neutral in NEUTRAL_ICON_COLORS:
            svg = svg.replace(neutral, color).replace(neutral.upper(), color)
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
