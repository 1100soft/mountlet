# Production release process

Version 0.7.0 is the first Tauri release. Version 0.6.8 is the final Python
release and remains frozen under `legacy/python-0.6.8/`.

## Release invariants

- Release from a clean commit that passed both **CI** and all eight jobs in
  **Native package CI**.
- Keep the versions in `app/package.json`, `app/src-tauri/Cargo.toml`, and
  `app/src-tauri/tauri.conf.json` identical to the release tag.
- A `wip` build is a preview build and uploads to preview R2. Production
  installers are uploaded to production R2 only by a `vX.Y.Z` tag. Pushes and
  manual workflow runs on `main` validate packages but must never publish them.
  Do not publish production installers by manually reusing preview artifacts.
- Only a `vX.Y.Z` tag publishes to the 1100 Software APT repository. APT carries
  the standard Linux package with bundled rclone; the lean package remains a
  direct download because both variants have the same Debian package identity.
- A manually dispatched Native package CI run on `wip` may publish the bundled
  build as `mountlet-preview`. Preview publication is opt-in and must never be
  enabled for an automatic branch or pull-request run.
- Packaged applications use `https://mountlet.app` for license and report APIs
  unless `MOUNTLET_LICENSE_API_URL` or `MOUNTLET_REPORT_API_URL` is set in the
  process environment. The build-channel marker does not change these URLs.
- Bug and crash reports are successful only after GitHub creates an issue.
  Email is an optional secondary sink, not a substitute.
- Never delete or reset user configuration, trial replicas, rclone data,
  offline files, or metadata during an upgrade. On Windows the NSIS installer
  removes the Python/Inno application but preserves those user directories.

## 1. Freeze and validate the candidate

1. Update the version and changelog, then run from the repository root:

   ```bash
   cd app
   npm ci
   npm run build
   cargo test --locked -j 2 --manifest-path src-tauri/Cargo.toml
   cargo clippy --locked -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
   cd ..
   npm ci
   npm run web:release:check
   npm run web:release:test
   npm run web:notices:test
   npm run web:reports:test
   ```

2. Push the candidate to `wip`. Wait for both workflows to pass; do not accept
   a successful aggregate job while one platform/variant job is cancelled or
   skipped unexpectedly.
3. Download installers by artifact name or use the uniquely versioned preview
   filenames. Do not identify a preview solely by the displayed `0.7.1`.

## 2. Exercise preview installers

Test the standard installer on each supported OS and at least install/start the
lean variant. The workflow smoke probe is necessary but does not replace these
upgrade tests:

- Linux X11: install the `.deb`, start from a desktop session, open both window
  modes, browse/download a file, mount/unmount, quit, and reopen from the tray.
- Windows: install over Python 0.6.8 with an existing expired trial, cached
  file, metadata database, non-default theme, and multi-window setting. Confirm
  that there is one Installed Apps entry, no default desktop shortcut, no
  console windows, the old trial remains expired, and License/Report/Quit work.
- Windows: verify file download and cache/conflict indicators, mounted-folder
  opening, Explorer access, usage loading, initial metadata indexing, and
  restoration of the last focused window.
- macOS arm64 and x64: launch the DMG application, verify tray-only lifecycle,
  both layouts, Finder opening, download/cache behavior, and quit.
- Standard builds must find their bundled rclone. Lean builds must report a
  missing system rclone cleanly and work after one is provided.

Submit one Windows report and verify its issue number in the configured private
GitHub repository. Delete or close the test issue according to support policy.

### Microsoft Store package

The bundled Windows x64 job also builds an unsigned MSIX with a temporary CI
identity and validates the archive with the Windows App Certification Kit. It
then development-registers the unpacked package layout, activates it through
its package application ID, and runs the same startup/behavior probe used for
NSIS. It uploads the package as the distinct
`microsoft-store-mountlet-x64` workflow artifact. Do not publish that artifact
on the website: ordinary sideload installation requires a trusted signature,
while Partner Center accepts the unsigned package and signs it after
certification.

Before the first Store submission, reserve **Mountlet** in Partner Center and
copy the three exact values from **Product identity** into GitHub repository
variables (values are case-sensitive):

- `MOUNTLET_STORE_IDENTITY_NAME` = Package/Identity/Name
- `MOUNTLET_STORE_PUBLISHER` = Package/Identity/Publisher
- `MOUNTLET_STORE_PUBLISHER_DISPLAY_NAME` = Package/Properties/PublisherDisplayName

A tagged build deliberately fails its bundled Windows job if any identity value
is absent. Mountlet `0.7.1` maps to Store package version `1.7.1.0`: the major
component is offset because Store package majors cannot be zero, and the fourth
component remains zero because Microsoft reserves it. Application-visible
versions remain normal SemVer.

