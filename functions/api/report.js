const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";
const GITHUB_API_ENDPOINT = "https://api.github.com";
const MAX_FIELD_CHARS = 24_000;
const MAX_ISSUE_BODY_CHARS = 60_000;

export async function onRequestPost({request, env}) {
  let payload;
  try {
    payload = await request.json();
  } catch (_error) {
    return json({ok: false, error: "Invalid JSON."}, 400);
  }

  const report = normalizedReport(payload);
  const sinks = [];
  const failures = [];

  if (githubConfigured(env)) {
    try {
      sinks.push(await createGitHubIssue(env, report));
    } catch (error) {
      failures.push(`GitHub: ${clean(error.message || error, 500)}`);
    }
  }

  if (resendConfigured(env)) {
    try {
      sinks.push(await sendReportEmail(env, report));
    } catch (error) {
      failures.push(`Resend: ${clean(error.message || error, 500)}`);
    }
  }

  if (!githubConfigured(env) && !resendConfigured(env)) {
    return json({ok: false, error: "Bug reports are not configured."}, 503);
  }
  if (sinks.length === 0) {
    return json({ok: false, error: failures.join("\n") || "Bug report delivery failed."}, 502);
  }
  return json({
    ok: true,
    id: sinks[0]?.id || "",
    sinks,
    warning: failures.join("\n"),
  });
}

function normalizedReport(payload) {
  const kind = clean(payload.kind || "bug", 40);
  const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {};
  const logs = payload.logs && typeof payload.logs === "object" ? payload.logs : {};
  return {
    kind,
    subject: kind === "crash" ? "Mountlet crash report" : "Mountlet bug report",
    message: clean(payload.message || "", MAX_FIELD_CHARS),
    contact: clean(payload.contact || "", 240),
    metadata,
    runtimeLog: redact(clean(logs.runtime || "", MAX_FIELD_CHARS)),
    rcloneLog: redact(clean(logs.rclone || "", MAX_FIELD_CHARS)),
  };
}

function reportText(report) {
  return [
    report.subject,
    "",
    "Message:",
    report.message || "(none)",
    "",
    report.contact ? `Contact: ${report.contact}` : "Contact: (not provided)",
    "",
    "Metadata:",
    JSON.stringify(report.metadata, null, 2),
    "",
    "Runtime log:",
    report.runtimeLog || "(not included)",
    "",
    "rclone log:",
    report.rcloneLog || "(not included)",
  ].join("\n");
}

async function sendReportEmail(env, report) {
  const response = await fetch(RESEND_EMAIL_ENDPOINT, {
    method: "POST",
    headers: {
      authorization: `Bearer ${String(env.RESEND_API_KEY || "").trim()}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from: String(env.REPORT_FROM || env.RESEND_FROM || env.EMAIL_FROM || "").trim(),
      to: [String(env.REPORT_TO || env.EMAIL_REPLY_TO || env.RESEND_REPLY_TO || env.RESEND_FROM || env.EMAIL_FROM || "").trim()],
      reply_to: report.contact || undefined,
      subject: report.subject,
      text: reportText(report),
    }),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(resendErrorMessage(body));
  }
  let parsed = {};
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch (_error) {
    parsed = {};
  }
  return {kind: "email", id: parsed.id || ""};
}

async function createGitHubIssue(env, report) {
  const token = String(env.REPORT_GITHUB_TOKEN || env.GITHUB_REPORT_TOKEN || "").trim();
  const repo = normalizeRepo(env.REPORT_GITHUB_REPO || env.GITHUB_REPORT_REPO || "");
  const response = await fetch(`${GITHUB_API_ENDPOINT}/repos/${repo}/issues`, {
    method: "POST",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "user-agent": "mountlet-report-function",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({
      title: issueTitle(report),
      body: issueBody(report),
      labels: reportLabels(env, report.kind),
    }),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(githubErrorMessage(body, response.status));
  }
  let parsed = {};
  try {
    parsed = body ? JSON.parse(body) : {};
  } catch (_error) {
    parsed = {};
  }
  return {kind: "github", id: String(parsed.number || parsed.id || ""), url: parsed.html_url || ""};
}

function issueTitle(report) {
  const metadata = report.metadata || {};
  const version = clean(metadata.appVersion || "unknown", 40);
  const platform = clean(metadata.platform || "unknown platform", 80);
  return `[${report.kind}] Mountlet ${version} on ${platform}`;
}

function issueBody(report) {
  const body = [
    report.message || "_No user message provided._",
    "",
    "### Contact",
    report.contact || "_Not provided._",
    "",
    "### Metadata",
    fenced("json", JSON.stringify(report.metadata, null, 2)),
    "",
    "### Runtime Log",
    fenced("text", report.runtimeLog || "(not included)"),
    "",
    "### rclone Log",
    fenced("text", report.rcloneLog || "(not included)"),
  ].join("\n");
  return body.slice(0, MAX_ISSUE_BODY_CHARS);
}

function fenced(language, value) {
  return `\`\`\`${language}\n${String(value || "").replaceAll("```", "`\u200b``")}\n\`\`\``;
}

function githubConfigured(env) {
  return Boolean(
    String(env.REPORT_GITHUB_TOKEN || env.GITHUB_REPORT_TOKEN || "").trim()
    && normalizeRepo(env.REPORT_GITHUB_REPO || env.GITHUB_REPORT_REPO || "")
  );
}

function resendConfigured(env) {
  return Boolean(
    String(env.RESEND_API_KEY || "").trim()
    && String(env.REPORT_FROM || env.RESEND_FROM || env.EMAIL_FROM || "").trim()
    && String(env.REPORT_TO || env.EMAIL_REPLY_TO || env.RESEND_REPLY_TO || env.RESEND_FROM || env.EMAIL_FROM || "").trim()
  );
}

function normalizeRepo(value) {
  const repo = String(value || "").trim().replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "");
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) ? repo : "";
}

function reportLabels(env, kind) {
  const configured = String(env.REPORT_GITHUB_LABELS || env.GITHUB_REPORT_LABELS || "").trim();
  const labels = configured.split(",").map((label) => label.trim()).filter(Boolean);
  if (labels.length === 0) {
    return [];
  }
  if (kind === "crash" && !labels.includes("crash")) {
    labels.push("crash");
  }
  if (kind !== "crash" && !labels.includes("bug")) {
    labels.push("bug");
  }
  return labels;
}

function clean(value, limit) {
  return String(value || "").replace(/\r\n/g, "\n").slice(0, limit);
}

function redact(value) {
  return String(value || "")
    .replace(/((?:access|refresh|id)?_?token["'\s:=]+)[^\s"',}]+/gi, "$1[redacted]")
    .replace(/((?:client_)?secret["'\s:=]+)[^\s"',}]+/gi, "$1[redacted]")
    .replace(/((?:password|pass|api[_-]?key)["'\s:=]+)[^\s"',}]+/gi, "$1[redacted]")
    .replace(/(authorization:\s*bearer\s+)[^\s]+/gi, "$1[redacted]");
}

function resendErrorMessage(body) {
  try {
    const parsed = JSON.parse(body || "{}");
    return clean(parsed.message || parsed.error || body, 500);
  } catch (_error) {
    return clean(body, 500);
  }
}

function githubErrorMessage(body, status) {
  try {
    const parsed = JSON.parse(body || "{}");
    return clean(parsed.message || body || `GitHub returned ${status}.`, 500);
  } catch (_error) {
    return clean(body || `GitHub returned ${status}.`, 500);
  }
}

function json(body, status = 200) {
  return Response.json(body, {
    status,
    headers: {"cache-control": "no-store"},
  });
}
