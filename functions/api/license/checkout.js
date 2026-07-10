import {handleError, jsonResponse} from "../../_lib/license.js";

export async function onRequestGet({request, env}) {
  try {
    const url = new URL(request.url);
    const sessionId = String(url.searchParams.get("session_id") || "").trim();
    if (!sessionId) {
      return jsonResponse({error: "Checkout session id is required."}, 400);
    }
    const row = await env.DB.prepare(
      "SELECT p.license_key, p.quantity, p.created_at, l.plan, l.max_devices FROM payments p JOIN licenses l ON l.id = p.license_id WHERE p.stripe_session_id = ?"
    ).bind(sessionId).first();
    if (!row || !row.license_key) {
      return jsonResponse({error: "The license is not ready yet. Wait a few seconds, then refresh this page."}, 404);
    }
    return jsonResponse({
      licenseKey: row.license_key,
      devices: Number(row.max_devices || row.quantity || 0),
      plan: row.plan || "Mountlet License",
      createdAt: row.created_at || "",
    });
  } catch (error) {
    return handleError(error);
  }
}
