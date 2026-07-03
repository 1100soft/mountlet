# Mountlet

Mountlet is a desktop app for managing many cloud storage accounts from
different providers in one place.

This repository is organized as a small monorepo:

- `app/`: the Python desktop app, packaging scripts, tests, and app
  documentation.
- `web/`: the public website for commercial downloads, hosted on Cloudflare
  Pages.

Start with:

- [App README](app/README.md) for installation, use, provider support, and file
  locations.
- [Release notes](app/CHANGELOG.md) for version history.
- [Developer notes](app/docs/README.md) for app architecture and release
  workflow.
- [Website README](web/README.md) for Cloudflare Pages and Stripe setup.

The source is available for non-commercial use under `LICENSE`. Installer builds
are covered by `app/docs/EULA.md`.
