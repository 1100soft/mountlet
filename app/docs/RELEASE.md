# Production release process

Version 0.7.0 is the first Tauri release. Version 0.6.8 is the final Python
release and remains frozen under `legacy/python-0.6.8/`.

## Release invariants

- Release from a clean commit that passed both **App CI** and all eight jobs in
  **Native package CI**.
- **Website integration** independently checks web APIs and release-file
  compatibility. Native packaging and installer uploads do not depend on its
  integration tests. Website changes must pass that workflow before deployment.
- Keep the versions in `app/package.json`, `app/src-tauri/Cargo.toml`, and
  `app/src-tauri/tauri.conf.json` identical to the release tag's `X.Y.Z` core.
  Preview tags add `-preview.N` without changing the app's version. Update the
  corresponding npm and Cargo lockfile entries together.
- Branch pushes and manual branch runs validate packages without publishing.
  Branch pushes use App CI and policy/identity checks rather than duplicating a
  tagged eight-way package build; use a manual branch run for pre-tag native tests.
  Production installers are uploaded only by a `vX.Y.Z` tag on `main`;
  preview installers only by `vX.Y.Z-preview.N` on unreleased `wip`.
  Do not publish production installers by manually reusing preview artifacts.
- A `vX.Y.Z` tag on `main` publishes stable APT packages (`mountlet` and
  `mountlet-lean`). A `vX.Y.Z-preview.N` tag on an unreleased `wip` commit
  publishes preview APT packages (`mountlet-preview` and `mountlet-lean-preview`)
  and uses preview R2, never production storage.
- Tags do not record their originating branch. CI resolves the tagged commit's
  ancestry: previews must be reachable from `wip` but not `main`; stable tags
  must be reachable from `main`. A stable tag on wip alone, a preview tag on main,
  a mismatched app version, or a changed/mischecked-out tag fails before building.
  `N` is a positive decimal integer without leading zeroes. Other tag formats
  are not releases. `cerberus/source/*` tags never publish, even on unreleased wip.
- Packaged applications use `https://mountlet.app` for license and report APIs
  unless `MOUNTLET_LICENSE_API_URL` or `MOUNTLET_REPORT_API_URL` is set in the
  process environment. The build-channel marker does not change these URLs.
- Bug and crash reports are successful only after GitHub creates an issue.
  Email is an optional secondary sink, not a substitute.
- Never delete or reset user configuration, trial replicas, rclone data,
  offline files, or metadata during an upgrade. On Windows the NSIS installer
  removes the Python/Inno application but preserves those user directories.

## 1. Freeze and validate the candidate

1. Update the version, lockfiles, changelog, and affected release/upgrade notes.
   For `v0.7.7`, verify the unified stable/preview pipeline, independent Linux
   publication, immutable artifacts, and bounded macOS package checks. Retain
   the beta renewal and Google account upgrade checks below.
   This release adds no application data migration; preserve existing user
   data without a manual configuration reset.
   Run the project version check with the exact requested tag, then validate:

   ```bash
   RELEASE_TAG=v0.7.7 node app/scripts/check-version.mjs
   ```

   Run the remaining checks from the repository root:

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
   filenames. Do not identify a preview solely by the displayed application
   version.

## 2. Exercise preview installers

Test the standard installer on each supported OS and at least install/start the
lean variant. The workflow smoke probe is necessary but does not replace these
upgrade tests:

- Linux X11: install the `.deb`, start from a desktop session, open both window
  modes, browse/download a file, mount/unmount, quit, and reopen from the tray.
  Put a different normal window in front, hide/reopen Mountlet repeatedly, and
  confirm both panes return to the foreground with the last keyboard focus.
  Also test with pinned windows; reopening must not enable Mountlet's pin mode.
- In both window modes, select remote A, hover B, then leave the remote list:
  A stays selected while B is previewed, and A's remembered folder returns on
  leave. Clicking B makes it active. Rapidly hover B/C and leave before loading
  finishes; late responses must not replace the active remote's files. Verify
  keyboard selection and remote shortcuts still target the active remote.
  Confirm a dashed outline identifies B while A retains its
  selection highlight. In separate-browser mode, the browser must move beside B
  during preview, then back beside A on leave; clicking B removes its preview
  indicator and makes it the selected row.
  The production startup smoke probe covers row events and preview cancellation
  in both layouts. On an isolated X11 display with a window manager, also run
  `cargo test --locked --manifest-path app/src-tauri/Cargo.toml linux_tray_activation_refreshes_stale_user_time -- --ignored --test-threads=1`
  from the repository root to check stale-timestamp activation against another
  window without enabling always-on-top.
