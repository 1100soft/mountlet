import {activeDeviceCount, handleError, jsonResponse, loadActiveLicenseByKey, readJson} from "../../_lib/license.js";
import {assertLicenseUsable, refreshSubscriptionLicense} from "../../_lib/stripe-subscriptions.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    let license = await loadActiveLicenseByKey(env, body.licenseKey);
    license = await refreshSubscriptionLicense(env, license);
    assertLicenseUsable(license);
    const usedDevices = await activeDeviceCount(env, license.id);
    return jsonResponse({
      ok: true,
      plan: license.plan || "Mountlet License",
      maxDevices: Number(license.max_devices || 0),
      usedDevices,
      billingModel: license.billing_model || "lifetime",
      licenseKind: license.license_kind || "paid",
      expiresAt: license.expires_at || "",
    });
  } catch (error) {
    return handleError(error);
  }
}
