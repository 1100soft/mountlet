import {readFileSync} from "node:fs";
import {resolve} from "node:path";

export const RELEASE_INDEX_KEY = "releases/index.json";

export function readReleaseConfig(path = resolve("web", "release-files.json")) {
  const config = JSON.parse(readFileSync(path, "utf8"));
  if (!config.artifacts || !Object.keys(config.artifacts).length) {
    throw new Error("Release configuration has no artifacts.");
  }
  return config;
}

export function readProjectVersion(path = resolve("app", "pyproject.toml")) {
  const text = readFileSync(path, "utf8");
  const project = text.match(/\[project\][\s\S]*?\nversion\s*=\s*["']([^"']+)["']/);
  if (!project) {
    throw new Error(`Could not read the project version from ${path}.`);
  }
  return normalizeVersion(project[1]);
}

export function normalizeVersion(value) {
  const version = String(value || "").trim().replace(/^v/i, "");
  if (!version || !/^[0-9A-Za-z][0-9A-Za-z._+-]*$/.test(version)) {
    throw new Error(`Invalid release version: ${value}`);
  }
  return version;
}

export function releaseFileName(version, artifact) {
  const normalized = normalizeVersion(version);
  return `mountlet-v${normalized}-${artifact.platform}-${artifact.architecture}-${artifact.variant}${artifact.suffix}`;
}

export function releaseObjectKey(prefix, version, artifact) {
  const normalized = normalizeVersion(version);
  return `${String(prefix || "releases").replace(/^\/+|\/+$/g, "")}/v${normalized}/${artifact.platform}/${artifact.architecture}/${artifact.variant}/${releaseFileName(normalized, artifact)}`;
}

export function updatedReleaseIndex(existing, release, retention = 5) {
  const keep = Math.max(1, Number(retention) || 5);
  const previous = Array.isArray(existing?.releases) ? existing.releases : [];
  const releases = [release, ...previous.filter((item) => item?.version !== release.version)]
    .sort((left, right) => compareVersions(right.version, left.version)
      || String(right.publishedAt || "").localeCompare(String(left.publishedAt || "")))
    .slice(0, keep);
  return {
    schemaVersion: 1,
    latest: releases[0]?.version || release.version,
    retention: keep,
    releases,
  };
}

export function compareVersions(leftValue, rightValue) {
  const left = versionParts(leftValue);
  const right = versionParts(rightValue);
  const length = Math.max(left.core.length, right.core.length);
  for (let index = 0; index < length; index += 1) {
    const comparison = compareVersionPart(left.core[index] ?? "0", right.core[index] ?? "0");
    if (comparison) return comparison;
  }
  if (!left.pre.length && right.pre.length) return 1;
  if (left.pre.length && !right.pre.length) return -1;
  for (let index = 0; index < Math.max(left.pre.length, right.pre.length); index += 1) {
    if (left.pre[index] === undefined) return -1;
    if (right.pre[index] === undefined) return 1;
    const comparison = compareVersionPart(left.pre[index], right.pre[index]);
    if (comparison) return comparison;
  }
  return 0;
}

function versionParts(value) {
  const [withoutBuild] = normalizeVersion(value).split("+");
  const [core, prerelease = ""] = withoutBuild.split("-", 2);
  return {core: core.split("."), pre: prerelease ? prerelease.split(".") : []};
}

function compareVersionPart(left, right) {
  const leftNumeric = /^\d+$/.test(left);
  const rightNumeric = /^\d+$/.test(right);
  if (leftNumeric && rightNumeric) return Number(left) - Number(right);
  if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
  return left.localeCompare(right);
}

export function removedObjectKeys(existing, next) {
  const nextKeys = new Set((next?.releases || []).flatMap((release) => Object.values(release.files || {}).map((file) => file.objectKey)));
  return [...new Set((existing?.releases || []).flatMap((release) => Object.values(release.files || {}).map((file) => file.objectKey)))]
    .filter((key) => key && !nextKeys.has(key));
}
