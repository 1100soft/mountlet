import {handleError, jsonResponse} from "../_lib/license.js";
import {publicReleaseIndex, readReleaseIndex} from "../_lib/releases.js";

export async function onRequestGet({env}) {
  try {
    if (!env.DOWNLOADS) {
      return jsonResponse({error: "Download storage is not configured."}, 500);
    }
    const index = await readReleaseIndex(env);
    if (!index) {
      return jsonResponse({error: "No published releases were found."}, 404);
    }
    return Response.json(publicReleaseIndex(index), {
      headers: {"cache-control": "public, max-age=60"},
    });
  } catch (error) {
    return handleError(error);
  }
}
