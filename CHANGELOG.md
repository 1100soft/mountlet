# Changelog

## 0.1.0 - Unreleased

Initial public CLI release.

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
- Exported bundles are documented as sensitive credential backups.
