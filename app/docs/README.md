# Development Notes

This directory contains maintainer-facing notes. `app/README.md` is the
user-facing document used for package publication.

## Current Release (0.6.5)

Version `0.6.5` makes application zoom and window geometry deterministic and
makes cached file-browser navigation the foreground priority. See
`CHANGELOG.md` for the user-facing summary and `RELEASE.md` for the release
procedure.

### Implementation invariants

- Foreground remote and folder navigation must not enumerate the folder being
  left, copy its entry list, compare complete entry lists, or repaint every row
  when background metadata arrives. Selection caching is constant-time. Large
  offline-state scans may run in the background, but their results are consumed
  by a later ordinary render rather than triggering a front-end row loop.
- Once either window mode is constructed, navigation must not change runtime
  layout geometry. Keep the file-list scrollbar reserved, column widths stable,
  and window dimensions unchanged across remote/folder selection. Remote
  addition and removal are the only ordinary operations allowed to change the
  main content layout; mode, zoom, and explicit configuration changes remain
  deliberate user-requested rebuilds.
- `python packaging/run_tests.py --resource-report build/test-resources.json`
  records wall time, child-process CPU time, logical CPU count, and peak child
  RSS where the platform exposes it. This is a reproducible regression baseline,
  not the application's maximum memory requirement; test it on each release
  platform before revising the user-facing minimum and recommended resources.

- External and internal drag-and-drop can target the displayed folder, a
  visible child folder, or a remote row. Remote-row drops use that remote's
  remembered folder. Google Photos drops go to `upload`, except when a
  specific `album/<name>` folder is targeted.
- Folder loads are cancellable and isolated per remote. A stalled Google
  Photos virtual-folder request must not block navigation or operations on
  other remotes. Photos skips recursive auto-indexing, neighboring-folder
  prefetch, search-result API verification, and automatic cloud-side cache
  polling to conserve its restricted API quota.
- Google Photos date leaves are projected from the cached `media/all` listing.
  Media views are read-only; destructive operations are allowed only for media
  entries in albums created through rclone.
- rclone flags are now capability-sensitive. Mountlet probes `rclone help
  flags` once per binary and caches the result. Older binaries receive
  `--gphotos-read-size=false` when supported but do not receive the newer
  `--gphotos-batch-mode=off` option. Do not revert to unconditional
  provider-specific flags.
- Theme refresh now regenerates toolbar icons and repaints already rendered
  file rows. This is required because clearing the file-icon cache alone left
  existing rows with icons from the previous or startup palette.
- Drive and Google Photos remotes can store an optional
  `mountlet_google_account` email. Mountlet uses it as Google's `login_hint`
  during future authorization and as `authuser` for **Open in web**. The web
  hint selects only an account that already has a session in the default
  browser; it does not redirect an unsigned-in account through Google login.
  Changing this metadata does not require reauthentication or remounting.
- Default mount folders retain provider disambiguation in their leaf name (for
  example, `Google Drive/Work__Drive`) while the app continues to show the
  shorter `Work` alias. Empty legacy alias-only mount folders are removed after
  migration; mounted or nonempty folders are preserved.
- iCloud session failures are queued during startup until the main window is
  first shown. Only one prompt can be active per remote. Reauthentication uses
  rclone's noninteractive configuration state machine and renders its required
  follow-up questions, including trusted-device verification and the `sms`
  path with trusted-phone selection. On success Mountlet saves the session and
  retries the mount. Keep these dynamic question handlers intact: a simple
  reconnect command cannot supply iCloud's interactive verification answers.

### Website and service boundaries

- `web/` is the Cloudflare Pages site and Functions backend. D1 stores license,
  device, payment, and notice data; R2 stores installers. Stripe handles test
  or production checkout, and Resend is optional for transactional mail.
- Version tags use production services at `https://mountlet.app`; `wip` builds
  use `https://wip.mountlet.pages.dev`; local builds default to
  local endpoints. Keep build-channel URLs in `build_info.py` and generated
  build metadata instead of scattering literals through UI code.
- Production and preview require separate Cloudflare bindings and Stripe keys.
  Notice history and client state are channel-specific so preview notices do
  not appear in production.
- App support and crash reports post to `/api/report`. The backend forwards
  reports to the configured private GitHub issue repository and optionally by
  email; reports are not mirrored into D1. Do not reintroduce the removed D1
  report-management workaround.
- Release artifact definitions come from `web/release-files.json`. The native
  package workflow builds every platform/variant, verifies all package jobs,
  then publishes versioned installers and a five-version release index to the
  preview or production R2 bucket.

