import {ensureLicenseSchema} from "../../../_lib/license-schema.js";
import {handleError, jsonResponse, requireEnv} from "../../../_lib/license.js";
import {ensureNoticeSchema, inspectNoticeSchema} from "../../../_lib/notices.js";

export async function onRequestPost({request, env}) {
  try {
    const expected = requireEnv(env, "LICENSE_ADMIN_TOKEN");
    const provided = request.headers.get("authorization") || "";
    if (provided !== `Bearer ${expected}`) {
      return jsonResponse({error: "Unauthorized."}, 401);
    }
    const licenseDb = await ensureLicenseSchema(env);
    await ensureNoticeSchema(env);
    const notices = await inspectNoticeSchema(env);
    return jsonResponse({ok: licenseDb.ok && notices.ok, licenseDb, notices});
  } catch (error) {
    return handleError(error);
  }
}
