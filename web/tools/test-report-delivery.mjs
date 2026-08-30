import assert from "node:assert/strict";
import {onRequestPost} from "../../functions/api/report.js";

const originalFetch = globalThis.fetch;

function request(kind = "bug") {
  return new Request("https://mountlet.app/api/report", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({kind, message: "A sufficiently detailed test report."}),
  });
}

const env = {
  REPORT_GITHUB_TOKEN: "test-token",
  REPORT_GITHUB_REPO: "owner/repo",
  RESEND_API_KEY: "email-token",
  REPORT_FROM: "reports@example.com",
  REPORT_TO: "support@example.com",
};

try {
  globalThis.fetch = async (url) => String(url).includes("api.github.com")
    ? new Response(JSON.stringify({message: "repository unavailable"}), {status: 500})
    : new Response(JSON.stringify({id: "email-1"}), {status: 200});
  const failed = await onRequestPost({request: request(), env});
  assert.equal(failed.status, 502);
  assert.equal((await failed.json()).ok, false, "email delivery must not conceal a missing GitHub issue");

  globalThis.fetch = async (url) => String(url).includes("api.github.com")
    ? new Response(JSON.stringify({number: 42, html_url: "https://github.com/owner/repo/issues/42"}), {status: 201})
    : new Response(JSON.stringify({id: "email-2"}), {status: 200});
  const delivered = await onRequestPost({request: request(), env});
  const body = await delivered.json();
  assert.equal(delivered.status, 200);
  assert.equal(body.issueUrl, "https://github.com/owner/repo/issues/42");
  assert.equal(body.id, "42");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("Report delivery requires and returns a GitHub issue for bug and crash reports.");
