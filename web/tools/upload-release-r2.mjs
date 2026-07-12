import {spawnSync} from "node:child_process";
import {existsSync, readdirSync, readFileSync, statSync} from "node:fs";
import {basename, join, resolve} from "node:path";

const cliArgs = process.argv.slice(2);
const useRemote = cliArgs.includes("--remote") || process.env.MOUNTLET_R2_REMOTE === "1";
const dryRun = cliArgs.includes("--dry-run");
const positionalArgs = cliArgs.filter((arg) => arg !== "--remote" && arg !== "--dry-run");
const bucket = positionalArgs[0] || process.env.MOUNTLET_R2_BUCKET || "";
const sourcePath = positionalArgs[1] || process.env.MOUNTLET_RELEASE_SOURCE || resolve("release-artifacts");

if (!bucket) {
  console.error("Usage: node web/tools/upload-release-r2.mjs <bucket-name> <manifest.json|artifact-directory> [--remote] [--dry-run]");
  process.exit(1);
}

if (!existsSync(sourcePath)) {
  console.error(`Release source not found: ${sourcePath}`);
  process.exit(1);
}

const sourceStats = statSync(sourcePath);
const entries = sourceStats.isDirectory()
  ? entriesFromArtifactDirectory(sourcePath)
  : entriesFromManifest(sourcePath);

if (!entries.length) {
  console.error(`Release source has no files: ${sourcePath}`);
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
  if (dryRun) {
    console.log(`Would upload ${filePath} to ${bucket}/${key}${useRemote ? " remote" : " local"} R2.`);
    continue;
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

console.log(`${dryRun ? "Checked" : "Uploaded"} ${entries.length} release object(s) ${dryRun ? "for" : "to"} ${bucket}${useRemote ? " remote" : " local"} R2.`);

function entriesFromManifest(manifestPath) {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const files = manifest.files || {};
  return Object.entries(files)
    .map(([key, path]) => [String(key).trim(), String(path).trim()])
    .filter(([key, path]) => key && path);
}

function entriesFromArtifactDirectory(artifactDirectory) {
  const releaseFilePath = resolve("web", "release-files.json");
  const releaseFiles = JSON.parse(readFileSync(releaseFilePath, "utf8"));
  const expectedFiles = Object.values(releaseFiles.downloads || {})
    .map((fileName) => String(fileName).trim())
    .filter(Boolean);
  const availableFiles = listFilesRecursive(resolve(artifactDirectory));
  return expectedFiles.map((fileName) => {
    const match = availableFiles.find((candidate) => basename(candidate) === fileName);
    if (!match) {
      console.error(`Release artifact not found in ${artifactDirectory}: ${fileName}`);
      process.exit(1);
    }
    return [fileName, match];
  });
}

function listFilesRecursive(directory) {
  const result = [];
  for (const entry of readdirSync(directory, {withFileTypes: true})) {
    const entryPath = join(directory, entry.name);
    if (entry.isDirectory()) {
      result.push(...listFilesRecursive(entryPath));
    } else if (entry.isFile()) {
      result.push(entryPath);
    }
  }
  return result;
}
