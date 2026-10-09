# Shared CI migration handoff

Date: 2026-10-09
Audience: Mountlet's dedicated agent

## State

The migration is committed on `wip` as `927f1ac` —
`Use shared 1100soft CI with Mountlet packaging hooks`.
The shared repository is `/home/eh930/project/apps/CI`, with remote
`https://github.com/1100soft/CI.git`. All callers pin shared commit
`859fc753347512c1e64d50f487251d41b0778925` —
`Add reusable Tauri, Node, and APT workflows`.
Neither checkpoint was pushed during this work. The shared commit must be
accessible on GitHub before these caller workflows can run.

## Changes in Mountlet

- `.github/workflows/ci.yml` calls shared `tauri-check.yml`, using `app`,
  Ubuntu 24.04, Clippy, existing native tests, and the executable/version probe.
- `.github/workflows/package.yml` calls shared `tauri-package.yml`. Its eight
  platform/variant entries, installer filenames, artifact names, and 14-day
  installer retention remain intact. Nonsecret JSON configuration forwards
  `MOUNTLET_BUILD_CHANNEL`; explicitly mapped OAuth credentials travel in the
  `build-environment` secret and reach only the installer build subprocess.
- `.github/actions/tauri-prepare/action.yml` owns versioned rclone staging.
- `.github/actions/tauri-verify/action.yml` owns executable and rclone checks,
  stable installer collection, installed-app probes on Linux/Windows/macOS,
  MSIX creation, Store identity checks, Windows App Certification Kit checks,
  MSIX smoke testing, and the separate Store submission artifact.
  Hooks receive the complete matrix entry as JSON in `inputs.matrix`.
- The APT job in `package.yml` calls shared `apt-publish.yml`. Mountlet still
  owns Debian identity preparation and manual preview opt-in. The shared job
  downloads tested artifacts, uploads `apt-packages`, and dispatches the existing
  APT payload to `1100soft/1100`, using explicit `APT_DISPATCH_TOKEN` forwarding.
  Its caller grants `actions: read` for artifact downloads.
- `.github/workflows/publish-apt-backfill.yml` now validates the old source run
  in a separate job before calling shared APT transport. It still publishes only
  the missing lean package identity.
- `.github/workflows/web.yml` calls shared `node-check.yml` with the existing
  website/release checks. Website checks remain independent from app packaging.
- `web/tools/check-release-files.mjs` recognizes installer names in the new JSON
  matrix instead of the previous inline YAML matrix.
- `docs/ci.md` documents ownership and rollout; the root README links to it.

Mountlet retains event/path filters, concurrency, preview/stable release policy,
R2 upload tooling and manifest, bucket credentials, and Debian transformations.
Production R2 uploads remain tag-only; `wip` uploads previews. Hook changes are
included in package workflow path filters. No application implementation files,
remote settings, credentials, or published releases were changed.

## Validation completed

- Actionlint 1.7.12 passed for shared and both consumer repositories' workflows.
- All eight matrix entries and extracted verification steps were compared with
  the original package workflow; hook Bash syntax passed.
- Eight cross-repository callers passed input/secret contract checks; package
  artifact names were checked for uniqueness across each matrix.
- Shared CI's four offline contract tests passed: bundle outputs, credential
  scoping/JSON handling, nonsecret environment injection rejection, and artifact
  downloads/failure propagation.
- Mountlet `web:release:check`, `web:release:test`, `web:notices:test`, and
  `web:reports:test` passed, as did JavaScript syntax checks and diff checks.

Actual hosted native builds, installed-app probes, Store certification, R2 uploads,
and APT dispatch were not run. Full app compilation was not part of local
validation for this workflow-only migration.

## Next steps for the dedicated agent

1. Review the checkpoint and the shared repository's README/input contracts.
2. Ensure the shared checkpoint is pushed before publishing the Mountlet callers.
   Obtain push authorization through the normal workflow; none was given here.
3. If CI is private, configure its Actions access for Mountlet and confirm the
   caller's Actions policy allows the shared workflows. No remote configuration
   was performed by this migration.
4. Check branch protection/rulesets for changed nested reusable job names.
5. Run branch checks and manual packages on GitHub; verify all eight artifact
   names, runtime probes, Store tooling, and website download compatibility.
6. Exercise release/preview/APT gates under existing Mountlet policy. A successful
   APT dispatch only requests publication; it does not confirm index updates.
7. Keep app policy/hooks here and update shared pins explicitly for future changes.

Do not replace the shared pin with an unreviewed moving branch. This handoff
requires no application feature changes or automatic publication.
