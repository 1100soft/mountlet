import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {promisify} from "node:util";
import {releasePolicy, resolveReleasePolicy} from "./release-policy.mjs";

const preview = {channel: "preview", upload: true, publishApt: true};
const stable = {channel: "production", upload: true, publishApt: true};
assert.deepEqual(releasePolicy({event: "push", ref: "refs/tags/wip/2026-11-09", onWip: true}), preview);
assert.deepEqual(releasePolicy({event: "push", ref: "refs/tags/v0.7.6-preview.1", onWip: true}), preview);
assert.deepEqual(releasePolicy({event: "push", ref: "refs/tags/v0.7.6", onWip: true}), preview);
assert.deepEqual(releasePolicy({event: "push", ref: "refs/tags/v0.7.6", onMain: true, onWip: true}), stable);
assert.deepEqual(releasePolicy({event: "push", ref: "refs/heads/wip"}), {channel: "preview", upload: true, publishApt: false});
assert.deepEqual(releasePolicy({event: "workflow_dispatch", ref: "refs/heads/wip", manualPreview: true}), preview);
for (const input of [
  {event: "push", ref: "refs/heads/main"},
  {event: "push", ref: "refs/tags/cerberus/source/v0.7.6", onMain: true, onWip: true},
  {event: "push", ref: "refs/tags/unrelated"},
  {event: "pull_request", ref: "refs/pull/42/merge", onWip: true, manualPreview: true},
]) {
  const policy = releasePolicy(input);
  assert.equal(policy.upload, false);
  assert.equal(policy.publishApt, false);
}

// Exercise real ancestry and annotated-tag peeling without touching the worktree.
const directory = mkdtempSync(join(tmpdir(), "mountlet-release-policy-"));
const exec = promisify(execFile);
const git = async (...args) => (await exec("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], {cwd: directory})).stdout.trim();
try {
  await git("init");
  await git("commit", "--allow-empty", "-m", "Stable fixture");
  const main = await git("rev-parse", "HEAD");
  await git("update-ref", "refs/remotes/origin/main", main);
  await git("commit", "--allow-empty", "-m", "Wip fixture");
  await git("update-ref", "refs/remotes/origin/wip", "HEAD");
  await git("tag", "-a", "wip/test", "-m", "Preview fixture");
  await git("checkout", "--detach", "wip/test");
  const run = ref => resolveReleasePolicy({GITHUB_REF: ref, GITHUB_EVENT_NAME: "push"}, directory);
  assert.deepEqual(await run("refs/tags/wip/test"), preview);
  await git("checkout", "--detach", main);
  assert.deepEqual(await run("refs/tags/v0.7.6"), stable);
  await assert.rejects(run("refs/tags/provenance"), /neither an unreleased wip commit/);
  await git("update-ref", "-d", "refs/remotes/origin/wip");
  await assert.rejects(run("refs/tags/v0.7.6"), /Command failed/);
} finally {
  rmSync(directory, {recursive: true, force: true});
}
console.log("Release routing and tag ancestry checks passed.");
