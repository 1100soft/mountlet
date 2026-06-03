#!/usr/bin/env python3

from __future__ import annotations

import json
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


def start_drive_remote(remote_name: str) -> RcloneConfigStep:
    return _run_config_create(
        remote_name,
        "drive",
        [
            "client_id",
            "",
            "client_secret",
            "",
            "scope",
            "drive",
        ],
    )


def continue_drive_remote(remote_name: str, state: str, result: str) -> RcloneConfigStep:
    return _run_config_create(
        remote_name,
        "drive",
        [
            "--continue",
            "--state",
            state,
            "--result",
            result,
        ],
    )


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