### Google Photos compatibility

The reported symptoms were an apparent `/media` root, inaccessible albums,
only `media/all` being visible, failed JPEG drops, and dim file-browser icons.
The upload error was `unknown flag: --gphotos-batch-mode`.

The configured remote itself has no Mountlet remote subpath. Its remembered
browser path was under `media/all`, which explains why selection reopened that
view. Read-only live checks established that the backend root is healthy:

- both the current app-local rclone (`1.74.3`) and Ubuntu's older system rclone
  (`1.60.1`) list `album`, `feature`, `media`, `shared-album`, and `upload` at
  the remote root when given compatible flags;
- current rclone lists `all`, `by-day`, `by-month`, and `by-year` under
  `media`;
- the album root returns the configured album normally.

The root cause was therefore not a changed Photos root. The new Photos command
policy appended `--gphotos-batch-mode=off` to every command, while rclone
`1.60.1` does not advertise that flag. Listings failed and the UI retained
older cached folder contents, making the namespace appear truncated. The
capability probe fixes listings and transfers without dropping the anti-stall
batch setting on newer rclone versions.

If a Photos folder fails, collect the selected rclone version and raw rclone
output before changing namespace code. Distinguish a real listing failure from
the remembered browser path and cached fallback. Google Photos also has daily
API quotas; quota exhaustion must remain a bounded, explicit error rather than
trigger broad retries or background scans.

## Development

### Platform architecture

Operating-system behavior belongs in `src/mountlet/platform_services/`. Application
modules use the platform contract for:

- user config, state, cache, and mount paths;
- rclone discovery and filesystem-driver readiness;
- mount process creation, status checks, and unmounting;
- start-at-login registration and process-tree shutdown;
- desktop file opening, workspace movement, and keep-above capabilities.

`LinuxPlatformServices`, `WindowsPlatformServices`, and
`MacOSPlatformServices` provide the OS layer. `DesktopServices` supplies Qt
fallbacks and accepts optional desktop adapters. KDE/Dolphin/X11 behavior is an
enhancement on Linux; it must not be required for mounting or opening folders.
GNOME AppIndicator click routing and Wayland window-control restrictions are
documented limitations, not capabilities to emulate with compositor-specific
workarounds. Any future GNOME Shell extension must be versioned and tested as a
separate maintained integration.

File-manager integration must use documented platform interfaces. In
particular, Windows File Explorer does not provide a supported interface for
creating, selecting, or reliably identifying arbitrary tabs. Mountlet therefore
opens the requested path and lets Explorer choose the window or tab. Do not add
keyboard simulation, UI Automation, or undocumented Explorer internals to force
tab reuse; those approaches are locale-sensitive and unstable across Windows
updates. Guard the requested path before launching Explorer so stale or
temporarily unreachable mount folders do not open an unrelated default folder.
Finder and Linux file managers likewise retain final control over window and
tab reuse.

The adapters establish implementation boundaries and testable conventions.
Source-installed desktop and mount flows have been exercised on Linux, Windows,
and macOS. Windows and macOS remain experimental until signing, notarization,
and broader end-to-end testing are complete.

`cloud_browser.py` owns provider-neutral rclone listing, transfer, remembered
paths, and offline snapshots. `cloud_browser_ui.py` owns the compact Qt view
and must keep every rclone operation off the UI thread.

### UI performance and geometry invariants

Responsiveness is the highest UI priority. Pointer and keyboard navigation may
change selection, focus, and already-cached visuals only. It must not wait on
rclone, subprocesses, filesystem scans, settings reads, usage probes, license
verification, mount probes, theme refresh, icon regeneration, model rebuilds,
or per-item loops. The embedded and detached browser paths should perform
equivalent cache-only foreground work.

Persist folder metadata, usage values, and the selected item for each remote so
startup and mode changes can paint useful stale data immediately. Queue live
metadata as soon as a folder is selected, in bounded background work, but do
not commit results or trigger other foreground work while the user is moving
through remotes. A stale result may update the cache; it must not replace a
newer view. Refresh cached usage only after a known file-size mutation or when
new folder metadata reveals a changed aggregate size. Do not add a separate
polling loop for that comparison.

Retain path-to-item maps and complete cached item trees. Swap them with updates
disabled instead of reconstructing every row. Never make selection refresh the
palette, icons, usage, window size, or unrelated controls. Theme and icon
refresh belongs only to actual theme or zoom changes. Mode, theme, and layout
changes must not remount remotes or start authentication.

