import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {releasePolicy} from "./release-policy.mjs";

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
const script = fileURLToPath(new URL("./release-policy.mjs", import.meta.url));
const git = (...args) => execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], {cwd: directory, encoding: "utf8", stdio: "pipe"}).trim();
try {
  git("init");
  git("commit", "--allow-empty", "-m", "Stable fixture");
  const main = git("rev-parse", "HEAD");
  git("update-ref", "refs/remotes/origin/main", main);
  git("commit", "--allow-empty", "-m", "Wip fixture");
  git("update-ref", "refs/remotes/origin/wip", "HEAD");
  git("tag", "-a", "wip/test", "-m", "Preview fixture");
  git("checkout", "--detach", "wip/test");
  const run = ref => execFileSync(process.execPath, [script], {cwd: directory, encoding: "utf8", env: {...process.env, GITHUB_OUTPUT: "", GITHUB_REF: ref, GITHUB_EVENT_NAME: "push", PUBLISH_APT_PREVIEW: "false"}});
  assert.match(run("refs/tags/wip/test"), /channel=preview\nupload=true\npublish-apt=true/);
  git("checkout", "--detach", main);
  assert.match(run("refs/tags/v0.7.6"), /channel=production\nupload=true\npublish-apt=true/);
} finally {
  rmSync(directory, {recursive: true, force: true});
}
console.log("Release routing and tag ancestry checks passed.");
