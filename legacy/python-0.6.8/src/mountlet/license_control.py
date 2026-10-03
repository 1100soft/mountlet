from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
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
from . import build_info
from .config_tools.shared import app_cache_dir, app_config_dir, app_state_dir, apply_permissions, ensure_app_directories

TRIAL_DAYS = 7
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 12

DEFAULT_LICENSE_API_URL = "https://mountlet.app/api/license"
DEFAULT_LICENSE_SITE_URL = "https://mountlet.app"
LICENSE_USER_AGENT = f"Mountlet/{__version__} (+{DEFAULT_LICENSE_SITE_URL})"
LICENSE_API_URL_ENV = "MOUNTLET_LICENSE_API_URL"
LICENSE_SITE_URL_ENV = "MOUNTLET_LICENSE_SITE_URL"
LICENSE_PUBLIC_KEY_ENV = "MOUNTLET_LICENSE_PUBLIC_KEY"
LICENSE_PUBLIC_KEY_FILE_ENV = "MOUNTLET_LICENSE_PUBLIC_KEY_FILE"
TRIAL_DURABLE_DIR_ENV = "MOUNTLET_TRIAL_DURABLE_DIR"

_TRIAL_SALT = b"mountlet trial state v1"
_TRIAL_VERSION = 2
_LEGACY_TRIAL_VERSION = 1


@dataclass(frozen=True)
class LicenseStatus:
    state: str
    summary: str
    trial_days_remaining: int = 0
    license_key: str = ""
    licensed_email: str = ""
    plan: str = ""
    license_kind: str = ""
    max_devices: int = 0
    device_label: str = ""
    expires_at: str = ""

    @property
    def allowed(self) -> bool:
        return self.state in {"licensed", "trial"}


def _licensed_summary_parts(*, plan: str, license_kind: str, email: str = "") -> list[str]:
    parts = ["Beta license" if license_kind == "beta" else "Licensed"]
    if plan:
        parts.append(plan)
    if email:
        parts.append(email)
    return parts


def current_status(now: float | None = None) -> LicenseStatus:
    token = load_license_token()
    if token:
        try:
            token_payload = verify_license_token(token)
        except RuntimeError as exc:
            if "expired" in str(exc).lower():
                expired_payload = _unverified_license_payload(token)
                key = load_license_key()
                if str(expired_payload.get("licenseKind") or "") == "beta" or key.upper().startswith("MTB-"):
                    if key:
                        try:
                            return activate_license(key)
                        except RuntimeError:
                            pass
                    clear_license_token()
                    clear_license_key()
                    return LicenseStatus(
                        "expired",
                        "Public beta access has ended. Buy a license to continue.",
                        license_kind="beta",
                    )
                clear_license_token()
                clear_license_key()
                reset_trial(now=now)
            else:
                return LicenseStatus("expired", f"License cannot be verified: {exc}")
            token_payload = None
        if token_payload is not None:
            email = str(token_payload.get("email") or "")
            plan = str(token_payload.get("plan") or "")
            license_kind = str(token_payload.get("licenseKind") or "paid")
            max_devices = _int_value(token_payload.get("maxDevices"), 0)
            device_label = str(token_payload.get("deviceLabel") or "")
            expires_at = _display_timestamp(str(token_payload.get("expiresAt") or ""))
            parts = _licensed_summary_parts(plan=plan, license_kind=license_kind, email=email)
            return LicenseStatus(
                state="licensed",
                summary=" - ".join(parts),
                license_key=load_license_key(),
                licensed_email=email,
                plan=plan,
                license_kind=license_kind,
                max_devices=max_devices,
                device_label=device_label,
                expires_at=expires_at,
            )

    trial = load_or_create_trial(now=now)
    now_value = _now(now)
    started = _float_value(trial.get("started_at"), now_value)
    last_seen = _float_value(trial.get("last_seen_at"), started)
    if now_value + 86_400 < last_seen:
        return LicenseStatus("expired", "Trial needs activation")
    trial_ends = started + TRIAL_SECONDS
    remaining = int(max(0, trial_ends - now_value) // 86_400)
    ends_at = _format_local_timestamp(trial_ends)
    if now_value <= trial_ends:
        day_text = "1 day" if remaining == 1 else f"{remaining} days"
        if remaining == 0:
            day_text = "less than 1 day"
        return LicenseStatus(
            "trial",
            f"Trial: {day_text} remaining; ends {ends_at}",
            trial_days_remaining=remaining,
            expires_at=ends_at,
        )
    return LicenseStatus("expired", f"Trial expired {ends_at}", expires_at=ends_at)


def status_summary() -> str:
    return current_status().summary


def license_site_url(*, api_url: str | None = None) -> str:
    configured = os.environ.get(LICENSE_SITE_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    packaged_site = _packaged_license_site_url()
    if packaged_site:
        return packaged_site.rstrip("/")
    api_base = (api_url or _license_api_base()).strip()
    if api_base:
        parsed = urllib.parse.urlsplit(api_base)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/license"):
            path = path[: -len("/api/license")]
        elif path.endswith("/api"):
            path = path[: -len("/api")]
        if parsed.scheme and parsed.netloc:
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path or "", "", "")).rstrip("/")
    return DEFAULT_LICENSE_SITE_URL


