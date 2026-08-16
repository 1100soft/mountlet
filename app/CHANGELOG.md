# Changelog

## 0.6.8 - 2026-08-16

- Added Explorer-style inline file and folder renaming: select an item and
  click its name again or press F2. For files, editing initially selects the
  basename while preserving the extension.
- Made newly downloaded cache and offline states appear immediately on the
  affected file rows. The browser now reads the completed on-disk state before
  repainting instead of waiting for a restart or a later folder scan.
- Kept file and cache-overlay icons at their intended colors across startup,
  selection, themes, and zoom levels by supplying stable pixmaps for every
  enabled icon state.
- Bound release-test subprocesses to the checked-out source and removed a
  duplicated version literal that could let local tests pass against an older
  installation while clean package builds failed.

## 0.6.6 - 2026-08-12

- Reworked global and per-remote search around one local SQLite query: unordered
  AND terms and quoted phrases match filenames and parent paths, path-only hits
  are excluded, and exact/filename/mixed matches are ranked and color-coded.
  SQLite performs filtering, scoring, ordering, and limiting before returning
  at most 81 global or 51 remote rows; capped counts display as `80+` or `50+`,
  and search no longer performs a separate cloud verification pass.
- Made search result viewports exactly one integer header plus six integer rows,
  reserved scrollbars to prevent layout shifts, constrained columns to the
  viewport, retained queries across window modes, and reran active remote search
  when selecting another remote.
- Added drag-and-drop remote reordering, simplified remote cards and controls,
  centralized semantic and theme colors, and fixed stale selection brushes that
  could highlight unrelated file rows while navigating search results.
- Unified child-dialog centering and platform work-area resolution, and fixed
  detached-browser sizing and placement against panel-excluded desktop bounds.

- Kept file-list scrollbars reserved in both window modes so folder changes do
  not alter viewport or column geometry, and removed scroll-position caching in
  favor of revealing the remembered current row directly.
- Restored strict available-desktop clamping even when the calculated window
  size is unchanged or the window manager reports a managed window state.
- Persisted per-folder selected row indices, moved large offline-state updates
  entirely off the navigation path, and documented resource guidance and test
  resource reporting.
- Added a detected-platform download action to the website home page while
  retaining the full download page for versions, lean builds, and alternatives.

## 0.6.5 - 2026-08-10

- Expanded Ctrl++ and Ctrl+- from file-list zoom into persistent application-wide
  scaling for fonts, controls, icons, spacing, rows, styles, and open windows;
  added Ctrl+0 and bottom-right zoom controls, scalable SVG button icons, and
  stable cached-edge window anchoring without cumulative browser growth; the
  compact footer shares one row with Add remote and uses overflow scrolling
  when desktop bounds prevent controls from fitting. Ordinary half-screen and
  near-desktop window sizes no longer get mistaken for externally managed
  tiling and disable subsequent zoom repositioning. Main and file-browser
  rectangles are calculated directly from the cached tray anchor and their new
  theoretical sizes, without post-render clamping or geometry corrections;
  remote selection, zoom, layout changes, and manual movement now share one
  browser-positioning function so overlap and row alignment remain consistent.
  SVG button, provider, and mounted-state icons render deterministically from
  their immutable base size and current zoom instead of accumulating refresh
  and startup scaling state.
- Made cached folder contents, the remembered selection, and cached usage text
  available immediately at startup, after mode changes, and while moving
  rapidly between remotes. Live folder metadata is queued in one background
  worker, but results update the foreground only after the user remains on the
  same view; stale results still warm the persistent cache.
- Removed eager neighboring-folder prefetch and repeated selection-time tree,
  theme, icon, usage, and layout work. Single-window navigation now swaps
  retained item trees in batches and follows the same cache-only hot-path
  rules as the detached browser.
- Replaced rendered size hints and cumulative scaling with integer metrics for
  every discrete zoom level. Detached list height is derived from fixed chrome
  plus row height times the current item count, while deterministic columns
  reserve fixed Size and Modified widths and give remaining space to Name.
- Stabilized first-open dialog centering and window placement, added geometry
  diagnostics to bug reports, and prevented mode or presentation changes from
  triggering unrelated remount and reauthentication prompts.

## 0.6.4 - 2026-08-08

- Added direct external and internal drag-and-drop into the displayed folder,
  child folders, and remote rows, with provider-aware Google Photos upload
  targets and bounded background work.
- Improved Google Photos navigation, cached date views, quota-conscious
  behavior, compatibility with older rclone releases, and restrictions around
  provider read-only media paths.
- Added optional Google account hints for Drive and Photos authorization and
  account-aware web links without forcing reauthentication or remounting.
- Added guided iCloud reauthentication, trusted-device and SMS verification
  flows, deferred startup prompts, and automatic mount retry after refreshed
  credentials are saved.
- Kept provider-qualified remote names in mounted folders while preserving
  short aliases in the app, and added safer cleanup for stale or disconnected
  mounts.
- Restored live system-theme updates throughout the remote list, main window,
  file browser, controls, file rows, and search fields.
- Fixed live single/multiple-window mode switching, avoided unchanged settings
  writes, improved layout responsiveness, and made file-list height adaptive
  with an optional item limit.
- Clarified managed file access versus mounted-folder access across the app,
  documentation, and website, including the different edit-safety behavior.
- Added a platform/architecture download matrix, safer macOS architecture
  fallback, platform marks, standard/lean selection, and a single contextual
  download action.
- Added versioned R2 release folders, consistent installer names, a public
  five-version catalog, version selection on the website, and automatic
  retirement of older installer objects.
- Separated preview and production build metadata, service endpoints, notice
  audiences, and local client state.

## 0.6.3 - 2026-07-23

- Made tray opening, remote hover and keyboard navigation, file selection, and
  remote reordering update immediately without waiting for cloud, mount,
  filesystem, settings, or license checks.
- Moved mount probes, usage retrieval, local-file scans, entry-state scans, and
  download completion checks off the UI thread, with bounded concurrency and
  targeted row updates.
- Reduced unnecessary menu rebuilds, full-window repaints, repeated shortcut
  parsing, settings reads, file-icon lookups, and operation-state scans.
- Added regression coverage for cached status paths, stale background results,
  incremental selection painting, shortcut invalidation, and responsive tray
  behavior.

## 0.6.2 - 2026-07-23

- Added guided MEGA and Nextcloud setup, and marked Google Photos, iCloud, and
  MEGA as locally tested.
- Repaired production and preview license verification, live activation, public
  beta keys, and public-key loading across packaged builds.
- Kept trial expiration across Linux reinstalls and improved stable device
  identification on macOS.
- Made remote reordering incremental and keyboard-repeat friendly, while
  removing unnecessary periodic window fitting, icon rendering, and event
  handler recreation.

## 0.6.1 - 2026-07-19

- Added an app notification inbox with server-managed active/archive lifecycle,
  notification cards, timestamps, unread state, and website history.
- Added searchable website notices and FAQ entries, archived-notice grouping,
  and a private FAQ-first support request form backed by the report pipeline.
- Added provider icons, remote context actions, and configurable S3-compatible
  remote credentials for key rotation.
- Improved bundled installer reliability, current rclone staging, Linux/X11
  stability, and automated preview/production installer uploads to R2.

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
- Switched Linux `.deb` packaging from a PyInstaller-frozen Qt app to a
  system-Python bundle with app-local Python libraries, avoiding the bundled
  Qt/X11 key-input crash seen on Plasma.

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
