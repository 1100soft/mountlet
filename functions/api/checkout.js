import {handleError, HttpError, jsonResponse, loadActiveLicenseByKey, readJson, requireEnv} from "../_lib/license.js";

const PLANS = {
  monthly: {
    label: "Mountlet Monthly",
    billingModel: "monthly",
    mode: "subscription",
    interval: "month",
    baseCents: 500,
    extraDeviceCents: 100,
  },
  annual: {
    label: "Mountlet Annual",
    billingModel: "annual",
    mode: "subscription",
    interval: "year",
    baseCents: 3000,
    extraDeviceCents: 600,
  },
  lifetime: {
    label: "Mountlet Lifetime",
    billingModel: "lifetime",
    mode: "payment",
    baseCents: 5000,
    extraDeviceCents: 1000,
  },
};

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const kind = String(body.kind || "new_license").trim();
    const origin = new URL(request.url).origin;
    const successUrl = env.STRIPE_SUCCESS_URL || `${origin}/?checkout_session_id={CHECKOUT_SESSION_ID}#license`;
    const cancelUrl = env.STRIPE_CANCEL_URL || `${origin}/#pricing`;
    let session;
    if (kind === "add_devices") {
      const license = await loadActiveLicenseByKey(env, body.licenseKey);
      if (license.billing_model && license.billing_model !== "lifetime") {
        throw new HttpError(409, "Changing device count for subscriptions is not available yet.");
      }
      const requestedDevices = Number(body.deviceCount || 1);
      const deviceCount = Number.isFinite(requestedDevices)
        ? Math.min(50, Math.max(1, Math.floor(requestedDevices)))
        : 1;
      session = await createCheckoutSession(env, {
        mode: "payment",
        lineItems: [{
          name: "Mountlet extra device",
          unitAmount: PLANS.lifetime.extraDeviceCents,
          quantity: deviceCount,
        }],
        successUrl,
        cancelUrl,
        metadata: {
          kind,
          license_id: license.id,
          device_count: String(deviceCount),
        },
      });
    } else {
      const planKey = String(body.plan || "monthly").trim();
      const plan = PLANS[planKey] || PLANS.monthly;
      const requestedExtraDevices = Number(body.deviceCount || 0);
      const extraDevices = Number.isFinite(requestedExtraDevices)
        ? Math.min(49, Math.max(0, Math.floor(requestedExtraDevices)))
        : 0;
      const deviceCount = 1 + extraDevices;
      const unitAmount = plan.baseCents + extraDevices * plan.extraDeviceCents;
      session = await createCheckoutSession(env, {
        mode: plan.mode,
        lineItems: [{
          name: `${plan.label} (${deviceCount} device${deviceCount === 1 ? "" : "s"})`,
          unitAmount,
          quantity: 1,
          recurringInterval: plan.interval,
        }],
        successUrl,
        cancelUrl,
        metadata: {
          kind: "new_license",
          plan: plan.label,
          license_kind: "paid",
          billing_model: plan.billingModel,
          device_count: String(deviceCount),
        },
      });
    }
    return jsonResponse({url: session.url, id: session.id});
  } catch (error) {
    return handleError(error);
  }
}

async function createCheckoutSession(env, {mode, lineItems, successUrl, cancelUrl, metadata}) {
  const body = new URLSearchParams();
  body.set("mode", mode);
  lineItems.forEach((item, index) => {
    body.set(`line_items[${index}][price_data][currency]`, "usd");
    body.set(`line_items[${index}][price_data][unit_amount]`, String(item.unitAmount));
    body.set(`line_items[${index}][price_data][product_data][name]`, item.name);
    if (item.recurringInterval) {
      body.set(`line_items[${index}][price_data][recurring][interval]`, item.recurringInterval);
    }
    body.set(`line_items[${index}][quantity]`, String(item.quantity));
  });
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
