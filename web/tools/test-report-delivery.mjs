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
  REPORT_GITHUB_REPO: "1100soft/1100-shared-feedback",
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
  // Optional label failures may retry, but project routing must never disappear.
  const issueLabels=[];let attempt=0;
  globalThis.fetch=async(url,options)=>{
    if(!String(url).includes("api.github.com"))return new Response(JSON.stringify({id:"email"}),{status:200});
    issueLabels.push(JSON.parse(options.body).labels);
    return ++attempt===1?new Response(JSON.stringify({message:"Invalid label"}),{status:422}):new Response(JSON.stringify({number:43,html_url:"https://github.com/owner/repo/issues/43"}),{status:201});
  };
  const fallback=await onRequestPost({request:request(),env:{...env,REPORT_GITHUB_LABELS:"unavailable-label"}});
  assert.equal(fallback.status,200);assert.ok(issueLabels[0].includes("Mountlet"));assert.deepEqual(issueLabels[1],["Mountlet"]);
  issueLabels.length=0;attempt=0;
  const custom=await onRequestPost({request:request(),env:{...env,REPORT_GITHUB_PROJECT_LABEL:"project:mountlet"}});
  assert.equal(custom.status,200);assert.ok(issueLabels.every(labels=>labels.includes("project:mountlet")));
  for (const kind of ["bug", "crash", "support"]) {
    const submissions = [];
    globalThis.fetch = async (url, options) => {
      if (!String(url).includes("api.github.com")) return new Response(JSON.stringify({id: "email"}));
      assert.equal(String(url), "https://api.github.com/repos/1100soft/1100-shared-feedback/issues");
      const issue = JSON.parse(options.body);
      submissions.push(issue);
      assert.equal(issue.labels.filter(label => label === "Mountlet").length, 1);
      assert.ok(issue.labels.includes(kind));
      return new Response(JSON.stringify({number: 44, html_url: "https://github.com/1100soft/1100-shared-feedback/issues/44"}), {status: 201});
    };
    const spoofed = new Request("https://mountlet.app/api/report", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({kind, message: "A sufficiently detailed report.", projectLabel: "AnotherProject", labels: ["AnotherProject"]}),
    });
    const scoped = await onRequestPost({request: spoofed, env});
    assert.equal(scoped.status, 200);
    assert.equal(submissions.length, 1);
    assert.ok(!submissions[0].labels.includes("AnotherProject"), "Clients must not choose project scope");
  }
  const rejectedLabels = [];
  globalThis.fetch = async (url, options) => {
    if (!String(url).includes("api.github.com")) return new Response(JSON.stringify({id: "email"}));
    rejectedLabels.push(JSON.parse(options.body).labels);
    return new Response(JSON.stringify({message: "Project label rejected"}), {status: 422});
  };
  const rejected = await onRequestPost({request: request(), env});
  assert.equal(rejected.status, 502, "Rejected project label must not become an unscoped success");
  assert.deepEqual(rejectedLabels, [["Mountlet", "bug"], ["Mountlet"]]);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("Report delivery requires and returns a GitHub issue for bug and crash reports.");
