# Desktop release process

Version 0.7.0 and later are built from the Tauri application in `app/`.

1. Update the matching versions in `app/package.json`,
   `app/src-tauri/Cargo.toml`, and `app/src-tauri/tauri.conf.json`.
2. Run the required checks documented in `app/README.md`.
3. Push a matching `vX.Y.Z` tag or run **Native package CI** manually.
4. Verify all eight standard/lean installers and their startup probes.
5. The release job uploads installers using the stable names in
   `web/release-files.json`; the website publishes versioned R2 object names.

Bundled builds stage an official current rclone under an app-versioned resource
directory. Lean builds intentionally contain no rclone executable. Windows
copies bundled rclone to a versioned LocalAppData runtime directory so upgrades
do not overwrite a binary still serving a mount.

Version 0.6.8 is the final Python release. Its reproducible source, tests, and
packaging scripts remain under `legacy/python-0.6.8/` and are not part of the
current release pipeline.