All supported zoom levels are discrete. Precompute integer pixels for the
lowest-level constants—font, icon, row, spacing, margins, and fixed browser
chrome—and derive all higher-level geometry from them once per zoom level.
Never use a rendered `sizeHint`, current widget geometry, or a post-render move
as an input to window placement. Never cache a folder's total list height:
derive it from fixed chrome plus integer row height times the current item
count. The file tree is deliberately frameless because native frame extents
vary by Qt style and make that equality impossible; do not restore a native
frame without adding its exact, deterministic geometry to every zoom metric.
Its item delegate enforces the cached integer row height for every item; an
item icon, cached state, folder change, or stale `QTreeWidgetItem` size hint
must never be allowed to choose the effective row height.
The file-tree headers and icon sizes are owned by the file-browser zoom metrics
and are excluded from the generic application zoom walker. Otherwise the
startup and show-event passes can scale an already-scaled metric a second time.
Dialog and detached-window positions must come from the cached tray
anchor, inferred panel edge, selected remote row, theoretical target sizes, and
the screen's available geometry before the native window is shown.

Keep diagnostic geometry logging for startup, mode, zoom, remote selection,
and first-open dialogs. When changing the hot path, compare single- and
multi-window navigation using those logs and add a regression test for any
loop, layout activation, or background result that reaches the foreground.
Release cleanup must preserve these behavioral tests. Required regression
tests are named explicitly by `packaging/run_tests.py`, so removing or renaming
one must fail the release rather than quietly reducing coverage.
Copy, move, mkdir, and delete actions are direct rclone operations and must stay
behind the `integrated_file_edits` app setting. Do not present them as undoable
or trash-backed until Mountlet has a provider-aware trash/restore design.
Offline snapshots are local files under the configured app folder's `offline`
directory, not two-way sync; refresh them by removing and recreating the
snapshot. Keep this location inside the user-visible app folder because
sandboxed viewers and office applications may not be able to open files from
hidden app cache directories. Do not make offline files OS-level read-only
because some external viewers need write access for lock or temporary files.
Keyboard shortcuts are scoped by context. Fixed navigation keys such as Up,
Down, Return, Escape, side-aware Left/Right handoff, and Qt's standard copy,
cut, paste, and delete keys should be shown as fixed guidance. Optional
alternatives for common list navigation, remote reordering, browser entry,
per-remote actions, and file-browser actions can be reused between contexts,
but conflicts inside one context should remain blocked in the shortcut editor.
Ctrl++ and Ctrl+- scale the complete application, including fonts, controls,
icons, spacing, rows, and open windows. Ctrl+0 restores the system-derived
baseline. The zoom level is saved as `ui.zoom_steps` and follows config sync.
The main-window footer places Add remote at the left and the current percentage
plus minus, plus, and reset controls at the right. Zoom resizing preserves the
tray edge cached during initial popup placement and positions the file browser
from the final calculated window geometry. Windows stay inside the available
desktop; constrained combined layouts expose scrollbars instead of expanding
beyond it.
Folder listings are persistent caches for remembered paths. Selecting a folder
queues that folder's live metadata immediately, but Mountlet does not eagerly
prefetch its parent or visible children: speculative requests compete with
navigation and consume provider API quota. Deeper recursive indexing remains
opt-in per remote and needs invalidation rules for changes made outside
Mountlet. Offline snapshot
metadata records relative path, type, size, modification time, and local cache
state when available. Uncached files must remain metadata-only entries rather
than fake local filesystem placeholders. Do not treat rclone VFS blocks as an
offline guarantee.
Remote-side cache refresh is metadata-first: background checks should poll
managed cached/offline files in bounded batches with `lsjson --stat --hash`,
initialize missing remote metadata without downloading file bodies, and only
download a cloud copy when metadata differs from the recorded baseline. Keep
the interval configurable and allow manual global and per-file/folder checks so
large caches do not force aggressive provider polling.

Install from a local checkout:

```bash
python -m pip install -e .
```

Run the stdlib test suite:

```bash
python -m unittest discover -s tests
```

Run a syntax check:

```bash
python -m compileall -q src tests
```

Optional development tools are declared in the `dev` extra:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

Native packaging uses a Linux system-Python bundle and PyInstaller on Windows
and macOS. The Linux `.deb` uses `/usr/bin/python3` with app-local Python
libraries to avoid frozen Qt/X11 instability. Windows and macOS bundles include
their own Python runtime.

```bash
python -m pip install -e ".[desktop,packaging]"
python packaging/build_linux_bundle.py
python packaging/verify_bundle.py
python packaging/archive_bundle.py --name mountlet-local
```

