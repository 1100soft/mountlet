# Development Notes

This directory contains maintainer-facing notes. The root `README.md` is the
user-facing document used for package publication.

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
Source-installed tray and mount flows have been exercised on Linux, Windows,
and macOS. Windows and macOS remain experimental until native packaging and
broader end-to-end testing are complete.

`cloud_browser.py` owns provider-neutral rclone listing, transfer, remembered
paths, and the future managed-offline storage layer. `cloud_browser_ui.py` owns
the compact Qt view and must keep every rclone operation off the UI thread.
Copy, move, mkdir, and delete actions are direct rclone operations. Do not
present them as undoable or trash-backed until Mountlet has a provider-aware
trash/restore design.
Keyboard shortcuts are scoped by context. Fixed navigation keys such as Up,
Down, Return, Escape, and side-aware Left/Right handoff should not be exposed
as editable shortcuts. A configurable optional key can be reused between the
remote list and file browser, but conflicts inside one context should remain
blocked in the shortcut editor.
Folder listings are session caches and are preloaded for each remembered remote
path. When a folder is displayed, Mountlet silently prefetches one displayed
level deeper with a bounded queue so navigation into visible folders is often
instant without scanning an entire remote. Deeper recursive indexing must stay
opt-in per remote: it can improve navigation after the first scan, but it
consumes provider API quota, stores more metadata locally, and needs
invalidation rules for changes made outside Mountlet. Offline pinning remains
disabled: its eventual manifest must record the remote object identity,
relative path, type, size, modification time, and hash when available. Pinned
folders need a cached metadata tree, but unpinned files must remain
metadata-only entries rather than fake local filesystem placeholders. Do not
treat rclone VFS blocks as an offline guarantee.

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

Native packaging uses PyInstaller separately on each target operating system:

```bash
python -m pip install -e ".[tray,packaging]"
python -m PyInstaller --clean --noconfirm packaging/mountlet.spec
python packaging/verify_bundle.py
python packaging/archive_bundle.py --name mountlet-local
```

The `Native package CI` workflow builds portable bundles plus a Linux `.deb`,
Windows setup `.exe`, and macOS `.dmg` for both Apple architectures. The Windows
installer registers an uninstaller; Linux and macOS use their normal package or
application removal flow. These development artifacts are not Windows-signed or
Apple-notarized and expire from GitHub Actions after 14 days.

Install the optional tray dependencies when working on the desktop preview:

```bash
python -m pip install -e ".[dev,tray]"
mountlet tray
```

The repository-level `secrets/` directory is for local development only. It is
ignored by git and must not be part of the installed-user workflow.

## Release Checklist

- Confirm support contact.
- Add screenshots or terminal recordings for the package page.
- Publish PyPI/pipx CLI installation instructions.
- Publish `.deb` installation instructions for the later desktop package.
- Run CI on every pull request.
- Build a wheel and install it in a clean virtual environment.
- Test on a fresh Ubuntu installation with `rclone` and `fuse3`.
- Verify import/export flows with non-sensitive sample configs.
- Update the provider support table in the root README after checking real
  setup paths.
- Confirm the built wheel and source distribution do not include local secrets.
- Follow [RELEASE.md](RELEASE.md) when merging `wip` to `main`, tagging, and publishing.

## Provider Test Status

The 0.3.0 release documents provider status based on local remotes in
`~/.config/rclone/rclone.conf` and recent GUI setup work.

Locally tested:

- Google Drive
- Dropbox
- Microsoft OneDrive
- Box
- pCloud
- Cloudflare R2
- Koofr

Available but untested:

- Amazon S3
- MinIO and other S3-compatible storage
- Wasabi
- WebDAV providers including Nextcloud, ownCloud, SharePoint, and Fastmail Files

## Release Strategy

- Keep the CLI/TUI core MIT licensed.
- Publish CLI builds to PyPI for lightweight `pipx` installation.
- Build unsigned standalone Linux, Windows, and macOS development artifacts in
  GitHub Actions before introducing installers and code signing.
- Publish signed native desktop packages through GitHub Releases once startup,
  updates, and prerequisite handling are ready for nontechnical users.
- Build the desktop tray app as the first commercial product layer.
- Evaluate `.deb`, AppImage, Windows installer, and macOS DMG distribution after
  the standalone bundles are stable.

## Monetization Direction

The free package should remain useful as a local CLI/TUI. Paid value should be
centered on reliability, convenience, support, and managed configuration.

The first paid product direction is a desktop tray app.

Initial desktop tray scope:

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
