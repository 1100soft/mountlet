import {spawnSync} from "node:child_process";
import {existsSync, readFileSync} from "node:fs";
import {resolve} from "node:path";

const cliArgs = process.argv.slice(2);
const useRemote = cliArgs.includes("--remote") || process.env.MOUNTLET_R2_REMOTE === "1";
const positionalArgs = cliArgs.filter((arg) => arg !== "--remote");
const bucket = positionalArgs[0] || process.env.MOUNTLET_R2_BUCKET || "";
const manifestPath = positionalArgs[1] || process.env.MOUNTLET_RELEASE_MANIFEST || resolve("web", "release-manifest.example.json");

if (!bucket) {
  console.error("Usage: node web/tools/upload-release-r2.mjs <bucket-name> <manifest.json> [--remote]");
  process.exit(1);
}

if (!existsSync(manifestPath)) {
  console.error(`Release manifest not found: ${manifestPath}`);
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const files = manifest.files || {};
const entries = Object.entries(files)
  .map(([key, path]) => [String(key).trim(), String(path).trim()])
  .filter(([key, path]) => key && path);

if (!entries.length) {
  console.error(`Release manifest has no files: ${manifestPath}`);
  process.exit(1);
}

for (const [key, sourcePath] of entries) {
  if (key.includes("/") || key.includes("..")) {
    console.error(`Invalid R2 object key: ${key}`);
    process.exit(1);
  }
  const filePath = resolve(sourcePath);
  if (!existsSync(filePath)) {
    console.error(`Release artifact not found for ${key}: ${filePath}`);
    process.exit(1);
  }
  const result = spawnSync(
    "wrangler",
    ["r2", "object", "put", `${bucket}/${key}`, "--file", filePath, ...(useRemote ? ["--remote"] : [])],
    {stdio: "inherit"}
  );
  if (result.error) {
    console.error(`Could not run wrangler: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

console.log(`Uploaded ${entries.length} release object(s) to ${bucket}${useRemote ? " remote" : " local"} R2.`);
