import {spawnSync} from "node:child_process";
import {createHmac, createHash} from "node:crypto";
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
  if (useRemote && hasS3Credentials()) {
    await uploadWithS3Api(bucket, key, filePath);
  } else {
    uploadWithWrangler(bucket, key, filePath, useRemote);
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

function hasS3Credentials() {
  return Boolean(process.env.CLOUDFLARE_R2_ACCESS_KEY_ID && process.env.CLOUDFLARE_R2_SECRET_ACCESS_KEY);
}

function uploadWithWrangler(bucket, key, filePath, remote) {
  const result = spawnSync(
    "wrangler",
    ["r2", "object", "put", `${bucket}/${key}`, "--file", filePath, ...(remote ? ["--remote"] : [])],
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

async function uploadWithS3Api(bucket, key, filePath) {
  const accountId = process.env.CLOUDFLARE_ACCOUNT_ID || "";
  const accessKeyId = process.env.CLOUDFLARE_R2_ACCESS_KEY_ID || "";
  const secretAccessKey = process.env.CLOUDFLARE_R2_SECRET_ACCESS_KEY || "";
  if (!accountId) {
    console.error("CLOUDFLARE_ACCOUNT_ID is required for R2 S3 uploads.");
    process.exit(1);
  }
  const body = readFileSync(filePath);
  const host = `${accountId}.r2.cloudflarestorage.com`;
  const path = `/${encodeURIComponent(bucket)}/${encodeR2Key(key)}`;
  const url = `https://${host}${path}`;
  const now = new Date();
  const amzDate = toAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = sha256Hex(body);
  const headers = {
    "content-length": String(body.byteLength),
    "host": host,
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": amzDate,
  };
  const signedHeaders = Object.keys(headers).sort().join(";");
  const canonicalHeaders = Object.keys(headers)
    .sort()
    .map((name) => `${name}:${headers[name]}\n`)
    .join("");
  const canonicalRequest = [
    "PUT",
    path,
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const credentialScope = `${dateStamp}/auto/s3/aws4_request`;
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    sha256Hex(canonicalRequest),
  ].join("\n");
  const signature = hmacHex(signingKey(secretAccessKey, dateStamp), stringToSign);
  const authorization = [
    `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${credentialScope}`,
    `SignedHeaders=${signedHeaders}`,
    `Signature=${signature}`,
  ].join(", ");
  console.log(`Uploading ${filePath} to ${bucket}/${key} via R2 S3 API.`);
  const response = await fetch(url, {
    method: "PUT",
    headers: {...headers, authorization},
    body,
  });
  if (!response.ok) {
    const text = await response.text();
    console.error(`R2 S3 upload failed for ${bucket}/${key}: ${response.status} ${response.statusText}`);
    if (text) {
      console.error(text);
    }
    process.exit(1);
  }
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
