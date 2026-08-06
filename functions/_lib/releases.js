export const RELEASE_INDEX_KEY = "releases/index.json";

export async function readReleaseIndex(env) {
  if (!env.DOWNLOADS) return null;
  const object = await env.DOWNLOADS.get(RELEASE_INDEX_KEY);
  if (!object) return null;
  try {
    const index = await object.json();
    if (!Array.isArray(index?.releases) || !index.releases.length) return null;
    return index;
  } catch (_error) {
    throw new Error("The release index in download storage is invalid.");
  }
}

export function selectedRelease(index, requestedVersion = "") {
  const version = String(requestedVersion || index?.latest || "").trim().replace(/^v/i, "");
  return (index?.releases || []).find((release) => release?.version === version) || null;
}

export function publicReleaseIndex(index) {
  return {
    schemaVersion: Number(index?.schemaVersion || 1),
    latest: String(index?.latest || ""),
    retention: Number(index?.retention || 5),
    releases: (index?.releases || []).map((release) => ({
      version: String(release.version || ""),
      publishedAt: String(release.publishedAt || ""),
      files: Object.fromEntries(Object.entries(release.files || {}).map(([key, file]) => [key, {
        fileName: String(file.fileName || ""),
        platform: String(file.platform || ""),
        architecture: String(file.architecture || ""),
        variant: String(file.variant || ""),
        size: Number(file.size || 0),
        sha256: String(file.sha256 || ""),
      }])),
    })),
  };
}
