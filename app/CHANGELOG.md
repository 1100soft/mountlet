# Changelog

## 0.7.0 - 2026-08-27

- Replaced the Python/Qt desktop application with the Rust/Tauri implementation.
- Preserved existing settings, rclone, cache, offline, license, notification,
  shortcut, and encrypted configuration-bundle formats.
- Added a virtualized file list and immediate cached navigation for substantially
  lower memory use and faster remote and folder switching.
- Moved rclone, network, sync, report, and file operations out of foreground UI
  paths to keep the application responsive.
- Retained system-tray lifecycle, deterministic tray-relative window placement,
  single and detached browser layouts, and platform-native integration.
- Replaced Python/PyInstaller packaging with Tauri `.deb`, NSIS `.exe`, and
  `.dmg` installers for the existing standard and lean release matrix.

The archived Python changelog is available under
`legacy/python-0.6.8/CHANGELOG.md`.