- Windows: install over Python 0.6.8 with an existing expired trial, cached
  file, metadata database, non-default theme, and multi-window setting. Confirm
  that there is one Installed Apps entry, no default desktop shortcut, no
  console windows, the old trial remains expired, and License/Report/Quit work.
- Windows: verify file download and cache/conflict indicators, mounted-folder
  opening, Explorer access, usage loading, initial metadata indexing, and
  restoration of the last focused window.
- macOS arm64 and x64: launch the DMG application, verify tray-only lifecycle,
  both layouts, Finder opening, download/cache behavior, and quit.
  The automated startup probe shows the main window before running its UI
  checks, matching normal startup and avoiding hidden WebKit suspension. It
  still requires the full behavior marker and a visible main window.
- Standard builds must find their bundled rclone. Lean builds must report a
  missing system rclone cleanly and work after one is provided.
- For each OAuth provider, confirm creation defaults to Mountlet only when its
  client ID was included in the build. Verify separate rclone and Custom choices
  in creation and settings, per-service Google-account reuse, masked custom secrets, and source
  persistence. Changing source must clear the previous token and reauthenticate;
  unrelated edits must preserve the saved credentials. Never log client secrets.
- With an expired trial or deactivated license, open License and paste a key
  using its Paste button. Confirm the key is trimmed, Copy and Activate become
  enabled, and keyboard paste/device-name editing/Close remain usable. A failed
  clipboard read must show an error and allow retry without erasing the key.
  An activated license remains read-only until this device is deactivated.
- Activate, quit, and restart: the saved key and valid activation must remain.
  Delay beta renewal: only a neutral checking indicator should appear until the
  response, with no premature expired-license dialog or purchase prompt. Test
  failure/offline startup and a late dialog status response; retain the saved
  key, stay locked on failed renewal, and never overwrite a newer activation.
  Repeat with a token requiring a cached signing key. An expired daily beta
  token must renew in the background; offline renewal failure must retain the
  key and remain locked, not reset the trial or erase activation state.
- Settings must use the creation provider form with saved values, no Open
  actions, and icon-only Save/Delete/Cancel. Rename each provider and confirm
  unchanged authentication and mount fields are preserved. The startup probe
  exercises all 12 provider forms using in-memory save callbacks only.
- Set up either Google service and confirm both are provisioned under one card
  with separate authorization. Cancel one sign-in and confirm the other still
  works. At launch, link older Drive/Photos remotes by their saved Google account
  emails without a confirmation dialog; provision a missing service immediately,
  without opening OAuth. Repeat launch and confirm no duplicate companions or
  config rewrites. Unknown accounts must not merge by card name. Preserve IDs,
  credentials, cached trees, and locations. Test both web icons, the unified
  account settings form, shared client categories, and advanced service overrides,
  keeping ordinary fields visible. Custom must list only cards with custom
  registrations and direct missing Drive/Photos clients to Advanced options.
  Verify one shared auto-mount control saves to both services, service groups
  use Drive/Photos icons, and long dropdowns near the bottom open upward with
  every option reachable by scrolling and keyboard.
  Verify partially configured custom registrations without requiring OAuth first
  and repeated browser service switches in both window modes. Grouped-card
  assertions must await the microtask that populates the newly attached remote
  list after rendering. The hover startup
  probe also checks grouped rows and independent service locations. Photos API
  limitations remain visible; no shared-storage explanatory badge is needed.
- First-time OAuth with Mountlet/rclone/reused registrations must not prompt for
  client IDs/secrets. Confirm the rclone shared-client warning advances using the
  selected default, saved credentials stay unchanged, browser mode opens OAuth,
  and paste mode stays in paste mode. Missing registrations show a settings error.
  Verify the shared category and Photos-only override survive reopening settings,
  and that renaming preserves both services' fields, locations, and credentials.
  With rclone installed, the isolated no-browser fixture can be run with
  `cargo test --locked --manifest-path app/src-tauri/Cargo.toml installed_rclone_oauth_skips_client_questions -- --ignored`.

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

The native package workflow forwards these repository variables through the shared
workflow's `environment-json` input. The verification composite action reads the
resulting environment values because the `vars` context is unavailable there.

A tagged build deliberately fails its bundled Windows job if any identity value
is absent. Mountlet `0.7.7` maps to Store package version `1.7.7.0`: the major
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

1. Prepare the exact requested version on a `correction/release-*` branch based
   on the captured `wip` source. Record the verified release plan, update version
   metadata and release documentation together, and commit for Review.
