export async function onRequestGet({env}) {
  const github = githubConfig(env);
  const email = emailConfigured(env);
  const licenseDb = await licenseDbStatus(env);
  const body = {
    ok: true,
    functions: true,
    dbBound: Boolean(env.DB),
    licenseDb,
    downloadsBound: Boolean(env.DOWNLOADS),
    stripeConfigured: Boolean(env.STRIPE_SECRET_KEY),
    stripeMode: stripeMode(env.STRIPE_SECRET_KEY),
    resendConfigured: Boolean(env.RESEND_API_KEY && (env.RESEND_FROM || env.EMAIL_FROM)),
    reportsConfigured: github.enabled || email,
    reportSinks: {
      github: github.enabled,
      githubNeedsAttention: github.present && !github.enabled,
      githubDiagnostic: {
        tokenPresent: github.tokenPresent,
        repoPresent: github.repoPresent,
        repoFormatValid: github.repoValid,
      },
      email,
    },
  };
  return Response.json(body, {
    headers: {
      "cache-control": "no-store",
    },
  });
}

async function licenseDbStatus(env) {
  if (!env.DB) {
    return {ok: false, error: "DB binding is missing."};
  }
  const required = {
    licenses: [
      "id",
      "license_key_hash",
      "status",
      "plan",
      "license_kind",
      "billing_model",
      "max_devices",
      "stripe_subscription_id",
      "subscription_status",
      "expires_at",
      "created_at",
      "updated_at",
    ],
    devices: [
      "id",
      "license_id",
      "device_hash",
      "device_label",
      "platform",
      "app_version",
      "activated_at",
      "last_seen_at",
      "deactivated_at",
    ],
    payments: [
      "id",
      "stripe_session_id",
      "stripe_customer_id",
      "license_id",
      "kind",
      "quantity",
      "license_key",
      "created_at",
    ],
  };
  try {
    const tables = {};
    for (const [table, columns] of Object.entries(required)) {
      const info = await env.DB.prepare(`PRAGMA table_info(${table})`).all();
      const names = new Set((info.results || []).map((row) => String(row.name || "")));
      const missing = columns.filter((column) => !names.has(column));
      tables[table] = {
        ok: missing.length === 0 && names.size > 0,
        missing,
      };
    }
    return {
      ok: Object.values(tables).every((table) => table.ok),
      tables,
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error?.message || error || "Could not inspect license database."),
    };
  }
}

function githubConfig(env) {
  const token = String(env.REPORT_GITHUB_TOKEN || env.GITHUB_REPORT_TOKEN || "").trim();
  const repo = String(env.REPORT_GITHUB_REPO || env.GITHUB_REPORT_REPO || "").trim();
  const repoValid = Boolean(normalizeRepo(repo));
  return {
    present: Boolean(token || repo),
    enabled: Boolean(token && repoValid),
    tokenPresent: Boolean(token),
    repoPresent: Boolean(repo),
    repoValid,
  };
}

function emailConfigured(env) {
  return Boolean(
    env.RESEND_API_KEY
    && (env.REPORT_FROM || env.RESEND_FROM || env.EMAIL_FROM)
    && (env.REPORT_TO || env.EMAIL_REPLY_TO || env.RESEND_REPLY_TO || env.RESEND_FROM || env.EMAIL_FROM)
  );
}

function normalizeRepo(value) {
  const repo = String(value || "").trim().replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "");
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) ? repo : "";
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
