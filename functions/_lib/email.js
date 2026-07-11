import {ENV_NAMES, pricingUrl, siteUrl} from "./site-config.js";

const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";

export async function sendLicenseEmail(env, requestUrl, options) {
  const apiKey = String(env?.[ENV_NAMES.resendApiKey] || "").trim();
  const from = String(env?.[ENV_NAMES.resendFrom] || "").trim();
  const to = String(options?.to || "").trim();
  if (!apiKey || !from || !to) {
    return {sent: false, skipped: true};
  }

  const licenseKey = String(options.licenseKey || "").trim();
  const plan = String(options.plan || "Mountlet License").trim();
  const billingModel = String(options.billingModel || "lifetime").trim();
  const devices = Number(options.maxDevices || 0);
  const expiresAt = String(options.expiresAt || "").trim();
  const appUrl = siteUrl(env, requestUrl);
  const addDevicesUrl = pricingUrl(env, requestUrl, {
    license_action: "add_devices",
    license_key: licenseKey,
  });
  const renewalText = billingModel === "lifetime" || !expiresAt
    ? ""
    : `\nRenewal: ${formatEmailTimestamp(expiresAt)}`;
  const deviceText = devices > 0 ? `${devices} device${devices === 1 ? "" : "s"}` : "your purchased devices";
  const text = [
    "Thanks for buying Mountlet.",
    "",
    `Plan: ${plan}`,
    `Devices: ${deviceText}`,
    renewalText.trim(),
    "",
    "License key:",
    licenseKey,
    "",
    "Keep this key somewhere safe. It is needed for activation and support.",
    "",
    `Add devices: ${addDevicesUrl}`,
    `Mountlet: ${appUrl}`,
  ].filter(Boolean).join("\n");
  const html = `<!doctype html>
<html>
  <body style="font-family: system-ui, -apple-system, Segoe UI, sans-serif; line-height: 1.5; color: #172033;">
    <h1 style="font-size: 20px;">Your Mountlet license</h1>
    <p>Thanks for buying Mountlet.</p>
    <p><strong>Plan:</strong> ${escapeHtml(plan)}<br>
    <strong>Devices:</strong> ${escapeHtml(deviceText)}${renewalText ? `<br><strong>Renewal:</strong> ${escapeHtml(formatEmailTimestamp(expiresAt))}` : ""}</p>
    <p style="margin: 18px 0 6px;"><strong>License key</strong></p>
    <p style="font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 16px; padding: 12px; border: 1px solid #d7dde8; background: #f8fafc;">${escapeHtml(licenseKey)}</p>
    <p>Keep this key somewhere safe. It is needed for activation and support.</p>
    <p><a href="${escapeHtml(addDevicesUrl)}">Add devices</a> · <a href="${escapeHtml(appUrl)}">Mountlet</a></p>
  </body>
</html>`;
  const payload = {
    from,
    to: [to],
    subject: "Your Mountlet license key",
    html,
    text,
  };
  const replyTo = String(env?.[ENV_NAMES.resendReplyTo] || "").trim();
  if (replyTo) {
    payload.reply_to = replyTo;
  }
  const response = await fetch(RESEND_EMAIL_ENDPOINT, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const body = await response.text();
  if (!response.ok) {
    return {sent: false, skipped: false, error: body.slice(0, 300)};
  }
  let parsed = {};
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch (_error) {
    parsed = {};
  }
  return {sent: true, id: parsed.id || ""};
}

export function checkoutEmail(session) {
  return String(
    session?.customer_details?.email
    || session?.customer_email
    || ""
  ).trim();
}

function formatEmailTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toUTCString();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}
