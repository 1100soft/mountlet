CI stages an official rclone binary below this directory for bundled builds:

`<app-version>/<os>-<rust-arch>/rclone[.exe]`

Lean builds contain only this note and use `RCLONE_PATH`, `RCLONE_BINARY`, or
the system PATH. On Windows, Mountlet copies a bundled binary to its versioned
LocalAppData runtime directory before launching it. Existing mounts can then
survive an app upgrade without locking installer-owned files.
