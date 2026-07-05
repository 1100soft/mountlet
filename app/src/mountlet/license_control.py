from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import secrets
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from . import __version__
from .config_tools.shared import app_cache_dir, app_config_dir, app_state_dir, apply_permissions, ensure_app_directories

TRIAL_DAYS = 7
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 12

DEFAULT_LICENSE_API_URL = "https://mountlet.app/api/license"
LICENSE_API_URL_ENV = "MOUNTLET_LICENSE_API_URL"
LICENSE_PUBLIC_KEY_ENV = "MOUNTLET_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "MOUNTLET_LICENSE_PUBLIC_KEY_FILE"

_TRIAL_SALT = b"mountlet trial state v1"
_TRIAL_VERSION = 1


@dataclass(frozen=True)
class LicenseStatus:
    state: str
    summary: str
    trial_days_remaining: int = 0
    licensed_email: str = ""
    plan: str = ""
    max_devices: int = 0
    device_label: str = ""
    expires_at: str = ""

    @property
    def allowed(self) -> bool:
        return self.state in {"licensed", "trial"}


def current_status(now: float | None = None) -> LicenseStatus:
    token_payload = load_license_payload()
    if token_payload is not None:
        email = str(token_payload.get("email") or "")
        plan = str(token_payload.get("plan") or "")
        max_devices = _int_value(token_payload.get("maxDevices"), 0)
        device_label = str(token_payload.get("deviceLabel") or "")
        parts = ["Licensed"]
        if plan:
            parts.append(plan)
        if email:
            parts.append(email)
        return LicenseStatus(
            state="licensed",
            summary=" - ".join(parts),
            licensed_email=email,
            plan=plan,
            max_devices=max_devices,
            device_label=device_label,
        )

    trial = load_or_create_trial(now=now)
    now_value = _now(now)
    started = _float_value(trial.get("started_at"), now_value)
    last_seen = _float_value(trial.get("last_seen_at"), started)
    if now_value + 86_400 < last_seen:
        return LicenseStatus("expired", "Trial needs activation")
    remaining = int(max(0, started + TRIAL_SECONDS - now_value) // 86_400)
    if now_value <= started + TRIAL_SECONDS:
        day_text = "1 day" if remaining == 1 else f"{remaining} days"
        if remaining == 0:
            day_text = "less than 1 day"
        return LicenseStatus("trial", f"Trial: {day_text} remaining", trial_days_remaining=remaining)
    return LicenseStatus("expired", "Trial expired")


def status_summary() -> str:
    return current_status().summary


def activate_license(license_key: str, *, device_label: str = "", api_url: str | None = None) -> LicenseStatus:
    key = license_key.strip()
    if not key:
        raise RuntimeError("Enter a license key.")
    label = device_label.strip() or default_device_label()
    response = _post_json(
        _api_endpoint(api_url, "activate"),
        {
            "licenseKey": key,
            "deviceLabel": label,
            "deviceFingerprint": device_fingerprint(),
            "platform": platform.platform(),
            "appVersion": __version__,
        },
    )
    token = str(response.get("token") or "")
    if not token:
        raise RuntimeError("The license server did not return a license token.")
    payload = verify_license_token(token)
    store_license_token(token)
    return LicenseStatus(
        state="licensed",
        summary="Licensed",
        licensed_email=str(payload.get("email") or ""),
        plan=str(payload.get("plan") or ""),
        max_devices=_int_value(payload.get("maxDevices"), 0),
        device_label=str(payload.get("deviceLabel") or label),
    )


def list_devices(api_url: str | None = None) -> list[dict[str, Any]]:
    token = load_license_token()
    if not token:
        raise RuntimeError("Activate Mountlet before listing devices.")
    response = _post_json(_api_endpoint(api_url, "devices"), {"token": token})
    devices = response.get("devices", [])
    if not isinstance(devices, list):
        return []
    return [device for device in devices if isinstance(device, dict)]


def deactivate_device(device_id: str | None = None, *, api_url: str | None = None) -> None:
    token = load_license_token()
    if not token:
        clear_license_token()
        return
    body: dict[str, Any] = {"token": token}
    if device_id:
        body["deviceId"] = device_id
    _post_json(_api_endpoint(api_url, "deactivate"), body)
    payload = load_license_payload()
    if not device_id or (payload and str(payload.get("deviceId") or "") == device_id):
        clear_license_token()


def default_device_label() -> str:
    node = platform.node() or socket.gethostname() or "This device"
    system = platform.system() or "Desktop"
    return f"{node} ({system})"


def device_fingerprint() -> str:
    trial = load_or_create_trial()
    install_id = str(trial.get("install_id") or "")
    material = "|".join([machine_hint(), install_id])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def machine_hint() -> str:
    material = "|".join(
        [
            platform.system(),
            platform.machine(),
            platform.node(),
            str(uuid.getnode()),
            str(Path.home()),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_or_create_trial(now: float | None = None) -> dict[str, Any]:
    ensure_app_directories()
    records = [_decode_trial_record(path) for path in _trial_paths()]
    valid = [record for record in records if record is not None]
    now_value = _now(now)
    if valid:
        selected = min(valid, key=lambda item: _float_value(item.get("started_at"), now_value))
        selected["last_seen_at"] = max(_float_value(selected.get("last_seen_at"), now_value), now_value)
    else:
        selected = {
            "version": _TRIAL_VERSION,
            "install_id": secrets.token_urlsafe(24),
            "machine_hint": machine_hint(),
            "started_at": now_value,
            "last_seen_at": now_value,
        }
    _write_trial_record(selected)
    return selected


def load_license_token() -> str:
    for path in _license_token_paths():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def load_license_payload() -> dict[str, Any] | None:
    token = load_license_token()
    if not token:
        return None
    try:
        return verify_license_token(token)
    except RuntimeError:
        return None


def store_license_token(token: str) -> None:
    for path in _license_token_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token.strip() + "\n", encoding="utf-8")
            apply_permissions(path)
        except OSError:
            continue


def clear_license_token() -> None:
    for path in _license_token_paths():
        try:
            path.unlink()
        except OSError:
            continue


def verify_license_token(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise RuntimeError("Invalid license token format.")
    header_bytes = _b64decode(parts[0])
    payload_bytes = _b64decode(parts[1])
    signature = _b64decode(parts[2])
    try:
        header = json.loads(header_bytes.decode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid license token data.") from exc
    if header.get("alg") not in {"ES256", "ES256-DER"}:
        raise RuntimeError("Unsupported license token signature.")
    public_key = _load_public_key()
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    if len(signature) == 64:
        signature = encode_dss_signature(
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"),
        )
    try:
        public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise RuntimeError("License token signature is not valid.") from exc
    expires_at = str(payload.get("expiresAt") or "")
    if expires_at:
        with _suppress_time_parse_errors():
            if _parse_timestamp(expires_at) < _now(None):
                raise RuntimeError("License token has expired.")
    return payload


def _load_public_key() -> ec.EllipticCurvePublicKey:
    pem = os.environ.get(LICENSE_PUBLIC_KEY_ENV, "").strip()
    key_file = os.environ.get(LICENSE_PUBLIC_KEY_FILE_ENV, "").strip()
    if not pem and key_file:
        try:
            pem = Path(key_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("Could not read the license public key file.") from exc
    if not pem:
        raise RuntimeError("License public key is not configured for this build.")
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise RuntimeError("License public key must be an ECDSA P-256 public key.")
    return key


def _api_endpoint(api_url: str | None, action: str) -> str:
    base = (api_url or os.environ.get(LICENSE_API_URL_ENV) or DEFAULT_LICENSE_API_URL).rstrip("/")
    return f"{base}/{action}"


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(detail)
            message = str(parsed.get("error") or detail)
        except json.JSONDecodeError:
            message = detail or str(exc)
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the license server: {exc.reason}") from exc
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The license server returned invalid data.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("The license server returned invalid data.")
    if parsed.get("error"):
        raise RuntimeError(str(parsed["error"]))
    return parsed


def _decode_trial_record(path: Path) -> dict[str, Any] | None:
    try:
        encoded = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not encoded:
        return None
    try:
        envelope = json.loads(_b64decode(encoded).decode("utf-8"))
        payload = str(envelope["payload"])
        signature = str(envelope["signature"])
        expected = _trial_signature(payload)
        if not hmac.compare_digest(signature, expected):
            return None
        record = json.loads(_b64decode(payload).decode("utf-8"))
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("version") != _TRIAL_VERSION or record.get("machine_hint") != machine_hint():
        return None
    return record


def _write_trial_record(record: dict[str, Any]) -> None:
    payload = _b64encode(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    envelope = {
        "payload": payload,
        "signature": _trial_signature(payload),
    }
    encoded = _b64encode(json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for path in _trial_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded + "\n", encoding="utf-8")
            apply_permissions(path)
        except OSError:
            continue


def _trial_signature(payload: str) -> str:
    key = hashlib.sha256(_TRIAL_SALT + machine_hint().encode("utf-8")).digest()
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _trial_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "trial.dat",
        app_config_dir() / ".license-trial",
        app_cache_dir() / ".license-trial",
    )


def _license_token_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "license-token.jwt",
        app_config_dir() / "license-token.jwt",
    )


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class _suppress_time_parse_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return exc_type in {TypeError, ValueError, OSError}


__all__ = [
    "DEFAULT_LICENSE_API_URL",
    "LICENSE_API_URL_ENV",
    "LICENSE_PUBLIC_KEY_ENV",
    "LicenseStatus",
    "activate_license",
    "clear_license_token",
    "current_status",
    "deactivate_device",
    "default_device_label",
    "device_fingerprint",
    "list_devices",
    "load_license_payload",
    "load_or_create_trial",
    "status_summary",
    "store_license_token",
    "verify_license_token",
]
