# Branch policy

- Always perform development and edits on `wip`.
- `main` is reserved for stable releases. Do not work directly on `main`.
- Keep app CI and website integration checks in independent workflows. App
  packaging must not depend on website integration tests.

# Wip checkpoints

- Review the complete working tree and account for every modified, deleted,
  and untracked file. Investigate unfamiliar changes and check for unfinished
  edits, secrets, generated artifacts, and accidental changes.
- Complete the current scope and update affected documentation before staging.
- Run relevant formatting, lint, type, test, and build checks. Fix failures
  caused by the work and report checks that could not be run.
- Review final status and the staged diff, then create a concise checkpoint
  commit on `wip`. Do not push unless explicitly requested.
- Report the commit hash and message, validation results, and any deliberately
  uncommitted files with their reasons.
