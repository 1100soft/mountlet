from __future__ import annotations

import json
import os
import threading
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
    updated_at: str = ""

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
    remember_notices(notices)
    return notices


def unseen_notices(notices: list[Notice]) -> list[Notice]:
    state = _load_state()
    return [notice for notice in notices if notice.key not in state["seen"] and notice.key not in state["deleted"]]


def mark_seen(notice: Notice) -> None:
    with _STATE_LOCK:
        state = _load_state()
        state["seen"][notice.key] = int(time.time())
        _save_state(state)


def mark_seen_many(notices: list[Notice]) -> None:
    with _STATE_LOCK:
        state = _load_state()
        now = int(time.time())
        for notice in notices:
            state["seen"][notice.key] = now
        _save_state(state)


def mark_unread(notice: Notice) -> None:
    with _STATE_LOCK:
        state = _load_state()
        state["seen"].pop(notice.key, None)
        _save_state(state)


def remember_notices(notices: list[Notice]) -> None:
    if not notices:
        return
    with _STATE_LOCK:
        state = _load_state()
        now = int(time.time())
        for notice in notices:
            current = state["history"].get(notice.key, {})
            state["history"][notice.key] = {
                **_notice_to_dict(notice),
                "receivedAt": int(current.get("receivedAt") or now),
            }
        _save_state(state)


def notification_history() -> list[Notice]:
    state = _load_state()
    values: list[tuple[int, Notice]] = []
    for key, raw in state["history"].items():
        if key in state["deleted"] or not isinstance(raw, dict):
            continue
        notice = _notice_from_history(raw)
        if notice is not None:
            values.append((int(raw.get("receivedAt") or 0), notice))
    values.sort(key=lambda item: item[0], reverse=True)
    return [notice for _received, notice in values]


def is_seen(notice: Notice) -> bool:
    return notice.key in _load_state()["seen"]


def delete_notice(notice: Notice) -> bool:
    if notice.critical:
        return False
    with _STATE_LOCK:
        state = _load_state()
        state["deleted"][notice.key] = int(time.time())
        state["seen"].pop(notice.key, None)
        _save_state(state)
    return True


def clear_deletable_history() -> int:
    with _STATE_LOCK:
        state = _load_state()
        deleted = 0
        for key, raw in list(state["history"].items()):
            notice = _notice_from_history(raw) if isinstance(raw, dict) else None
            if notice is None or notice.critical:
                continue
            state["deleted"][key] = int(time.time())
            state["seen"].pop(key, None)
            deleted += 1
        _save_state(state)
    return deleted


def _notice_api_url(api_url: str | None = None) -> str:
    configured = os.environ.get(NOTICE_API_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    if api_url:
        return api_url.rstrip("/")
    return f"{license_site_url().rstrip('/')}/api/notices"


def _notice_from_dict(raw: dict[str, Any], *, now: float, active_only: bool = True) -> Notice | None:
    notice_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    message = str(raw.get("message") or "").strip()
    if not notice_id or not title or not message:
        return None
    starts_at = _parse_time(raw.get("startsAt") or raw.get("starts_at"))
    ends_at = _parse_time(raw.get("endsAt") or raw.get("ends_at"))
    if active_only and starts_at and now < starts_at:
        return None
    if active_only and ends_at and now > ends_at:
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
        updated_at=str(raw.get("updatedAt") or raw.get("updated_at") or "").strip(),
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


_STATE_LOCK = threading.RLock()


def _empty_state() -> dict[str, dict[str, Any]]:
    return {"seen": {}, "deleted": {}, "history": {}}


def _load_state() -> dict[str, dict[str, Any]]:
    path = _seen_path()
    with _STATE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    if not any(key in data for key in ("seen", "deleted", "history")):
        data = {"seen": data, "deleted": {}, "history": {}}
    state = _empty_state()
    for group in state:
        value = data.get(group, {})
        if isinstance(value, dict):
            state[group] = {str(key): item for key, item in value.items()}
    return state


def _save_state(state: dict[str, dict[str, Any]]) -> None:
    path = _seen_path()
    with _STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        apply_permissions(path)


def _notice_to_dict(notice: Notice) -> dict[str, str]:
    return {
        "id": notice.id,
        "title": notice.title,
        "message": notice.message,
        "level": notice.level,
        "type": notice.type,
        "url": notice.url,
        "version": notice.version,
        "updatedAt": notice.updated_at,
    }


def _notice_from_history(raw: dict[str, Any]) -> Notice | None:
    return _notice_from_dict(raw, now=time.time(), active_only=False)
