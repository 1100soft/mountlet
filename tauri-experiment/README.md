# Mountlet Tauri experiment

This directory is an isolated Tauri 2 prototype. It does not replace or modify
the Python/Qt application under `app/`.

## What it reproduces

- Single-window and detached-browser modes.
- Compact remote cards, hover/click/arrow-key selection, provider identity,
  mount state, cached usage, global filtering, fixed scrollbars, and the current
  Mountlet light/dark palette.
- A Rust-owned system tray lifecycle. The main and detached browser windows are
  omitted from the taskbar/panel, the browser is transient to the main window,
  closing either hides the window stack, and Show/Quit live in the tray menu.
  The app starts hidden, like the Python tray application.
- Deterministic native layout at every discrete zoom level. TypeScript caches
  integer metrics for all eleven zoom levels; Rust consumes those integers,
  combines them with native frame margins and a platform work-area helper, and
  positions both windows in one pass. No content size hint or post-render
  correction is used. The detached browser is attached to the selected card.
- Discovery of every remote from Mountlet's active `rclone.conf`, preserving
  Mountlet's configured order and enabled state. Only section names, backend
  types, and public provider metadata are read; credentials are never sent to
  the webview.
- Real remote-root and folder navigation through `rclone lsjson`, exposed as
  immutable folder snapshots and cached for instant revisits.
- Immediate cached-folder swaps followed by background listing, parent/root
  navigation, refresh invalidation, retained paths and selections, and
  download to Mountlet's managed temporary cache when opening an unmounted file. Signed rclone
  sizes are accepted because directories and virtual entries can report `-1`;
  those values are normalized to zero only at the frontend model boundary.
- The Python command map, including directional pane transfer, Escape, search,
  Space/Enter, Shift+arrows, Home/End, Page Up/Down, parent/root/refresh,
  open-mounted-folder, copy/cut/paste/delete/new-folder, F2 rename, zoom, and
  Ctrl+wheel. User-configured alternatives are read from Mountlet's existing
  `[shortcuts]` settings.
- Rust-side asynchronous file mutations using rclone: inline rename, folder
  creation, deletion, and cross-remote copy/move. Only the affected folder
  snapshot is invalidated after completion, so leaving a large folder never
  performs an item-by-item frontend pass.
- Filename-only global and per-remote search through Mountlet's existing
  SQLite metadata index, with exact/phrase/all-term quality ordering and the
  same capped-result convention. Displaying a result never verifies it against
  the cloud.
- Fixed Size and Modified columns with the remaining width assigned to Name.
- Remembered per-folder selection and cache/offline badges derived from
  Mountlet's actual manifest and offline folder.
- Real save-offline, remove-offline, temporary cache clearing, local file-drop
  upload, and internal cross-remote drag copy/move. Scoped deletion preserves
  temporary descendants inside an offline directory and vice versa.
- The existing notification history, unread state, critical-delete rule,
  notice-server polling/delivery policy, runtime diagnostics, redacted report
  upload, crash recovery prompt, and rclone log viewer.
- Remote configuration editing, first-run remote creation, rclone's complete
  noninteractive question flow, Google account hints, and interactive 2FA.
- Application-wide 60–160% zoom through Ctrl++, Ctrl+-, Ctrl+0, or the footer.
- Python-compatible encrypted configuration bundles, remote config push/pull,
  local-change indication, trial/license verification, activation, and device
  deactivation.
- Managed-offline SHA-256 baselines, idle local-change scans, cloud metadata
  checks after stable folder navigation, and newer/older/keep-both conflict
  resolution. These jobs are sequential and never run in the selection path.
- A virtualized file list: a 2,500-item folder creates only the rows visible in
  the viewport instead of one widget per item.
- A typed Tauri command boundary. The Rust process owns remote/folder state;
  the webview receives snapshots and sends small commands.
- Mountlet's actual provider, mount-state, action, and layout assets instead of
  placeholder provider marks wherever a corresponding source asset exists.
- Browser-only fallback data, so the frontend can be evaluated with Vite before
  installing Rust or native Tauri prerequisites.

The native application modifies the same rclone configuration, Mountlet
settings, mount folders, offline manifest, and metadata index as the Python
application. Test destructive file actions against disposable content. Sample
data exists only in the browser-only Vite fallback and Rust tests; a native
launch never invents accounts when rclone is unavailable.

Remaining replacement gates are tracked in [PARITY.md](PARITY.md). Native
outward file drag is integrated without replacing in-WebView remote moves; the
native-platform acceptance matrix must still run on the actual target operating
systems.

Implementation invariants and the regression gate are recorded in
[DEVELOPMENT.md](DEVELOPMENT.md). They are part of the migration contract, not
optional cleanup guidance.

## Packaging and resources

The migration packaging workflow builds lean and bundled-rclone installers for
Linux x64, Windows x64, macOS arm64, and macOS x64. Bundled rclone binaries use
an app-versioned resource path. Windows copies that binary to an app-versioned
LocalAppData runtime directory before launching it, so a Mountlet upgrade does
not terminate existing mounts or try to overwrite an in-use `rclone.exe`.

Minimum planning target: a 64-bit desktop, two CPU cores, and 2 GB of available
memory. Four CPU cores and 4 GB of available memory are recommended for many
remotes, very large folders, concurrent transfers, or mounted drives. Release
acceptance must record startup/idle/10,000-row CPU and resident memory on each
platform rather than treating these planning values as benchmark results.

## Run the frontend prototype

```bash
npm install
npm run dev
```

Open the printed local URL. Enter the `Projects` folder to exercise the
virtualized 2,500-row list. Ctrl+T cycles system, light, and dark themes.

## Run as Tauri

Install the Rust toolchain and Tauri's platform prerequisites, then:

```bash
npm install
npm run tauri:dev
```

The application intentionally opens no taskbar window at startup. Use its tray
icon (or the tray menu's **Show Mountlet** action) to reveal it.

## Architectural rules under test

1. Foreground selection swaps an immutable cached snapshot immediately.
2. Background operations never mutate DOM or frontend state directly; they
   return snapshots or emit narrowly scoped events.
3. No operation iterates the folder being left.
4. List geometry is fixed at each discrete zoom level. Scrollbars are always
   reserved, and changing folders cannot trigger surrounding layout changes.
5. Large folders are virtualized. DOM node count is proportional to viewport
   height, not folder size.
6. Selection persistence is an asynchronous, non-blocking command.
7. Local/cloud reconciliation and notice/license/report network requests run in
   bounded background jobs; remote selection never waits for them.
8. Adding, deleting, or renaming a remote is the only operation allowed to
   rebuild the remote/tray collection. Folder changes only swap snapshots.

## Evaluation gate before a migration

Test Linux/KWin, Windows, macOS arm64, and macOS x64 for:

- remote-to-browser selection latency while key-repeat is active;
- a 10,000-row folder and rapid switching between empty and large folders;
- live system theme changes and every discrete zoom level;
- tray anchoring, usable work areas, taskbars/panels, and multiple monitors;
- detached-window creation, positioning, focus, and dialog ownership;
- native file drag/drop, inline rename, system file icons, and accessibility;
- idle/startup/large-folder memory and CPU usage.

A migration should proceed only if the prototype improves these measurements
without losing native integration.
