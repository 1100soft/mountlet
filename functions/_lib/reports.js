import {HttpError, jsonResponse, nowIso, randomId, readJson, requireEnv} from "./license.js";

const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";
const GITHUB_API_ENDPOINT = "https://api.github.com";
const MAX_FIELD_CHARS = 24_000;
const MAX_ISSUE_BODY_CHARS = 60_000;

export {jsonResponse, readJson};

export async function requireReportAdmin(request, env) {
  const expected = String(env.REPORT_ADMIN_TOKEN || env.LICENSE_ADMIN_TOKEN || "").trim();
  if (!expected) {
    throw new HttpError(500, "Missing REPORT_ADMIN_TOKEN.");
  }
  const provided = String(request.headers.get("authorization") || "").trim();
  if (provided !== `Bearer ${expected}`) {
    throw new HttpError(401, "Unauthorized.");
  }
}

export async function ensureReportSchema(env) {
  if (!env.DB) {
    throw new HttpError(500, "Missing DB binding.");
  }
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS reports (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      subject TEXT NOT NULL,
      message TEXT NOT NULL,
      contact TEXT NOT NULL,
      metadata_json TEXT NOT NULL,
      runtime_log TEXT NOT NULL,
      rclone_log TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      github_issue_number INTEGER,
      github_issue_url TEXT NOT NULL DEFAULT '',
      github_sync_status TEXT NOT NULL DEFAULT '',
      github_sync_error TEXT NOT NULL DEFAULT '',
      email_id TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
  `).run();
  await env.DB.prepare("CREATE INDEX IF NOT EXISTS idx_reports_status_created ON reports(status, created_at)").run();
  await env.DB.prepare("CREATE INDEX IF NOT EXISTS idx_reports_github_issue ON reports(github_issue_number)").run();
}

export function normalizedReport(payload) {
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

export async function storeReport(env, report) {
  await ensureReportSchema(env);
  const now = nowIso();
  const id = randomId("rpt");
  await env.DB.prepare(`
    INSERT INTO reports (
      id, kind, subject, message, contact, metadata_json, runtime_log, rclone_log,
      status, github_sync_status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', 'pending', ?, ?)
  `).bind(
    id,
    report.kind,
    report.subject,
    report.message,
    report.contact,
    JSON.stringify(report.metadata || {}),
    report.runtimeLog,
    report.rcloneLog,
    now,
    now,
  ).run();
  return id;
}

export async function setReportGitHubMirror(env, id, sink) {
  await env.DB.prepare(`
    UPDATE reports
    SET github_issue_number = ?, github_issue_url = ?, github_sync_status = 'mirrored',
        github_sync_error = '', updated_at = ?
    WHERE id = ?
  `).bind(Number(sink.id || 0) || null, sink.url || "", nowIso(), id).run();
}

export async function setReportGitHubError(env, id, error) {
  await env.DB.prepare(`
    UPDATE reports
    SET github_sync_status = 'error', github_sync_error = ?, updated_at = ?
    WHERE id = ?
  `).bind(clean(error, 500), nowIso(), id).run();
}

export async function setReportEmailMirror(env, id, sink) {
  await env.DB.prepare("UPDATE reports SET email_id = ?, updated_at = ? WHERE id = ?")
    .bind(sink.id || "", nowIso(), id)
    .run();
}

export async function listReports(env, request) {
  await ensureReportSchema(env);
  const url = new URL(request.url);
  const status = String(url.searchParams.get("status") || "open").trim();
  const limit = Math.max(1, Math.min(100, Number(url.searchParams.get("limit") || 50)));
  const rows = status === "all"
    ? await env.DB.prepare(`
      SELECT id, kind, subject, status, contact, metadata_json, github_issue_number,
             github_issue_url, github_sync_status, github_sync_error, email_id, created_at, updated_at
      FROM reports
      ORDER BY created_at DESC
      LIMIT ?
    `).bind(limit).all()
    : await env.DB.prepare(`
      SELECT id, kind, subject, status, contact, metadata_json, github_issue_number,
             github_issue_url, github_sync_status, github_sync_error, email_id, created_at, updated_at
      FROM reports
      WHERE status = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).bind(status, limit).all();
  return (rows.results || []).map(reportSummaryFromRow);
}

export async function loadReport(env, id) {
  await ensureReportSchema(env);
  const row = await env.DB.prepare("SELECT * FROM reports WHERE id = ?").bind(id).first();
  if (!row) {
    throw new HttpError(404, "Report not found.");
  }
  return reportFromRow(row);
}

export async function updateReport(env, id, updates) {
  const current = await loadReport(env, id);
  const allowedStatuses = new Set(["open", "triaged", "resolved", "closed", "deleted"]);
  const status = updates.status === undefined ? current.status : String(updates.status || "").trim().toLowerCase();
  if (!allowedStatuses.has(status)) {
    throw new HttpError(400, "Invalid report status.");
  }
  await env.DB.prepare("UPDATE reports SET status = ?, updated_at = ? WHERE id = ?")
    .bind(status, nowIso(), id)
    .run();
  return loadReport(env, id);
}

export async function deleteReport(env, id) {
  const report = await loadReport(env, id);
  await env.DB.prepare("DELETE FROM reports WHERE id = ?").bind(id).run();
  return report;
}

export async function mirrorReportToGitHub(env, report) {
  const githubState = githubConfig(env);
  if (!githubState.enabled) {
    throw new HttpError(409, githubConfigurationError(githubState));
  }
  const labels = reportLabels(env, report.kind);
  let response = await postGitHubIssue(githubState.token, githubState.repo, report, labels);
  if (response.status === 422 && labels.length > 0) {
    response = await postGitHubIssue(githubState.token, githubState.repo, report, []);
  }
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

export async function updateGitHubIssueState(env, issueNumber, state) {
  const githubState = githubConfig(env);
  if (!githubState.enabled) {
    throw new HttpError(409, githubConfigurationError(githubState));
  }
  const response = await fetch(`${GITHUB_API_ENDPOINT}/repos/${githubState.repo}/issues/${issueNumber}`, {
    method: "PATCH",
    headers: githubHeaders(githubState.token),
    body: JSON.stringify({state}),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(githubErrorMessage(body, response.status));
  }
  return body ? JSON.parse(body) : {};
}

export async function commentOnGitHubIssue(env, issueNumber, comment) {
  const githubState = githubConfig(env);
  if (!githubState.enabled) {
    throw new HttpError(409, githubConfigurationError(githubState));
  }
  const response = await fetch(`${GITHUB_API_ENDPOINT}/repos/${githubState.repo}/issues/${issueNumber}/comments`, {
    method: "POST",
    headers: githubHeaders(githubState.token),
    body: JSON.stringify({body: clean(comment, 4_000)}),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(githubErrorMessage(body, response.status));
  }
  return body ? JSON.parse(body) : {};
}

export async function sendReportEmail(env, report) {
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

export function resendConfigured(env) {
  return Boolean(
    String(env.RESEND_API_KEY || "").trim()
    && String(env.REPORT_FROM || env.RESEND_FROM || env.EMAIL_FROM || "").trim()
    && String(env.REPORT_TO || env.EMAIL_REPLY_TO || env.RESEND_REPLY_TO || env.RESEND_FROM || env.EMAIL_FROM || "").trim()
  );
}

export function githubConfig(env) {
  const token = String(env.REPORT_GITHUB_TOKEN || env.GITHUB_REPORT_TOKEN || "").trim();
  const repo = normalizeRepo(env.REPORT_GITHUB_REPO || env.GITHUB_REPORT_REPO || "");
  const repoRaw = String(env.REPORT_GITHUB_REPO || env.GITHUB_REPORT_REPO || "").trim();
  return {
    present: Boolean(token || repoRaw),
    enabled: Boolean(token && repo),
    tokenPresent: Boolean(token),
    repoPresent: Boolean(repoRaw),
    repoValid: Boolean(repo),
    token,
    repo,
  };
}

export function githubConfigurationError(state) {
  const problems = [];
  if (!state.tokenPresent) {
    problems.push("REPORT_GITHUB_TOKEN is missing");
  }
  if (!state.repoPresent) {
    problems.push("REPORT_GITHUB_REPO is missing");
  } else if (!state.repoValid) {
    problems.push("REPORT_GITHUB_REPO must be in owner/repo format");
  }
  return `GitHub: ${problems.join("; ")}.`;
}

export function reportText(report) {
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

export function reportFromRow(row) {
  return {
    id: row.id,
    kind: row.kind,
    subject: row.subject,
    message: row.message,
    contact: row.contact,
    metadata: parseJson(row.metadata_json, {}),
    runtimeLog: row.runtime_log,
    rcloneLog: row.rclone_log,
    status: row.status,
    githubIssueNumber: row.github_issue_number || null,
    githubIssueUrl: row.github_issue_url || "",
    githubSyncStatus: row.github_sync_status || "",
    githubSyncError: row.github_sync_error || "",
    emailId: row.email_id || "",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function reportSummaryFromRow(row) {
  const metadata = parseJson(row.metadata_json, {});
  return {
    id: row.id,
    kind: row.kind,
    subject: row.subject,
    status: row.status,
    contact: row.contact,
    appVersion: metadata.appVersion || "",
    platform: metadata.platform || "",
    githubIssueNumber: row.github_issue_number || null,
    githubIssueUrl: row.github_issue_url || "",
    githubSyncStatus: row.github_sync_status || "",
    githubSyncError: row.github_sync_error || "",
    emailId: row.email_id || "",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function postGitHubIssue(token, repo, report, labels) {
  return fetch(`${GITHUB_API_ENDPOINT}/repos/${repo}/issues`, {
    method: "POST",
    headers: githubHeaders(token),
    body: JSON.stringify({
      title: issueTitle(report),
      body: issueBody(report),
      labels,
    }),
  });
}

function githubHeaders(token) {
  return {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
    "user-agent": "mountlet-report-function",
    "x-github-api-version": "2022-11-28",
  };
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

function normalizeRepo(value) {
  const repo = String(value || "").trim().replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "");
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) ? repo : "";
}

function fenced(language, value) {
  return `\`\`\`${language}\n${String(value || "").replaceAll("```", "`\u200b``")}\n\`\`\``;
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
    return clean(parsed.message || body, 500) || `GitHub returned ${status}.`;
  } catch (_error) {
    return clean(body, 500) || `GitHub returned ${status}.`;
  }
}

function parseJson(value, fallback) {
  try {
    return JSON.parse(value || "");
  } catch (_error) {
    return fallback;
  }
}
