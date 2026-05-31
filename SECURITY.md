# Security

Cloud Mount Manager works with `rclone` configuration files. Those files may
contain OAuth tokens, refresh tokens, client secrets, provider credentials, and
remote paths that identify private infrastructure.

## Reporting Issues

This repository does not have a public security contact configured yet. Before
public release, enable GitHub private vulnerability reporting or add a dedicated
security contact.

## Handling Local Secrets

- Keep real `rclone.conf` files out of version control.
- Keep `client_secret*.json` files out of version control.
- Treat exported bundles as credentials.
- Prefer private backup locations outside this repository and outside package
  install directories.
- Keep installed-user configuration in user config directories such as
  `~/.config/rclone/` and `~/.config/cloud-mount-manager/`.
- Rotate provider credentials if a real config bundle is shared accidentally.

## Supported Versions

No public stable version is supported yet.
