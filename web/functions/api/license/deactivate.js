import {handleError, jsonResponse, nowIso, readJson, verifyLicenseToken} from "../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const payload = await verifyLicenseToken(env, body.token);
    const deviceId = String(body.deviceId || payload.deviceId || "");
    if (!deviceId) {
      return jsonResponse({error: "Device id is required."}, 400);
    }
    await env.DB.prepare(
      "UPDATE devices SET deactivated_at = ? WHERE id = ? AND license_id = ? AND deactivated_at IS NULL"
    ).bind(nowIso(), deviceId, payload.licenseId).run();
    return jsonResponse({ok: true});
  } catch (error) {
    return handleError(error);
  }
}
