import {HttpError, nowIso, requireEnv} from "./license.js";
import {ENV_NAMES} from "./site-config.js";

const ALLOWED_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"]);

export async function refreshSubscriptionLicense(env, license) {
  if (!license?.stripe_subscription_id || license.billing_model === "lifetime") {
    return license;
  }
  const subscription = await fetchStripeSubscription(env, license.stripe_subscription_id);
  const status = String(subscription.status || license.subscription_status || "");
  const expiresAt = subscriptionPeriodEnd(subscription) || String(license.expires_at || "");
  const now = nowIso();
  await env.DB.prepare(
    "UPDATE licenses SET subscription_status = ?, expires_at = CASE WHEN ? != '' THEN ? ELSE expires_at END, updated_at = ? WHERE id = ?"
  ).bind(status, expiresAt, expiresAt, now, license.id).run();
  return {
    ...license,
    subscription_status: status,
    expires_at: expiresAt,
    updated_at: now,
  };
}

export function assertLicenseUsable(license) {
  if (!license || license.status !== "active") {
    throw new HttpError(404, "License not found or inactive.");
  }
  const expiresAt = String(license.expires_at || "");
  if (expiresAt && Date.parse(expiresAt) <= Date.now()) {
    throw new HttpError(404, "License not found or inactive.");
  }
  if (license.stripe_subscription_id) {
    const status = String(license.subscription_status || "");
    if (status && !ALLOWED_SUBSCRIPTION_STATUSES.has(status)) {
      throw new HttpError(404, "License not found or inactive.");
    }
  }
}

export async function fetchStripeSubscription(env, subscriptionId) {
  if (!subscriptionId) {
    throw new HttpError(409, "This subscription is missing its Stripe subscription id.");
  }
  const response = await fetch(
    `https://api.stripe.com/v1/subscriptions/${subscriptionId}?expand[]=items.data.price&expand[]=items.data.price.product`,
    {headers: {authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`}}
  );
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new HttpError(502, `Stripe returned non-JSON response: ${text.slice(0, 240)}`);
  }
  if (!response.ok) {
    throw new HttpError(502, String(data.error?.message || "Stripe subscription lookup failed."));
  }
  return data;
}

export function subscriptionPeriodEnd(subscription) {
  const itemPeriodEnd = Number(subscription?.items?.data?.[0]?.current_period_end || 0);
  const value = Number(subscription?.current_period_end || 0) || itemPeriodEnd;
  return value > 0 ? new Date(value * 1000).toISOString() : "";
}
