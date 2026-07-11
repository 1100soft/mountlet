export async function onRequestGet({env}) {
  const body = {
    ok: true,
    functions: true,
    dbBound: Boolean(env.DB),
    downloadsBound: Boolean(env.DOWNLOADS),
    stripeConfigured: Boolean(env.STRIPE_SECRET_KEY),
    stripeMode: stripeMode(env.STRIPE_SECRET_KEY),
    resendConfigured: Boolean(env.RESEND_API_KEY && (env.RESEND_FROM || env.EMAIL_FROM)),
  };
  return Response.json(body, {
    headers: {
      "cache-control": "no-store",
    },
  });
}

function stripeMode(value) {
  const key = String(value || "");
  if (key.startsWith("sk_live_")) {
    return "live";
  }
  if (key.startsWith("sk_test_")) {
    return "test";
  }
  return "";
}
