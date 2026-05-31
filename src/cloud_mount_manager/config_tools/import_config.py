#!/usr/bin/env python3

"""Import an rclone configuration bundle into the active environment."""

from __future__ import annotations

import argparse
from pathlib import Path

from .shared import (
    copy_file,
    default_config_path,
    ensure_dir,
    find_client_secrets,
    print_remote_list,
    read_remotes,
    run_rclone_version,
    verify_remotes,
    reconnect_remotes,
)


def import_bundle(args: argparse.Namespace) -> int:
    target_conf = default_config_path()
    target_dir = target_conf.parent

    if args.target_dir:
        target_dir = Path(args.target_dir).expanduser().resolve()
        target_conf = target_dir / target_conf.name

    ensure_dir(target_dir)

    src_config = Path(args.config).expanduser().resolve()
    if not src_config.exists():
        print(f"[!] Config file not found: {src_config}")
        return 1

    copied_conf = copy_file(src_config, target_conf, backup=args.backup, dry_run=args.dry_run)
    if copied_conf:
        action = "would copy" if args.dry_run else "copied"
        print(f"[*] {action} {src_config} -> {copied_conf}")

    secrets: list[Path] = []
    for secret in args.client_secret:
        secret_path = Path(secret).expanduser().resolve()
        if secret_path.exists():
            secrets.append(secret_path)
        else:
            print(f"[!] client secret missing: {secret_path}")

    if args.auto_discover_secrets:
        for item in find_client_secrets(src_config.parent):
            if item not in secrets:
                secrets.append(item)

    for secret in secrets:
        dest_secret = target_dir / secret.name
        copied_secret = copy_file(secret, dest_secret, backup=args.backup, dry_run=args.dry_run)
        if copied_secret:
            action = "would copy" if args.dry_run else "copied"
            print(f"[*] {action} {secret} -> {copied_secret}")

    reference_config = src_config if args.dry_run else target_conf
    remotes = read_remotes(reference_config)
    print_remote_list(remotes, reference_config)

    if args.dry_run:
        return 0

    version_info = run_rclone_version()
    print(f"[*] rclone check: {version_info}")
    if not remotes or args.skip_verify:
        if remotes and args.skip_verify:
            print("[i] Verification skipped. Run 'cloud-mount-manager verify' when ready.")
        elif remotes:
            print("[i] No remotes found to verify.")
        return 0

    print("[i] Verifying imported remotes...")
    results = verify_remotes(remotes)
    failures = [name for name, (ok, _) in results.items() if not ok]

    if failures and args.verify_auto_reconnect:
        print("[i] Attempting to reconnect failing remotes...")
        reconnect_remotes(failures, args.reconnect_auto_confirm)
        print_remote_list(failures, reference_config, label="Re-checking remotes after reconnect from")
        results.update(verify_remotes(failures))
        failures = [name for name, (ok, _) in results.items() if not ok]

    if failures:
        for name in failures:
            print(f"[!] {name}: still failing after import.")
        return 1

    print("[✓] All imported remotes verified.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import an rclone configuration and credentials bundle.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the rclone.conf file to import.",
    )
    parser.add_argument(
        "--client-secret",
        action="append",
        default=[],
        help="Path to a client_secret JSON file to import (repeatable).",
    )
    parser.add_argument(
        "--no-auto-discover",
        dest="auto_discover_secrets",
        action="store_false",
        help="Disable searching for client_secret*.json next to the config file.",
    )
    parser.add_argument(
        "--target-dir",
        help="Override destination directory (default: OS rclone directory).",
    )
    parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Do not backup existing files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without copying.")
    parser.add_argument(
        "--no-verify",
        dest="skip_verify",
        action="store_true",
        help="Skip verification after import.",
    )
    parser.add_argument(
        "--verify-auto-reconnect",
        dest="verify_auto_reconnect",
        action="store_true",
        help="After import verification, run reconnect for failing remotes.",
    )
    parser.add_argument(
        "--no-reconnect-auto-confirm",
        dest="reconnect_auto_confirm",
        action="store_false",
        help="When auto-reconnecting, do not pass --auto-confirm to rclone.",
    )
    parser.set_defaults(
        auto_discover_secrets=True,
        backup=True,
        skip_verify=False,
        verify_auto_reconnect=False,
        reconnect_auto_confirm=True,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return import_bundle(args)


if __name__ == "__main__":
    raise SystemExit(main())
