# Release Process

This project uses `wip` for active work and `main` for release-ready code.
Released versions are marked with git tags, not long-lived version branches.

## Branches

- `wip`: active development and automatic pushes.
- `main`: stable branch used for public releases.
- `vX.Y.Z` tags: immutable release points.

Create release branches only if a maintained older line needs fixes while
`main` has moved ahead. That is not expected for `0.1.x`.

## v0.1.0 Checklist

Run these from `wip` first:

```bash
python -m unittest discover -s tests
python -m compileall -q src tests
python -m pip wheel . -w /tmp/cloud-mount-manager-release --no-deps --no-build-isolation
```

Confirm:

- `README.md` describes the user flow.
- `CHANGELOG.md` has a `0.1.0` section.
- `SECURITY.md` has an active security reporting path or GitHub private vulnerability reporting is enabled.
- Built distributions do not include `secrets/`, `rclone.conf`, or `client_secret*.json`.
- The package exposes only the `cloud-mount-manager` console command.

## Merge To Main

```bash
git checkout main
git pull origin main
git merge --no-ff wip
python -m unittest discover -s tests
python -m compileall -q src tests
```

Build from `main`:

```bash
python -m build
python -m twine check dist/*
```

## Tag

After the final release commit is on `main`:

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin main --tags
```

## Publish

Publish from the tagged `main` commit, not from `wip`.

Recommended first public upload:

```bash
python -m twine upload dist/*
```

For a trial run before production PyPI:

```bash
python -m twine upload --repository testpypi dist/*
```
