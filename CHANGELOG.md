# Changelog

## Unreleased

- Renamed the project, import package, installed command, and user config directory to Mountlet.
- Added an optional PySide6 desktop tray preview with mount, unmount, restart-mount, and open-folder actions.
- Split tray interactions between hover status, a left-click Mountlet window, and right-click app actions.
- Added visual and numerical storage usage to the Mountlet window.
- Added app-level and per-remote Mountlet config files.
- Open-folder actions can use current-desktop Dolphin windows on X11 when available, with fallback open strategies elsewhere.

## 0.1.1 - 2026-05-31

- Improved setup guidance when `rclone` is not installed yet.
- Honored `RCLONE_CONFIG` consistently in setup and config helper commands.

## 0.1.0 - 2026-05-31

Initial public CLI release.

- Single public command: `mountlet`.
- Fast readiness check before opening the menu.
- Guided setup flow with `mountlet setup`.
- Optional rclone connection flow with `mountlet setup --configure-rclone`.
- Interactive menu for mounting, unmounting, refreshing, and verifying remotes.
- Subcommands for setup, path inspection, verification, reconnect, import, and export.
- User-specific app directories for config, state, and cache.
- Import/export helpers for rclone configuration bundles.
- `--version` / `-V` version output.
- CI workflow for tests and package build.
- Exported bundles are documented as sensitive credential backups.
