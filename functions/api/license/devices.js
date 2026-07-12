import {handleError, jsonResponse, readJson, signLicenseToken, tokenPayload, verifyLicenseToken} from "../../_lib/license.js";
import {assertLicenseUsable, refreshSubscriptionLicense} from "../../_lib/stripe-subscriptions.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const payload = await verifyLicenseToken(env, body.token);
    const rows = await env.DB.prepare(
      "SELECT id, device_label, platform, app_version, activated_at, last_seen_at FROM devices WHERE license_id = ? AND deactivated_at IS NULL ORDER BY activated_at"
    ).bind(payload.licenseId).all();
    let license = await env.DB.prepare(
      "SELECT * FROM licenses WHERE id = ?"
    ).bind(payload.licenseId).first();
    license = await refreshSubscriptionLicense(env, license);
    assertLicenseUsable(license);
    const devices = (rows.results || []).map((device) => ({
      ...device,
      current: device.id === payload.deviceId
    }));
    const currentDevice = (rows.results || []).find((device) => device.id === payload.deviceId);
    const token = currentDevice ? await signLicenseToken(env, tokenPayload(license, currentDevice)) : "";
    return jsonResponse({
      devices,
      usedDevices: devices.length,
      maxDevices: Number(license?.max_devices || payload.maxDevices || 0),
      expiresAt: license?.expires_at || payload.expiresAt || "",
      plan: license?.plan || payload.plan || "Mountlet License",
      billingModel: license?.billing_model || "",
      token,
    });
  } catch (error) {
    return handleError(error);
  }
}
