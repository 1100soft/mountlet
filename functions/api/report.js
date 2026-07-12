const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";
const MAX_FIELD_CHARS = 24_000;

export async function onRequestPost({request, env}) {
  let payload;
  try {
    payload = await request.json();
  } catch (_error) {
    return json({ok: false, error: "Invalid JSON."}, 400);
  }
  const apiKey = String(env.RESEND_API_KEY || "").trim();
  const from = String(env.REPORT_FROM || env.RESEND_FROM || env.EMAIL_FROM || "").trim();
  const to = String(env.REPORT_TO || env.EMAIL_REPLY_TO || env.RESEND_REPLY_TO || env.RESEND_FROM || env.EMAIL_FROM || "").trim();
  if (!apiKey || !from || !to) {
    return json({ok: false, error: "Bug reports are not configured."}, 503);
  }
  const kind = clean(payload.kind || "bug", 40);
  const message = clean(payload.message || "", MAX_FIELD_CHARS);
  const contact = clean(payload.contact || "", 240);
  const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {};
  const logs = payload.logs && typeof payload.logs === "object" ? payload.logs : {};
  const subject = kind === "crash" ? "Mountlet crash report" : "Mountlet bug report";
  const text = [
    subject,
    "",
    "Message:",
    message || "(none)",
    "",
    contact ? `Contact: ${contact}` : "Contact: (not provided)",
    "",
    "Metadata:",
    JSON.stringify(metadata, null, 2),
    "",
    "Runtime log:",
    clean(logs.runtime || "", MAX_FIELD_CHARS) || "(not included)",
    "",
    "rclone log:",
    clean(logs.rclone || "", MAX_FIELD_CHARS) || "(not included)",
  ].join("\n");
  const response = await fetch(RESEND_EMAIL_ENDPOINT, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from,
      to: [to],
      reply_to: contact || undefined,
      subject,
      text,
    }),
  });
  const body = await response.text();
  if (!response.ok) {
    return json({ok: false, error: body.slice(0, 500)}, 502);
  }
  let parsed = {};
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch (_error) {
    parsed = {};
  }
  return json({ok: true, id: parsed.id || ""});
}

function clean(value, limit) {
  return String(value || "").replace(/\r\n/g, "\n").slice(0, limit);
}

function json(body, status = 200) {
  return Response.json(body, {
    status,
    headers: {"cache-control": "no-store"},
  });
}
