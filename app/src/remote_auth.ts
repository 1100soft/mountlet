/** Providers that use rclone's browser-based OAuth configuration flow. */
export const OAUTH_REMOTE_TYPES = new Set(["drive", "gphotos", "dropbox", "onedrive", "box", "pcloud"]);

export const OAUTH_CLIENT_OPTIONS = [["mountlet", "Mountlet"], ["rclone", "rclone"], ["custom", "Custom"]] as const;

export function defaultOAuthClient(provider: string, availability: Record<string, boolean>): string {
  return availability[provider] ? "mountlet" : "rclone";
}

export function updateOAuthClientOptions(select: HTMLSelectElement, provider: string, availability: Record<string, boolean>): void {
  for (const option of select.options) {
    const reuse = option.value.startsWith("reuse:");
    option.hidden = reuse && provider !== "drive";
    option.disabled = option.hidden || (option.value === "mountlet" && !availability[provider]);
    if (option.value === "mountlet") option.text = availability[provider] ? "Mountlet" : "Mountlet (unavailable)";
  }
}

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
