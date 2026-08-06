import {readFileSync} from "node:fs";

const releaseFiles = JSON.parse(readFileSync("web/release-files.json", "utf8"));
const packageWorkflow = readFileSync(".github/workflows/package.yml", "utf8");
const website = readFileSync("web/index.html", "utf8");
const expectedFiles = Object.values(releaseFiles.downloads || {})
  .map((fileName) => String(fileName).trim())
  .filter(Boolean);

if (!expectedFiles.length) {
  fail("web/release-files.json has no download files.");
}

const duplicates = expectedFiles.filter((fileName, index) => expectedFiles.indexOf(fileName) !== index);
if (duplicates.length) {
  fail(`web/release-files.json has duplicate download files: ${[...new Set(duplicates)].join(", ")}`);
}

for (const fileName of expectedFiles) {
  if (!packageWorkflow.includes(`installer: ${fileName}`)) {
    fail(`Package workflow does not build release file: ${fileName}`);
  }
}

const selectorKeys = [...website.matchAll(/data-download-(?:standard|lean)="([^"]+)"/g)].map((match) => match[1]);
for (const key of selectorKeys) {
  if (!releaseFiles.downloads?.[key]) {
    fail(`Download selector references missing release key: ${key}`);
  }
}

console.log(`Checked ${expectedFiles.length} release file name(s).`);

function fail(message) {
  console.error(message);
  process.exit(1);
}
