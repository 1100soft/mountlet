from __future__ import annotations

import json
import os
from functools import lru_cache
from importlib.resources import files
from typing import Any

BUILD_CHANNELS = {"production", "preview", "local"}


@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    try:
        resource = files("mountlet").joinpath("mountlet-build-info.json")
        if not resource.is_file():
            return {}
        value = json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def channel() -> str:
    configured = os.environ.get("MOUNTLET_BUILD_CHANNEL", "").strip().lower()
    if configured in BUILD_CHANNELS:
        return configured
    packaged = str(data().get("channel") or "").strip().lower()
    return packaged if packaged in BUILD_CHANNELS else "local"


def identifier() -> str:
    return (
        os.environ.get("MOUNTLET_BUILD_ID", "").strip()
        or str(data().get("buildId") or "").strip()
        or "source"
    )


def notice_api_url() -> str:
    return str(data().get("noticeApiUrl") or "").strip()


def visible_label() -> str:
    current = channel()
    if current == "production":
        return ""
    return f"{current.title()} {identifier()}"
