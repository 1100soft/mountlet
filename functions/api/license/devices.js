import {handleError, jsonResponse, readJson, verifyLicenseToken} from "../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const payload = await verifyLicenseToken(env, body.token);
    const rows = await env.DB.prepare(
      "SELECT id, device_label, platform, app_version, activated_at, last_seen_at FROM devices WHERE license_id = ? AND deactivated_at IS NULL ORDER BY activated_at"
    ).bind(payload.licenseId).all();
    const license = await env.DB.prepare(
      "SELECT max_devices, expires_at FROM licenses WHERE id = ?"
    ).bind(payload.licenseId).first();
    const devices = (rows.results || []).map((device) => ({
      ...device,
      current: device.id === payload.deviceId
    }));
    return jsonResponse({
      devices,
      usedDevices: devices.length,
      maxDevices: Number(license?.max_devices || payload.maxDevices || 0),
      expiresAt: license?.expires_at || payload.expiresAt || "",
    });
  } catch (error) {
    return handleError(error);
  }
}
