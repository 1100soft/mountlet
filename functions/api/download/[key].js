import {handleError, jsonResponse} from "../../_lib/license.js";

export async function onRequestGet({params, env}) {
  try {
    const key = String(params.key || "").trim();
    if (!key || key.includes("..") || key.includes("/")) {
      return jsonResponse({error: "Invalid download key."}, 400);
    }
    if (!env.DOWNLOADS) {
      return jsonResponse({error: "Download storage is not configured."}, 500);
    }
    const object = await env.DOWNLOADS.get(key);
    if (!object) {
      return jsonResponse({error: "Download not found."}, 404);
    }
    return new Response(object.body, {
      headers: {
        "cache-control": "private, max-age=60",
        "content-type": object.httpMetadata?.contentType || "application/octet-stream",
        "content-disposition": `attachment; filename="${key.replace(/"/g, "")}"`,
      },
    });
  } catch (error) {
    return handleError(error);
  }
}
