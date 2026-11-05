import type { LicenseStatus } from "./backend.ts";

/** Expiry of a daily token is not evidence that the saved key is invalid. */
export async function resolveStartupLicense(
  status: LicenseStatus | null,
  renew: () => Promise<LicenseStatus>,
): Promise<LicenseStatus | null> {
  if (status?.state !== "expired" || !status.licenseKey) return status;
  try { return await renew(); }
  catch { return status; } // Retain the saved key and stay locked on failure.
}
