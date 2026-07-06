import {handleError, HttpError, jsonResponse, readJson, requireEnv} from "../_lib/license.js";

const PLANS = {
  personal: {
    name: "Personal",
    priceEnv: "STRIPE_PRICE_PERSONAL",
    defaultDevices: 3,
  },
  pro: {
    name: "Pro",
    priceEnv: "STRIPE_PRICE_PRO",
    defaultDevices: 5,
  },
};

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const planKey = String(body.plan || "personal").trim().toLowerCase();
    const plan = PLANS[planKey];
    if (!plan) {
      return jsonResponse({error: "Unknown checkout plan."}, 400);
    }

    const requestedDevices = Number(body.deviceCount || plan.defaultDevices);
    const deviceCount = Number.isFinite(requestedDevices)
      ? Math.min(50, Math.max(1, Math.floor(requestedDevices)))
      : plan.defaultDevices;
    const origin = new URL(request.url).origin;
    const successUrl = env.STRIPE_SUCCESS_URL || `${origin}/#download`;
    const cancelUrl = env.STRIPE_CANCEL_URL || `${origin}/#pricing`;
    const session = await createCheckoutSession(env, {
      priceId: requireEnv(env, plan.priceEnv),
      planName: plan.name,
      deviceCount,
      successUrl,
      cancelUrl,
    });
    return jsonResponse({url: session.url, id: session.id});
  } catch (error) {
    return handleError(error);
  }
}

async function createCheckoutSession(env, {priceId, planName, deviceCount, successUrl, cancelUrl}) {
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
  body.set("metadata[plan]", planName);
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
  const data = await response.json();
  if (!response.ok) {
    throw new HttpError(502, String(data.error?.message || "Stripe checkout failed."));
  }
  return data;
}
