import {spawnSync} from "node:child_process";
import {createHash, createHmac} from "node:crypto";
import {existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {basename, join, resolve} from "node:path";
import {
  RELEASE_INDEX_KEY,
  normalizeVersion,
  readProjectVersion,
  readReleaseConfig,
  releaseFileName,
  releaseObjectKey,
  removedObjectKeys,
  updatedReleaseIndex,
  validateReleaseRef,
} from "./release-layout.mjs";

const cliArgs = process.argv.slice(2);
const useRemote = takeFlag("--remote") || process.env.MOUNTLET_R2_REMOTE === "1";
const dryRun = takeFlag("--dry-run");
const requestedVersion = takeOption("--version") || process.env.MOUNTLET_RELEASE_VERSION || readProjectVersion();
const requestedRetention = takeOption("--retain") || process.env.MOUNTLET_RELEASE_RETENTION || "";
const positionalArgs = cliArgs.filter((arg) => !arg.startsWith("--"));
const bucket = positionalArgs[0] || process.env.MOUNTLET_R2_BUCKET || "";
const sourcePath = positionalArgs[1] || process.env.MOUNTLET_RELEASE_SOURCE || resolve("release-artifacts");
const version = normalizeVersion(requestedVersion);
validateReleaseRef(version);
const config = readReleaseConfig();
const retention = Math.max(1, Number(requestedRetention || config.retention || 5));

if (!bucket) {
  fail("Usage: node web/tools/upload-release-r2.mjs <bucket-name> <artifact-directory> [--version X.Y.Z] [--retain 5] [--remote] [--dry-run]");
}
if (!existsSync(sourcePath) || !statSync(sourcePath).isDirectory()) {
  fail(`Release artifact directory not found: ${sourcePath}`);
}
if (useRemote && !hasS3Credentials()) {
  fail("Remote publication requires CLOUDFLARE_ACCOUNT_ID and R2 S3 credentials.");
}

const availableFiles = listFilesRecursive(resolve(sourcePath));
const publishedAt = new Date().toISOString();
const release = {version, publishedAt, files: {}};
const uploads = [];
for (const [logicalKey, artifact] of Object.entries(config.artifacts)) {
  const source = availableFiles.find((candidate) => basename(candidate) === artifact.source);
  if (!source) {
    fail(`Release artifact not found in ${sourcePath}: ${artifact.source}`);
  }
  const filePath = resolve(source);
  const objectKey = releaseObjectKey(config.objectPrefix, version, artifact);
  const fileName = releaseFileName(version, artifact);
  release.files[logicalKey] = {
    objectKey,
    fileName,
    platform: artifact.platform,
    architecture: artifact.architecture,
    variant: artifact.variant,
    size: statSync(filePath).size,
    sha256: sha256Hex(readFileSync(filePath)),
  };
  uploads.push({objectKey, filePath, contentType: contentTypeFor(fileName)});
}

const existing = dryRun ? null : await readReleaseIndex(bucket);
const index = updatedReleaseIndex(existing, release, retention);
const legacyObjectKeys = Object.values(config.legacyDownloads || {}).map(String).filter(Boolean);
const removals = [...new Set([
  ...(existing?.pendingDeletion || []),
  ...removedObjectKeys(existing, index),
  ...(existing ? [] : legacyObjectKeys),
])];
if (removals.length) index.pendingDeletion = removals;

for (const upload of uploads) {
  if (dryRun) {
    console.log(`Would upload ${upload.filePath} to ${bucket}/${upload.objectKey}.`);
  } else {
    await putObject(bucket, upload.objectKey, readFileSync(upload.filePath), upload.contentType);
  }
}

const indexBody = Buffer.from(`${JSON.stringify(index, null, 2)}\n`);
if (dryRun) {
  console.log(`Would publish ${bucket}/${RELEASE_INDEX_KEY} with ${index.releases.length} release(s), retaining ${retention}.`);
} else {
  await putObject(bucket, RELEASE_INDEX_KEY, indexBody, "application/json; charset=utf-8");
}

const failedRemovals = [];
for (const objectKey of removals) {
  if (dryRun) {
    console.log(`Would delete retired object ${bucket}/${objectKey}.`);
  } else {
    try {
      await deleteObject(bucket, objectKey);
    } catch (error) {
      failedRemovals.push(objectKey);
      console.error(error.message || String(error));
    }
  }
}

if (!dryRun && removals.length) {
  if (failedRemovals.length) index.pendingDeletion = failedRemovals;
  else delete index.pendingDeletion;
  await putObject(bucket, RELEASE_INDEX_KEY, Buffer.from(`${JSON.stringify(index, null, 2)}\n`), "application/json; charset=utf-8");
}

console.log(`${dryRun ? "Checked" : "Published"} Mountlet ${version}: ${uploads.length} installer(s), ${index.releases.length}/${retention} retained release(s), ${removals.length} retired object(s).`);
if (failedRemovals.length) {
  console.error(`${failedRemovals.length} retired object(s) remain queued for deletion.`);
  process.exitCode = 1;
}

function takeFlag(name) {
  const index = cliArgs.indexOf(name);
  if (index < 0) return false;
  cliArgs.splice(index, 1);
  return true;
}

function takeOption(name) {
  const index = cliArgs.indexOf(name);
  if (index < 0) return "";
  const value = cliArgs[index + 1] || "";
  cliArgs.splice(index, 2);
  return value;
}

function listFilesRecursive(directory) {
  const result = [];
  for (const entry of readdirSync(directory, {withFileTypes: true})) {
    const entryPath = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...listFilesRecursive(entryPath));
    else if (entry.isFile()) result.push(entryPath);
  }
  return result;
}

