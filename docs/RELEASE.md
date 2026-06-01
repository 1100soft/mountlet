# Release Process

This project uses `wip` for active work and `main` for release-ready code.
Released versions are marked with git tags, not long-lived version branches.

## Branches

- `wip`: active development and automatic pushes.
- `main`: stable branch used for public releases.
- `vX.Y.Z` tags: immutable release points.

Create release branches only if a maintained older line needs fixes while
`main` has moved ahead. That is not expected for `0.1.x`.

## Release Checklist

Run these from `wip` first:

```bash
VERSION=0.2.0
python -m unittest discover -s tests
python -m compileall -q src tests
python -m pip wheel . -w /tmp/mountlet-release --no-deps --no-build-isolation
```

Confirm:

- `README.md` describes the user flow.
- `CHANGELOG.md` has a section for the version being released.
- `pyproject.toml` and `src/mountlet/__init__.py` have the same version.
- `SECURITY.md` has an active security reporting path or GitHub private vulnerability reporting is enabled.
- Built distributions do not include `secrets/`, `rclone.conf`, or `client_secret*.json`.
- The package exposes only the `mountlet` console command.

## Merge To Main

```bash
git checkout main
git pull origin main
git merge --squash wip
git commit -m "Release v$VERSION"
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
git tag -a "v$VERSION" -m "Release v$VERSION"
git push origin main --tags
```

## Publish

Publish from the tagged `main` commit, not from `wip`.

Publishing is handled by GitHub Actions through PyPI trusted publishing.
The PyPI publisher must match:

- Repository: `eric-holt/mountlet`
- Workflow: `python-publish.yml`
- Environment: `pypi`

Pushing a release tag starts the publish workflow:

```bash
git push origin main --tags
```

Manual PyPI uploads are only a fallback:

```bash
python -m twine upload dist/*
```
