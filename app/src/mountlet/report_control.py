from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from . import build_info
from .config_tools.shared import app_state_dir, apply_permissions
from .license_control import license_site_url

REPORT_API_URL_ENV = "MOUNTLET_REPORT_API_URL"
HTTP_TIMEOUT_SECONDS = 15
MAX_LOG_CHARS = 18_000
MAX_MESSAGE_CHARS = 8_000
CLEAN_SHUTDOWN_MARKER = "Mountlet shutdown cleanly"
REPORT_USER_AGENT = f"Mountlet/{__version__} (+https://mountlet.app)"
LICENSE_KEY_RE = re.compile(r"\bMNT-[A-Z0-9-]{8,}\b", re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|secret|password|pass|access_key|secret_key|client_secret)\b([\"'\s:=]+)([^\"'\s,;]+)"
)
URL_SECRET_RE = re.compile(r"(?i)([?&](?:token|key|secret|password|code)=)[^&\s]+")


def report_api_url(*, site_url: str | None = None) -> str:
    configured = os.environ.get(REPORT_API_URL_ENV, "").strip()
    if configured:
        return configured
    packaged = _packaged_report_api_url()
    if packaged:
        return packaged
    base = (site_url or license_site_url()).rstrip("/")
    return f"{base}/api/report"


def _packaged_report_api_url() -> str:
    return str(build_info.data().get("reportApiUrl") or "").strip()


def runtime_log_path() -> Path:
    return app_state_dir() / "runtime.log"


def rclone_log_path() -> Path:
    return app_state_dir() / "rclone-output.log"


def crash_report_marker_path() -> Path:
    return app_state_dir() / "last-crash-report.json"


def read_text_tail(path: Path, limit: int = MAX_LOG_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:] if len(text) > limit else text


def redact_text(text: str) -> str:
    redacted = LICENSE_KEY_RE.sub("MNT-[redacted]", text)
    redacted = URL_SECRET_RE.sub(r"\1[redacted]", redacted)
    return SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", redacted)


def latest_crash_log() -> str:
    text = read_text_tail(runtime_log_path())
    if "Fatal Python error" not in text and "Unhandled exception" not in text:
        return ""
    marker = max(text.rfind("Fatal Python error"), text.rfind("Unhandled exception"))
    clean_marker = text.rfind(CLEAN_SHUTDOWN_MARKER)
    if clean_marker > marker:
        return ""
    return text[marker:] if marker >= 0 else text


def crash_fingerprint(text: str) -> str:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def unreported_crash_log() -> str:
    text = latest_crash_log()
    if not text:
        return ""
    fingerprint = crash_fingerprint(text)
    try:
        marker = json.loads(crash_report_marker_path().read_text(encoding="utf-8"))
    except Exception:
        marker = {}
    if marker.get("fingerprint") == fingerprint:
        return ""
    return text


def mark_crash_reported(text: str) -> None:
    if not text:
        return
    path = crash_report_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": crash_fingerprint(text), "reportedAt": int(time.time())}
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    apply_permissions(path)


def mark_clean_shutdown() -> None:
    path = runtime_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{CLEAN_SHUTDOWN_MARKER}\n")
    apply_permissions(path)


def report_payload(
    *,
    kind: str,
    message: str,
    contact: str = "",
    include_logs: bool = True,
    crash_log: str = "",
) -> dict[str, Any]:
    logs: dict[str, str] = {}
    if include_logs:
        runtime = crash_log or read_text_tail(runtime_log_path())
        rclone = read_text_tail(rclone_log_path())
        if runtime:
            logs["runtime"] = redact_text(runtime)
        if rclone:
            logs["rclone"] = redact_text(rclone)
    return {
        "kind": kind,
        "message": redact_text(message.strip())[:MAX_MESSAGE_CHARS],
        "contact": contact.strip()[:240],
        "metadata": {
            "appVersion": __version__,
            "buildChannel": build_info.channel(),
            "buildId": build_info.identifier(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "node": platform.node() or socket.gethostname(),
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "logs": logs,
    }


def submit_report(payload: dict[str, Any], *, api_url: str | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url or report_api_url(),
        data=data,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": REPORT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc.read().decode("utf-8", errors="replace"))
        raise RuntimeError(f"Report server returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach report server: {exc.reason}") from exc
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("Report server returned an invalid response.") from exc
    if not parsed.get("ok"):
        raise RuntimeError(str(parsed.get("error") or "Report was not accepted."))
    return parsed


def _http_error_detail(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    detail = parsed.get("error") if isinstance(parsed, dict) else ""
    if not isinstance(detail, str) or not detail:
        return body[:500]
    try:
        nested = json.loads(detail)
    except json.JSONDecodeError:
        return detail[:500]
    if isinstance(nested, dict):
        message = nested.get("message") or nested.get("error") or detail
        return str(message)[:500]
    return detail[:500]
