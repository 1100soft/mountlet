# Cloud Mount Manager

A small toolkit for mounting rclone remotes, plus helper commands to manage rclone configuration bundles.

## Layout

```
cloud_mount_manager/
├── src/cloud_mount_manager/
│   ├── __init__.py
│   ├── core.py
│   ├── tui.py
│   └── config_tools/
│       ├── shared.py
│       ├── import_config.py
│       ├── export_config.py
│       ├── verify_config.py
│       ├── reconnect_config.py
│       └── path_config.py
├── secrets/               # ignored – place real rclone.conf/client_secret here
├── docs/
├── examples/
├── scripts/
├── tests/
└── README.md
```

The `src` package contains the TUI (`python -m cloud_mount_manager.tui`) and the config helper commands (`python -m cloud_mount_manager.config_tools.import_config`, etc.). The `secrets/` folder is ignored by git; copy your `rclone.conf` and `client_secret*.json` into `secrets/` (or into `~/.config/rclone/`) after cloning.

## Running the TUI

```bash
PYTHONPATH=src python -m cloud_mount_manager.tui
```

By default remotes mount under `~/cloud_mounts/<provider>/<alias>`. Override with `CLOUD_MOUNT_BASE=/path/to/mounts`.

## Config Helpers

Import a bundle (copies config + secrets from `secrets/`, then verifies remotes):

```bash
PYTHONPATH=src python -m cloud_mount_manager.config_tools.import_config --config secrets/rclone.conf
```

Export the current configuration:

```bash
PYTHONPATH=src python -m cloud_mount_manager.config_tools.export_config backups/
```

Verify/remount credentials:

```bash
PYTHONPATH=src python -m cloud_mount_manager.config_tools.verify_config
PYTHONPATH=src python -m cloud_mount_manager.config_tools.reconnect_config --remote MyRemote
```

## Development

- Dependencies are standard library only (no external requirements yet).
- Add unit tests under `tests/`.
- Secrets (`secrets/`, `*.json`, backup files) are excluded via `.gitignore`.
