# Mountlet

Mountlet manages many cloud storage accounts from one desktop app. It provides
two complete file-management workflows: an integrated browser with managed
cache, offline files, and conflict checks; and mounted folders for Finder,
Explorer, Dolphin, command-line tools, and other desktop apps.

<!-- mountlet-vars:start -->
- Paid downloads and license purchases: https://mountlet.app
- Default license API: https://mountlet.app/api/license
- Override license API: `MOUNTLET_LICENSE_API_URL`
- Override public purchase site: `MOUNTLET_LICENSE_SITE_URL`
- Override crash/bug report API: `MOUNTLET_REPORT_API_URL`
<!-- mountlet-vars:end -->

## Install

Use the bundled installer unless you specifically want to manage rclone
yourself. The bundled build includes an app-local rclone and is the normal
choice for most users. The lean build uses an rclone already installed on the
computer.

Mounted folder access uses a platform filesystem driver:

- Linux: install FUSE 3 from the distribution package manager
  ([libfuse](https://github.com/libfuse/libfuse)):

  ```bash
  # Debian or Ubuntu
  sudo apt install fuse3

  # Fedora
  sudo dnf install fuse3

  # Arch Linux
  sudo pacman -S fuse3
  ```
- Windows: download and run the
  [WinFsp installer](https://winfsp.dev/rel/).
- macOS: follow the official
  [macFUSE installation guide](https://github.com/macfuse/macfuse/wiki/Getting-Started),
  approve its system software in **System Settings > Privacy & Security** when
  prompted, and restart when macOS requests it.

Mountlet file management runs through the integrated browser. The two workflows
operate independently and can be used together, per remote.

### Unsigned Builds

Current builds are not code-signed or Apple-notarized. Use only installers from
Mountlet releases or Mountlet's own GitHub Actions artifacts.

- **Windows**: Microsoft Defender SmartScreen may show **Windows protected your
  PC**. Choose **More info**, then **Run anyway** if you trust the downloaded
  Mountlet installer.
- **macOS**: Gatekeeper may block the app. In Finder, Control-click
  **Mountlet**, choose **Open**, then confirm **Open**. If macOS still blocks
  it, open **System Settings > Privacy & Security** and choose **Open Anyway**
  for Mountlet. Do not disable Gatekeeper globally.
- **Linux**: install the `.deb` with your package installer. If the desktop
  blocks execution from a downloaded archive, extract it to a normal user
  folder and mark the launcher executable through the file manager or
  `chmod +x`.

If macOS shows a prohibitory mark, the build probably does not match the Mac:
use `macos-arm64` for Apple Silicon and `macos-x64` for Intel.

## First Run

Start Mountlet from the app launcher or run:

```bash
mountlet
```

The app checks for required tools and shows setup instructions if something is
missing. Add cloud accounts with the `+` button. Mountlet uses rclone's
authentication flow, so OAuth stays with the provider and rclone.

The app starts with a 7-day trial in commercial builds. Activate a license from
the License window. Lifetime licenses keep working offline after activation;
subscription licenses are refreshed at renewal boundaries. Subscription prices
may change for future renewals with advance notice.

## Daily Use

- Select a remote to show its file browser.
- For Google Drive and Google Photos, the optional **Google account** setting
  helps **Open in web** choose the matching account. The account must already
  be signed in within the default browser; the hint does not start a new Google
  sign-in session. Leave the setting blank to use the browser's current
  account.
- Type in the main search box to search all indexed remotes.
- Type in the file-browser search box to search the current remote.
- Open files directly. Mountlet opens a managed local cache copy whether or
  not the remote is mounted. This also lets rclone export native Google
  documents for local editing.
- Use **Make available offline** to protect selected files or folders from cache
  cleanup.
- Use **Sync now** to check cached/offline files immediately.
- Drag local files and folders into the browser to upload them. Drag browser
  items into a system file manager to copy them out. Mountlet prepares
  uncached items in the background first; drag them again when the status says
  they are ready.
- Mount a remote from the file browser to expose it as a native folder in
  Finder, Explorer, Dolphin, or another file manager.

Files opened through a system file manager use the live mounted folder. Close
them before unmounting. Mountlet refuses a busy unmount rather than detaching a
path that an editor could later recreate; empty stale directories are cleaned
before the next mount, while actual local files are never deleted automatically.

Integrated file edits are disabled by default. If enabled, copy, move, upload,
delete, and drag-and-drop operations are direct cloud operations and are not
undoable by Mountlet. Dragging out is always copy-only, so an external file
manager cannot move or delete Mountlet's managed cache.

## Supported Providers

Mountlet uses rclone, so provider support follows rclone backend support. The
GUI has guided setup for major providers and an **Other provider** fallback that
opens rclone's terminal setup.

Locally tested:

- Google Drive
- Dropbox
- Microsoft OneDrive
- Box
- pCloud
- Cloudflare R2 through S3-compatible setup
- Koofr
- Proton Drive with recent rclone releases
- iCloud Drive and iCloud Photos with recent rclone releases
- MEGA. Sign in through MEGA's website once before setup so the account
  encryption keys exist.
- Google Photos, with a major limitation: current rclone releases can only
  download media that rclone uploaded. Mountlet sends drops outside a specific
  album to its `upload` folder. Media views are read-only; Google permits
  rclone to remove media only from albums it created. See the
  [rclone Google Photos limitations](https://rclone.org/googlephotos/#limitations) for its
  specialized layout and API limitations. To conserve Google's restricted API
  quota, Mountlet does not recursively index Photos or poll its cached files
  for cloud changes; folders refresh when visited and manual **Sync now**
  remains available.

Available but less tested:

- Nextcloud through its WebDAV interface. Mountlet derives the DAV endpoint
  from the server address and username.
- Amazon S3, MinIO, Wasabi, and other S3-compatible storage.
- Other WebDAV providers such as ownCloud, SharePoint, and Fastmail Files.
- Other rclone backends through the terminal fallback.

Some providers may require reconnecting on each device even when config files
are synced. iCloud and Google Photos may not expose reliable quota information,
so usage can show as `?`.

## Config Sync

Use **Config > Export config bundle** to create one `.mountlet` file containing
Mountlet settings and rclone config files. Use a password when storing the
bundle in cloud storage.

For repeated use, set a config sync location and use the top-row push/pull
buttons. Mountlet asks for the bundle password each time and shows a dot when
local or remote config changes are detected.

## Bug Reports

If Mountlet closes unexpectedly, it can offer to send a crash report on the next
launch. You can also use **App > Report bug**. Reports are reviewed before
sending and go to Mountlet's private GitHub report tracker. Mountlet redacts
obvious secrets, but logs can still contain paths, remote names, and filenames.

## File Locations

Mountlet keeps user data outside the install directory. Run:

```bash
mountlet path
```

Default app folders:

- Linux: `~/Mountlet`
- Windows: `%USERPROFILE%\Mountlet`
- macOS: `~/Mountlet`

Inside the app folder:

- `mounted/`: native mount folders
- `offline/`: managed cached and offline files

rclone credentials remain in rclone's standard config location.

## Source Install

Source installs are for technical users and do not enforce commercial licensing
by default:

```bash
pipx install "mountlet[desktop] @ https://github.com/eric-holt/mountlet/archive/refs/heads/main.zip"
mountlet
```

For a checkout:

```bash
cd app
python -m pip install ".[desktop]"
mountlet
```

## License

The source is available for non-commercial use under the repository license.
Installer builds are covered by `docs/EULA.md`.
