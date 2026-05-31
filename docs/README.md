# Development Notes

This directory contains maintainer-facing notes. The root `README.md` is the
user-facing document used for package publication.

## Development

Install from a local checkout:

```bash
python -m pip install -e .
```

Run the stdlib test suite:

```bash
python -m unittest discover -s tests
```

Run a syntax check:

```bash
python -m compileall -q src tests
```

Optional development tools are declared in the `dev` extra:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

The repository-level `secrets/` directory is for local development only. It is
ignored by git and must not be part of the installed-user workflow.

## Release Checklist

- Confirm support contact.
- Add a changelog before tagging `v0.1.0`.
- Add screenshots or terminal recordings for the package page.
- Publish PyPI/pipx CLI installation instructions.
- Publish `.deb` installation instructions for the later desktop package.
- Run CI on every pull request.
- Build a wheel and install it in a clean virtual environment.
- Test on a fresh Ubuntu installation with `rclone` and `fuse3`.
- Verify import/export flows with non-sensitive sample configs.
- Confirm the built wheel and source distribution do not include local secrets.

## Release Strategy

- Keep the CLI/TUI core MIT licensed.
- Publish CLI builds to PyPI for lightweight `pipx` installation.
- Publish the first user-facing desktop package as a native Ubuntu `.deb` from
  GitHub Releases.
- Build the desktop tray app as the first commercial product layer.
- Keep Snap and AppImage as later distribution options after the mount and tray
  flows are proven in the `.deb`.

## Monetization Direction

The free package should remain useful as a local CLI/TUI. Paid value should be
centered on reliability, convenience, support, and managed configuration.

The first paid product direction is a desktop tray app.

Initial desktop tray scope:

- Auto-mount at login.
- Remote health checks and notifications.
- One-click credential reconnect flows.
- Per-remote mount policies.
- Commercial support.

Later paid candidates:

- Encrypted local config vault.
- Team configuration templates.

Open questions:

- What support channel should paid users receive?
- What platforms are included in the first paid release?
