# Mountlet

Mountlet manages many cloud storage accounts from one desktop app. It can browse
files without mounting, open cloud files in local apps, keep selected files
available offline, sync safe local edits back to the cloud, search across
remotes, and optionally mount remotes as native folders.

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

Native folder mounting is optional:

- Linux: install FUSE 3 if you want native mounted folders.
- Windows: install WinFsp if you want native mounted folders.
- macOS: install macFUSE if you want native mounted folders.

Without those filesystem drivers, Mountlet still browses, opens, caches, and
syncs files through the integrated file browser.

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
- Type in the main search box to search all indexed remotes.
- Type in the file-browser search box to search the current remote.
- Open files directly. If the remote is not mounted, Mountlet downloads a
  managed local cache copy and opens that file.
- Use **Make available offline** to protect selected files or folders from cache
  cleanup.
- Use **Sync now** to check cached/offline files immediately.
- Enable optional native mounting from the file browser when you want Finder,
  Explorer, Dolphin, or another file manager to see the remote as a folder.

Integrated file edits are disabled by default. If enabled, copy, move, upload,
delete, and drag-and-drop operations are direct cloud operations and are not
undoable by Mountlet.

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

Available but less tested:

- MEGA. Sign in through MEGA's website once before setup so the account
  encryption keys exist.
- Nextcloud through its WebDAV interface. Mountlet derives the DAV endpoint
  from the server address and username.
- Google Photos, with a major limitation: current rclone releases can only
  download media that rclone uploaded.
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
