import {jsonResponse, requireEnv} from "../../_lib/license.js";

export async function onRequestGet({env}) {
  try {
    const publicKey = requireEnv(env, "LICENSE_SIGNING_PUBLIC_KEY");
    return jsonResponse({publicKey});
  } catch (error) {
    return jsonResponse({error: String(error?.message || error || "License public key is unavailable.")}, 500);
  }
}
