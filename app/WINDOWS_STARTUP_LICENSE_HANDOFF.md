# Handoff: Windows installed app remains partially initialized and license-locked

Date: 2026-08-29  
Branch: `wip`  
Latest attempted fix: `9e71924` (`Match Python license key precedence`)

## Current user-visible behavior

This reproduces on the user's existing Windows installation/profile. The X11
`.deb` has worked normally, and clean installed-package CI probes pass.

- The main window now opens immediately at process launch.
- It is still not reliably positioned against the tray icon.
- Mountlet detects an expired/unverified license state and correctly blocks
  remote and file functionality.
- The License dialog does not open automatically.
- Opening License manually shows `Checking license...` and eventually reports a
  frontend timeout.
- About does not open. The main Buy license button does not respond.
- In earlier builds, Bug report, settings persistence, mounting, downloads and
  file actions were also inert. This looked like the webview could render cached
  state but calls into the native backend did not complete.
- Installing uniquely named build `9e71924` produced no observable improvement,
  so stale installer/CDN reuse is no longer a plausible explanation.

Treat the common failure as more important than individual dialog symptoms.
About waits for `app_version`, License waits for `license_status`, Buy waits for
`open_external`, and file operations wait for other Tauri commands. The likely
problem is partial startup, IPC/runtime starvation, an invisible modal/state
race, or user-profile-specific blocking native I/O—not four separate UI bugs.

## Important reproduction distinction

The GitHub Windows installed-package smoke test uses a clean runner profile. It
successfully invokes all of the following before exiting:

- app version
- remotes and preferences/settings compatibility
- shortcuts
- license status
- report preview
- prerequisites and desktop hints
- tray-menu refresh
- Add Remote behavioral checks
- frontend rendering and native main-window visibility

It does **not** reproduce an upgraded Python `0.6.8` user profile with an old,
expired or invalid token/trial/public-key cache. It also does not exercise the
normal expired-license startup branch or verify that dialogs and arbitrary
commands remain interactive after that branch.

The Windows smoke test is in `.github/workflows/package.yml`, approximately the
`Smoke-test installed Windows package` step. It sets `MOUNTLET_STARTUP_SMOKE`,
removes `HOME`, writes multiple-window/dark settings, starts the installed EXE,
and validates a JSON marker.

## Relevant frontend flow

`app/src/main.ts`, `start()`:

1. Installs the restore-focus listener.
2. Calls `showStartupWindows()` immediately so the process is visible.
3. Loads preferences/settings/browser memory/shortcuts/order.
4. Awaits bounded `licenseStatus()` (15 seconds); on timeout it fabricates an
   expired status.
5. Installs most remaining native listeners and loads remotes.
6. Renders.
7. If `licenseLocked()`, disables the detached browser, lays out windows, shows
   startup windows and directly awaits `showLicense()`.

`showLicense()` immediately appends its modal with `Checking license...`, then
awaits another bounded `licenseStatus()` call. If the modal truly never appears,
determine whether execution reaches this branch and whether another modal or
render removes it. If it appears and times out, instrument the Rust command by
stage rather than adding another frontend timeout.

`showAbout()` awaits `appVersion()` before constructing its dialog. That makes
About a useful minimal IPC probe but a poor diagnostic UI when IPC is broken.

## Relevant native flow

`app/src-tauri/src/lib.rs`:

- `license_status` is currently a synchronous Tauri command calling
  `license::status()` directly. It previously used `spawn_blocking`; both forms
  reportedly showed the same installed behavior.
- `show_startup_windows` calls `show_window_stack` and schedules several native
  tray-rectangle/layout retries on Windows/macOS.
- `seed_tray_anchor_from_os` uses `TrayIcon::rect()`. Windows/macOS deliberately
  do not cache the cursor as a fake anchor anymore. Linux retains cursor fallback.
- Startup creates a TCP single-instance listener on `127.0.0.1:47653` and a
  thread that dispatches show requests with `run_on_main_thread`.
- Windows `cleanup_stale_mounts` is a no-op. Prior attempts removed stale mount
  enumeration from Windows startup because WinFsp reparse points can block.
- Native commands are registered in the large `generate_handler!` list near the
  end of `lib.rs`.

`app/src-tauri/src/license.rs`:

- Windows machine identity now reads `MachineGuid` via `winreg`; it no longer
  starts `reg.exe`.
- A public P-256 verification key is bundled.
- Primary-key order now matches Python: explicitly configured key, otherwise
  bundled key. Cached keys are only tried as rotation fallbacks.
- Expired beta status no longer performs automatic online reactivation during
  status evaluation.
- Trial migration scans Tauri and Python legacy paths and chooses the oldest
  valid clock.
- Unlike Python, several Rust write helpers return on the first filesystem
  error. Audit this, but a permission error should return quickly rather than
  explain a 15-second timeout.

## Python reference

The last Python implementation is under `legacy/python-0.6.8/`.

Key references:

- `src/mountlet/license_control.py::current_status`
- `src/mountlet/license_control.py::_load_public_key`
- `src/mountlet/license_control.py::_trial_paths`
- `src/mountlet/tray.py::LicenseDialog`
- `src/mountlet/tray.py::_open_external_url`

