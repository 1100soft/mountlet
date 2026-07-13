# Mountlet

Mountlet is a desktop app for managing many cloud storage accounts from
different providers in one place. Use it to browse, open, sync, cache, and
organize files across Google Drive, Dropbox, OneDrive, Box, Proton Drive,
S3-compatible storage, and more without jumping between provider apps.

Mountlet can work as a compact file browser, a cache/offline-file manager, and
an optional native folder mounter. For many daily file tasks, it can replace
opening each provider's desktop app separately.

Source code is available for non-commercial use under `LICENSE`. Installer
builds are covered by `docs/EULA.md`. Paid installer downloads and license
purchases are distributed through the production site listed below; source
releases remain available on GitHub. PyPI publishing is currently disabled.

Paid installer builds use a 7-day trial and online activation. Lifetime
licenses keep working offline. Subscriptions store a signed local license
through the current paid period.
Source and development runs do not enforce licensing by default; commercial
installer builds can set `MOUNTLET_REQUIRE_LICENSE=1`.
Beta keys are labeled separately in the License and About windows.
Commercial builds use the generated license endpoints below by default. Test
or relocated deployments can override them with the listed environment
variables.

<!-- mountlet-vars:start -->
- Paid downloads and license purchases: https://mountlet.app
- Default license API: https://mountlet.app/api/license
- Override license API: `MOUNTLET_LICENSE_API_URL`
- Override public purchase site: `MOUNTLET_LICENSE_SITE_URL`
- Override crash/bug report API: `MOUNTLET_REPORT_API_URL`
<!-- mountlet-vars:end -->

## What You Can Do

- Keep many cloud accounts visible in one tray app.
- Browse accounts without mounting them as operating-system folders.
- Open files in the apps already associated with those file types.
- Keep selected files or folders available offline.
- Sync local edits back when the cloud copy has not changed.
- Move settings between computers with encrypted config bundles.
- Optionally expose cloud accounts as folders in Finder, Explorer, Dolphin, or
  another file manager.
- Review and send optional crash or bug reports from inside the app.

## How It Works

Mountlet is a friendly desktop layer over `rclone`, a proven cloud access tool:

- `rclone` signs in to cloud storage providers such as Google Drive, Dropbox,
  OneDrive, Box, Proton Drive, S3-compatible storage, and WebDAV.
- Mountlet Files is the integrated browser. It lists cloud accounts through
  `rclone`, opens files through the operating system, and keeps explicit
  cached and offline copies under the app folder.
- Native folder mounting turns cloud accounts into ordinary folders for Finder,
  Explorer, Dolphin, and other file managers. It uses the platform filesystem
  driver: FUSE on Linux, WinFsp on Windows, or macFUSE on macOS.

The bundled build includes an app-local rclone for convenience. The lean build
uses a separately installed rclone, which is useful for technical users,
administrators, and managed machines. Mountlet does not store cloud credentials
inside the application install directory.

## Bug Reports

If Mountlet closes unexpectedly, it can ask on the next launch whether to review
and send a crash report. The App menu also includes **Report bug** for voluntary
reports. Nothing is sent automatically: the report window shows the message,
system details, recent runtime log, and recent rclone log before you send it.
Mountlet redacts license keys and obvious secrets, but logs can still include
file paths, remote names, and filenames.

## Requirements

- **Bundled native build**: includes an app-local rclone. Linux uses system
  Python with app-local Python libraries; Windows and macOS include their own
  Python runtime.
- **Lean native build**: uses a separate rclone already installed on the
  computer. Linux uses system Python with app-local Python libraries; Windows
  and macOS include their own Python runtime.
- **Source install**: requires Python 3.10 or newer and rclone.
- **Native folder mounting**: requires FUSE on Linux, WinFsp on Windows, or
  macFUSE on macOS.

On Ubuntu, a source or lean install can use the system rclone:

```bash
sudo apt install rclone
```

Install `fuse3` as well if you want native folder mounting.

## Install

For current native builds, use GitHub Releases when release assets are
available. Development builds are available from the GitHub Actions artifacts
described below.

For a source-based Python install from GitHub:

```bash
pipx install "mountlet[desktop] @ https://github.com/eric-holt/mountlet/archive/refs/heads/main.zip"
```

