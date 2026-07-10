import {handleError, HttpError, jsonResponse, loadActiveLicenseByKey, readJson, requireEnv} from "../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const kind = String(body.kind || "new_license").trim();
    const origin = new URL(request.url).origin;
    const successUrl = env.STRIPE_SUCCESS_URL || `${origin}/?checkout_session_id={CHECKOUT_SESSION_ID}#pricing`;
    const cancelUrl = env.STRIPE_CANCEL_URL || `${origin}/#pricing`;
    let session;
    if (kind === "add_devices") {
      const license = await loadActiveLicenseByKey(env, body.licenseKey);
      const requestedDevices = Number(body.deviceCount || 1);
      const deviceCount = Number.isFinite(requestedDevices)
        ? Math.min(50, Math.max(1, Math.floor(requestedDevices)))
        : 1;
      session = await createCheckoutSession(env, {
        priceId: requireEnv(env, "STRIPE_PRICE_DEVICE"),
        quantity: deviceCount,
        successUrl,
        cancelUrl,
        metadata: {
          kind,
          license_id: license.id,
          device_count: String(deviceCount),
        },
      });
    } else {
      session = await createCheckoutSession(env, {
        priceId: requireEnv(env, "STRIPE_PRICE_LICENSE"),
        quantity: 1,
        successUrl,
        cancelUrl,
        metadata: {
          kind: "new_license",
          plan: "Mountlet License",
          license_kind: "paid",
          device_count: "1",
        },
      });
    }
    return jsonResponse({url: session.url, id: session.id});
  } catch (error) {
    return handleError(error);
  }
}

async function createCheckoutSession(env, {priceId, quantity, successUrl, cancelUrl, metadata}) {
  const body = new URLSearchParams();
  body.set("mode", "payment");
  body.set("line_items[0][price]", priceId);
  body.set("line_items[0][quantity]", String(quantity));
  body.set("success_url", successUrl);
  body.set("cancel_url", cancelUrl);
  for (const [key, value] of Object.entries(metadata)) {
    body.set(`metadata[${key}]`, String(value));
  }

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
