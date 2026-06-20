# Mountlet

Mountlet is a desktop tray and terminal tool for mounting and unmounting
`rclone` remotes. It uses your existing `rclone` configuration and does not
store cloud credentials inside the application install directory.

## How It Works

Mountlet is a friendly control panel for two standard tools:

- `rclone` connects to cloud storage providers such as Google Drive, Dropbox,
  S3-compatible storage, and WebDAV.
- FUSE lets Linux show a cloud remote as if it were a normal folder on your
  computer.

This app reads your `rclone` remotes, creates local mount folders, and starts or
stops `rclone mount` for you.

## Requirements

- Python 3.10 or newer.
- `rclone`, which connects to your cloud storage.
- FUSE, which lets Linux show mounted cloud storage as folders.

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
current supported platform; Windows and macOS builds are available for early
testing while their mount integration is developed.

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

The app checks whether your computer is ready before it opens the menu. If
something is missing, it prints the next step instead of dropping you into an
empty screen.

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

On macOS, Mountlet runs as a menu-bar utility without a separate Dock icon.
Left-click opens or closes the Mountlet window, and right-click opens the app
menu, matching the other supported desktops.

The Mountlet window provides:

- Compact remote strips with storage usage and mount-state toggles.
- Click-to-open folders, provider website shortcuts, and per-remote settings.
- A guided `+` flow for adding supported cloud remotes through rclone.
- Sorting by registration time, name, provider, total size, used space, or
  remaining space, with manual move controls for final adjustments.
- A pin control that keeps the window above other windows without tying it to
  one desktop.
- A file-manager selector in App settings. Mountlet follows the Linux desktop
  default, uses File Explorer by default on Windows, and Finder on macOS; other
  detected managers can be selected without changing the operating-system
  default.

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

Available but not yet locally tested:

- Amazon S3
- MinIO and other S3-compatible providers
- Wasabi
- WebDAV providers such as Nextcloud, ownCloud, SharePoint, and Fastmail Files

In the setup window, tested options are shown in white and untested options in
yellow. Untested providers may work through rclone, but expect rough edges until
the wizard path is tested with a real account.

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

`rclone.conf` can contain OAuth tokens and provider credentials. Treat exported
bundles as sensitive files.

- Do not share real `rclone.conf` files.
- Do not share `client_secret*.json` files.
- Store backups outside application install directories.
- Review exported bundles before copying them to another machine.

## Status

The current public target is Linux CLI and desktop tray use. The tray is still
early, but it is the main direction for the app.

See the [changelog](https://github.com/eric-holt/mountlet/blob/main/CHANGELOG.md)
for version history.
