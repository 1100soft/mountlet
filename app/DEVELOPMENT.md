# Desktop implementation invariants

The archived Python 0.6.8 application is the historical behavioral baseline.
Refactoring is not a reason to weaken a behavior reproduced by the current app.

## Responsiveness

- Remote selection must synchronously swap the cached immutable snapshot. It
  must not enumerate the folder being left, query rclone, hash files, recalculate
  columns, or change scrollbar policy.
- Lists always reserve scrollbar space. File rows are virtualized, and DOM work
  is bounded by visible rows rather than folder size.
- Network, rclone, usage, metadata, offline reconciliation, notices, licensing,
  and persistence stay outside the selection path. Results may update only if
  their generation and remote/path still match.
- A mode switch reuses caches and browser memory. It does not rebuild metadata.

## Geometry

- `geometry.ts` is the single source for integer metrics at every supported
  zoom step. Do not use rendered size hints or fractional layout calculations.
- Rust computes window frames from those metrics and the platform work-area
  helper before showing or moving a window. Never add a compositor-confirmed
  correction pass.
- Place the popup beside the tray icon (`anchor ± 8px`), then clamp into a
  work area that has already had `exclude_tray_panel` applied. Do not align
  the frame to the raw screen edge: that hides the window under the panel.
- Oversize single windows are resized to the usable work area; only their list
  viewports scroll. Detached browser placement always uses the same selected-card
  anchor formula.

## Focus and tray activation

- On Linux, first-show keyboard activation must happen on GTK's next idle turn
  after `show_all`: before the initial map, Tauri/DOM focus can report success
  while WebKit receives no key events. Keep the post-map `present()` and WebKit
  `grab_focus()` in `activate_linux_keyboard`.
- Do not suppress native focus events for a time window after showing. That can
  discard a real click and destroys last-window restoration.
- Snapshot the natively focused window immediately before hiding when one is
  still reported, retain the last genuine `Focused(true)` event otherwise, and
  restore that label only after both windows have been shown.

## Packaged startup smoke test

- Release builds on Windows must retain the GUI subsystem declaration in
  `src-tauri/src/main.rs`; removing it makes the installed tray application
  open an empty command window.
- The package workflow installs every generated DEB and NSIS package and
  launches every generated DMG. Its internal `MOUNTLET_STARTUP_SMOKE` marker
  requires the production WebView to call back into Rust, proving that the
  process, frontend, IPC bridge, main window, and application state initialized.
- Keep the probe ahead of interactive prerequisite dialogs so lean installers
  remain testable on clean CI runners.
- All background child processes must be created through
  `src-tauri/src/child_process.rs`. It applies `CREATE_NO_WINDOW` on Windows;
  use `std::process::Command` directly only for an explicitly requested visible
  terminal. The Windows package smoke test repeatedly runs prerequisite and
  desktop probes while watching for newly visible console windows.
- The NSIS finish-page desktop-shortcut option is unchecked by default through
  `windows/hooks.nsh`. Silent installation tests also pass `/NS` and assert that
  no desktop shortcut was created.

## State and compatibility

- Read and write the same rclone, Mountlet, offline-manifest, metadata-index,
  shortcut, license, and encrypted-bundle formats as Python.
- Credentials never cross into the WebView. Diagnostics must pass through the
  central redactor.
- Only remote add/delete/rename may rebuild the remote or tray collections.
- Colors belong in the central CSS variables and icons remain scalable assets.
- Modal dialogs use `trapModalFocus`: Enter activates the enabled primary
  action (except multiline text entry), and Escape activates Cancel/Close.
- `src/shortcuts.ts` is the single registry for shortcut labels, scopes, fixed
  keys, customizable defaults, normalization, and runtime matching. The
  shortcut dialog is generated from it; never duplicate shortcut instructions
  or defaults in a UI module or the Rust settings layer.

## Required checks

Run these sequentially on constrained development machines:

```bash
npm run build
cargo test --offline -j 2 --manifest-path src-tauri/Cargo.toml
cargo clippy --offline -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

The native acceptance matrix in `PARITY.md` remains the release gate. A cleanup
may not change a checked behavior without a specific regression test and an
explicit ledger update.

## Current desktop coverage

The Tauri desktop covers the Python 0.6.8 desktop behaviors. The retired
CLI/TUI and Qt-only test harness remain in the archived source:

- Startup checks rclone (required) and FUSE/WinFsp/macFUSE (optional) in a
  graphical wizard. Incomplete rclone remotes stay out of the list.
- Add Remote collects provider-specific fields before `rclone config create`,
  including Drive client reuse without sending stored secrets into the WebView,
  local-or-paste OAuth, shared drives, S3/R2/MinIO/Wasabi, WebDAV/Nextcloud,
  iCloud, MEGA, Proton Drive, Google Photos limits, OAuth port 53682 conflict
  handling, and opening `rclone config` in a terminal for other providers.
- File-manager settings are executed (Dolphin tab / `xdg-mime` / FileManager1
  on Linux, Explorer/Finder elsewhere). Wayland forces single-window mode;
  GNOME on Wayland disables pin.
- The public beta key is shared by all beta users. The License dialog must not
  request or display its server-side device list; device management is only
  user-facing for individual paid licenses.
- The window opens during startup. Linux StatusNotifierItem does not expose
  tray geometry until a click, so startup uses the work-area fallback and the
  first tray activation supplies the anchor (matching Python when
  `QSystemTrayIcon.geometry()` is empty). Windows and macOS use
  `TrayIcon::rect()` when the OS reports it. After that the popup stays beside
  the tray unless the user drags the window. Zoom and reopen both
  re-anchor to the tray; a true user drag (`Moved` after the programmatic
  layout grace period) keeps resize-in-place. Linux tray callbacks only cache
  the click and hop to the UI thread; GTK window layout must not run on the
  StatusNotifier thread or left- and right-click both go dead. Layout then
  runs before `show()` so a double-click cannot spawn the frame at 0,0. X11
  uses `_NET_WORKAREA`, while Wayland uses GDK's native monitor work area so
  compositor panels and usable bounds constrain the window. Zoom, theme, and
  window mode are written to `config.toml` as they change so App settings
  cannot restore a stale startup zoom.
- The window is placed beside the tray icon, inset from the panel by the
  Python `exclude_tray_panel` rule, or at the work-area corner used by Python
  when tray geometry is missing (GNOME Wayland: top-right). It no longer
  stays at 0,0 or flush against the screen edge.
- Left-clicking the tray icon toggles the window. Right-click opens the nested
  tray menu. Linux uses a StatusNotifierItem so the menu is not shown on
  left-click. The tooltip includes mount status. macOS stays an accessory
  process (no Dock icon).
- Native bundle pickers, desktop notifications, lazy `fusermount -u -z`,
  Google Photos album-only mutations, mount-driver diagnostics, and best-effort
  Linux theme file icons follow the Python behavior.
