# Mountlet desktop

This directory contains the shipping Mountlet desktop application. Version
0.7.0 is the first Rust/Tauri release and replaces the archived Python/Qt
desktop application without changing Mountlet's user-data formats.

## Install and prerequisites

The standard installer includes rclone. The lean installer uses an existing
rclone installation. Native mounted folders additionally require FUSE 3 on
Linux, WinFsp on Windows, or macFUSE on macOS; Mountlet's integrated file
browser works without those optional mount drivers.

Current release targets are:

- Linux x64: `.deb`, standard and lean
- Windows x64: NSIS setup `.exe`, standard and lean
- macOS arm64: `.dmg`, standard and lean
- macOS x64: `.dmg`, standard and lean

Current builds are unsigned. Windows SmartScreen and macOS Gatekeeper can
therefore require explicit confirmation. Only use installers from Mountlet's
release site or repository Actions artifacts.

## First run and daily use

Mountlet starts hidden in the system tray. Open the tray icon, add a remote,
and follow the provider or rclone authentication flow. The app supports both
its integrated, cached file browser and native mounted folders.

Integrated edits are disabled by default because cloud copy, move, upload,
rename, and delete operations are direct and cannot be undone by Mountlet.
Offline files and ordinary cached files live under the configured Mountlet app
folder. rclone credentials remain in rclone's standard configuration location.

The application preserves the Python release's configuration and data formats,
including settings, mount definitions, per-remote browser locations and
selections, keyboard shortcuts, notifications, licenses, offline metadata, and
encrypted `.mountlet` configuration bundles.

## Development

Install Node.js 22, stable Rust, and the platform prerequisites listed in the
[Tauri documentation](https://v2.tauri.app/start/prerequisites/).

```bash
npm ci
npm run tauri:dev
```

The frontend can be run independently with `npm run dev`; it uses fallback
data and never reads native credentials. Production frontend and native builds:

```bash
npm run build
npm run tauri:build
```

The application intentionally opens no taskbar window at startup. Use the tray
icon or **Open Mountlet** tray action to reveal it.

## Architecture and maintenance rules

- Rust owns credentials, rclone processes, filesystem state, persistence, and
  platform integration. Credentials never cross into the WebView.
- Folder snapshots are immutable and the file list is virtualized, so changing
  remotes or folders does not iterate the folder being left.
- Network, mount, metadata, offline reconciliation, notice, license, and report
  work remains outside foreground selection paths.
- `src/geometry.ts` is the source of truth for discrete UI geometry.
- `src/shortcuts.ts` is the single registry for shortcut defaults, matching,
  scopes, and user-visible shortcut documentation.
- Browser-to-native calls are typed in `src/backend.ts`; dialogs share the
  keyboard and focus behavior in `src/dialogs.ts`.

See [DEVELOPMENT.md](DEVELOPMENT.md) for regression-sensitive invariants and
[PARITY.md](PARITY.md) for the native acceptance ledger.

## Required checks

```bash
npm run build
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

`npm run build` includes strict TypeScript unused-code checks. The release CI
runs these checks and builds all eight platform/variant installers.

## Support and privacy

Use **App > Report bug** to review and submit a report. Obvious credentials and
license keys are redacted, but optional diagnostic logs can still contain file
paths, remote names, and filenames.

Mountlet user data is stored outside the installation directory and is retained
when the application is upgraded or uninstalled.

On Windows, installing 0.7.0 or later removes the retired Python/Qt application
before installing the Tauri application. Settings, trial history, rclone data,
and managed cached files are preserved during this replacement.
