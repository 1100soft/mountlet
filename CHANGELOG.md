# Changelog

## Unreleased

- Added a compact rclone-backed file browser connected directly to the remote
  strips, with remembered per-remote paths and adaptive left/right placement.
- Added cross-remote copy, cut, paste, and drag-and-drop transfers that run
  outside the UI thread.
- Added managed offline files and complete folders, offline availability icons,
  explicit local-copy removal, and disconnected browsing of completed folders.
- Added PyInstaller-based native bundle definitions and GitHub Actions builds
  for Linux x64, Windows x64, macOS arm64, and macOS x64.
- Added automated frozen-executable smoke checks and macOS bundle validation
  for menu-bar-only operation.
- Added platform-native artifact archives that preserve macOS bundle links and
  avoid redundant GitHub upload compression.
- Added test installers for Linux (`.deb`), Windows (setup `.exe` with an
  uninstaller), and macOS (`.dmg`), with CI validation and portable bundles
  retained alongside them.
- Added a resumable graphical prerequisite check that opens installation
  guidance and starts Mountlet when rclone and filesystem support are ready.
- Fixed Wayland tray activation and top-level window handling, and prevented
  frozen Linux builds from passing bundled library paths to Dolphin.
- Added editable Google Drive client IDs and client secrets to remote settings.
- Improved Windows mountpoint detection, rclone mount diagnostics, and installer
  maintenance options for repair/update and uninstall.

## 0.3.0 - 2026-06-20

- Refactored paths, prerequisites, mount lifecycle, process handling,
  start-at-login registration, and desktop integration behind Linux, Windows,
  macOS, and generic Qt platform services.
- Kept Dolphin and Plasma X11 behavior as optional Linux enhancements while
  providing default file-manager and window fallbacks for other environments.
- Added platform adapter tests and Windows-specific directory mountpoint
  preparation. Source-installed tray and mount flows have now been exercised
  on Linux, Windows, and macOS; Windows and macOS remain experimental until
  native packages are available.
- Added cross-platform file-manager discovery and an app-specific selector,
  with platform defaults and automatic fallback when a selected manager is no
  longer installed.
- Opening App settings from the tray now positions and focuses the main window
  at the tray before showing the settings dialog.
- Child dialogs, including Add Remote, now establish their positioned main
  window before appearing when launched directly from the tray.
- Added Windows prerequisite discovery for rclone and WinFsp, PowerShell-safe
  setup guidance, and File Explorer integration.
- Added macOS prerequisite guidance for rclone and macFUSE, Finder integration,
  menu-bar-only operation, native tray click handling, and keep-above behavior.

## 0.2.2 - 2026-06-19

- Added a guided new-remote wizard for major cloud providers, with browser and
  token-based rclone authentication flows, provider-specific fields, official
  setup links, connection validation, and cleanup of incomplete remotes.
- Added remote ordering controls, saved one-time sorting by registration time,
  name, provider, and storage usage, and per-remote move buttons.
- Added provider-colored labels and browser shortcuts, compact mount controls,
  dynamic window sizing, and responsive background mount, unmount, usage, and
  folder-opening operations.
- Added frameless main and configuration windows, an in-window keep-above
  control, and reliable current-desktop tray behavior on Plasma X11. Pinning and
  desktop movement now use EWMH requests without remapping the Qt window.
- Added provider status colors in the new-remote wizard to distinguish locally
  tested providers from untested setup paths without adding extra label text.
- Added a dedicated Koofr setup path using rclone's Koofr backend instead of
  routing Koofr through WebDAV.
- Added provider-specific S3 setup hints and links for Cloudflare R2, MinIO,
  Amazon S3, Wasabi, and other S3-compatible providers.
- Added post-registration and post-mount connection checks so failed setup does
  not quietly leave unusable remotes in the app list.
- Improved remote naming, provider suffixes, credential reuse, OAuth port
  handling, wizard cancellation, application shutdown, and child-window
  lifecycle behavior.
- Documented locally tested providers and untested provider paths.

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
