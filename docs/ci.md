# Release and validation workflows

Mountlet owns release classification, tag conventions, version/package identities,
OAuth forwarding, installer manifests and platform-specific probes.
`1100soft/CI` owns the common build/check/verification/artifact pipeline and APT
transport/confirmation. `package-platform.yml` is Mountlet's single app-specific
adapter to that pipeline; Linux and other platforms invoke it with disjoint
partitions of `.github/scripts/package-matrix.json`.

## Gates and sequence

1. `release-policy` tests and resolves the context once. Branch/PR/manual branch
   runs only validate. `cerberus/source/*` never publishes (event filter plus
   defensive classifier exclusion). Releases require push events, matching event
   SHA/tag/checkout, tag core equal to the app version, and complete branch refs.
   New previews must advance the live APT version floor for both package variants.
   That approval is checked once: a later preview finishing first does not
   invalidate an earlier approved release or downgrade clients.
   Ordinary pushes run App CI plus release-policy/package-identity validation,
   not a second eight-way packaging matrix beside the tag run. PRs and manual
   branch runs may validate native installers but never publish them.
2. `vX.Y.Z` on a main ancestor is stable. `vX.Y.Z-preview.N` on a wip ancestor
   not reachable from main is preview. Wrong branch/format/version fails; no
   inferred arbitrary tag channels or run-number-derived preview versions.
3. Linux and Windows/macOS run independently through the same pinned workflow
   with fail-fast disabled and bounded checks/builds/verification. Tagged runs
   cannot auto-cancel each other. Branch validation cannot publish or cancel a
   release because it has a different ref/concurrency key.
4. APT depends **only** on classification and the two Linux successes.
   Mountlet prepares the channel-selected package pair once. Shared preflight
   rejects altered immutable identities, and the shared workflow transports the
   verified bytes unchanged. InRelease/Packages
   checksum consistency, public index records and
   actual download hashes must confirm publication before the job succeeds.
5. The complete installer index publishes only after all eight platform variants
   pass. Version-qualified objects use atomic create-only PUTs and hash conflict
   checks. An index is never advanced to conflicting bytes.
6. A final tagged-release result fails for any failed/skipped/timed-out required
   job, making genuine failures visible to Cerberus's failure/timed_out/
   startup_failure CI-fix triggers. Do not mask verification with continue-on-error
   or force-cancel hung checks. APT can succeed while the full release fails.

APT and per-channel R2 publishers use `queue: max`, `cancel-in-progress: false`.
GitHub supports up to 100 pending jobs; inspect a queue-limit cancellation rather
than retrying writes blindly. Do not collapse distinct releases into GitHub's
default one-pending-slot deployment group.

## v0.7.6 investigation

[Run 42574504412](https://github.com/1100soft/mountlet/actions/runs/42574504412)
finished Linux near 06:43 UTC on 2026-11-09 but waited on macOS before APT.
The macOS installed-DMG hooks stalled after attaching the image; the old scripts
polled readiness for 60 seconds but then waited for app exit without a deadline
and did not retain app output. The logs alone cannot prove the app's internal
reason for hanging. macOS now bounds attach/version/readiness/exit/detach, captures
a process sample and app logs before terminating the isolated app process group,
and returns failure. Its remaining real GUI behavior must be checked on macOS.

## Tests and rollout

The reviewed shared revision is `7ac17c500e9b30cdcddeefcd5232ff937c76946d`.
It already exists as a CI autosave checkpoint and its files were compared to the
tested local CI implementation before pinning. No new CI commit or push was
needed to establish this pin. All Mountlet shared-workflow references and APT
helper checkouts use this same immutable SHA.

```bash
node .github/scripts/test-release-policy.mjs
node .github/scripts/test-release-pipeline.mjs
python3 app/scripts/test-package-smoke.py
npm run web:release:test
```

Run CI's `python3 -m unittest discover -s tests -v` and lint both repositories.
Actionlint 1.7.12 does not yet recognize GitHub's documented `queue: max`; when
using that version ignore only `unexpected key "queue" for "concurrency" section`
and verify queue/max/non-cancellation contracts separately. No other lint error
is suppressed.

For future shared changes, review and commit CI first, then pin the caller
workflow and `shared-ci-ref` to the **same new immutable SHA**. Push CI only with
authorization and confirm that SHA is available remotely before publishing
Mountlet's caller update. The reviewed revision above is already available.
Do not roll out a caller pointing to older CI code that lacks the confirmation
helper/inputs. A local test is not a hosted release or a macOS GUI verification.
Keep website integration independent of app packaging.

The tracked `.github/scripts/package-matrix.json` preserves the eight installer
configurations formerly embedded in the package workflow. Both release policy
partitioning and the independent website release-file check read this file;
matrix changes also trigger Website integration.
