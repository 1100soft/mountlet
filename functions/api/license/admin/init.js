import {ensureLicenseSchema} from "../../../_lib/license-schema.js";
import {handleError, jsonResponse, requireEnv} from "../../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const expected = requireEnv(env, "LICENSE_ADMIN_TOKEN");
    const provided = request.headers.get("authorization") || "";
    if (provided !== `Bearer ${expected}`) {
      return jsonResponse({error: "Unauthorized."}, 401);
    }
    const licenseDb = await ensureLicenseSchema(env);
    return jsonResponse({ok: licenseDb.ok, licenseDb});
  } catch (error) {
    return handleError(error);
  }
}
