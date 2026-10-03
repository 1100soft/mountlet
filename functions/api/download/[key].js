import {handleError, jsonResponse} from "../../_lib/license.js";
import {readReleaseIndex, selectedRelease} from "../../_lib/releases.js";

export async function onRequestGet({params, env, request}) {
  try {
    const key = String(params.key || "").trim();
    if (!key || key.includes("..") || key.includes("/")) {
      return jsonResponse({error: "Invalid download key."}, 400);
    }
    if (!env.DOWNLOADS) {
      return jsonResponse({error: "Download storage is not configured."}, 500);
    }
    const index = await readReleaseIndex(env);
    const requestedVersion = new URL(request.url).searchParams.get("version") || "";
    const release = index ? selectedRelease(index, requestedVersion) : null;
    if (requestedVersion && !release) {
      return jsonResponse({error: "Release version not found."}, 404);
    }
    const file = release?.files?.[key] || null;
    const objectKey = file?.objectKey || key;
    const object = await env.DOWNLOADS.get(objectKey);
    if (!object) {
      return jsonResponse({error: "Download not found."}, 404);
    }
    return new Response(object.body, {
      headers: {
        "cache-control": "private, max-age=60",
        "content-type": object.httpMetadata?.contentType || "application/octet-stream",
        "content-disposition": `attachment; filename="${safeFileName(file?.fileName || key)}"`,
        "x-mountlet-version": release?.version || "legacy",
      },
    });
  } catch (error) {
    return handleError(error);
  }
}

function safeFileName(value) {
  return String(value || "mountlet-download").replace(/["\\/\r\n]/g, "");
}
