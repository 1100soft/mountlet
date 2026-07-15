import {handleError, jsonResponse} from "../_lib/license.js";
import {updateReportByGitHubIssue} from "../_lib/reports.js";

export async function onRequestPost({request, env}) {
  try {
    const rawBody = await request.text();
    if (!(await verifyGitHubSignature(rawBody, request.headers.get("x-hub-signature-256") || "", env))) {
      return jsonResponse({error: "Invalid GitHub signature."}, 401);
    }
    if (request.headers.get("x-github-event") !== "issues") {
      return jsonResponse({ok: true, ignored: true});
    }
    const payload = JSON.parse(rawBody || "{}");
    const issue = payload.issue || {};
    const issueNumber = Number(issue.number || 0);
    if (!issueNumber) {
      return jsonResponse({ok: true, ignored: true});
    }
    const status = statusFromGitHubIssue(payload.action, issue.state);
    if (!status) {
      return jsonResponse({ok: true, ignored: true});
    }
    const changed = await updateReportByGitHubIssue(env, issueNumber, {
      status,
      githubIssueUrl: issue.html_url || "",
    });
    return jsonResponse({ok: true, issueNumber, status, changed});
  } catch (error) {
    return handleError(error);
  }
}

function statusFromGitHubIssue(action, state) {
  if (state === "closed" || action === "closed") {
    return "resolved";
  }
  if (state === "open" || action === "reopened") {
    return "open";
  }
  return "";
}

async function verifyGitHubSignature(rawBody, signatureHeader, env) {
  const secret = String(env.REPORT_GITHUB_WEBHOOK_SECRET || "").trim();
  if (!secret) {
    return false;
  }
  const expected = await hmacSha256Hex(secret, rawBody);
  const provided = String(signatureHeader || "").replace(/^sha256=/, "").trim();
  return timingSafeEqual(expected, provided);
}

async function hmacSha256Hex(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    {name: "HMAC", hash: "SHA-256"},
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return Array.from(new Uint8Array(signature), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(left, right) {
  if (!left || !right || left.length !== right.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < left.length; index += 1) {
    diff |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return diff === 0;
}
