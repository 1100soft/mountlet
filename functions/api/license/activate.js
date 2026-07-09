import {
  activeDeviceCount,
  deviceHash,
  handleError,
  jsonResponse,
  loadActiveLicenseByKey,
  nowIso,
  randomId,
  readJson,
  signLicenseToken,
  tokenPayload
} from "../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const license = await loadActiveLicenseByKey(env, body.licenseKey);
    const hash = await deviceHash(body.deviceFingerprint);
    const now = nowIso();
    let device = await env.DB.prepare(
      "SELECT * FROM devices WHERE license_id = ? AND device_hash = ? AND deactivated_at IS NULL"
    ).bind(license.id, hash).first();
    if (device) {
      await env.DB.prepare(
        "UPDATE devices SET device_label = ?, platform = ?, app_version = ?, last_seen_at = ? WHERE id = ?"
      ).bind(
        String(body.deviceLabel || device.device_label || "This device"),
        String(body.platform || ""),
        String(body.appVersion || ""),
        now,
        device.id
      ).run();
      device = await env.DB.prepare("SELECT * FROM devices WHERE id = ?").bind(device.id).first();
    } else {
      const activeCount = await activeDeviceCount(env, license.id);
      if (activeCount >= Number(license.max_devices || 0)) {
        return jsonResponse({error: "This license has no remaining device slots."}, 409);
      }
      const id = randomId("dev");
      await env.DB.prepare(
        "INSERT INTO devices (id, license_id, device_hash, device_label, platform, app_version, activated_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
      ).bind(
        id,
        license.id,
        hash,
        String(body.deviceLabel || "This device"),
        String(body.platform || ""),
        String(body.appVersion || ""),
        now,
        now
      ).run();
      device = await env.DB.prepare("SELECT * FROM devices WHERE id = ?").bind(id).first();
    }
    const token = await signLicenseToken(env, tokenPayload(license, device));
    return jsonResponse({token});
  } catch (error) {
    return handleError(error);
  }
}