For terminal-only use from source:

```bash
pipx install "mountlet @ https://github.com/eric-holt/mountlet/archive/refs/heads/main.zip"
mountlet menu
```

For a local checkout:

```bash
cd app
python -m pip install ".[desktop]"
```

## Install a GitHub Preview

GitHub previews are source snapshots from the `wip` branch, not signed native
installers. They may be unstable and can change without notice. Linux is the
primary supported platform. Source-installed Windows and macOS desktop and
mount flows are available as experimental support while native packaging is
developed.

The `Native package CI` workflow produces short-lived, unsigned portable bundles
and test installers for Linux x64, Windows x64, macOS Apple Silicon, and macOS
Intel. Operating-system security warnings are expected until signing and Apple
notarization are configured.

Open a successful workflow run under **Actions > Native package CI** and download
the artifact for your platform and preferred dependency model. On macOS, use
`macos-arm64` for Apple Silicon and `macos-x64` for Intel. Artifacts whose name
ends in `system-rclone` expect rclone to be installed separately. Artifacts whose
name ends in `bundled-rclone` include an app-local rclone binary.

Each native artifact contains both the portable archive and:

- Linux: a `.deb` package, removable with your package manager.
- Windows: a setup `.exe`, including an entry in **Installed apps** and an
  uninstaller.
- macOS: a `.dmg`; drag Mountlet to Applications and move the app to Trash to
  uninstall it.

Mountlet has three practical install tracks:

- **Source-based Python install**: uses your Python environment through `pipx`
  or a source checkout. This is the lightest path for technical users.
- **Native system-rclone build**: includes Mountlet and uses a separately
  installed rclone.
- **Native bundled-rclone build**: includes Mountlet and an app-local rclone
  binary. This does not install rclone globally or replace a user's existing
  rclone. On macOS, this build uses the official rclone binary because Homebrew
  rclone does not support `rclone mount`.

Both variants keep native folder mounting optional. The Linux package suggests
FUSE, the Windows installer does not require WinFsp, and the macOS DMG does not
bundle macFUSE.

Uninstalling Mountlet does not remove a system rclone, FUSE/WinFsp/macFUSE,
`rclone.conf`, or Mountlet's per-user settings.

Each section starts with the system prerequisites and installs Mountlet in an
isolated environment, so a GitHub preview does not replace another Mountlet
installation.

Use only the subsection for your operating system. Linux and macOS use shell
commands; Windows uses PowerShell. Their syntax is not interchangeable.

### Linux

Install Python, rclone, and optionally FUSE 3 through your distribution. On
Ubuntu or Debian:

```bash
sudo apt update
sudo apt install rclone python3-venv
```

Add `fuse3` to that command if you want native folder mounting.

Install and start the preview:

```bash
PREVIEW="$HOME/.local/share/mountlet-preview"
python3 -m venv "$PREVIEW"
"$PREVIEW/bin/python" -m pip install --upgrade pip
"$PREVIEW/bin/python" -m pip install --upgrade --force-reinstall \
  "mountlet[desktop] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
"$PREVIEW/bin/mountlet"
```

### Windows (Experimental)

Windows Defender or SmartScreen may warn about preview builds downloaded from
GitHub because the executable is unsigned and has little reputation. Use only
artifacts from this repository's GitHub Actions runs, and expect to allow the
download or app explicitly until release signing is configured.

Install Python 3.12 and rclone with WinGet:

```powershell
winget install --id Python.Python.3.12 --exact
winget install --id Rclone.Rclone --exact
```

