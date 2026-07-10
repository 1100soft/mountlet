import {handleError, jsonResponse} from "../../_lib/license.js";

export async function onRequestGet({request, env}) {
  try {
    const url = new URL(request.url);
    const sessionId = String(url.searchParams.get("session_id") || "").trim();
    if (!sessionId) {
      return jsonResponse({error: "Checkout session id is required."}, 400);
    }
    const row = await env.DB.prepare(
      "SELECT p.license_key, p.quantity, p.created_at, l.plan, l.max_devices, (SELECT COUNT(*) FROM devices d WHERE d.license_id = p.license_id AND d.deactivated_at IS NULL) AS used_devices FROM payments p JOIN licenses l ON l.id = p.license_id WHERE p.stripe_session_id = ?"
    ).bind(sessionId).first();
    if (!row || !row.license_key) {
      if (row) {
        return jsonResponse({
          kind: "add_devices",
          devices: Number(row.max_devices || 0),
          usedDevices: Number(row.used_devices || 0),
          addedDevices: Number(row.quantity || 0),
          plan: row.plan || "Mountlet License",
          createdAt: row.created_at || "",
        });
      }
      return jsonResponse({error: "The purchase is not ready yet. Wait a few seconds, then refresh this page."}, 202);
    }
    return jsonResponse({
      licenseKey: row.license_key,
      devices: Number(row.max_devices || row.quantity || 0),
      usedDevices: Number(row.used_devices || 0),
      plan: row.plan || "Mountlet License",
      createdAt: row.created_at || "",
    });
  } catch (error) {
    return handleError(error);
  }
}
