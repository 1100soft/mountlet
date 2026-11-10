import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {mkdtempSync, rmSync, mkdirSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {promisify} from "node:util";
import {releasePolicy, resolveReleasePolicy, validatePreviewOrder} from "./release-policy.mjs";

const version = "0.7.7";
const input = {event: "push", version};
const stable = releasePolicy({...input, ref: "refs/tags/v0.7.7", onMain: true, onWip: true});
assert.equal(stable.channel, "production");
assert.equal(stable.debianVersion, version);
const preview = releasePolicy({...input, ref: "refs/tags/v0.7.7-preview.1", onWip: true});
assert.equal(preview.channel, "preview");
assert.equal(preview.debianVersion, "0.7.7~preview.1");
const legacy = ['mountlet-preview','mountlet-lean-preview'].map(Package=>({Package,Architecture:'amd64',Version:'0.7.6~preview.469+ge3ff340'}));
await assert.rejects(validatePreviewOrder(preview,[]), /Cannot establish preview ordering/);
await validatePreviewOrder(preview, legacy);
await assert.rejects(validatePreviewOrder({...preview,debianVersion:'0.7.6~preview.469'},legacy), /must be newer/);
const approved470 = {...preview,debianVersion:'0.7.6~preview.470'};
await validatePreviewOrder(approved470, legacy);
await validatePreviewOrder({...preview,debianVersion:'0.7.6~preview.471'}, legacy);
await assert.rejects(validatePreviewOrder(approved470,legacy.map(entry=>({...entry,Version:'0.7.6~preview.471'}))), /must be newer/);
for (const event of ["push", "pull_request", "workflow_dispatch"]) {
  for (const ref of ["refs/heads/wip", "refs/heads/main", "refs/pull/42/merge", "refs/tags/cerberus/source/v0.7.7"]) {
    const policy = releasePolicy({...input, event, ref, onWip: true, manualPreview: true});
    assert.equal(policy.upload, false);
    assert.equal(policy.publishApt, false);
  }
}
for (const tag of ["wip/preview", "v0.7.7-preview.0", "v0.7.7-preview.01", "v00.7.7", "v0.7.7-rc.1", "v0.7.7-preview.1+build", "v0.7.8"]) {
  assert.throws(() => releasePolicy({...input, ref: "refs/tags/" + tag, onWip: true}));
}
for (const context of [
  {ref:"refs/tags/v0.7.7", onWip:true},
  {ref:"refs/tags/v0.7.7-preview.1", onWip:true, onMain:true},
  {ref:"refs/tags/v0.7.7-preview.1", onMain:true},
  {ref:"refs/tags/v0.7.7", event:"workflow_dispatch", onMain:true},
  {ref:"refs/tags/v0.7.7"},
]) assert.throws(() => releasePolicy({...input, ...context}));

const directory = mkdtempSync(join(tmpdir(), "mountlet-release-policy-"));
const exec = promisify(execFile);
const git = async (...args) => (await exec("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", ...args], {cwd: directory})).stdout.trim();
try {
  await git("init");
  mkdirSync(join(directory,"app"));
  writeFileSync(join(directory,"app/package.json"), JSON.stringify({version}));
  await git("add", "app/package.json");
  await git("commit", "-m", "Stable fixture");
  const main = await git("rev-parse", "HEAD");
  await git("update-ref", "refs/remotes/origin/main", main);
  await git("tag", "v0.7.7");
  await git("commit", "--allow-empty", "-m", "Wip fixture");
  const wip = await git("rev-parse", "HEAD");
  await git("update-ref", "refs/remotes/origin/wip", wip);
  await git("tag", "-a", "v0.7.7-preview.1", "-m", "Preview fixture");
  await git("checkout", "--detach", "v0.7.7-preview.1");
  let indexReads = 0;
  const run = (ref, sha) => resolveReleasePolicy({GITHUB_REF:ref, GITHUB_EVENT_NAME:"push", GITHUB_SHA:sha}, directory, {readAptIndex:async()=>{indexReads++;return legacy;}});
  assert.deepEqual(await run("refs/tags/v0.7.7-preview.1", wip), preview);
  assert.equal(indexReads, 1);
  await assert.rejects(run("refs/tags/v0.7.7", wip), /same commit/);
  await assert.rejects(run("refs/tags/v0.7.7-preview.1", main), /same commit/);
  await git("checkout", "--detach", main);
  assert.deepEqual(await run("refs/tags/v0.7.7", main), stable);
  assert.equal(indexReads, 1, 'Stable classification must not depend on the preview index');
  await git("update-ref", "-d", "refs/remotes/origin/wip");
  await assert.rejects(run("refs/tags/v0.7.7", main), /Command failed/);
} finally { rmSync(directory, {recursive:true, force:true}); }
console.log("Release classification, provenance exclusion, ancestry and event consistency checks passed.");
