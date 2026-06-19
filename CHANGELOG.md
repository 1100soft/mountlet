# Changelog

## 0.2.2 - 2026-06-19

- Added provider status labels in the new-remote wizard to distinguish locally
  tested providers from untested setup paths.
- Added a dedicated Koofr setup path using rclone's Koofr backend instead of
  routing Koofr through WebDAV.
- Added provider-specific S3 setup hints and links for Cloudflare R2, MinIO,
  Amazon S3, Wasabi, and other S3-compatible providers.
- Added post-registration and post-mount connection checks so failed setup does
  not quietly leave unusable remotes in the app list.
- Documented locally tested providers and untested provider paths for the next
  release.

## 0.2.1 - 2026-06-02

- Added a cropped transparent Mountlet icon for the tray and window.
- Improved tray-window behavior so left-click refocuses the existing window and first show positions it near the tray icon.
- Improved GUI quit handling by stopping refresh timers, hiding UI surfaces, and ignoring late background UI updates during shutdown.
- Reordered app and mount settings by likely everyday use.
- Renamed the per-remote mount path field to "Local folder name" to clarify that it changes the local mount folder, not the cloud remote.
- Improved advanced rclone controls with checkboxes and combo boxes for boolean and limited-choice fields.
- Added GUI menu options to open app, mount, rclone, and FUSE config files.

## 0.2.0 - 2026-06-02

- Renamed the project, import package, installed command, and user config directory to Mountlet.
- Added an optional PySide6 desktop tray preview with mount, unmount, restart-mount, and open-folder actions.
- Split tray interactions between hover status, a left-click Mountlet window, and right-click app actions.
- Added compact remote strips with visual and numerical storage usage, mount toggles, and click-to-open behavior.
- Added separate GUI settings dialogs for app-level and per-remote Mountlet config fields, with raw file access for technical users.
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
