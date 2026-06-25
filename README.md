# Mountlet

Mountlet is a desktop tray and terminal tool for mounting and unmounting
`rclone` remotes. It uses your existing `rclone` configuration and does not
store cloud credentials inside the application install directory.

## How It Works

Mountlet is a friendly control panel for two standard tools:

- `rclone` connects to cloud storage providers such as Google Drive, Dropbox,
  S3-compatible storage, and WebDAV.
- A filesystem driver lets the operating system show a cloud remote as if it
  were a normal folder: FUSE on Linux, WinFsp on Windows, or macFUSE on macOS.

This app reads your `rclone` remotes, creates local mount folders, and starts or
stops `rclone mount` for you.

## Requirements

- Python 3.10 or newer.
- `rclone`, which connects to your cloud storage.
- A compatible filesystem driver: FUSE on Linux, WinFsp on Windows, or macFUSE
  on macOS.

On Ubuntu, install the system tools with:

```bash
sudo apt install rclone fuse3
```

## Install

For isolated CLI use:

```bash
pipx install mountlet
```

For the desktop tray preview:

```bash
pipx install "mountlet[tray]"
```

For a local checkout:

```bash
python -m pip install .
```

## Install a GitHub Preview

GitHub previews are source snapshots from the `wip` branch, not signed native
installers. They may be unstable and can change without notice. Linux is the
primary supported platform. Source-installed Windows and macOS tray and mount
flows are available as experimental support while native packaging is developed.

The `Native package CI` workflow produces short-lived, unsigned portable bundles
and test installers for Linux x64, Windows x64, macOS Apple Silicon, and macOS
Intel. Operating-system security warnings are expected until signing and Apple
notarization are configured.

Open a successful workflow run under **Actions > Native package CI** and download
the artifact for your platform. It contains both the portable archive and:

- Linux: a `.deb` package, removable with your package manager.
- Windows: a setup `.exe`, including an entry in **Installed apps** and an
  uninstaller.
- macOS: a `.dmg`; drag Mountlet to Applications and move the app to Trash to
  uninstall it.

The Linux package recommends rclone and FUSE through package metadata. The
Windows installer checks for rclone and WinFsp before copying Mountlet. The
macOS DMG has no executable installation phase, so its prerequisite check runs
when Mountlet first starts.

Uninstalling Mountlet does not remove rclone, FUSE/WinFsp/macFUSE, `rclone.conf`,
or Mountlet's per-user settings.

Each section starts with the system prerequisites and installs Mountlet in an
isolated environment, so a GitHub preview does not replace a stable PyPI
installation.

Use only the subsection for your operating system. Linux and macOS use shell
commands; Windows uses PowerShell. Their syntax is not interchangeable.

### Linux

Install FUSE 3 through your distribution. On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install rclone fuse3 python3-venv
```

Install and start the preview:

```bash
PREVIEW="$HOME/.local/share/mountlet-preview"
python3 -m venv "$PREVIEW"
"$PREVIEW/bin/python" -m pip install --upgrade pip
"$PREVIEW/bin/python" -m pip install --upgrade --force-reinstall \
  "mountlet[tray] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
"$PREVIEW/bin/mountlet" tray
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
pipx install --force "mountlet[tray] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
mountlet tray
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

Install Python, `pipx`, and macFUSE:

```bash
brew install python@3.12 pipx
brew install --cask macfuse
pipx ensurepath
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

For a development artifact downloaded directly from this repository's GitHub
Actions, remove quarantine from that app only if macOS offers neither option:

```bash
xattr -dr com.apple.quarantine /Applications/Mountlet.app
```

Install rclone using its official script. Do not use `brew install rclone` for
Mountlet: that macOS build does not include mount support.

```bash
sudo -v
curl https://rclone.org/install.sh | sudo bash
```

Finally, install Mountlet with the Homebrew Python:

```bash
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
"$PYTHON" --version
pipx install --force --python "$PYTHON" \
  "mountlet[tray] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
```

The version check must report Python 3.10 or newer. Close and reopen the
terminal after `pipx ensurepath`, then start the preview:

```bash
mountlet tray
```

Run the same install command again to update an existing preview. To test a
specific tagged pre-release instead, replace `refs/heads/wip.zip` with
`refs/tags/vX.Y.Z.zip`.

## Use

Open Mountlet:

```bash
mountlet
```

The desktop app checks for rclone and the platform filesystem driver at startup.
If either is missing, a setup window shows the relevant official installation
instructions and checks again while it remains open. Mountlet starts
automatically when both are available. If an installer requires a restart,
reopen Mountlet and the same checks resume; existing rclone remotes are used
without being copied.

For a guided setup check:

```bash
mountlet setup
```

If you have not added any cloud storage to `rclone` yet, let setup open
`rclone`'s connection flow:

```bash
mountlet setup --configure-rclone
```

Normal use is:

```bash
mountlet
```

Quitting the menu leaves mounted remotes connected. Use `u` in the menu to
unmount everything.

## Desktop Tray Preview

The tray app is optional and uses PySide6. Start it with:

```bash
mountlet tray
```

If you installed the CLI without tray support, add PySide6 with:

```bash
pipx inject mountlet PySide6
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

- Compact remote strips with storage usage and mount-state toggles.
- Remote strips that open a compact file browser and switch its active remote
  on hover while the browser is open.
- Provider website shortcuts and per-remote settings.
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
  arrow pointing back toward the main window also returns. Space, Shift+Up,
  Shift+Down, Backspace, Alt+Home, F5, Ctrl+Return, and other optional shortcut
  alternatives can be changed from `Config` > `Keyboard shortcuts`.
- Editing inside Mountlet Files is disabled by default. Enable it from `App`
  > `Settings` > `Allow edits in Mountlet Files` only if you want direct cloud
  edits from the integrated browser.
- When integrated edits are enabled, use item and folder context menus, or
  `Ctrl+C`, `Ctrl+X`, and `Ctrl+V`, to transfer files and folders within or
  between remotes.
- Press Delete to permanently delete selected cloud items after confirmation.
- Right-click an item for open, copy, cut, and delete commands. Folder menus
  can also open the mounted location in the configured file manager. Right-click
  the current path or empty list area to paste, open the current mounted folder,
  or create a folder.
- Drag files onto another remote strip to copy them to that remote's remembered
  folder. Hold Shift while dropping to move them.
- **Make available offline** is visible but disabled while snapshot metadata,
  local-edit behavior, and conflict handling are designed.

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

Available but not yet locally tested:

- Amazon S3
- MinIO and other S3-compatible providers
- Wasabi
- WebDAV providers such as Nextcloud, ownCloud, SharePoint, and Fastmail Files

In the setup window, tested options are shown in white and untested options in
yellow. Untested providers may work through rclone, but expect rough edges until
the wizard path is tested with a real account.

Some providers can still require per-device reauthentication after config sync.
Box has shown this behavior in local testing even when the synced config bundle
contains all Mountlet and rclone config files.

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
- `~/cloud_mounts/`: default mount root.

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

The current public target is Linux CLI and desktop tray use. The tray is still
early, but it is the main direction for the app.

See the [changelog](https://github.com/eric-holt/mountlet/blob/main/CHANGELOG.md)
for version history.
