import {handleError, jsonResponse, loadActiveLicenseByKey, readJson} from "../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const license = await loadActiveLicenseByKey(env, body.licenseKey);
    return jsonResponse({
      ok: true,
      plan: license.plan || "Mountlet License",
      maxDevices: Number(license.max_devices || 0),
      licenseKind: license.license_kind || "paid",
    });
  } catch (error) {
    return handleError(error);
  }
}
