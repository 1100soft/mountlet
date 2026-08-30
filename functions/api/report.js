import {
  createReportIssue,
  githubConfig,
  githubConfigurationError,
  jsonResponse,
  normalizedReport,
  readJson,
  resendConfigured,
  sendReportEmail,
} from "../_lib/reports.js";

export async function onRequestPost({request, env}) {
  let payload;
  try {
    payload = await readJson(request);
  } catch (_error) {
    return jsonResponse({ok: false, error: "Invalid JSON."}, 400);
  }

  if (String(payload.website || "").trim()) {
    return jsonResponse({ok: true, id: ""});
  }

  const report = normalizedReport(payload);
  if (report.kind === "support" && report.message.trim().length < 10) {
    return jsonResponse({ok: false, error: "Please provide a little more detail."}, 400);
  }
  const sinks = [];
  const failures = [];
  const githubState = githubConfig(env);
  const githubRequired = report.kind === "bug" || report.kind === "crash";

  if (githubState.enabled) {
    try {
      sinks.push(await createReportIssue(env, report));
    } catch (error) {
      failures.push(`GitHub: ${String(error.message || error).slice(0, 500)}`);
    }
  } else if (githubState.present || githubRequired) {
    failures.push(githubConfigurationError(githubState));
  }

  if (resendConfigured(env)) {
    try {
      sinks.push(await sendReportEmail(env, report));
    } catch (error) {
      failures.push(`Resend: ${String(error.message || error).slice(0, 500)}`);
    }
  }

  if (!githubState.present && !resendConfigured(env)) {
    return jsonResponse({ok: false, error: "Report delivery is not configured."}, 503);
  }
  const githubSink = sinks.find((sink) => sink.kind === "github");
  if (githubRequired && !githubSink) {
    return jsonResponse({
      ok: false,
      error: failures.find((failure) => failure.startsWith("GitHub:")) || "GitHub issue creation failed.",
      sinks,
    }, 502);
  }
  if (sinks.length === 0) {
    return jsonResponse({ok: false, error: failures.join("\n") || "Report delivery failed."}, 502);
  }
  return jsonResponse({
    ok: true,
    id: githubSink?.id || sinks[0]?.id || "",
    issueUrl: githubSink?.url || "",
    sinks,
    warning: failures.join("\n"),
  });
}
