import {
  generateLicenseKey,
  handleError,
  jsonResponse,
  licenseKeyHash,
  normalizeLicenseKey,
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
    const prefix = licenseKind === "beta" ? "MTB" : "MNT";
    const licenseKey = normalizeLicenseKey(body.licenseKey || generateLicenseKey(prefix));
    if (!/^(MNT|MTB)-[A-Z2-9]{5}-[A-Z2-9]{5}-[A-Z2-9]{5}-[A-Z2-9]{5}$/.test(licenseKey)) {
      return jsonResponse({error: "licenseKey must match MNT/MTB-XXXXX-XXXXX-XXXXX-XXXXX."}, 400);
    }
    const hash = await licenseKeyHash(env, licenseKey);
    const existing = await env.DB.prepare(
      "SELECT id, license_kind, plan, max_devices FROM licenses WHERE license_key_hash = ?"
    ).bind(hash).first();
    if (existing) {
      return jsonResponse({
        licenseId: existing.id,
        licenseKey,
        licenseKind: existing.license_kind || licenseKind,
        plan: existing.plan || plan,
        maxDevices: Number(existing.max_devices || maxDevices),
        alreadyExists: true,
      });
    }
    const licenseId = randomId("lic");
    const now = nowIso();
    await env.DB.prepare(
      "INSERT INTO licenses (id, license_key_hash, status, plan, license_kind, max_devices, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?, ?, ?)"
    ).bind(
      licenseId,
      hash,
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
