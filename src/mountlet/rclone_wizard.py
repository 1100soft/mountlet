#!/usr/bin/env python3

from __future__ import annotations

import json
import configparser
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_tools.shared import default_config_path, find_rclone


class RcloneWizardError(RuntimeError):
    pass


@dataclass(frozen=True)
class RcloneConfigStep:
    state: str
    option: dict[str, Any]
    error: str = ""
    result: str = ""

    @property
    def complete(self) -> bool:
        return not self.state


def start_drive_remote(
    remote_name: str,
    *,
    client_id: str = "",
    client_secret: str = "",
    local_auth: bool = True,
    shared_drive: bool = False,
    team_drive: str = "",
) -> RcloneConfigStep:
    return _run_config_create(
        remote_name,
        "drive",
        _drive_config_args(
            client_id=client_id,
            client_secret=client_secret,
            local_auth=local_auth,
            shared_drive=shared_drive,
            team_drive=team_drive,
        ),
    )


def continue_drive_remote(
    remote_name: str,
    state: str,
    result: str,
    *,
    client_id: str = "",
    client_secret: str = "",
    local_auth: bool = True,
    shared_drive: bool = False,
    team_drive: str = "",
) -> RcloneConfigStep:
    _ensure_drive_remote_config(remote_name, client_id=client_id, client_secret=client_secret)
    return _run_config_create(
        remote_name,
        "drive",
        [
            *_drive_config_args(
                client_id=client_id,
                client_secret=client_secret,
                local_auth=local_auth,
                shared_drive=shared_drive,
                team_drive=team_drive,
            ),
            "--continue",
            "--state",
            state,
            "--result",
            result,
        ],
    )


def _drive_config_args(
    *,
    client_id: str = "",
    client_secret: str = "",
    local_auth: bool | None = None,
    shared_drive: bool | None = None,
    team_drive: str = "",
) -> list[str]:
    args = [
        "client_id",
        client_id.strip(),
        "client_secret",
        client_secret.strip(),
        "scope",
        "drive",
    ]
    if local_auth is not None:
        args.extend(["config_is_local", "true" if local_auth else "false"])
    if shared_drive is not None:
        args.extend(["config_team_drive", "true" if shared_drive else "false"])
    if team_drive.strip():
        args.extend(["team_drive", team_drive.strip()])
    return args


def _run_config_create(remote_name: str, remote_type: str, args: list[str]) -> RcloneConfigStep:
    binary = find_rclone()
    if not binary:
        raise RcloneWizardError("rclone is not installed or RCLONE_PATH is not set.")

    config_path = default_config_path()
    _ensure_config_parent(config_path)
    command = [
        binary,
        "--config",
        str(config_path),
        "config",
        "create",
        remote_name,
        remote_type,
        "--non-interactive",
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise RcloneWizardError(f"Could not run rclone: {exc}") from exc

    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip())
    if completed.returncode != 0:
        raise RcloneWizardError(output.strip() or f"rclone exited with code {completed.returncode}.")

    if not output.strip():
        return RcloneConfigStep(state="", option={})

    data = _extract_json_object(output)
    return RcloneConfigStep(
        state=str(data.get("State", "")),
        option=data.get("Option") or {},
        error=str(data.get("Error", "")),
        result=str(data.get("Result", "")),
    )


def _ensure_config_parent(config_path: Path) -> None:
    config_path.expanduser().parent.mkdir(parents=True, exist_ok=True)


def _ensure_drive_remote_config(remote_name: str, *, client_id: str = "", client_secret: str = "") -> None:
    config_path = default_config_path()
    _ensure_config_parent(config_path)
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path, encoding="utf-8")
    if not config.has_section(remote_name):
        config.add_section(remote_name)
    section = config[remote_name]
    section["type"] = "drive"
    section["scope"] = section.get("scope", "drive") or "drive"
    section["client_id"] = client_id.strip()
    section["client_secret"] = client_secret.strip()
    with config_path.open("w", encoding="utf-8") as handle:
        config.write(handle)


def _extract_json_object(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    start = output.find("{")
    while start >= 0:
        try:
            parsed, _end = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            start = output.find("{", start + 1)
            continue
        if isinstance(parsed, dict):
            return parsed
        start = output.find("{", start + 1)
    raise RcloneWizardError("rclone did not return a usable JSON response.")


__all__ = [
    "RcloneConfigStep",
    "RcloneWizardError",
    "continue_drive_remote",
    "start_drive_remote",
]