On Windows and macOS, replace `build_linux_bundle.py` with PyInstaller:

```bash
python -m PyInstaller --clean --noconfirm packaging/mountlet.spec
```

By default this produces the lean variant, which uses a system `rclone`. To
produce a bundled-rclone variant with the current official platform binary,
stage rclone first:

```bash
python packaging/stage_rclone.py
```

With no argument, the staging script downloads rclone's official current
archive. An explicit `/path/to/rclone` is available only when deliberately
testing another compatible binary. The staged binary is copied into
`vendor/rclone/`, included in the native bundle, and ignored by git. On
Windows, explicit staging rejects package-manager shim executables and requires
the real `rclone.exe`. Lean installs discover `RCLONE_PATH` or a system rclone;
bundled installs use their app-local binary. FUSE, WinFsp, and macFUSE are not
bundled; they remain optional native-folder support.

The `Native package CI` workflow builds visible `system-rclone` and
`bundled-rclone` artifacts. Each artifact contains a portable bundle plus a
Linux `.deb`, Windows setup `.exe`, or macOS `.dmg` for that target. The Windows
installer registers an uninstaller; Linux and macOS use their normal package or
application removal flow. Bundled-rclone jobs verify that the packaged app can
run the packaged rclone before uploading artifacts. These development artifacts
are not Windows-signed or Apple-notarized and expire from GitHub Actions after
14 days.

Install the desktop dependencies when working on the local app:

```bash
python -m pip install -e ".[dev,desktop]"
mountlet
```

The repository-level `secrets/` directory is for local development only. It is
ignored by git and must not be part of the installed-user workflow.

## Release Checklist

- Confirm support contact.
- Add screenshots or terminal recordings for the package page.
- Publish native installer instructions.
- Keep source-based `pipx` instructions for technical users.
- Run CI on every pull request.
- Build a wheel and install it in a clean virtual environment.
- Test on a fresh Ubuntu installation with `rclone` and `fuse3`.
- Verify import/export flows with non-sensitive sample configs.
- Update the provider support table in `app/README.md` after checking real
  setup paths.
- Confirm the built wheel and source distribution do not include local secrets.
- Follow [RELEASE.md](RELEASE.md) when merging `wip` to `main`, tagging, and collecting release artifacts.

## Provider Test Status

`app/README.md` documents provider status based on local remotes in
`~/.config/rclone/rclone.conf` and recent GUI setup work.

Locally tested:

- Google Drive
- Dropbox
- Microsoft OneDrive
- Box
- pCloud
- Cloudflare R2
- Koofr
- Proton Drive
- Google Photos
- iCloud Drive and iCloud Photos
- MEGA

Available but untested:

- Nextcloud through its guided WebDAV setup
- Amazon S3
- MinIO and other S3-compatible storage
- Wasabi
- Other WebDAV providers including ownCloud, SharePoint, and Fastmail Files

Box has been observed to require platform-specific reauthentication even after
syncing the complete Mountlet config bundle.

## Release Strategy

- PyPI publishing is stopped while Mountlet uses the source-available license
  and native desktop distribution.
- Keep source-based installs available for technical users.
- Build unsigned standalone Linux, Windows, and macOS development artifacts in
  GitHub Actions before introducing signing and notarization.
- Publish native desktop packages through the website's R2-backed download
  routes. Keep unsigned builds clearly labeled until Windows code signing and
  Apple Developer ID notarization are configured.

Native packages embed a build channel and build identifier separately from the
public version. Version tags produce production builds; `wip` produces preview
builds; other local packaging runs produce local builds.
Preview and local builds expose that identity in the main toolbar, window
title, tray tooltip, and About dialog. Keep notice endpoints and local notice
history channel-specific so preview messages cannot appear in production.
- Build the desktop app as the first commercial product layer.
- Evaluate `.deb`, AppImage, Windows installer, and macOS DMG distribution after
  the standalone bundles are stable.

## Monetization Direction

The free package should remain useful as a local desktop and terminal tool.
Paid value should be centered on reliability, convenience, support, and managed
configuration.

The first paid product direction is the desktop app.

Initial desktop scope:

- Basic mount, unmount, restart-mount, and open-folder actions.
- Auto-mount at login.
- Remote health checks and notifications.
- One-click credential reconnect flows.
- Per-remote mount policies.
- Commercial support.

Later paid candidates:

- Encrypted local config vault.
- Team configuration templates.

Open questions:

- What support channel should paid users receive?
- What platforms are included in the first paid release?
