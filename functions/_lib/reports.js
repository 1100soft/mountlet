import {HttpError, jsonResponse, readJson} from "./license.js";

const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";
const GITHUB_API_ENDPOINT = "https://api.github.com";
const MAX_FIELD_CHARS = 24_000;
const MAX_ISSUE_BODY_CHARS = 60_000;

export {jsonResponse, readJson};

export function normalizedReport(payload) {
  const requestedKind = clean(payload.kind || "bug", 40).toLowerCase();
  const kind = ["bug", "crash", "support"].includes(requestedKind) ? requestedKind : "support";
  const metadata = normalizedMetadata(payload.metadata);
  const logs = payload.logs && typeof payload.logs === "object" ? payload.logs : {};
  return {
    kind,
    subject: {
      crash: "Mountlet crash report",
      bug: "Mountlet bug report",
      support: "Mountlet support request",
    }[kind],
    message: clean(payload.message || "", MAX_FIELD_CHARS),
    contact: clean(payload.contact || "", 240),
    metadata,
    runtimeLog: redact(clean(logs.runtime || "", MAX_FIELD_CHARS)),
    rcloneLog: redact(clean(logs.rclone || "", MAX_FIELD_CHARS)),
  };
}

function normalizedMetadata(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const result = {};
  for (const [rawKey, rawValue] of Object.entries(value).slice(0, 24)) {
    const key = clean(rawKey, 80);
    if (!key) {
      continue;
    }
    if (rawValue === null || ["boolean", "number"].includes(typeof rawValue)) {
      result[key] = rawValue;
    } else if (typeof rawValue === "string") {
      result[key] = clean(rawValue, 1000);
    } else {
      result[key] = clean(JSON.stringify(rawValue), 1000);
    }
  }
  return result;
}

export async function createReportIssue(env, report) {
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
  if (report.kind === "support") {
    const category = clean(metadata.category || "request", 40);
    return `[support] Mountlet ${category}`;
  }
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
  } else if (kind === "bug" && !labels.includes("bug")) {
    labels.push("bug");
  } else if (kind === "support" && !labels.includes("support")) {
    labels.push("support");
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
