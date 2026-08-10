# Release Process

This project uses `wip` for active work and `main` for release-ready code.
Released versions are marked with git tags, not long-lived version branches.

## Branches

- `wip`: active development and automatic pushes.
- `main`: stable branch used for public releases.
- `vX.Y.Z` tags: immutable release points.

Create release branches only if a maintained older line needs fixes while
`main` has moved ahead.

## Release Checklist

Run these from `wip` inside `app/` first:

```bash
VERSION=0.6.5
python packaging/run_tests.py
python -m unittest tests.test_tray
python -m compileall -q src tests packaging
python -m pip wheel . -w /tmp/mountlet-release --no-deps --no-build-isolation
```

Confirm:

- `README.md` describes the user flow and current installer status.
- `README.md` documents tested and untested provider setup paths.
- `CHANGELOG.md` has a section for the version being released.
- `pyproject.toml` and `src/mountlet/__init__.py` have the same version.
- `SECURITY.md` has an active security reporting path or GitHub private vulnerability reporting is enabled.
- Built distributions do not include `secrets/`, `rclone.conf`, or `client_secret*.json`.
- The native package workflow passes for Linux, Windows, macOS arm64, and macOS x64.
- Bundled-rclone workflow jobs pass the packaged-rclone smoke test before artifacts are uploaded.
- The `Upload installers to R2` job publishes every installer defined in
  `web/release-files.json`, updates `releases/index.json`, and retains the five
  newest app versions. The upload uses R2's S3-compatible API, so GitHub
  needs `CLOUDFLARE_R2_ACCESS_KEY_ID` and
  `CLOUDFLARE_R2_SECRET_ACCESS_KEY` secrets with bucket-item read/write access.

## Local Native Package Smoke Test

On Linux, the native packaging path can be smoke-tested locally:

```bash
python -m pip install -e ".[desktop,packaging]"
python packaging/build_linux_bundle.py
python packaging/verify_bundle.py
python packaging/archive_bundle.py --name mountlet-local-system-rclone
python packaging/build_installer.py --name mountlet-local-system-rclone.deb
python packaging/verify_installer.py artifacts/mountlet-local-system-rclone.deb
```

Run equivalent native packaging checks on Windows and macOS through GitHub
Actions unless you are testing on those platforms directly.

## Merge To Main

```bash
git checkout main
git pull origin main
git merge --squash wip
git commit -m "Release v$VERSION"
cd app
python packaging/run_tests.py
python -m compileall -q src tests packaging
```

If you also build Python package artifacts for validation, check only package
files, not the PyInstaller bundle directory:

```bash
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
```

## Tag

After the final release commit is on `main`:

```bash
git tag -a "v$VERSION" -m "Release v$VERSION"
git push origin main --tags
```

Pushing a version tag starts the production native package workflow. The workflow builds
the installers, verifies them, and uploads versioned objects defined by
`web/release-files.json` to the production R2 bucket. It replaces an existing
entry for the same app version or adds a new entry and removes versions beyond
the newest five. Confirm the release list and download routes after completion.

## Python Package Publishing

PyPI publishing is currently disabled. The former trusted publisher was removed
and `python-publish.yml` is now a manual package-check workflow only.

Do not publish new desktop releases to PyPI unless the distribution and license
strategy changes again.