def license_purchase_url(*, add_devices: bool = False, license_key: str = "", api_url: str | None = None) -> str:
    base = license_site_url(api_url=api_url).rstrip("/")
    query: dict[str, str] = {}
    if add_devices:
        query["license_action"] = "add_devices"
    key = license_key.strip()
    if key:
        query["license_key"] = key
    suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
    return f"{base}/{suffix}#pricing"


def is_beta_status(status: LicenseStatus | None = None) -> bool:
    status = status or current_status()
    return status.license_kind == "beta" or status.license_key.upper().startswith("MTB-")


def _packaged_build_info() -> dict[str, Any]:
    return build_info.data()


def _packaged_license_api_url() -> str:
    return str(_packaged_build_info().get("licenseApiUrl") or "").strip()


def _packaged_license_site_url() -> str:
    return str(_packaged_build_info().get("licenseSiteUrl") or "").strip()


def _license_api_base() -> str:
    return (
        os.environ.get(LICENSE_API_URL_ENV, "").strip()
        or _packaged_license_api_url()
        or DEFAULT_LICENSE_API_URL
    ).rstrip("/")


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
    store_license_key(key)
    email = str(payload.get("email") or "")
    plan = str(payload.get("plan") or "")
    license_kind = str(payload.get("licenseKind") or "paid")
    return LicenseStatus(
        state="licensed",
        summary=" - ".join(_licensed_summary_parts(plan=plan, license_kind=license_kind, email=email)),
        license_key=key,
        licensed_email=email,
        plan=plan,
        license_kind=license_kind,
        max_devices=_int_value(payload.get("maxDevices"), 0),
        device_label=str(payload.get("deviceLabel") or label),
        expires_at=_display_timestamp(str(payload.get("expiresAt") or "")),
    )


def license_devices(api_url: str | None = None) -> dict[str, Any]:
    token = load_license_token()
    if not token:
        raise RuntimeError("Activate Mountlet before listing devices.")
    response = _post_json(_api_endpoint(api_url, "devices"), {"token": token})
    refreshed_token = str(response.get("token") or "")
    if refreshed_token:
        verify_license_token(refreshed_token)
        store_license_token(refreshed_token)
    devices = response.get("devices", [])
    if not isinstance(devices, list):
        devices = []
    normalized = [device for device in devices if isinstance(device, dict)]
    return {
        "devices": normalized,
        "usedDevices": _int_value(response.get("usedDevices"), len(normalized)),
        "maxDevices": _int_value(response.get("maxDevices"), current_status().max_devices),
        "expiresAt": str(response.get("expiresAt") or ""),
        "billingModel": str(response.get("billingModel") or ""),
        "licenseKind": str(response.get("licenseKind") or ""),
        "plan": str(response.get("plan") or ""),
    }


