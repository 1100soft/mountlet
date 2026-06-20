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

All three options below use a separate virtual environment, so they do not
replace a stable `pipx` or PyPI installation. Install Python 3.10 or newer and
[`rclone`](https://rclone.org/install/) first.

### Linux

Install FUSE 3 through your distribution. On Ubuntu or Debian:

```bash
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

Install [`rclone`](https://rclone.org/install/) and
[`WinFsp`](https://winfsp.dev/rel/). Installing rclone with WinGet is the
simplest option because it makes the executable discoverable:

```powershell
winget install Rclone.Rclone
```

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
Then install and start Mountlet:

```powershell
$Preview = "$env:LOCALAPPDATA\Mountlet\preview"
py -3 -m venv $Preview
& "$Preview\Scripts\python.exe" -m pip install --upgrade pip
& "$Preview\Scripts\python.exe" -m pip install --upgrade --force-reinstall `
  "mountlet[tray] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
& "$Preview\Scripts\mountlet.exe" tray
```

### macOS (Experimental)

Install [`rclone`](https://rclone.org/install/) and
[`macFUSE`](https://macfuse.github.io/), then run:

```bash
PREVIEW="$HOME/Library/Application Support/Mountlet/preview"
python3 -m venv "$PREVIEW"
"$PREVIEW/bin/python" -m pip install --upgrade pip
"$PREVIEW/bin/python" -m pip install --upgrade --force-reinstall \
  "mountlet[tray] @ https://github.com/eric-holt/mountlet/archive/refs/heads/wip.zip"
"$PREVIEW/bin/mountlet" tray
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
  status, app settings, raw app, mount, rclone, and FUSE config files, and
  quit.

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
