# Migration invariants

The Python application is the behavioral specification. Refactoring is not a
reason to weaken a behavior already reproduced here.

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

## State and compatibility

- Read and write the same rclone, Mountlet, offline-manifest, metadata-index,
  shortcut, license, and encrypted-bundle formats as Python.
- Credentials never cross into the WebView. Diagnostics must pass through the
  central redactor.
- Only remote add/delete/rename may rebuild the remote or tray collections.
- Colors belong in the central CSS variables and icons remain scalable assets.

## Required checks

Run these sequentially on constrained development machines:

```bash
npm run build
cargo test --offline -j 2 --manifest-path src-tauri/Cargo.toml
cargo clippy --offline -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

The native acceptance matrix in `PARITY.md` is required before replacing the
Python application. A cleanup may not change a checked behavior without a
specific regression test and an explicit ledger update.

## Current Tauri coverage

This experiment now covers the remaining Python desktop behaviors except the
CLI/TUI and Qt tests:

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
- The window starts hidden. Linux StatusNotifierItem does not expose tray
  geometry until a click, so the first tray activation supplies the anchor
  (matching Python when `QSystemTrayIcon.geometry()` is empty). Windows and
  macOS use `TrayIcon::rect()` when the OS reports it. After that the popup
  stays beside the tray unless the user drags the window. Zoom resizes in
  place for a user-moved window and re-anchors to the tray otherwise.
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