2. After Review, Integrate the preparation into `wip` and Publish that reviewed
   development commit to its configured upstream. Reverify the original source
   ancestry, previous `main` tip, remote URL, version tag, and provenance tag.
   A source or target race requires renewed verification and review.
3. Only the authorized final Release publication stage may append one release
   commit to the previous `main` tip using the exact reviewed prepared tree and
   atomically publish the new immutable `vX.Y.Z` and
   `cerberus/source/vX.Y.Z` tags. The provenance tag identifies the reviewed
   prepared source commit. Never move existing tags or rerun a manual Release
   request to bypass preparation. Release publication does not push `wip`.
4. Wait for Native package CI on `main`: all eight package jobs must pass. The
   main-branch run deliberately does not upload production installers.
5. Wait for the production Pages deployment, then initialize/upgrade D1 and
   run the deployment check described in `web/README.md`. Confirm `/api/health`
   reports healthy licensing, GitHub reporting, D1, and R2 bindings. Exercise a
   live-mode-safe license status check and a controlled report test.

6. Wait for the tagged Native package CI run. Its **Upload installers to R2**
   job validates `web/release-files.json`, writes versioned production objects,
   updates the release index, and retains the configured five versions. Also
   wait for **Publish Linux package to APT** in the tagged Native
   package CI run, followed by **Publish APT repository** in `1100soft/1100`.
   Only the tested Linux artifacts are required for APT; Windows/macOS failures
   do not block it. Publication starts immediately after both verified Linux
   builds finish, independently of Windows/macOS. A failed Linux verification
   cannot publish. The job remains pending until the public APT index and package
   downloads contain the exact expected SHA256 hashes and InRelease advertises
   the same Packages checksum (ten-minute confirmation
   bound). Dispatch acceptance is not success. Any failure makes the tagged run
   fail for Cerberus CI-fix automation, not merely appear cancelled.
   The source workflow validates and uploads one `apt-packages` artifact
   containing both `mountlet` and `mountlet-lean`, while the central workflow
   signs and publishes them. The lean package declares `rclone` as an APT
   dependency.

## Preview APT package

For an unreleased `wip` change with application version `0.7.7`, an example
preview tag is `v0.7.7-preview.1`. Verify that the tag is unused and that its
sequence exceeds any published preview for this core version before tagging:

```bash
git tag -a v0.7.7-preview.1 origin/wip -m "Mountlet 0.7.7 preview 1"
git push origin refs/tags/v0.7.7-preview.1
```

Lightweight and annotated release tags use the same policy and pipeline. The
workflow must already exist in the tagged commit. Manual runs never create a
release. Both tested Linux variants must pass; other platforms need not finish
before APT publication. Installer publication waits for all eight variants.

The public index inspected on 2026-11-09 contains stable `0.7.6` and preview
`0.7.6~preview.469+ge3ff340` for both package variants. The ordered sequence is:

```
0.7.6~preview.469+ge3ff340 < 0.7.6~preview.470 < 0.7.6~preview.471
                       < 0.7.7~preview.1 < 0.7.7
```

`v0.7.7-preview.1` requires app version `0.7.7`. Stable `v0.7.7` publication
must follow section 4: Review, Integrate into `wip`, Publish the reviewed
development commit, then let the authorized Release stage append the release
commit and atomically publish both tags. Do not manually create the stable tag.

Do not recreate or move `v0.7.6`, or any published tag. Preview tags map directly
to Debian `X.Y.Z~preview.N`, stable tags to `X.Y.Z`; run numbers and attempts never
change identity. Classification checks new preview ordering against the live
index once, before builds. Already accepted releases can finish/publish out of
order without losing an earlier release; Debian version ordering keeps the
newest candidate selected. Preflight rejects reused versions with different bytes.
Re-running an identical artifact is
idempotent; a rebuild with different bytes requires a new tag. If a downstream
publication failed, **Resume tested APT publication** validates the original run
and downloads its retained `apt-packages` artifact unchanged. It does not rebuild
or repack. If the artifact expired, do not rebuild the same version; make a new
reviewed release instead.

Tagged runs never auto-cancel each other. APT and R2 publishers serialize using
`queue: max` (100 pending-job limit); they do not replace pending releases as a
single-slot concurrency queue would. Reaching that GitHub limit is an operational
blocker requiring inspection before retrying, not permission to overwrite bytes.

macOS installed-DMG checks bound attach, version, readiness, process exit, and
detach. Failure retains `artifacts/diagnostics` (marker, log, process sample and
process list). Shared check/build/hook timeouts fail steps before the outer job
deadline; the final release-result job propagates failure for CI-fix automation.
Actual macOS GUI verification still requires hosted macOS runners.

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
