import {strict as assert} from "node:assert";
import {
  compareVersions,
  normalizeVersion,
  releaseFileName,
  releaseObjectKey,
  removedObjectKeys,
  updatedReleaseIndex,
} from "./release-layout.mjs";

const artifact = {platform: "windows", architecture: "x64", variant: "standard", suffix: "-setup.exe"};
assert.equal(normalizeVersion("v0.6.3"), "0.6.3");
assert.ok(compareVersions("0.7.0", "0.6.9") > 0);
assert.ok(compareVersions("0.7.0", "0.7.0-beta.2") > 0);
assert.equal(releaseFileName("0.6.3", artifact), "mountlet-v0.6.3-windows-x64-standard-setup.exe");
assert.equal(
  releaseObjectKey("releases", "0.6.3", artifact),
  "releases/v0.6.3/windows/x64/standard/mountlet-v0.6.3-windows-x64-standard-setup.exe"
);

const oldReleases = Array.from({length: 5}, (_, index) => ({
  version: `0.6.${index}`,
  publishedAt: `2026-07-0${index + 1}T00:00:00.000Z`,
  files: {windows: {objectKey: `releases/v0.6.${index}/windows.exe`}},
}));
const current = {
  version: "0.6.5",
  publishedAt: "2026-07-06T00:00:00.000Z",
  files: {windows: {objectKey: "releases/v0.6.5/windows.exe"}},
};
const index = updatedReleaseIndex({releases: oldReleases}, current, 5);
assert.equal(index.latest, "0.6.5");
assert.deepEqual(index.releases.map((release) => release.version), ["0.6.5", "0.6.4", "0.6.3", "0.6.2", "0.6.1"]);
assert.deepEqual(removedObjectKeys({releases: oldReleases}, index), ["releases/v0.6.0/windows.exe"]);

const rebuiltOld = updatedReleaseIndex(index, {
  version: "0.6.2",
  publishedAt: "2026-08-07T00:00:00.000Z",
  files: {windows: {objectKey: "releases/v0.6.2/rebuilt-windows.exe"}},
}, 5);
assert.equal(rebuiltOld.latest, "0.6.5");
assert.deepEqual(rebuiltOld.releases.map((release) => release.version), ["0.6.5", "0.6.4", "0.6.3", "0.6.2", "0.6.1"]);

console.log("Release layout checks passed.");