Install [`WinFsp`](https://winfsp.dev/rel/) using its Windows installer, then
close and reopen PowerShell so Python and rclone are available.

If you downloaded the portable `rclone.exe` instead, place it in a permanent
folder and tell Mountlet where it is. Replace the example path as needed:

```powershell
$env:RCLONE_PATH = "C:\Tools\rclone\rclone.exe"
[Environment]::SetEnvironmentVariable("RCLONE_PATH", $env:RCLONE_PATH, "User")
```

Mountlet also checks `PATH` and common WinGet, Chocolatey, Scoop, and manual
installation folders. Confirm that rclone is available before continuing:

```powershell
& $env:RCLONE_PATH version
```

If you used WinGet and did not set `RCLONE_PATH`, use `rclone version` instead.

Install `pipx` and add its application directory to your user `PATH`:

```powershell
py -3.12 -m pip install --user --upgrade pipx
py -3.12 -m pipx ensurepath
```

Close and reopen PowerShell so the updated `PATH` is loaded. Then install and
start Mountlet. `pipx` keeps the preview isolated while making the `mountlet`
command available to your user account:

```powershell
pipx install --force "mountlet[desktop] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
mountlet
```

### macOS (Experimental)

Install Apple's Command Line Tools first. A system dialog opens; finish that
installation before continuing:

```bash
xcode-select --install
```

Install [Homebrew](https://brew.sh/) and activate it in the current shell. The
path check supports both Apple Silicon and Intel Macs:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
BREW=/opt/homebrew/bin/brew
[ -x "$BREW" ] || BREW=/usr/local/bin/brew
printf 'eval "$(%s shellenv)"\n' "$BREW" >> "$HOME/.zprofile"
eval "$("$BREW" shellenv)"
```

Install Python and `pipx`:

```bash
brew install python@3.12 pipx
pipx ensurepath
```

Install macFUSE to enable native folder mounting:

```bash
brew install --cask macfuse
```

Before macFUSE can mount anything, macOS may block its kernel extension. Follow
the prompts shown when you first try to mount a remote:

1. Choose **Open System Settings**, then open **Privacy & Security**.
2. If an **Allow** button appears for system software from developer Benjamin
   Fleischer, select it, authenticate, and restart the Mac.
3. On an Apple Silicon Mac, macOS may first show **Enable System Extensions**.
   Select it and shut down when prompted. Hold the power button to enter
   Recovery, open **Startup Security Utility**, select the macOS volume, and
   choose **Security Policy**.
4. Select **Reduced Security**, enable **Allow user management of kernel
   extensions from identified developers**, and restart.
5. Try mounting again, return to **Privacy & Security**, select **Allow** for
   macFUSE if requested, and restart once more.

These security changes are required by macFUSE's kernel backend, not by
Mountlet. See the official
[macFUSE setup guide](https://github.com/macfuse/macfuse/wiki/Getting-Started)
for screenshots and troubleshooting.

Unsigned test DMGs are also subject to Gatekeeper. After copying Mountlet to
Applications, Control-click **Mountlet**, choose **Open**, then confirm **Open**.
If macOS still blocks it, open **System Settings > Privacy & Security** and use
**Open Anyway** for Mountlet. Public releases require Developer ID signing and
Apple notarization; do not disable Gatekeeper globally.

If Finder shows a prohibitory mark and says the app is not supported, first
check that the downloaded artifact matches the Mac: `macos-arm64` for Apple
Silicon and `macos-x64` for Intel. Current native artifacts target macOS 11 or
newer.

If the app opens silently and no menu-bar icon appears, check Mountlet's startup
log:

```bash
cat "$HOME/Library/Application Support/mountlet/State/startup.log"
```

For a development artifact downloaded directly from this repository's GitHub
Actions, remove quarantine from that app only if macOS offers neither option:

```bash
xattr -dr com.apple.quarantine /Applications/Mountlet.app
```

Install rclone using its official script if you are using a source install or
the lean native build and want native folder mounting. Do not use
`brew install rclone` for that path: Homebrew rclone does not support
`rclone mount` on macOS. The bundled-rclone native build already includes a
compatible app-local rclone.

```bash
sudo -v
curl https://rclone.org/install.sh | sudo bash
```

Finally, install Mountlet with the Homebrew Python:

```bash
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
"$PYTHON" --version
pipx install --force --python "$PYTHON" \
  "mountlet[desktop] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
```

The version check must report Python 3.10 or newer. Close and reopen the
terminal after `pipx ensurepath`, then start the preview:

```bash
mountlet
```

Run the same install command again to update an existing preview. To test a
specific tagged pre-release instead, replace `refs/heads/wip.zip` with
`refs/tags/vX.Y.Z.zip`.

## Use

Open Mountlet:

```bash
mountlet
```

The desktop app checks for rclone at startup. If rclone is missing, a setup
window shows the relevant official installation instructions and checks again
while it remains open. If the optional filesystem driver is missing, Mountlet
still starts; only native folder mounting is disabled.

For a guided setup check:

```bash
mountlet setup
```

If you have not added any cloud storage to `rclone` yet, let setup open
`rclone`'s sign-in flow:

```bash
mountlet setup --configure-rclone
```

Normal use is:

```bash
mountlet
```

For the terminal menu instead, run:

```bash
mountlet menu
```

Quitting the terminal menu leaves mounted remotes mounted. Use `u` in the menu
to unmount everything.

## Desktop App

The desktop app uses PySide6 Essentials. Start it with:

```bash
mountlet
```

If you installed the terminal-only package and later want the desktop app, add
PySide6 Essentials with:

```bash
pipx inject mountlet PySide6-Essentials
```

The tray app uses the tray icon this way:

- Hover shows a short mounted/unmounted summary.
- Left-click opens or closes the Mountlet window. If it is behind another
  window, the first click brings it forward. On Plasma X11, opening it from a
  different desktop moves it to the current desktop.
- Right-click shows app-level actions such as mount all, unmount all, update
  status, app settings, available configuration files, and quit.

### Platform behavior

Desktop integration is constrained by the APIs each operating system and
desktop exposes:

| Platform | Known behavior and limitations |
| --- | --- |
| Plasma X11 | Provides the most complete tray placement, workspace movement, pinning, and Dolphin integration. Dolphin tab reuse is best-effort. |
| GNOME | The AppIndicator bridge may route a primary click to the app menu instead of reporting distinct left and right clicks. Use **Open Mountlet** from the menu; double-click is also accepted when GNOME reports it. |
| Wayland | Compositors restrict global placement, workspace, focus, and stacking control. Placement near the tray is approximate, pinning may be unavailable, and Mountlet may appear as a normal taskbar window. The file browser is embedded beside the remote list so the compositor cannot overlap two Mountlet windows. |
| Windows | Windows may initially place Mountlet in the notification overflow area. File Explorer has no supported interface for creating or selecting an arbitrary tab, so opening a mount may create another Explorer window, including a duplicate. Mountlet checks that the requested mount folder is reachable before handing it to Explorer. |
| macOS | Mountlet runs as a menu-bar utility without a separate Dock icon. Finder decides whether an opened mount uses an existing window, a tab, or a new window. The in-window App, Mount, and Config menus are kept inside Mountlet rather than moved to the macOS system menu bar. |

These are integration limits rather than mounting restrictions. Selecting a
different detected file manager in App settings may provide different behavior.

The Mountlet window provides:

- Compact remote strips with storage usage, provider shortcuts, and quick
  access to per-remote settings.
- Remote strips that open a compact file browser and switch its active remote
  on hover while the browser is open.
- Mount and unmount controls in the file browser for the active remote.
- A guided `+` flow for adding supported cloud remotes through rclone.
- Sorting by registration time, name, provider, total size, used space, or
  remaining space, with manual move controls for final adjustments.
- A pin control that keeps the window above other windows where the desktop
  compositor supports it.
- A file-manager selector in App settings. Mountlet follows the Linux desktop
  default, uses File Explorer by default on Windows, and Finder on macOS; other
  detected managers can be selected without changing the operating-system
  default.

### File browser

Click a remote strip to open Mountlet Files beside the main window. Each remote
remembers its last folder. The browser lists the remote through rclone, so the
remote does not need to be mounted.

- Double-click folders to navigate and files to open them. Use the parent and
  root buttons to move out of the current folder.
- Hover over a remote strip to open or switch the browser without moving
  keyboard focus. Click the strip to focus the browser.
- With the main window focused, use Up and Down to select remote strips and
  Return to enter the browser. Left or Right also enters the browser when that
  key points toward the side where the browser is displayed; the opposite arrow
  does nothing. Shift+Up and Shift+Down move the selected remote. In the
  browser, Return opens an item, Escape returns to the selected strip, and the
  arrow pointing back toward the main window also returns. Fixed inputs are
  shown separately from configurable alternatives in `Config` > `Keyboard
  shortcuts`; alternatives can be added for common list navigation, per-remote
  actions, and file-browser commands.
- Editing inside Mountlet Files is disabled by default. Enable it from `App`
  > `Settings` > `Allow edits in Mountlet Files` only if you want direct cloud
  edits from the integrated browser.
- When integrated edits are enabled, use item and folder context menus, or
  `Ctrl+C`, `Ctrl+X`, and `Ctrl+V`, to transfer files and folders within or
  between remotes. These standard shortcuts remain fixed, and optional
  alternatives can be assigned in the shortcut settings.
- Press Delete to permanently delete selected cloud items after confirmation.
- Right-click an item for open, copy, cut, and delete commands. Folder menus
  can also open the mounted location in the configured file manager. Right-click
  the current path or empty list area to paste, open the current mounted folder,
  or create a folder.
- Drag files onto another remote strip to copy them to that remote's remembered
  folder. Hold Shift while dropping to move them.
- When a remote is mounted, opening a file uses the mounted file path. When it
  is not mounted, Mountlet downloads a managed cached copy and opens that local
  file with the operating system's file association.
- Mountlet tracks managed cached files. If a cached file changes locally and
  the cloud file has not changed since the cache was created, Mountlet uploads
  the local change automatically when the remote is reachable. If both changed,
  Mountlet asks which version to keep or whether to keep both.
- Mountlet also checks cached and offline files for cloud-side changes in the
  background. The default interval is 30 seconds and can be changed in `App` >
  `Settings` > `Cloud check interval`; set it to `0` for manual checks only.
  Use `App` > `Sync cached files now`, or a file/folder context menu in
  Mountlet Files, to check immediately.
- Use **Make available offline** to protect selected cached files or folders
  from normal cache cleanup. These protected files use the same conflict flow as
  ordinary cached files, but **Free resolved cache** leaves them in place.
- Folder context menus can free resolved ordinary cache for the current folder
  or all remotes. Files with unresolved local changes are kept until the remote
  can be checked and the change is uploaded or resolved.

Integrated edits are direct rclone operations. Mountlet does not keep an
undo/redo history, and deleted cloud items are not moved to the system trash.
Use the system file manager when you want file-manager buffering, undo, or
trash behavior.

Mountlet caches folder listings in memory, preloads each remote's remembered
folder, and silently prefetches one displayed level deeper when folders are
shown. Use the refresh button when cloud contents have changed outside
Mountlet.

If your desktop session does not expose a system tray, use the terminal menu
instead.

## Provider Support

Mountlet uses `rclone` under the hood, so provider support depends on both
Mountlet's setup UI and rclone's backend behavior.

Locally tested with the current GUI flow and/or active local remotes:

- Google Drive
- Dropbox
- Microsoft OneDrive
- Box
- pCloud
- Cloudflare R2 through the S3-compatible wizard
- Koofr through rclone's dedicated Koofr backend
- Proton Drive with current rclone releases
- iCloud Drive and iCloud Photos with recent rclone releases

Available but not yet locally tested:

- Google Photos (limited: current rclone releases can only download media that
  rclone uploaded)
- Amazon S3
- MinIO and other S3-compatible providers
- Wasabi
- WebDAV providers such as Nextcloud, ownCloud, SharePoint, and Fastmail Files
- Other rclone backends through the **Other provider** terminal fallback

In the setup window, tested options are shown in white and untested options in
yellow. Untested providers may work through rclone, but expect rough edges until
the wizard path is tested with a real account.

For providers without a Mountlet-specific setup form, choose **Other provider
(rclone terminal)**. Mountlet opens `rclone config` in an external terminal
using the same config file. After finishing the terminal wizard, use **Update
status** or reopen Mountlet if the new remote is not visible yet.

Some providers can still require per-device reauthentication after config sync.
Box has shown this behavior in local testing even when the synced config bundle
contains all Mountlet and rclone config files.

iCloud remotes may show `?` for usage. rclone can access iCloud Drive and
Photos, but it does not expose reliable quota data for this backend.

Google Photos is a specialized media backend, not a general cloud drive. Due to
Google API policy changes, current rclone releases can only download media that
rclone uploaded, so existing Photos libraries may appear empty in Mountlet. See
the [rclone Google Photos limitations](https://rclone.org/googlephotos/#limitations).

## Extra Commands

These are useful for backup, troubleshooting, or moving to another computer:

```bash
mountlet path
mountlet verify
mountlet verify --auto-reconnect
mountlet reconnect --remote MyRemote
mountlet export ~/mountlet-backup
mountlet import --config ~/mountlet-backup/rclone.conf
```

In the tray app, use `Config` > `Export config bundle` to create one
`.mountlet` file containing `rclone.conf`, nearby client-secret files,
`config.toml`, and `mounts.toml`. Mountlet can encrypt the bundle with a
password; leave the password blank only when you are comfortable storing the
bundle as plain text. Use `Config` > `Import config bundle` on another device to
restore that bundle. Mountlet saves the current local config as one restorable
backup bundle before importing; use `Config` > `Open config backup folder` to
restore or delete old backups.

You can save or open a bundle through a mounted remote. When the selected path
is inside a mounted remote, Mountlet stages the transfer through `rclone` so the
remote sees the file directly instead of relying on filesystem-driver behavior.
Encrypted bundles are recommended for cloud storage.

For regular multi-device use, set `Config` > `Set config sync location` to a
remote and bundle path, then use the top-row up/down arrow buttons to push or
pull the encrypted config bundle. The arrows show a small dot when the local
config has changed since the last push, or when Mountlet sees a different
bundle at the sync location. This avoids the Windows file dialog limitation
where mounted remote folders can appear empty even though Explorer and Mountlet
Files can browse them. Mountlet does not store the bundle password; it asks each
time you push or pull. Automatic sync is not enabled yet because hidden conflict
resolution could overwrite a newer local change on another device.

Technical users can open the raw config files from `Config` > `Open config
file`.

Most personal rclone remotes can use the same `rclone.conf` on your own
devices. Some providers may still require reconnecting on the new device, and
provider-specific local prerequisites such as rclone and the filesystem driver
must still be installed there.

## File Locations

Mountlet keeps application data in user-specific locations and leaves
`rclone` credentials in the standard `rclone` location.

On Linux:

- `~/.config/rclone/rclone.conf`: rclone remotes and credentials.
- `~/.config/mountlet/config.toml`: Mountlet preferences.
- `~/.config/mountlet/mounts.toml`: per-remote mount preferences.
- `~/.local/state/mountlet/`: runtime state.
- `~/.cache/mountlet/`: cache files.
- `~/Mountlet/`: default app folder.
- `~/Mountlet/mounted/`: default mount root.
- `~/Mountlet/offline/`: default offline snapshots.

Print the paths for your system:

```bash
mountlet path
```

Create the Mountlet user folders:

```bash
mountlet path --ensure
```

That command also creates starter `config.toml` and `mounts.toml` files if they
do not exist yet.

Override the mount root for a shell session:

```bash
export MOUNTLET_MOUNT_BASE=/path/to/mounts
```

The tray app's App settings use an app-folder picker. When you choose an app
folder, Mountlet keeps mounted remotes in its `mounted` subfolder and offline
snapshots in its `offline` subfolder.

### App Settings

In the tray app, use `Config` > `App settings` to edit app-wide behavior. Use
the gear button on a remote strip to edit only that mount. The settings
windows show the available fields with text boxes, checkboxes, and dropdowns,
then write `config.toml` and `mounts.toml` for you.

Technical users can still open the raw text files from the app-level config
menu.

Keep cloud account details in `rclone.conf`; Mountlet settings only control
local app and mount behavior.

## Credentials

`rclone.conf` can contain OAuth tokens and provider credentials. Treat
unencrypted `.mountlet` bundles as sensitive files. Copy them only between
devices you control. Password-protected bundles are encrypted with AES-256-GCM
using a key derived from the password.

- Do not share real `rclone.conf` files.
- Do not share `client_secret*.json` files.
- Store backups outside application install directories.
- Review exported bundles before copying them to another machine.

## Status

The current public target is a desktop-first beta for Linux, with Windows and
macOS available as experimental platforms until signing, notarization, and
broader end-to-end testing are complete. The terminal menu remains available
for systems without tray support.

## License

Mountlet source is source-available for non-commercial use. Commercial use or
redistribution requires separate permission. Installer builds use the concise
installer license in `docs/EULA.md`.

See the [changelog](https://github.com/eric-holt/mountlet/blob/main/app/CHANGELOG.md)
for version history.
