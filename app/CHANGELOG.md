# Changelog

## 0.6.0 - 2026-07-12

- Added paid-license enforcement for expired trials: Mountlet now opens the
  license window, disables cloud controls, blocks rclone operations, and keeps
  Buy License, About, License, and Quit available.
- Added license purchase, activation, deactivation, device-count, expiry, and
  renewal UI backed by the Mountlet website license API.
- Added a commercial website under `web/` with Stripe checkout, license
  management, Cloudflare Pages Functions, D1 license storage, and R2 download
  routes.
- Centralized website URLs, license API paths, and release filenames so the app,
  website, docs, and deployment checks share the same values.
- Added automated installer uploads from the native package workflow to preview
  or production R2 buckets after all platform package checks pass.
- Updated bundled-rclone packaging to stage official current rclone binaries on
  Linux, Windows, and macOS, avoiding stale package-manager backends for newer
  providers such as Proton Drive and iCloud Drive.
- Fixed a packaged Linux/X11 focus-change crash by removing an accidental
  close-event call from the main-window activation handler, and added persistent
  runtime crash logging for frozen desktop builds.

## 0.5.1 - 2026-07-05

- Fixed macOS bundled builds so they stage official rclone binaries instead of
  Homebrew rclone, restoring native folder mounting with macFUSE.
- Added a live rclone output window with separate current-operation and raw-log
  views, copy output support, and background updates that do not steal focus.
- Improved macOS mount diagnostics, macFUSE-aware mount detection, and mount
  startup timeout guidance.
- Fixed macOS file-browser mouse focus behavior.
- Fixed platform test portability in the macOS mount detection tests.

## 0.5.0 - 2026-07-03

- Made `mountlet` open the desktop app by default, kept `mountlet tray` as a
  compatibility alias, and moved the terminal menu to the explicit
  `mountlet menu` path.
- Added a `desktop` package extra, kept the existing `tray` extra as a
  compatibility name, refreshed install documentation around the desktop-first
  beta path, and included the macOS icon asset in package data.
- Split rclone and native-folder prerequisites: rclone remains required for
  cloud access, while FUSE, WinFsp, and macFUSE are optional and only gate
  native folder mounting. Packaging can now build a lean installer that uses a
  system rclone or a bundled-rclone installer with an app-local rclone binary.
- Split native package CI artifacts into visible `system-rclone` and
  `bundled-rclone` variants, while keeping pipx and source installs as the
  system-Python path for technical users.
- Made keyboard navigation scroll the remote list so the selected remote remains
  visible when the list exceeds the main window's allocated height.
- Allowed per-remote settings to rename the alias part of a remote, preserving
  the provider suffix while migrating Mountlet settings, remembered browser
  paths, and offline snapshots. Mounted remotes can now be renamed or deleted
  after confirmation; Mountlet unmounts first and remounts renamed remotes.
- Enabled offline snapshots in Mountlet Files, including manifest metadata for
  deep cached paths so parent folders remain browseable without a live remote
  connection.
- Made unmounted file opening download a managed local cache copy and open it
  through operating-system file associations. Mountlet now tracks local cache
  edits, uploads them automatically when the cloud file is unchanged, and prompts
  for conflict resolution when both sides changed.
- Added cache cleanup for resolved ordinary cached files while preserving files
  marked as available offline.
- Kept offline cache files user-writable so external apps such as PDF readers
  and spreadsheet editors can open cached files normally.
- Moved offline snapshots out of hidden or separate cache locations and into
  the configured app folder, with best-effort migration of previous caches.
- Consolidated user-visible Mountlet files under one app folder with
  `mounted` and `offline` subfolders, and added an app-folder picker to App
  settings.
- Added real-time cache/offline status refresh, manual sync controls, rclone
  transfer details, Google Docs import-format handling, and drag-and-drop
  uploads into Mountlet Files.
- Improved native packaging for Linux, Windows, macOS arm64, and macOS x64,
  including bundled-rclone variants and macOS bundled dependency validation.
