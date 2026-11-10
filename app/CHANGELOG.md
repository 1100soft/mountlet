# Changelog

## 0.7.7

- Use one classified release pipeline for stable `vX.Y.Z` and preview
  `vX.Y.Z-preview.N` tags; ordinary pushes only validate and provenance tags
  never publish. Preserve Debian preview upgrade ordering with explicit sequences.
- Publish verified Linux packages without waiting for Windows/macOS. Bound
  macOS startup and exit checks, retain diagnostics, and report genuine release
  failures to CI-fix automation rather than cancelling them.
- Preserve queued releases and immutable package bytes. Confirm APT publication
  using InRelease/Packages consistency and the actual downloadable checksums;
  resume from retained prepared artifacts instead of repacking old versions.
- Track the shared installer package matrix so website validation can resolve
  every supported installer variant.
- Upgrade using the normal installer or the same APT package identity. This
  release changes publication tooling and introduces no application data
  migration; retain configuration, license, trial, rclone, metadata, and
  offline/cache data.

## 0.7.6

- Route tags on unreleased `wip` commits to preview R2 and automatic preview
  APT publication, while stable `vX.Y.Z` tags on `main` publish production
  packages. Reject unrelated tags on released commits before packaging.
- Allow preview tag names to differ from the checked-in application version;
  stable release tags still require matching version metadata.
- Keep website integration checks independent from native packaging and retain
  the requirement for both tested Linux variants before APT publication.
- Upgrade using the normal installer or the same APT package identity. This
  release changes publication tooling and introduces no application data
  migration; retain configuration, license, trial, rclone, metadata, and
  offline/cache data.

## 0.7.5

- Renew beta licenses before showing the launch UI, retaining saved keys when
  renewal fails or the device is offline.
- Group Google Drive and Photos under one account card with separate service
  authorization, shared settings, and preserved per-service locations.
- Reuse configured OAuth clients without prompting for client credentials; keep
  custom service overrides available in advanced settings.
- Route feedback using the configured GitHub label and require issue creation
  before reporting success.
- Allow tested Linux APT packages to publish even when other platform jobs fail.
- Fix grouped-card startup probe timing and blocking reads in the macOS license
  renewal fixture.
- Upgrade with the normal installer or the same APT package identity. Existing
  Google remotes are linked by their saved account emails and missing companion
  services are provisioned at launch without OAuth. Retain configuration, license,
  trial, rclone, metadata, and offline/cache data; no manual reset is required.

## 0.7.4

- Align desktop manifests and lockfiles with the `v0.7.4` release tag.
- The captured development source has the same application tree as `v0.7.3`;
  this release corrects version metadata and release documentation and adds no
  application behavior changes relative to that release.
- Upgrade using the normal installer or the same APT package identity. No new
  data migration is introduced; retain existing configuration, license, trial,
  rclone, metadata, and offline/cache data.

## 0.7.2 - 2026-10-21

- Align desktop package versions with the `v0.7.2` release tag.

## 0.7.1 - 2026-09-01

- Added a concise first-run tutorial for creating the first remote, with
  animated, provider-aware highlights and a reusable Help menu entry.
- Restored Google OAuth client setup guidance and warned that rclone's shared
  Google client is retiring during 2026.
- Made dialogs dismiss when their backdrop is clicked and resize the native
  parent window to fit when the monitor has enough room.
- Kept tutorial state and focus intact when reopening Mountlet from the tray.
- Restricted production installer publication to version-tag builds.

## 0.7.0 - 2026-08-27

- Replaced the Python/Qt desktop application with the Rust/Tauri implementation.
- Preserved existing settings, rclone, cache, offline, license, notification,
  shortcut, and encrypted configuration-bundle formats.
- Added a virtualized file list and immediate cached navigation for substantially
  lower memory use and faster remote and folder switching.
- Moved rclone, network, sync, report, and file operations out of foreground UI
  paths to keep the application responsive.
- Retained system-tray lifecycle, deterministic tray-relative window placement,
  single and detached browser layouts, and platform-native integration.
- Replaced Python/PyInstaller packaging with Tauri `.deb`, NSIS `.exe`, and
  `.dmg` installers for the existing standard and lean release matrix.
- Made the Windows installer replace the retired Python/Qt installation while
  preserving its user data, trial history, and managed cache.
- Kept all managed cached files in change detection and conflict resolution,
  including temporary copies created by opening a remote file.
- Recovered overwritten Windows trial clocks from preserved NTFS creation times
  without allowing a trial to be extended.
- Restored local and remote configuration-update indicators and lazy mounted
  leaf-folder opening.
- Kept cache badges synchronized with files on disk, queued Windows usage data
  immediately, and added a one-time low-priority recursive metadata crawl.
- Removed the temporary cache-sync diagnostics from user-facing menus.

The archived Python changelog is available under
`legacy/python-0.6.8/CHANGELOG.md`.
