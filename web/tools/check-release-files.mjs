import {readFileSync} from "node:fs";

const releaseFiles = JSON.parse(readFileSync("web/release-files.json", "utf8"));
const packageWorkflow = readFileSync(".github/workflows/package.yml", "utf8");
const website = readFileSync("web/index.html", "utf8");
const websiteScript = readFileSync("web/script.js", "utf8");
const artifacts = releaseFiles.artifacts || {};
const expectedFiles = Object.values(artifacts)
  .map((artifact) => String(artifact?.source || "").trim())
  .filter(Boolean);

if (!expectedFiles.length) {
  fail("web/release-files.json has no release artifacts.");
}

if (releaseFiles.retention !== 5) {
  fail("Release retention must be set to 5.");
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
  if (!artifacts[key]) {
    fail(`Download selector references missing release key: ${key}`);
  }
}

if (!website.includes('id="home-download-button"') || !websiteScript.includes("detectedHomeDownloadKey")) {
  fail("The home page must retain its direct detected-platform download action.");
}

if (!website.includes('class="resource-guidance"')) {
  fail("The website must retain minimum and recommended resource guidance.");
}


for (const [key, artifact] of Object.entries(artifacts)) {
  for (const field of ["source", "platform", "architecture", "variant", "suffix"]) {
    if (!String(artifact?.[field] || "").trim()) {
      fail(`Release artifact ${key} is missing ${field}.`);
    }
  }
  if (releaseFiles.legacyDownloads?.[key] !== artifact.source) {
    fail(`Legacy download mapping for ${key} must match its artifact source.`);
  }
}

console.log(`Checked ${expectedFiles.length} release file name(s).`);

function fail(message) {
  console.error(message);
  process.exit(1);
}
