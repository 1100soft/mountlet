from __future__ import annotations

from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping


PROVIDER_ASSETS = {
    "drive": "google-drive.png",
    "gphotos": "google-photos.svg",
    "dropbox": "dropbox.svg",
    "onedrive": "onedrive.svg",
    "box": "box.svg",
    "pcloud": "pcloud.png",
    "koofr": "koofr.png",
    "protondrive": "proton-drive.svg",
    "iclouddrive": "icloud.svg",
    "mega": "mega.svg",
    "cloudflare r2": "cloudflare-r2.svg",
    "cloudflare": "cloudflare-r2.svg",
    "amazon s3": "amazon-s3.svg",
    "aws": "amazon-s3.svg",
    "s3": "amazon-s3.svg",
    "other": "amazon-s3.svg",
    "minio": "minio.svg",
    "wasabi": "wasabi.svg",
    "nextcloud": "nextcloud.svg",
    "owncloud": "owncloud.png",
}


def provider_icon(
    qt: Any,
    backend_type: str,
    *,
    provider_name: str = "",
    extra_info: Mapping[str, str] | None = None,
    color: str = "#64748b",
    size: int = 22,
) -> Any:
    key = _provider_key(backend_type, provider_name=provider_name, extra_info=extra_info)
    asset_name = PROVIDER_ASSETS.get(key)
    if asset_name:
        path = _provider_asset_path(asset_name)
        if path:
            with suppress(Exception):
                icon = qt.QIcon(path)
                if not icon.isNull():
                    return icon
    return _initial_icon(qt, _initial_label(provider_name or backend_type), color=color, size=size)


def _provider_key(
    backend_type: str,
    *,
    provider_name: str,
    extra_info: Mapping[str, str] | None,
) -> str:
    backend = str(backend_type or "").strip().casefold()
    provider = str(provider_name or "").strip().casefold()
    details = extra_info or {}
    if backend == "s3":
        return str(details.get("provider") or provider or "s3").strip().casefold()
    if backend == "webdav":
        return str(details.get("vendor") or provider or "webdav").strip().casefold()
    return backend or provider


def _provider_asset_path(name: str) -> str | None:
    try:
        asset = files("mountlet").joinpath(f"assets/providers/{name}")
        if asset.is_file():
            return str(asset)
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent / "assets" / "providers" / name
    return str(fallback) if fallback.is_file() else None


def _initial_label(value: str) -> str:
    for character in str(value or "?"):
        if character.isalnum():
            return character.upper()
    return "?"


def _initial_icon(qt: Any, label: str, *, color: str, size: int) -> Any:
    pixmap = qt.QPixmap(qt.QSize(size, size))
    pixmap.fill(qt.Qt.GlobalColor.transparent)
    painter = qt.QPainter(pixmap)
    try:
        painter.setRenderHint(qt.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(qt.Qt.PenStyle.NoPen)
        painter.setBrush(qt.QColor(color))
        painter.drawRoundedRect(0, 0, size, size, max(3, size // 5), max(3, size // 5))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(max(10, int(size * 0.58)))
        painter.setFont(font)
        painter.setPen(qt.QColor("#ffffff"))
        painter.drawText(0, 0, size, size, int(qt.Qt.AlignmentFlag.AlignCenter), label[:1])
    finally:
        painter.end()
    return qt.QIcon(pixmap)


__all__ = ["provider_icon"]
