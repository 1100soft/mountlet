import {strict as assert} from "node:assert";
import {onRequestGet as downloadRelease} from "../../functions/api/download/[key].js";
import {onRequestGet as listReleases} from "../../functions/api/releases.js";

const index = {
  schemaVersion: 1,
  latest: "0.6.3",
  retention: 5,
  releases: [{
    version: "0.6.3",
    publishedAt: "2026-08-06T00:00:00.000Z",
    files: {
      windows: {
        objectKey: "releases/v0.6.3/windows/x64/standard/mountlet-v0.6.3-windows-x64-standard-setup.exe",
        fileName: "mountlet-v0.6.3-windows-x64-standard-setup.exe",
        platform: "windows",
        architecture: "x64",
        variant: "standard",
        size: 4,
        sha256: "test-hash",
      },
    },
  }],
};
const env = {
  DOWNLOADS: {
    async get(key) {
      if (key === "releases/index.json") return {json: async () => index};
      if (key === index.releases[0].files.windows.objectKey) {
        return {body: new Uint8Array([1, 2, 3, 4]), httpMetadata: {contentType: "application/octet-stream"}};
      }
      return null;
    },
  },
};

const listResponse = await listReleases({env});
assert.equal(listResponse.status, 200);
const publicIndex = await listResponse.json();
assert.equal(publicIndex.latest, "0.6.3");
assert.equal(publicIndex.releases.length, 1);
assert.equal(publicIndex.releases[0].files.windows.objectKey, undefined);

const response = await downloadRelease({
  env,
  params: {key: "windows"},
  request: new Request("https://mountlet.app/api/download/windows?version=0.6.3"),
});
assert.equal(response.status, 200);
assert.equal(response.headers.get("x-mountlet-version"), "0.6.3");
assert.match(response.headers.get("content-disposition"), /mountlet-v0\.6\.3-windows-x64-standard-setup\.exe/);

const missing = await downloadRelease({
  env,
  params: {key: "windows"},
  request: new Request("https://mountlet.app/api/download/windows?version=0.5.0"),
});
assert.equal(missing.status, 404);

console.log("Release API checks passed.");
