# Handoff: Windows installed app IPC starvation after Python-profile upgrade

Date: 2026-08-30
Branch: `wip`  
HEAD: `47aa568` (`Move window, tray, and shell commands off the WebView IPC thread`)
Previous attempted preview: `43a1a70` (installed; did not restore backend communication)

Work in this thread was done on Linux. The user cannot run local Windows changes; preview installers from `wip` are the only validation path. Identify builds by the short commit in the filename, not `0.7.0`.

Release index: `https://wip.mountlet.pages.dev/api/releases`

## Problem (do not treat this as separate UI bugs)

On the user's existing Windows profile (upgraded from Python `0.6.8`), the webview paints but many native Tauri commands do not complete. About, Buy, License, layout, and file actions all wait on IPC. Fixing dialog chrome without unblocking the command path is a dead end.

The X11 `.deb` works. Clean GitHub Windows installed-package smoke (`MOUNTLET_STARTUP_SMOKE` in `.github/workflows/package.yml`) also works. That smoke uses a fresh runner profile and the special smoke frontend branch. It does **not** cover a legacy Python trial/token cache, the normal `start()` path, or command interactivity after first paint.

## User-visible timeline

### Through `9e71924` (before this thread)

- Main window opens at launch (`1488c03`).
- Not reliably snapped to the tray.
- UI behaved as license-locked.
- License did not auto-open; manual License stuck on `Checking license…` then frontend timeout.
- About did not appear. Buy did not respond.
- Unique preview names (`abfcf23`) ruled out stale installer reuse.

### After `43a1a70` (first commit from this thread)

Installed uniquely named preview. Partial change only:

- License dialog **does** open.
- Dialog reported **5 days of trial remaining**.
- Main UI stayed **locked** anyway.
- Buy remained **unresponsive**.
- About opened but showed **Version is unavailable** (frontend 5s `appVersion()` timeout).

Interpretation: `license_status` (already async/`spawn_blocking` in `43a1a70`) can eventually return. Sync commands on the WebView IPC thread (`app_version`, `open_external`, layout, tray, clipboard) still do not. Startup also **fabricated `expired`** after a 15s license timeout and never re-rendered the shell when real trial status arrived, so lock state and License text disagreed.

The user was explicit: opening dialogs is not a fix. Backend communication has to work.

### After `47aa568` (pushed; not yet user-tested)

Intended probes for the next uniquely named installer:

- Main UI not locked solely because license IPC was slow; when trial status arrives, shell should unlock.
- About should show a version.
- Buy should open the pricing page.

If those three fail, IPC is still blocked. Do not add more dialog fallbacks.

## Commits from this thread

### `43a1a70` — Keep Windows startup IPC responsive during license and tray layout

Pushed to `origin/wip`. User tested. **Insufficient.**

Native:

