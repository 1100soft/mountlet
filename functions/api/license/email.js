import {handleError, HttpError, jsonResponse, readJson, requireEnv} from "../../_lib/license.js";
import {checkoutEmail, sendLicenseEmail} from "../../_lib/email.js";
import {ENV_NAMES} from "../../_lib/site-config.js";
import {refreshSubscriptionLicense} from "../../_lib/stripe-subscriptions.js";

export async function onRequestPost({request, env}) {
  try {
    const body = await readJson(request);
    const sessionId = String(body.sessionId || body.session_id || "").trim();
    if (!sessionId) {
      throw new HttpError(400, "Checkout session id is required.");
    }
    const row = await env.DB.prepare(
      "SELECT p.license_key, p.quantity, p.created_at AS payment_created_at, l.* FROM payments p JOIN licenses l ON l.id = p.license_id WHERE p.stripe_session_id = ?"
    ).bind(sessionId).first();
    if (!row || !row.license_key) {
      throw new HttpError(404, "No license key is available for this checkout session.");
    }
    const session = await fetchStripeCheckoutSession(env, sessionId);
    const emailAddress = checkoutEmail(session);
    if (!emailAddress) {
      throw new HttpError(409, "Stripe did not return a customer email for this checkout session.");
    }
    const license = await refreshSubscriptionLicense(env, row);
    const result = await sendLicenseEmail(env, request.url, {
      to: emailAddress,
      licenseKey: row.license_key,
      plan: license.plan || "Mountlet License",
      billingModel: license.billing_model || "lifetime",
      maxDevices: license.max_devices || row.quantity || 0,
      expiresAt: license.expires_at || "",
    });
    if (!result.sent) {
      throw new HttpError(502, result.skipped ? "License email is not configured." : `Resend failed: ${result.error || "unknown error"}`);
    }
    return jsonResponse({ok: true, emailId: result.id || ""});
  } catch (error) {
    return handleError(error);
  }
}

async function fetchStripeCheckoutSession(env, sessionId) {
  const response = await fetch(`https://api.stripe.com/v1/checkout/sessions/${sessionId}`, {
    headers: {authorization: `Bearer ${requireEnv(env, ENV_NAMES.stripeSecretKey)}`},
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new HttpError(502, `Stripe returned non-JSON response: ${text.slice(0, 240)}`);
  }
  if (!response.ok) {
    throw new HttpError(502, String(data.error?.message || "Stripe checkout lookup failed."));
  }
  return data;
}
