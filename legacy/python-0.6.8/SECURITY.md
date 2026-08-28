# Security

Mountlet works with `rclone` configuration files. Those files may
contain OAuth tokens, refresh tokens, client secrets, provider credentials, and
remote paths that identify private infrastructure.

## Reporting Issues

GitHub private vulnerability reporting is enabled for this repository. Use the
repository's private vulnerability reporting flow for security issues so they
can be reviewed before public disclosure.

## Handling Local Secrets

- Keep real `rclone.conf` files out of version control.
- Keep `client_secret*.json` files out of version control.
- Treat exported bundles as credentials.
- Prefer private backup locations outside this repository and outside package
  install directories.
- Keep installed-user configuration in user config directories such as
  `~/.config/rclone/` and `~/.config/mountlet/`.
- Rotate provider credentials if a real config bundle is shared accidentally.

## Supported Versions

Mountlet is still pre-1.0. Security fixes are intended for the latest public
0.6.x release line.
