import {
  generateLicenseKey,
  handleError,
  jsonResponse,
  licenseKeyHash,
  nowIso,
  randomId,
  readJson,
  requireEnv
} from "../../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const expected = requireEnv(env, "LICENSE_ADMIN_TOKEN");
    const provided = request.headers.get("authorization") || "";
    if (provided !== `Bearer ${expected}`) {
      return jsonResponse({error: "Unauthorized."}, 401);
    }
    const body = await readJson(request);
    const licenseKind = String(body.licenseKind || "beta").trim().toLowerCase();
    if (!["beta", "paid"].includes(licenseKind)) {
      return jsonResponse({error: "licenseKind must be beta or paid."}, 400);
    }
    const plan = String(body.plan || (licenseKind === "beta" ? "Beta" : "Mountlet License")).trim();
    const requestedDevices = Number(body.maxDevices || 3);
    const maxDevices = Number.isFinite(requestedDevices) && requestedDevices > 0 ? Math.floor(requestedDevices) : 3;
    const email = String(body.email || "").trim();
    const prefix = licenseKind === "beta" ? "MTB" : "MNT";
    const licenseKey = generateLicenseKey(prefix);
    const licenseId = randomId("lic");
    const now = nowIso();
    await env.DB.prepare(
      "INSERT INTO licenses (id, license_key_hash, email, status, plan, license_kind, max_devices, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)"
    ).bind(
      licenseId,
      await licenseKeyHash(env, licenseKey),
      email,
      plan,
      licenseKind,
      maxDevices,
      now,
      now
    ).run();
    return jsonResponse({
      licenseId,
      licenseKey,
      licenseKind,
      plan,
      maxDevices
    });
  } catch (error) {
    return handleError(error);
  }
}
