/** Providers that use rclone's browser-based OAuth configuration flow. */
export const OAUTH_REMOTE_TYPES = new Set(["drive", "gphotos", "dropbox", "onedrive", "box", "pcloud"]);

export function googleAccountValue(value: string): string {
  const account = value.trim();
  return account && !account.includes("@") ? `${account}@gmail.com` : account;
}

/** Password controls use a placeholder for an unchanged stored value. */
export function authenticationFieldChanged(value: string, previous: string, secret: boolean): boolean {
  if (secret) return Boolean(value && value !== "••••••");
  const normalize = (input: string) => input.trim();
  const next = normalize(value);
  const old = normalize(previous);
  const isFalse = (input: string) => ["", "false"].includes(input.toLowerCase());
  return !(isFalse(next) && isFalse(old)) && next !== old;
}
