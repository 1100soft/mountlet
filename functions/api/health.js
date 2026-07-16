import {inspectLicenseSchema} from "../_lib/license-schema.js";
import {signLicenseToken, verifyLicenseToken} from "../_lib/license.js";

export async function onRequestGet({env}) {
  const github = githubConfig(env);
  const email = emailConfigured(env);
  const licenseDb = await inspectLicenseSchema(env);
  const licenseSigning = await licenseSigningStatus(env);
  const body = {
    ok: true,
    functions: true,
    dbBound: Boolean(env.DB),
    licenseDb,
    licenseKeyPepperConfigured: Boolean(env.LICENSE_KEY_PEPPER),
    licenseSigningConfigured: Boolean(env.LICENSE_SIGNING_PRIVATE_KEY && env.LICENSE_SIGNING_PUBLIC_KEY),
    licenseSigning,
    licenseAdminConfigured: Boolean(env.LICENSE_ADMIN_TOKEN),
    noticesConfigured: Boolean(env.MOUNTLET_NOTICES_JSON || env.NOTICES_JSON),
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

async function licenseSigningStatus(env) {
  if (!env.LICENSE_SIGNING_PRIVATE_KEY || !env.LICENSE_SIGNING_PUBLIC_KEY) {
    return {ok: false, error: "License signing keys are missing."};
  }
  try {
    const token = await signLicenseToken(env, {
      licenseId: "health",
      deviceId: "health",
      plan: "Health check",
      licenseKind: "health",
      maxDevices: 0,
      issuedAt: new Date().toISOString(),
      expiresAt: "",
    });
    const payload = await verifyLicenseToken(env, token);
    return {ok: payload.licenseId === "health"};
  } catch (error) {
    return {
      ok: false,
      error: String(error?.message || error || "License signing check failed."),
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