Download `microsoft-store-mountlet-x64`, review its certification report, and
upload the `.msix` on the Partner Center submission's **Packages** page.
Complete pricing/availability, properties, age rating, listing assets, release
notes, certification notes, and gradual rollout there. After certification,
record the Store product ID and replace the website's primary Windows action
with `https://apps.microsoft.com/detail/<product-id>`; retain NSIS as the
explicit direct-download fallback.

For the first Store migration test, install over a machine with NSIS/Python
history and confirm the existing trial, license, configuration, offline cache,
and rclone configuration are reused. The full-trust `packagedClassicApp`
identity intentionally preserves normal access to those existing user-profile
locations. Uninstall the old NSIS application only after that verification;
removing the application must never remove user data.

## 3. Prepare production services

Before merging, verify the Cloudflare Pages production environment separately
from preview:

- Production branch is `main`; project root is the repository root; output is
  `web`; Functions are loaded from `functions/`.
- `DB` points to production D1 and `DOWNLOADS` points to production R2.
- Live Stripe keys and webhook secret are present.
- License signing keys, key pepper, and admin token are present and match the
  public key compiled into the desktop app.
- `REPORT_GITHUB_TOKEN` has Issues read/write access to
  `REPORT_GITHUB_REPO`; Resend settings are optional.
- Production and preview notice audiences and secrets are not interchanged.
- GitHub Actions has production/preview R2 bucket variables plus bucket-scoped
  R2 upload credentials.
- GitHub Actions has an `APT_DISPATCH_TOKEN` secret able to create a repository
  dispatch event in `1100soft/1100`. Do not store APT signing or repository
  storage credentials in this repository.

## 4. Promote and publish

1. Merge the tested `wip` commit to `main` without adding untested changes.
2. Wait for Native package CI on `main`: all eight package jobs must pass. The
   main-branch run deliberately does not upload production installers.
3. Wait for the production Pages deployment, then initialize/upgrade D1 and
   run the deployment check described in `web/README.md`.
4. Confirm `/api/health` reports healthy licensing, GitHub reporting, D1, and
   R2 bindings. Exercise a live-mode-safe license status check and a controlled
   report test.
5. Tag the exact tested `main` commit as an immutable release point:

   ```bash
   git tag -a vX.Y.Z -m "Mountlet X.Y.Z"
   git push origin vX.Y.Z
   ```

6. Wait for the tagged Native package CI run. Its **Upload installers to R2**
   job validates `web/release-files.json`, writes versioned production objects,
   updates the release index, and retains the configured five versions. Also
   wait for **Publish Linux package to APT** in the tagged Native
   package CI run, followed by **Publish APT repository** in `1100soft/1100`.
   The source workflow validates and uploads one `apt-packages` artifact
   containing both `mountlet` and `mountlet-lean`, while the central workflow
   signs and publishes them. The lean package declares `rclone` as an APT
   dependency.

## Preview APT package

For a deliberately selected preview, run **Native package CI** manually on the
`wip` branch and enable `publish_apt_preview`. After the complete native matrix
passes, the workflow rebuilds the bundled Linux package metadata as
`mountlet-preview`, gives it a unique version based on the workflow run number
and commit, and sends it to the central APT publisher.

`mountlet-preview` and `mountlet-lean-preview` are the preview counterparts of
`mountlet` and `mountlet-lean`. All four identities conflict because they
install the same application files. Configuration, license, metadata, and
offline storage remain shared, so switching packages must not reset user data:

```bash
sudo apt remove mountlet
sudo apt install mountlet-preview
```

APT upgrades an installed identity but never switches identities implicitly.
Use `mountlet-lean-preview` when testing the system-rclone build.

Do not publish every development commit. The APT pool is immutable and preview
packages are intentionally retained, so use this option only for builds that
need installation testing or deliberate public preview access.

Bundled builds stage an official current rclone under an app-versioned resource
directory. Lean builds intentionally contain no rclone executable. Windows
copies bundled rclone to a versioned LocalAppData runtime directory so upgrades
do not overwrite a binary still serving a mount.

## 5. Post-publication verification

- Run `npm run web:deploy:check -- https://mountlet.app`.
- Confirm `/api/releases` reports `vX.Y.Z` and download each of the eight
  logical artifacts through `/api/download/...`; do not validate R2 URLs only.
- Confirm `apt-cache policy mountlet` reports the new version from
  `https://apt.1100soft.com`, then install or upgrade it with APT and run the
  Linux acceptance checks above.
- Install the public Windows standard installer over 0.6.8 once more and verify
  retained trial/license/config/cache state.
- Confirm purchase, webhook fulfillment, license activation/deactivation,
  report issue creation, notices, and installer links in production.
- Record the workflow run, tag, installer checksums, report test issue, and any
  known limitations in the release record.

If any production check fails, stop linking the new release index. Fix from a
new commit and tag; do not replace installer bytes underneath an existing tag.
