# Branch policy

- Always perform development and edits on `wip`.
- `main` is reserved for stable releases. Do not work directly on `main`.
- Keep app CI and website integration checks in independent workflows. App
  packaging must not depend on website integration tests.
