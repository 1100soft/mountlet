import {handleError, jsonResponse, requireEnv} from "../../../_lib/license.js";

export async function onRequestGet({request, env}) {
  try {
    const expected = requireEnv(env, "LICENSE_ADMIN_TOKEN");
    const provided = request.headers.get("authorization") || "";
    if (provided !== `Bearer ${expected}`) {
      return jsonResponse({error: "Unauthorized."}, 401);
    }
    const rows = await env.DB.prepare(
      "SELECT p.id, p.stripe_session_id, p.kind, p.quantity, p.license_key, p.created_at, l.plan, l.license_kind, l.max_devices FROM payments p JOIN licenses l ON l.id = p.license_id ORDER BY p.created_at DESC LIMIT 25"
    ).all();
    return jsonResponse({payments: rows.results || []});
  } catch (error) {
    return handleError(error);
  }
}