async function readReleaseIndex(bucketName) {
  const response = useRemote
    ? await s3Request("GET", bucketName, RELEASE_INDEX_KEY)
    : wranglerGet(bucketName, RELEASE_INDEX_KEY);
  if (!response || response.status === 404) return null;
  if (!response.ok) fail(`Could not read ${bucketName}/${RELEASE_INDEX_KEY}: ${response.status}`);
  try {
    return JSON.parse(await response.text());
  } catch (error) {
    fail(`Existing release index is invalid: ${error.message}`);
  }
}

async function putObject(bucketName, key, body, contentType) {
  if (useRemote) {
    const response = await s3Request("PUT", bucketName, key, {body, contentType});
    if (!response.ok) fail(`R2 upload failed for ${key}: ${response.status} ${await response.text()}`);
    console.log(`Uploaded ${bucketName}/${key}.`);
    return;
  }
  const temporary = mkdtempSync(join(tmpdir(), "mountlet-r2-put-"));
  const file = join(temporary, "object");
  try {
    writeFileSync(file, body);
    runWrangler(["r2", "object", "put", `${bucketName}/${key}`, "--local", "--file", file, "--content-type", contentType]);
  } finally {
    rmSync(temporary, {recursive: true, force: true});
  }
}

async function deleteObject(bucketName, key) {
  if (useRemote) {
    const response = await s3Request("DELETE", bucketName, key);
    if (!response.ok && response.status !== 404) throw new Error(`R2 delete failed for ${key}: ${response.status} ${await response.text()}`);
    console.log(`Deleted ${bucketName}/${key}.`);
    return;
  }
  runWrangler(["r2", "object", "delete", `${bucketName}/${key}`, "--local"]);
}

function wranglerGet(bucketName, key) {
  const temporary = mkdtempSync(join(tmpdir(), "mountlet-r2-get-"));
  const file = join(temporary, "object");
  const result = spawnSync("wrangler", ["r2", "object", "get", `${bucketName}/${key}`, "--local", "--file", file], {encoding: "utf8"});
  if (result.status !== 0) {
    rmSync(temporary, {recursive: true, force: true});
    return {ok: false, status: 404, text: async () => ""};
  }
  const body = readFileSync(file);
  rmSync(temporary, {recursive: true, force: true});
  return {ok: true, status: 200, text: async () => body.toString("utf8")};
}

function runWrangler(args) {
  const result = spawnSync("wrangler", args, {stdio: "inherit"});
  if (result.error) fail(`Could not run wrangler: ${result.error.message}`);
  if (result.status !== 0) process.exit(result.status || 1);
}

async function s3Request(method, bucketName, key, {body = null, contentType = ""} = {}) {
  const accountId = process.env.CLOUDFLARE_ACCOUNT_ID || "";
  const accessKeyId = process.env.CLOUDFLARE_R2_ACCESS_KEY_ID || "";
  const secretAccessKey = process.env.CLOUDFLARE_R2_SECRET_ACCESS_KEY || "";
  const host = `${accountId}.r2.cloudflarestorage.com`;
  const path = `/${encodeURIComponent(bucketName)}/${encodeR2Key(key)}`;
  const now = new Date();
  const amzDate = toAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = sha256Hex(body || Buffer.alloc(0));
  const headers = {host, "x-amz-content-sha256": payloadHash, "x-amz-date": amzDate};
  if (body !== null) headers["content-length"] = String(body.byteLength);
  if (contentType) headers["content-type"] = contentType;
  const signedHeaders = Object.keys(headers).sort().join(";");
  const canonicalHeaders = Object.keys(headers).sort().map((name) => `${name}:${headers[name]}\n`).join("");
  const canonicalRequest = [method, path, "", canonicalHeaders, signedHeaders, payloadHash].join("\n");
  const credentialScope = `${dateStamp}/auto/s3/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", amzDate, credentialScope, sha256Hex(canonicalRequest)].join("\n");
  const signature = hmacHex(signingKey(secretAccessKey, dateStamp), stringToSign);
  headers.authorization = `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
  return fetch(`https://${host}${path}`, {method, headers, body});
}

function hasS3Credentials() {
  return Boolean(process.env.CLOUDFLARE_ACCOUNT_ID && process.env.CLOUDFLARE_R2_ACCESS_KEY_ID && process.env.CLOUDFLARE_R2_SECRET_ACCESS_KEY);
}

function contentTypeFor(fileName) {
  if (fileName.endsWith(".exe")) return "application/vnd.microsoft.portable-executable";
  if (fileName.endsWith(".dmg")) return "application/x-apple-diskimage";
  if (fileName.endsWith(".deb")) return "application/vnd.debian.binary-package";
  return "application/octet-stream";
}

function encodeR2Key(key) {
  return key.split("/").map(encodeURIComponent).join("/");
}

function toAmzDate(date) {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, "");
}

function sha256Hex(value) {
  return createHash("sha256").update(value).digest("hex");
}

function hmac(key, value) {
  return createHmac("sha256", key).update(value).digest();
}

function hmacHex(key, value) {
  return createHmac("sha256", key).update(value).digest("hex");
}

function signingKey(secretAccessKey, dateStamp) {
  const dateKey = hmac(`AWS4${secretAccessKey}`, dateStamp);
  const regionKey = hmac(dateKey, "auto");
  const serviceKey = hmac(regionKey, "s3");
  return hmac(serviceKey, "aws4_request");
}

function fail(message) {
  console.error(message);
  process.exit(1);
}
