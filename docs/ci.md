# Shared CI workflows

Desktop checks, native packaging, website checks, and APT transport call the
reviewed commit of `1100soft/CI` pinned in `.github/workflows/`. The shared
repository documents input contracts and rollout requirements in its README.
Publish that shared commit before pushing these callers.

Mountlet keeps event/path filters, concurrency, preview/stable policy, OAuth
credential mapping, R2 uploads and release manifests, and Debian identity
preparation. `.github/actions/tauri-prepare` stages rclone;
`.github/actions/tauri-verify` checks variants, collects stable installer names,
creates/certifies MSIX packages, and smoke-tests installed apps on each platform.
The eight-entry package matrix and existing artifact names remain the contract
used by website downloads and APT backfills. Hook changes trigger package CI.
The shared pre-build version check rejects tags that differ from the app version.
The MSIX hook bounds its startup probe to five minutes explicitly because
composite action steps cannot specify `timeout-minutes`.

OAuth credentials are explicitly forwarded as JSON in the shared workflow's
`build-environment` secret and reach only the installer build. The build channel
is passed separately as nonsecret environment configuration. APT jobs alone add
`actions: read` to download tested artifacts. Backfills validate their source run
before invoking shared APT transport; the maintenance workflow still publishes
only the missing lean package identity.

Website checks remain independent from app packaging. Production R2 publication
remains tag-only, with previews from `wip`; APT previews require manual opt-in.
Keep these policies in Mountlet when updating shared workflow pins. Before
rolling out a new pin, lint both repositories' workflows and run actual GitHub
CI/package jobs, including installed-app probes and Windows Store checks.

Local review on October 9, 2026 passed workflow linting and shared offline
contracts. GitHub returned HTTP 404 for the pinned shared commit using the
current credentials. Before rollout, confirm that the exact SHA exists remotely
and that Mountlet has access if CI is private. A 404 alone cannot distinguish an
unpublished commit from insufficient repository access. No push or hosted build
was performed during this review.
