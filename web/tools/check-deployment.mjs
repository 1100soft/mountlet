import {readFileSync} from "node:fs";

const baseUrl = String(process.argv[2] || process.env.MOUNTLET_SITE_URL || "http://127.0.0.1:8788").replace(/\/+$/, "");
const releaseFiles = JSON.parse(readFileSync("web/release-files.json", "utf8"));
const downloadKey = process.argv[3] || process.env.MOUNTLET_DOWNLOAD_CHECK_KEY || releaseFiles.downloads?.linux;

let health;
let download;
try {
  health = await readJson(`${baseUrl}/api/health`);
  download = await fetch(`${baseUrl}/api/download/${encodeURIComponent(downloadKey)}`, {redirect: "manual"});
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}

const result = {
  site: baseUrl,
  health,
  download: {
    key: downloadKey,
    status: download.status,
    contentType: download.headers.get("content-type") || "",
  },
};

console.log(JSON.stringify(result, null, 2));

if (!health.ok || !health.functions) {
  fail("Pages Functions are not active.");
}
if (!health.dbBound) {
  fail("DB binding is missing.");
}
if (!health.downloadsBound) {
  fail("DOWNLOADS R2 binding is missing.");
}
if (!health.stripeConfigured) {
  fail("STRIPE_SECRET_KEY is missing.");
}
if (download.status === 404) {
  fail(`Download object is missing from the bound R2 bucket: ${downloadKey}`);
}
if (download.status >= 400 || (download.headers.get("content-type") || "").includes("text/html")) {
  fail(`Download route is not returning an R2 object for ${downloadKey}.`);
}

async function readJson(url) {
  const response = await fetch(url, {headers: {accept: "application/json"}});
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch (_error) {
    throw new Error(`${url} did not return JSON. Status ${response.status}. Body: ${text.slice(0, 200)}`);
  }
}

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}