def list_devices(api_url: str | None = None) -> list[dict[str, Any]]:
    return list(license_devices(api_url).get("devices") or [])


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
        clear_license_key()


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
    identifier = _stable_machine_identifier()
    material = "|".join(
        [
            platform.system(),
            platform.machine(),
            identifier,
            str(Path.home()),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _stable_machine_identifier() -> str:
    system = platform.system()
    if system == "Linux":
        for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return f"machine-id:{value}"
    elif system == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if str(value).strip():
                return f"machine-guid:{str(value).strip()}"
        except (ImportError, OSError):
            pass
    elif system == "Darwin":
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', str(getattr(result, "stdout", "")))
            if match:
                return f"platform-uuid:{match.group(1)}"
        except (OSError, subprocess.SubprocessError):
            pass

    # uuid.getnode() sets the multicast bit when it had to invent a random
    # value. Never persist that random fallback as a machine identity.
    node = uuid.getnode()
    hardware = f"node:{node:012x}" if not node & (1 << 40) else ""
    return "|".join(part for part in (platform.node(), socket.gethostname(), hardware) if part)


def load_or_create_trial(now: float | None = None) -> dict[str, Any]:
    ensure_app_directories()
    paths = _trial_paths()
    records = [_decode_trial_record(path) for path in paths]
    valid = [record for record in records if record is not None]
    now_value = _now(now)
    if valid:
        selected = min(valid, key=lambda item: _float_value(item.get("started_at"), now_value))
        selected["last_seen_at"] = max(_float_value(selected.get("last_seen_at"), now_value), now_value)
    else:
        selected = _recover_replicated_legacy_trial(paths, now_value) or {
            "install_id": secrets.token_urlsafe(24),
            "started_at": now_value,
            "last_seen_at": now_value,
        }
    selected["version"] = _TRIAL_VERSION
    selected["machine_hint"] = machine_hint()
    _write_trial_record(selected)
    return selected


def reset_trial(now: float | None = None) -> dict[str, Any]:
    now_value = _now(now)
    selected = {
        "version": _TRIAL_VERSION,
        "install_id": secrets.token_urlsafe(24),
        "machine_hint": machine_hint(),
        "started_at": now_value,
        "last_seen_at": now_value,
    }
    _write_trial_record(selected)
    return selected


def expire_trial_for_debug(now: float | None = None) -> dict[str, Any]:
    now_value = _now(now)
    selected = {
        "version": _TRIAL_VERSION,
        "install_id": secrets.token_urlsafe(24),
        "machine_hint": machine_hint(),
        "started_at": now_value - TRIAL_SECONDS - 60,
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


def load_license_key() -> str:
    for path in _license_key_paths():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def store_license_key(license_key: str) -> None:
    value = license_key.strip()
    if not value:
        return
    for path in _license_key_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n", encoding="utf-8")
            apply_permissions(path)
        except OSError:
            continue


def clear_license_key() -> None:
    for path in _license_key_paths():
        try:
            path.unlink()
        except OSError:
            continue


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
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    if len(signature) == 64:
        signature = encode_dss_signature(
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"),
        )
    try:
        _load_public_key().verify(signature, signed, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        if _explicit_public_key_configured():
            raise RuntimeError("License token signature is not valid.") from exc
        try:
            _load_public_key(refresh=True).verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as refreshed_exc:
            raise RuntimeError("License token signature is not valid.") from refreshed_exc
    expires_at = str(payload.get("expiresAt") or "")
    if expires_at:
        with _suppress_time_parse_errors():
            if _parse_timestamp(expires_at) < _now(None):
                raise RuntimeError("License token has expired.")
    return payload


def _unverified_license_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = json.loads(_b64decode(parts[1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_public_key(*, refresh: bool = False) -> ec.EllipticCurvePublicKey:
    pem = os.environ.get(LICENSE_PUBLIC_KEY_ENV, "").strip()
    key_file = os.environ.get(LICENSE_PUBLIC_KEY_FILE_ENV, "").strip()
    if not pem and key_file:
        try:
            pem = Path(key_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("Could not read the license public key file.") from exc
    if not pem and not refresh:
        pem = str(_packaged_build_info().get("licensePublicKey") or "").strip()
    if not pem and not refresh:
        pem = _load_cached_public_key()
    if not pem:
        pem = _fetch_license_public_key()
        _store_cached_public_key(pem)
    return _parse_public_key(pem)


def _parse_public_key(pem: str) -> ec.EllipticCurvePublicKey:
    normalized = pem.replace("\\n", "\n").strip()
    try:
        if normalized.startswith("-----BEGIN"):
            key = serialization.load_pem_public_key(normalized.encode("utf-8"))
        else:
            key = serialization.load_der_public_key(base64.b64decode("".join(normalized.split()), validate=True))
    except (TypeError, ValueError, binascii.Error) as exc:
        raise RuntimeError("License public key is invalid.") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise RuntimeError("License public key must be an ECDSA P-256 public key.")
    if not isinstance(key.curve, ec.SECP256R1):
        raise RuntimeError("License public key must be an ECDSA P-256 public key.")
    return key


def _explicit_public_key_configured() -> bool:
    return bool(
        os.environ.get(LICENSE_PUBLIC_KEY_ENV, "").strip()
        or os.environ.get(LICENSE_PUBLIC_KEY_FILE_ENV, "").strip()
    )


def _fetch_license_public_key() -> str:
    response = _get_json(_api_endpoint(None, "public-key"))
    pem = str(response.get("publicKey") or "").strip()
    if not pem:
        raise RuntimeError("The license server did not return its public key.")
    _parse_public_key(pem)
    return pem


def _load_cached_public_key() -> str:
    for path in _license_public_key_paths():
        try:
            pem = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if pem:
            return pem
    return ""


def _store_cached_public_key(pem: str) -> None:
    normalized = pem.replace("\\n", "\n").strip()
    for path in _license_public_key_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(normalized + "\n", encoding="utf-8")
            apply_permissions(path)
        except OSError:
            continue


def _api_endpoint(api_url: str | None, action: str) -> str:
    base = (api_url or _license_api_base()).rstrip("/")
    return f"{base}/{action}"


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": LICENSE_USER_AGENT,
        },
        method="POST",
    )
    return _read_json_response(request)


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": LICENSE_USER_AGENT},
        method="GET",
    )
    return _read_json_response(request)


def _read_json_response(request: urllib.request.Request) -> dict[str, Any]:
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


def _recover_replicated_legacy_trial(paths: tuple[Path, ...], now_value: float) -> dict[str, Any] | None:
    encoded_records: dict[str, int] = {}
    for path in paths:
        try:
            encoded = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if encoded:
            encoded_records[encoded] = encoded_records.get(encoded, 0) + 1

    # A legacy record used uuid.getnode() in its machine key. On systems where
    # Python generated a random fallback, it cannot be matched next launch.
    # Require matching replicas and its original self-consistent signature
    # before migrating it to the stable v2 machine identity.
    for encoded, count in sorted(encoded_records.items(), key=lambda item: item[1], reverse=True):
        if count < 2:
            continue
        try:
            envelope = json.loads(_b64decode(encoded).decode("utf-8"))
            payload = str(envelope["payload"])
            signature = str(envelope["signature"])
            record = json.loads(_b64decode(payload).decode("utf-8"))
            legacy_hint = str(record["machine_hint"])
            expected = _trial_signature(payload, machine=legacy_hint)
            started_at = float(record["started_at"])
            last_seen_at = float(record["last_seen_at"])
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if (
            record.get("version") != _LEGACY_TRIAL_VERSION
            or not re.fullmatch(r"[0-9a-f]{64}", legacy_hint)
            or not hmac.compare_digest(signature, expected)
            or started_at > now_value + 86_400
            or last_seen_at < started_at
        ):
            continue
        record["last_seen_at"] = max(last_seen_at, now_value)
        return record
    return None


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


def _trial_signature(payload: str, *, machine: str | None = None) -> str:
    key = hashlib.sha256(_TRIAL_SALT + (machine or machine_hint()).encode("utf-8")).digest()
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _trial_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "trial.dat",
        app_config_dir() / ".license-trial",
        app_cache_dir() / ".license-trial",
        *_durable_trial_paths(),
    )


def _durable_trial_paths() -> tuple[Path, ...]:
    override = os.environ.get(TRIAL_DURABLE_DIR_ENV, "").strip()
    if override:
        root = Path(override).expanduser()
        return (root / ".mountlet-trial", root / ".mountlet-trial-backup")

    paths = [Path.home() / ".mountlet-license-trial"]
    system = platform.system()
    if system == "Windows":
        for value in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")):
            if value:
                paths.append(Path(value) / "Microsoft" / "MountletTrial.dat")
    elif system == "Darwin":
        paths.append(Path.home() / "Library" / "Preferences" / ".mountlet-license-trial")
        paths.append(Path.home() / "Library" / "Application Support" / ".mountlet-license-trial")
    else:
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        paths.append(state_home / ".mountlet-license-trial")
        paths.append(config_home / ".mountlet-license-trial")
    return tuple(dict.fromkeys(paths))


def _license_token_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "license-token.jwt",
        app_config_dir() / "license-token.jwt",
    )


def _license_key_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "license-key.txt",
        app_config_dir() / "license-key.txt",
    )


def _license_public_key_paths() -> tuple[Path, ...]:
    return (
        app_state_dir() / "license" / "license-public.pem",
        app_config_dir() / "license-public.pem",
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


def _format_local_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _display_timestamp(value: str) -> str:
    if not value:
        return ""
    with _suppress_time_parse_errors():
        return _format_local_timestamp(_parse_timestamp(value))
    return value


def display_timestamp(value: str) -> str:
    return _display_timestamp(value)


class _suppress_time_parse_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return exc_type in {TypeError, ValueError, OSError}


__all__ = [
    "DEFAULT_LICENSE_API_URL",
    "DEFAULT_LICENSE_SITE_URL",
    "LICENSE_API_URL_ENV",
    "LICENSE_SITE_URL_ENV",
    "LICENSE_PUBLIC_KEY_ENV",
    "TRIAL_DURABLE_DIR_ENV",
    "LicenseStatus",
    "activate_license",
    "clear_license_key",
    "clear_license_token",
    "current_status",
    "deactivate_device",
    "display_timestamp",
    "default_device_label",
    "device_fingerprint",
    "expire_trial_for_debug",
    "list_devices",
    "license_devices",
    "license_purchase_url",
    "license_site_url",
    "is_beta_status",
    "load_license_key",
    "load_license_payload",
    "load_or_create_trial",
    "reset_trial",
    "status_summary",
    "store_license_token",
    "verify_license_token",
]
