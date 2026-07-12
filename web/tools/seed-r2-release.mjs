import {spawnSync} from "node:child_process";
import {readdirSync} from "node:fs";
import {resolve} from "node:path";

const bucket = process.argv[2] || process.env.MOUNTLET_R2_BUCKET || "";
const releaseDir = process.argv[3] || process.env.MOUNTLET_RELEASE_DIR || resolve("web", "dev", "release");

if (!bucket) {
  console.error("Usage: node web/tools/seed-r2-release.mjs <bucket-name> [release-dir]");
  process.exit(1);
}

const files = readdirSync(releaseDir, {withFileTypes: true})
  .filter((entry) => entry.isFile())
  .map((entry) => entry.name)
  .sort();

if (!files.length) {
  console.error(`No release files found in ${releaseDir}`);
  process.exit(1);
}

for (const file of files) {
  const result = spawnSync(
    "wrangler",
    ["r2", "object", "put", `${bucket}/${file}`, "--file", resolve(releaseDir, file)],
    {stdio: "inherit"}
  );
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

console.log(`Uploaded ${files.length} release object(s) to ${bucket}.`);