Python constructs the License dialog synchronously and calls
`license_control.current_status()` directly. Its public-key precedence is
explicit configuration, packaged build key, cached key, then network fetch.
Most replicated-path writes ignore individual `OSError`s instead of failing the
whole operation.

## Attempts already made (none fixed the user's current behavior)

### `e25d8a3`, `b8f086d` — Windows stale mounts

- Prevented startup cleanup from enumerating/removing Windows WinFsp reparse
  points.
- Scoped stale mount walking by platform.

### `2de5117` — Python settings/trial compatibility

- Corrected Windows configuration paths and `USERPROFILE`/`HOME` fallback.
- Read legacy Python trial locations and preserved the oldest clock.
- Intended to prevent a Tauri install from resetting the Python trial.

### `8805d73` — reports and expired startup

- Removed live `rclone version` diagnostics from report preparation.
- Added bounded report/license UI behavior.
- Suppressed remote work when expired and attempted automatic License display.

### `8a78507` — nonblocking Windows license detection

- Replaced `reg.exe` with direct `winreg` MachineGuid access.
- Checked token expiry before public-key retrieval.
- Replaced `cmd /C start` with `explorer.exe` for Buy/open-external.
- Converted startup license timeout into explicit expired state.

### `1488c03` — visible startup

- Main window now opens at startup instead of remaining hidden until a tray click.
- Added a native `show_startup_windows` command.
- Made the installed-app smoke marker require a visible main window.
- Added preview build revision to About/report/smoke metadata.

The user confirmed only the immediate opening changed.

### `3a3a46d`, `5d7b96c` — local license key and tray retry

- Bundled the current public verification key.
- Removed network beta reactivation from ordinary status evaluation.
- Directly awaited creation of the automatic expired License dialog.
- Stopped using the cursor as Windows/macOS tray anchor.
- Retried native tray geometry/layout during the first second.
- Follow-up fixed a platform-only strict-Clippy failure.

The user observed no license/dialog improvement and no correct tray snapping.

### `abfcf23` — identifiable preview installers

- Preview artifacts now include the commit in the attachment filename, for
  example `mountlet-v0.7.0-preview-abfcf23-windows-x64-standard-setup.exe`.
- Public release metadata includes `buildId`; download URLs use it for cache
  busting. Tagged production names remain stable.

This ruled out repeatedly installing an old same-named preview.

### `9e71924` — exact Python public-key precedence

- Corrected the Tauri port's erroneous cached-key-before-packaged-key order.
- Changed `license_status` from async `spawn_blocking` to a direct synchronous
  command, matching Python's local status call.

The user installed this uniquely named build and reported exactly the same
symptoms.

## Strong next steps

1. **Instrument command ingress and egress in the runtime log.** Add a tiny
   synchronous `ipc_probe` command and log timestamps at command entry/return.
   Add stage logging inside `license::status()` around token read, trial path
   scanning, machine hint, key verification and trial replication. Do not depend
   on the Bug report or About UI to retrieve this; the runtime log path must be
   documented or exposed through a standalone diagnostic file/CLI argument.
2. **Add an installed Windows expired-upgrade test.** Seed a realistic legacy
   Python profile before launch (trial replicas, license key/token/public-key
   cache), run the normal frontend rather than the special smoke branch, and use
   UI automation or a dedicated marker to assert:
   - startup reaches the expired branch;
   - License modal exists and is visible;
   - `license_status`, `app_version`, and `open_external` still return;
   - About can render while expired;
   - the main event loop stays responsive.
3. **Make diagnostics independent of Tauri IPC.** Add a safe command-line mode
   such as `--license-diagnostics <path>` that runs before `mountlet::run()` and
   records resolved config/state paths plus per-stage timings. The user can run
   the installed EXE from PowerShell even if WebView IPC is dead.
4. **Inspect modal/startup races.** Log every creation/removal of `.modal-layer`
   and every `start()` milestone. Verify the automatic License modal is not
   created and then removed by a second webview/start invocation.
5. **Audit user-profile path I/O.** In particular, avoid querying metadata or
   following reparse points during startup, and make replicated trial writes
   best-effort like Python. Record exact path timing to identify OneDrive,
   antivirus, stale junction or permission behavior.
6. **Test IPC after startup, not only during the special smoke branch.** Current
   CI proves clean startup commands work but does not prove the normal expired
   state remains interactive.

## Tray positioning note

`TrayIcon::rect()` is implemented natively on both Windows and macOS, but can
return `None` while the shell item is being registered. The current retry loop
should eventually call `relayout_from_cache`. If it still does not snap, log
each returned rect, scale factor, computed edge, `user_placed`, cached layout,
and final native position. Avoid guessing based on the cursor.

## Validation commands

```bash
cd app
npm run build
cargo test --locked -j 2 --manifest-path src-tauri/Cargo.toml
cargo clippy --locked -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

Release tooling checks from the repository root:

```bash
npm run web:release:check
npm run web:release:test
```

## Latest published-build identification

Preview installer names contain the short commit. Do not use only `0.7.0` or a
stable filename to identify a tested build. The release index is available at:

`https://wip.mountlet.pages.dev/api/releases`