- Switched the repository license from MIT to the concise source-available
  license and kept installer use under the separate EULA.
- Fixed config sync push-dot false positives after pull by ignoring rclone's
  automatically refreshed OAuth token when computing the operation-level config
  fingerprint.

## 0.4.1 - 2026-06-26

- Added operating-system metadata to config bundles and showed OS, device, and
  local-time details in config import and pull confirmations.
- Fixed manual config imports so importing a bundle records the imported
  configuration as the sync baseline and does not immediately show a push dot.
- Restored native title bars for child dialogs, constrained child dialogs to
  the visible desktop, and made the shortcut editor scroll internally on small
  screens.
- Fixed remote move shortcuts so Shift+Up and Shift+Down are parsed through
  Qt's explicit key-combination API and do not trigger an unnecessary rclone
  remote reload before reordering.
- Reorganized the keyboard shortcut dialog around fixed inputs and configurable
  alternatives, added shared list-navigation alternatives, added configurable
  per-remote and file-operation alternatives, and surfaced assigned remote
  action shortcuts in button tooltips.

## 0.4.0 - 2026-06-25

- Added a compact rclone-backed file browser connected directly to the remote
  strips, with remembered per-remote paths and adaptive left/right placement.
- Added cross-remote copy, cut, paste, and drag-and-drop transfers that run
  outside the UI thread.
- Added the managed-offline storage foundation and availability icons, while
  leaving offline creation disabled until edit and conflict semantics are safe.
- Added hover-open remote switching, keyboard focus navigation, retained Qt
  input handlers for X11 stability, file and folder context menus, permanent
  delete confirmation, and remote folder creation.
- Disabled integrated file edits by default and added an app setting with an
  explicit warning before enabling direct, non-undoable cloud file operations.
- Added a remote-root navigation button and configurable keyboard shortcuts for
  optional remote-list navigation, remote reordering, and file-browser actions,
  with up to three alternatives per action, grouped contexts, conflict checks,
  fixed-key guidance, and restore defaults.
- Added background preloading, one-level child-folder prefetch, and session
  caching for each remote's remembered folder, default remote-list keyboard
  focus, deterministic X11 row/pin visual state, and an embedded side-by-side
  browser on Wayland.
- Fixed remote row highlight geometry so focus and hover changes do not resize
  the row list, reserved a minimum embedded-browser size on Wayland, and kept
  the App/Mount/Config menus in the Mountlet window on macOS.
- Synchronized hover-selected and keyboard-selected remote rows by focusing the
  hovered Qt row, so Up and Down navigation starts from the row currently
  highlighted by the pointer.
- Made Left and Right navigation side-aware between the main window and file
  browser, while keeping Up, Down, Return, and Escape as fixed navigation keys.
- Guarded mounted-folder opening so Windows does not fall back to an unrelated
  Explorer location when a stale mount path is no longer reachable.
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
- Added encrypted config-bundle metadata with device, time, and content hash,
  compact top-row config sync buttons, and a nested raw-config-file menu.
- Added a Proton Drive setup path using current rclone's `protondrive` backend,
  with Proton username, password, optional 2FA, and mailbox-password fields.
- Improved first-show tray positioning, config sync dot clearing after
  successful push or pull, Proton Drive backend preflight errors for older
  rclone builds, and one-click reauthentication prompts for mount failures that
  look like expired or invalid cloud credentials.
- Tightened tray anchoring against transient `(0, 0)` tray geometry, re-anchored
  after config replacement, and changed sync-dot detection to use a semantic
  remote-operation config hash instead of raw app config bytes.
- Added an About dialog with app, Python, Qt, rclone, filesystem-driver,
  platform, config-path, and mount-folder details.
- Made config sync pull metadata easier to read by translating bundle device
  and timestamp values into user-facing device and local-time wording.
- Fixed config sync push-dot updates after shortcut, app, mount, remote-order,
  import, and new-remote changes.

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
