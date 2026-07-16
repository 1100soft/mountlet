from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config_tools.shared import app_state_dir, apply_permissions
from .license_control import HTTP_TIMEOUT_SECONDS, license_site_url

NOTICE_API_URL_ENV = "MOUNTLET_NOTICE_API_URL"
NOTICE_LEVEL_CRITICAL = "critical"
NOTICE_LEVEL_IMPORTANT = "important"
NOTICE_LEVEL_INFO = "info"
NOTICE_TYPE_PRICE = "price"


@dataclass(frozen=True)
class Notice:
    id: str
    title: str
    message: str
    level: str = NOTICE_LEVEL_INFO
    type: str = "general"
    url: str = ""
    version: str = "1"

    @property
    def key(self) -> str:
        return f"{self.id}:{self.version}"

    @property
    def critical(self) -> bool:
        return self.level == NOTICE_LEVEL_CRITICAL or self.type == NOTICE_TYPE_PRICE


def fetch_notices(api_url: str | None = None) -> list[Notice]:
    endpoint = _notice_api_url(api_url)
    query = urllib.parse.urlencode({"appVersion": __version__})
    response = _get_json(f"{endpoint}?{query}")
    raw_notices = response.get("notices", [])
    if not isinstance(raw_notices, list):
        return []
    notices: list[Notice] = []
    now = time.time()
    for raw in raw_notices:
        if not isinstance(raw, dict):
            continue
        notice = _notice_from_dict(raw, now=now)
        if notice is not None:
            notices.append(notice)
    return notices


def unseen_notices(notices: list[Notice]) -> list[Notice]:
    seen = _load_seen()
    return [notice for notice in notices if not seen.get(notice.key)]


def mark_seen(notice: Notice) -> None:
    state = _load_seen()
    state[notice.key] = int(time.time())
    _save_seen(state)


def mark_seen_many(notices: list[Notice]) -> None:
    state = _load_seen()
    now = int(time.time())
    for notice in notices:
        state[notice.key] = now
    _save_seen(state)


def _notice_api_url(api_url: str | None = None) -> str:
    configured = os.environ.get(NOTICE_API_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    if api_url:
        return api_url.rstrip("/")
    return f"{license_site_url().rstrip('/')}/api/notices"


def _notice_from_dict(raw: dict[str, Any], *, now: float) -> Notice | None:
    notice_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    message = str(raw.get("message") or "").strip()
    if not notice_id or not title or not message:
        return None
    starts_at = _parse_time(raw.get("startsAt") or raw.get("starts_at"))
    ends_at = _parse_time(raw.get("endsAt") or raw.get("ends_at"))
    if starts_at and now < starts_at:
        return None
    if ends_at and now > ends_at:
        return None
    level = str(raw.get("level") or NOTICE_LEVEL_INFO).strip().lower()
    if level not in {NOTICE_LEVEL_INFO, NOTICE_LEVEL_IMPORTANT, NOTICE_LEVEL_CRITICAL}:
        level = NOTICE_LEVEL_INFO
    notice_type = str(raw.get("type") or "general").strip().lower() or "general"
    return Notice(
        id=notice_id,
        title=title,
        message=message,
        level=level,
        type=notice_type,
        url=str(raw.get("url") or "").strip(),
        version=str(raw.get("version") or "1").strip() or "1",
    )


def _parse_time(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": f"Mountlet/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        try:
            data = json.loads(payload)
            message = str(data.get("error") or payload)
        except Exception:
            message = payload or str(exc)
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the notice server: {exc.reason}") from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The notice server returned invalid data.") from exc
    return data if isinstance(data, dict) else {}


def _seen_path() -> Path:
    return app_state_dir() / "notices.json"


def _load_seen() -> dict[str, int]:
    path = _seen_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): int(value) for key, value in data.items() if isinstance(value, (int, float))}


def _save_seen(state: dict[str, int]) -> None:
    path = _seen_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    apply_permissions(path)
