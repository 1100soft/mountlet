# Mountlet

Mountlet is a Tauri desktop application for managing, browsing, mounting, and
synchronizing cloud storage accounts through rclone.

## Repository layout

- `app/` — the current Rust/Tauri desktop application. Version 0.7.0 is the
  first release from this implementation.
- `legacy/python-0.6.8/` — the frozen Python/Qt implementation and its original
  packaging tools. Version 0.6.8 is the final Python release.
- `web/` — the public website, licensing API, notices, and release-download
  tooling hosted on Cloudflare.

The Tauri application reads the existing Mountlet settings, rclone
configuration, offline manifest, metadata index, shortcut definitions, and
encrypted config bundles. Upgrading does not require migrating user data.

## Desktop development

Install Node.js 22, the stable Rust toolchain, and the
[Tauri 2 system prerequisites](https://v2.tauri.app/start/prerequisites/), then:

```bash
cd app
npm ci
npm run tauri:dev
```

Mountlet opens its tray-adjacent window at startup and remains available from
the system tray after it is hidden. Use `npm run build` for the frontend
regression gate and `npm run tauri:build` to create the native installer for
the current platform.

See [the desktop README](app/README.md), [development invariants](app/DEVELOPMENT.md),
and [release notes](app/CHANGELOG.md).

## Validation

```bash
cd app
npm run build
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

Website checks remain available from the repository root with
`npm run web:release:test`, `npm run web:notices:test`, and
`npm run web:reports:test`.

Before publishing, follow the complete [production release checklist](app/docs/RELEASE.md).
Preview and production installers use different R2 buckets: `wip` publishes
preview artifacts, and a push to `main` or a version tag publishes production
native artifacts.

The source is available for non-commercial use under [LICENSE](LICENSE).
Installer builds are covered by [the installer EULA](app/docs/EULA.md).
