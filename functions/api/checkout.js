import {handleError, HttpError, jsonResponse, loadActiveLicenseByKey, readJson, requireEnv} from "../_lib/license.js";
import {ENV_NAMES} from "../_lib/site-config.js";

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
      const requestedDevices = Number(body.deviceCount || 1);
      const deviceCount = Number.isFinite(requestedDevices)
        ? Math.min(50, Math.max(1, Math.floor(requestedDevices)))
        : 1;
      if (license.billing_model && license.billing_model !== "lifetime") {
        const updated = await addSubscriptionDevices(env, license, deviceCount);
        return jsonResponse({
          ok: true,
          kind: "add_devices",
          billingModel: license.billing_model,
          addedDevices: deviceCount,
          devices: updated.maxDevices,
          usedDevices: updated.usedDevices,
          expiresAt: updated.expiresAt,
          message: "Subscription device slots were updated with prorated billing.",
        });
      }
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
      authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`,
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

async function addSubscriptionDevices(env, license, deviceCount) {
  const plan = PLANS[license.billing_model] || PLANS.monthly;
  if (!license.stripe_subscription_id) {
    throw new HttpError(409, "This subscription is missing its Stripe subscription id.");
  }
  const subscription = await fetchStripeSubscription(env, license.stripe_subscription_id);
  const item = subscription.items?.data?.[0];
  const rawProduct = item?.price?.product;
  const productId = typeof rawProduct === "string" ? rawProduct : rawProduct?.id;
  if (!item?.id || !productId) {
    throw new HttpError(409, "This subscription cannot be updated automatically.");
  }
  await ensureStripeProductActive(env, productId);
  const maxDevices = Math.min(50, Number(license.max_devices || 1) + deviceCount);
  const extraDevices = Math.max(0, maxDevices - 1);
  const unitAmount = plan.baseCents + extraDevices * plan.extraDeviceCents;
  const updatedItem = await updateStripeSubscriptionItem(env, item.id, {
    productId,
    interval: plan.interval,
    unitAmount,
  });
  const refreshedSubscription = await fetchStripeSubscription(env, license.stripe_subscription_id);
  const expiresAt = subscriptionPeriodEnd(refreshedSubscription);
  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE licenses SET max_devices = ?, expires_at = CASE WHEN ? != '' THEN ? ELSE expires_at END, subscription_status = ?, updated_at = ? WHERE id = ?"
  ).bind(
    maxDevices,
    expiresAt,
    expiresAt,
    String(refreshedSubscription.status || subscription.status || ""),
    now,
    license.id
  ).run();
  await env.DB.prepare(
    "INSERT INTO payments (id, stripe_session_id, stripe_customer_id, license_id, kind, quantity, license_key, created_at) VALUES (?, ?, ?, ?, 'add_devices_subscription', ?, '', ?)"
  ).bind(
    `pay_${crypto.randomUUID().replace(/-/g, "")}`,
    `sub_update_${updatedItem.id}_${Date.now()}`,
    String(subscription.customer || ""),
    license.id,
    deviceCount,
    now
  ).run();
  const usedRow = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM devices WHERE license_id = ? AND deactivated_at IS NULL"
  ).bind(license.id).first();
  return {
    maxDevices,
    usedDevices: Number(usedRow?.count || 0),
    expiresAt,
  };
}

async function fetchStripeSubscription(env, subscriptionId) {
  const response = await fetch(
    `https://api.stripe.com/v1/subscriptions/${subscriptionId}?expand[]=items.data.price&expand[]=items.data.price.product`,
    {headers: {authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`}}
  );
  return await parseStripeResponse(response, "Stripe subscription lookup failed.");
}

async function updateStripeSubscriptionItem(env, itemId, {productId, interval, unitAmount}) {
  const body = new URLSearchParams();
  body.set("price_data[currency]", "usd");
  body.set("price_data[product]", productId);
  body.set("price_data[unit_amount]", String(unitAmount));
  body.set("price_data[recurring][interval]", interval);
  body.set("quantity", "1");
  body.set("proration_behavior", "always_invoice");
  body.set("payment_behavior", "error_if_incomplete");
  const response = await fetch(`https://api.stripe.com/v1/subscription_items/${itemId}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });
  return await parseStripeResponse(response, "Stripe subscription update failed.");
}

async function ensureStripeProductActive(env, productId) {
  const product = await fetchStripeProduct(env, productId);
  if (product.active !== false) {
    return;
  }
  const body = new URLSearchParams();
  body.set("active", "true");
  const response = await fetch(`https://api.stripe.com/v1/products/${productId}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`,
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
  });
  await parseStripeResponse(response, "Stripe product update failed.");
}

async function fetchStripeProduct(env, productId) {
  const response = await fetch(`https://api.stripe.com/v1/products/${productId}`, {
    headers: {authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`},
  });
  return await parseStripeResponse(response, "Stripe product lookup failed.");
}

function subscriptionPeriodEnd(subscription) {
  const itemPeriodEnd = Number(subscription?.items?.data?.[0]?.current_period_end || 0);
  const value = Number(subscription?.current_period_end || 0) || itemPeriodEnd;
  return value > 0 ? new Date(value * 1000).toISOString() : "";
}

async function parseStripeResponse(response, fallbackMessage) {
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new HttpError(502, `Stripe returned non-JSON response: ${text.slice(0, 240)}`);
  }
  if (!response.ok) {
    throw new HttpError(502, String(data.error?.message || fallbackMessage));
  }
  return data;
}