- `license::status()` is local-only (no public-key HTTP during ordinary status). Rotation fetch remains for activate/devices.
- Trial/license replica writes are best-effort (Python-style); one bad path must not fail the whole status call.
- `license_status` is async + `spawn_blocking` again (undoes `9e71924`'s sync command).
- `layout_windows` no longer calls `TrayIcon::rect()` from a command handler. Tray seeding stays on the native retry loop (`run_on_main_thread`).
- Windows `path_is_mounted` (`symlink_metadata` / reparse) times out after 200ms on a helper thread.
- `list_remotes` mount checks run in `spawn_blocking`.
- CLI: `mountlet --license-diagnostics <path>` writes path/stage timings **before** `mountlet::run()`, usable if the GUI is dead.

Frontend:

- License modal and About chrome no longer wait on IPC before appearing.
- Automatic License is not gated on finishing layout first.

That is why License could show a real trial while About/Buy still failed: async license vs still-sync version/open/layout.

### `47aa568` — Move window, tray, and shell commands off the WebView IPC thread

Pushed to `origin/wip`. **Not yet confirmed on the user's machine.**

Tauri 2 runs **sync** `#[tauri::command]` handlers inline on the IPC thread. One hung handler (`apply_window_layout`, `refresh_tray_menu` / `tray.set_menu`, Windows `powershell` clipboard, window show/hide) prevents later sync invokes. Async commands are queued onto Tokio and can still complete — matching `43a1a70` symptoms.

Changes:

- Helper `run_on_main_async` (`tokio::sync::oneshot` + `AppHandle::run_on_main_thread`).
- These commands are now `async` and either run on Tokio or hop to the UI thread: `app_version`, `show_startup_windows`, `apply_window_layout`, `set_browser_window`, `focus_window`, `set_window_pinned`, `refresh_tray_menu`, `open_external`, `clipboard_text`, `license_default_device_label`, `check_prerequisites`.
- Windows Buy/open-external uses `cmd /C start "" <url>` via `spawn_blocking` (not `explorer.exe` on the IPC thread). `Command` still sets `CREATE_NO_WINDOW`.
- Linux clipboard still uses GTK `wait_for_text`, but only on the UI thread.
- Frontend no longer maps license timeout to fabricated `expired`. `licenseStatus()` keeps running; when it resolves, `applyLicense()` re-renders so lock state matches the backend.
- License dialog paint updates `currentLicense` and re-renders the shell so a trial result can clear a previous lock.

## Code map

Frontend: `app/src/main.ts` (`start()`, `applyLicense()`, `licenseLocked()`, `showLicense()`, `showAbout()`, `layoutNativeWindows()`).

Native: `app/src-tauri/src/lib.rs` (`run_on_main_async`, command list near `generate_handler!`), `app/src-tauri/src/license.rs`, `app/src-tauri/src/main.rs` (`--version`, `--license-diagnostics`).

Python reference: `legacy/python-0.6.8/src/mountlet/license_control.py`, `tray.py`.

## Diagnostics if `47aa568` is still inert

From PowerShell on the installed EXE (no IPC required):

```text
mountlet --license-diagnostics %TEMP%\mountlet-license.txt
```

That path is independent of the webview. If it is fast and reports `trial`, the hang is in Tauri/WebView2 command dispatch or window/tray APIs, not `license::status()` itself.

Do not rely on About, Bug report, or License to collect logs.

## What is still unverified

- Tray snapping to `TrayIcon::rect()` on this user's shell.
- Whether `47aa568` actually unblocks `app_version` / `open_external` on the upgraded profile.
- CI still does not seed a Python `0.6.8` profile or assert post-startup IPC on the **normal** frontend (only `MOUNTLET_STARTUP_SMOKE`).

## Suggested next steps if `47aa568` fails

1. Log command **entry and return** to `runtime.log` (under `%LOCALAPPDATA%\Mountlet`) with timestamps. Include a dedicated async `ipc_probe`. Do not use the Bug report UI to retrieve this.
2. Add an installed Windows job that seeds legacy trial/token files, launches **without** `MOUNTLET_STARTUP_SMOKE`, and asserts `app_version`, `license_status`, and `open_external` after first paint (UI automation or a marker).
3. If sync leftovers still run on the IPC thread (`load_preferences`, `desktop_hints`, `complete_startup_smoke`, `pick_config_bundle_path`, …), convert or hop those too. One remaining blocking handler can starve the rest.
4. If `run_on_main_async` deadlocks (Tokio task waiting on the UI thread that is waiting on the task), switch window ops to fire-and-forget `run_on_main_thread` without awaiting, or a dedicated UI channel with a timeout.

## Validation (Linux / CI)

```bash
cd app
cargo test --locked -j 2 --manifest-path src-tauri/Cargo.toml
cargo clippy --locked -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

## Older attempts (still relevant background)

| Commit | Intent | User result |
| --- | --- | --- |
| `e25d8a3`, `b8f086d` | Stop Windows stale-mount walk | Not the remaining IPC stall |
| `2de5117` | Python paths / oldest trial clock | Compatibility only |
| `8805d73` | Bounded reports; expired UI | Incomplete |
| `8a78507` | `winreg`; timeout → expired; `explorer` for URLs | Timeout-as-expired later caused false lock |
| `1488c03` | Show window at startup | Only visibility improved |
| `3a3a46d`, `5d7b96c` | Bundled key; no net on status; tray retry | No dialog/IPC improvement |
| `abfcf23` | Unique preview filenames | Ruled out CDN reuse |
| `9e71924` | Python key order; **sync** `license_status` | Same symptoms |
| `13591c8` | This handoff file (pre-thread) | Documentation only |
