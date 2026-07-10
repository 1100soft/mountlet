import {handleError, HttpError, jsonResponse, readJson, requireEnv} from "../_lib/license.js";

const DEFAULT_DEVICES = 3;

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const requestedDevices = Number(body.deviceCount || DEFAULT_DEVICES);
    const deviceCount = Number.isFinite(requestedDevices)
      ? Math.min(50, Math.max(1, Math.floor(requestedDevices)))
      : DEFAULT_DEVICES;
    const origin = new URL(request.url).origin;
    const successUrl = env.STRIPE_SUCCESS_URL || `${origin}/?checkout_session_id={CHECKOUT_SESSION_ID}#pricing`;
    const cancelUrl = env.STRIPE_CANCEL_URL || `${origin}/#pricing`;
    const session = await createCheckoutSession(env, {
      priceId: requireEnv(env, "STRIPE_PRICE_LICENSE"),
      deviceCount,
      successUrl,
      cancelUrl,
    });
    return jsonResponse({url: session.url, id: session.id});
  } catch (error) {
    return handleError(error);
  }
}

async function createCheckoutSession(env, {priceId, deviceCount, successUrl, cancelUrl}) {
  const body = new URLSearchParams();
  body.set("mode", "payment");
  body.set("line_items[0][price]", priceId);
  body.set("line_items[0][quantity]", String(deviceCount));
  body.set("line_items[0][adjustable_quantity][enabled]", "true");
  body.set("line_items[0][adjustable_quantity][minimum]", "1");
  body.set("line_items[0][adjustable_quantity][maximum]", "50");
  body.set("success_url", successUrl);
  body.set("cancel_url", cancelUrl);
  body.set("metadata[kind]", "new_license");
  body.set("metadata[plan]", "Mountlet License");
  body.set("metadata[license_kind]", "paid");
  body.set("metadata[device_count]", String(deviceCount));

  const response = await fetch("https://api.stripe.com/v1/checkout/sessions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${requireEnv(env, "STRIPE_SECRET_KEY")}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new HttpError(502, `Stripe returned non-JSON response: ${text.slice(0, 240)}`);
  }
  if (!response.ok) {
    throw new HttpError(502, String(data.error?.message || "Stripe checkout failed."));
  }
  return data;
}
