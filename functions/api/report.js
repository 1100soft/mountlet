import {
  githubConfig,
  githubConfigurationError,
  jsonResponse,
  mirrorReportToGitHub,
  normalizedReport,
  readJson,
  resendConfigured,
  sendReportEmail,
  setReportEmailMirror,
  setReportGitHubError,
  setReportGitHubMirror,
  storeReport,
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
  let reportId = "";

  try {
    reportId = await storeReport(env, report);
    sinks.push({kind: "d1", id: reportId});
  } catch (error) {
    failures.push(`D1: ${String(error.message || error).slice(0, 500)}`);
  }

  const githubState = githubConfig(env);
  if (githubState.enabled) {
    try {
      const sink = await mirrorReportToGitHub(env, report);
      sinks.push(sink);
      if (reportId) {
        await setReportGitHubMirror(env, reportId, sink);
      }
    } catch (error) {
      const message = String(error.message || error).slice(0, 500);
      failures.push(`GitHub: ${message}`);
      if (reportId) {
        await setReportGitHubError(env, reportId, message);
      }
    }
  } else if (githubState.present) {
    const message = githubConfigurationError(githubState);
    failures.push(message);
    if (reportId) {
      await setReportGitHubError(env, reportId, message);
    }
  }

  if (resendConfigured(env)) {
    try {
      const sink = await sendReportEmail(env, report);
      sinks.push(sink);
      if (reportId) {
        await setReportEmailMirror(env, reportId, sink);
      }
    } catch (error) {
      failures.push(`Resend: ${String(error.message || error).slice(0, 500)}`);
    }
  }

  if (!reportId && !githubState.present && !resendConfigured(env)) {
    return jsonResponse({ok: false, error: "Bug reports are not configured."}, 503);
  }
  if (!reportId && sinks.length === 0) {
    return jsonResponse({ok: false, error: failures.join("\n") || "Bug report delivery failed."}, 502);
  }
  return jsonResponse({
    ok: true,
    id: reportId || sinks[0]?.id || "",
    sinks,
    warning: failures.join("\n"),
  });
}
