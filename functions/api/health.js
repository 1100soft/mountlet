export async function onRequestGet({env}) {
  const github = githubConfig(env);
  const email = emailConfigured(env);
  const body = {
    ok: true,
    functions: true,
    dbBound: Boolean(env.DB),
    downloadsBound: Boolean(env.DOWNLOADS),
    stripeConfigured: Boolean(env.STRIPE_SECRET_KEY),
    stripeMode: stripeMode(env.STRIPE_SECRET_KEY),
    resendConfigured: Boolean(env.RESEND_API_KEY && (env.RESEND_FROM || env.EMAIL_FROM)),
    reportsConfigured: github.enabled || email,
    reportStoreConfigured: Boolean(env.DB),
    reportAdminConfigured: Boolean(env.REPORT_ADMIN_TOKEN || env.LICENSE_ADMIN_TOKEN),
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
