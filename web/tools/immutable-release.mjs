/** Validate an entire release before writing any objects or changing its index. */
export function assertImmutableRelease(existing, release) {
  const previous = existing?.releases?.find(item => item.version === release.version);
  if (!previous) return;
  const before = Object.entries(previous.files || {}).map(([key, file]) => [key, file.objectKey, file.sha256]).sort();
  const after = Object.entries(release.files || {}).map(([key, file]) => [key, file.objectKey, file.sha256]).sort();
  if (JSON.stringify(before) !== JSON.stringify(after)) throw Error(`Release ${release.version} already exists with different contents; use a new release tag.`);
}
