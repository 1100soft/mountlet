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

  const report = normalizedReport(payload);
  const sinks = [];
  const failures = [];
  const githubState = githubConfig(env);

  if (githubState.enabled) {
    try {
      sinks.push(await createReportIssue(env, report));
    } catch (error) {
      failures.push(`GitHub: ${String(error.message || error).slice(0, 500)}`);
    }
  } else if (githubState.present) {
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
    return jsonResponse({ok: false, error: "Bug reports are not configured."}, 503);
  }
  if (sinks.length === 0) {
    return jsonResponse({ok: false, error: failures.join("\n") || "Bug report delivery failed."}, 502);
  }
  return jsonResponse({
    ok: true,
    id: sinks[0]?.id || "",
    sinks,
    warning: failures.join("\n"),
  });
}
