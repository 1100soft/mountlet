import {
  generateLicenseKey,
  handleError,
  jsonResponse,
  licenseKeyHash,
  nowIso,
  randomId,
  requireEnv,
  sha256Hex
} from "../../_lib/license.js";

export async function onRequestPost({request, env}) {
  try {
    const rawBody = await request.text();
    if (!(await verifyStripeSignature(rawBody, request.headers.get("stripe-signature") || "", env))) {
      return jsonResponse({error: "Invalid Stripe signature."}, 401);
    }
    const event = JSON.parse(rawBody);
    if (event.type !== "checkout.session.completed") {
      return jsonResponse({ok: true, ignored: true});
    }
    const session = event.data.object;
    const quantity = await checkoutQuantity(env, session);
    const now = nowIso();
    const metadata = session.metadata || {};
    const existingPayment = await env.DB.prepare(
      "SELECT id FROM payments WHERE stripe_session_id = ?"
    ).bind(session.id).first();
    if (existingPayment) {
      return jsonResponse({ok: true, duplicate: true});
    }
    if (metadata.kind === "add_devices" && metadata.license_id) {
      await env.DB.prepare("UPDATE licenses SET max_devices = max_devices + ?, updated_at = ? WHERE id = ?")
        .bind(quantity, now, metadata.license_id)
        .run();
      await env.DB.prepare(
        "INSERT INTO payments (id, stripe_session_id, stripe_customer_id, license_id, kind, quantity, license_key, created_at) VALUES (?, ?, ?, ?, 'add_devices', ?, '', ?)"
      ).bind(
        randomId("pay"),
        session.id,
        String(session.customer || ""),
        metadata.license_id,
        quantity,
        now
      ).run();
      return jsonResponse({ok: true, kind: "add_devices"});
    }

    const licenseKey = generateLicenseKey();
    const licenseId = randomId("lic");
    const plan = String(metadata.plan || "Mountlet License");
    const licenseKind = String(metadata.license_kind || "paid");
    const maxDevices = Math.max(1, quantity);
    await env.DB.prepare(
      "INSERT INTO licenses (id, license_key_hash, status, plan, license_kind, max_devices, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?, ?, ?)"
    ).bind(
      licenseId,
      await licenseKeyHash(env, licenseKey),
      plan,
      licenseKind,
      maxDevices,
      now,
      now
    ).run();
    await env.DB.prepare(
      "INSERT INTO payments (id, stripe_session_id, stripe_customer_id, license_id, kind, quantity, license_key, created_at) VALUES (?, ?, ?, ?, 'new_license', ?, ?, ?)"
    ).bind(
      randomId("pay"),
      session.id,
      String(session.customer || ""),
      licenseId,
      maxDevices,
      licenseKey,
      now
    ).run();
    return jsonResponse({ok: true});
  } catch (error) {
    return handleError(error);
  }
}

async function checkoutQuantity(env, session) {
  const metadataQuantity = Number(session.metadata?.device_count || 0);
  if (Number.isFinite(metadataQuantity) && metadataQuantity > 0) {
    return Math.floor(metadataQuantity);
  }
  const secret = env.STRIPE_SECRET_KEY;
  if (secret && session.id) {
    const response = await fetch(`https://api.stripe.com/v1/checkout/sessions/${session.id}/line_items?limit=10`, {
      headers: {authorization: `Bearer ${secret}`}
    });
    if (response.ok) {
      const data = await response.json();
      const quantity = Number((data.data || []).reduce((total, item) => total + Number(item.quantity || 0), 0));
      if (quantity > 0) {
        return quantity;
      }
    }
  }
  return 3;
}

async function verifyStripeSignature(rawBody, header, env) {
  const secret = requireEnv(env, "STRIPE_WEBHOOK_SECRET");
  const values = Object.fromEntries(
    header.split(",").map((part) => {
      const [key, value] = part.split("=", 2);
      return [key, value];
    })
  );
  if (!values.t || !values.v1) {
    return false;
  }
  const timestamp = Number(values.t);
  if (!Number.isFinite(timestamp) || Math.abs(Date.now() / 1000 - timestamp) > 300) {
    return false;
  }
  const signedPayload = `${values.t}.${rawBody}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    {name: "HMAC", hash: "SHA-256"},
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedPayload));
  const expected = Array.from(new Uint8Array(signature), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return await timingSafeEqual(expected, values.v1);
}

async function timingSafeEqual(left, right) {
  if (left.length !== right.length) {
    await sha256Hex(`${left}:${right}`);
    return false;
  }
  let result = 0;
  for (let index = 0; index < left.length; index += 1) {
    result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return result === 0;
}
