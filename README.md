# Cloud Mount Manager

Cloud Mount Manager is a terminal app for mounting and unmounting `rclone`
remotes from a simple menu. It uses your existing `rclone` configuration and
does not store cloud credentials inside the application install directory.

## How It Works

Cloud Mount Manager is a friendly control panel for two standard tools:

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
pipx install cloud-mount-manager
```

For the desktop tray preview:

```bash
pipx install "cloud-mount-manager[tray]"
```

For a local checkout:

```bash
python -m pip install .
```

## Use

Open Cloud Mount Manager:

```bash
cloud-mount-manager
```

The app checks whether your computer is ready before it opens the menu. If
something is missing, it prints the next step instead of dropping you into an
empty screen.

For a guided setup check:

```bash
cloud-mount-manager setup
```

If you have not added any cloud storage to `rclone` yet, let setup open
`rclone`'s connection flow:

```bash
cloud-mount-manager setup --configure-rclone
```

Normal use is:

```bash
cloud-mount-manager
```

Quitting the menu leaves mounted remotes connected. Use `u` in the menu to
unmount everything.

## Desktop Tray Preview

The tray app is optional and uses PySide6. Start it with:

```bash
cloud-mount-manager tray
```

If you installed the CLI without tray support, add PySide6 with:

```bash
pipx inject cloud-mount-manager PySide6
```

The tray app shows each remote with mount, unmount, refresh, and open-folder
actions. If your desktop session does not expose a system tray, use the terminal
menu instead.

## Extra Commands

These are useful for backup, troubleshooting, or moving to another computer:

```bash
cloud-mount-manager path
cloud-mount-manager verify
cloud-mount-manager verify --auto-reconnect
cloud-mount-manager reconnect --remote MyRemote
cloud-mount-manager export ~/cloud-mount-backup
cloud-mount-manager import --config ~/cloud-mount-backup/rclone.conf
```

## File Locations

Cloud Mount Manager keeps application data in user-specific locations and leaves
`rclone` credentials in the standard `rclone` location.

On Linux:

- `~/.config/rclone/rclone.conf`: rclone remotes and credentials.
- `~/.config/cloud-mount-manager/config.toml`: Cloud Mount Manager preferences.
- `~/.local/state/cloud-mount-manager/`: runtime state.
- `~/.cache/cloud-mount-manager/`: cache files.
- `~/cloud_mounts/`: default mount root.

Print the paths for your system:

```bash
cloud-mount-manager path
```

Create the Cloud Mount Manager user folders:

```bash
cloud-mount-manager path --ensure
```

Override the mount root for a shell session:

```bash
export CLOUD_MOUNT_BASE=/path/to/mounts
```

## Credentials

`rclone.conf` can contain OAuth tokens and provider credentials. Treat exported
bundles as sensitive files.

- Do not share real `rclone.conf` files.
- Do not share `client_secret*.json` files.
- Store backups outside application install directories.
- Review exported bundles before copying them to another machine.

## Status

The current public target is Linux CLI use. The desktop tray is an early preview
for the next product layer.

See the [changelog](https://github.com/eric-holt/cloud-mount-manager/blob/main/CHANGELOG.md)
for version history.
