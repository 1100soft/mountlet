# Changelog

## 0.1.0 - Unreleased

Initial public CLI release target.

### Added

- Single public command: `cloud-mount-manager`.
- Fast readiness check before opening the menu.
- Guided setup flow with `cloud-mount-manager setup`.
- Optional rclone connection flow with `cloud-mount-manager setup --configure-rclone`.
- Interactive menu for mounting, unmounting, refreshing, and verifying remotes.
- Subcommands for setup, path inspection, verification, reconnect, import, and export.
- User-specific app directories for config, state, and cache.
- Import/export helpers for rclone configuration bundles.
- `--version` / `-V` version output.
- CI workflow for tests and package build.

### Changed

- Quitting the menu now leaves mounted remotes connected.
- Public package installs only one console script: `cloud-mount-manager`.
- User-facing documentation now focuses on install, setup, and normal use.

### Security

- Real `rclone.conf` and `client_secret*.json` files are excluded from the package.
- Installed users keep credentials in rclone/user config locations, not the app install directory.
- Exported bundles are documented as sensitive credential backups.
