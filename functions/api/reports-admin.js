import {handleError} from "../_lib/license.js";
import {
  commentOnGitHubIssue,
  deleteReport,
  jsonResponse,
  listReports,
  loadReport,
  mirrorReportToGitHub,
  readJson,
  requireReportAdmin,
  setReportGitHubError,
  setReportGitHubMirror,
  updateGitHubIssueState,
  updateReport,
} from "../_lib/reports.js";

export async function onRequestGet({request, env}) {
  try {
    await requireReportAdmin(request, env);
    const url = new URL(request.url);
    const id = String(url.searchParams.get("id") || "").trim();
    if (id) {
      return jsonResponse({report: await loadReport(env, id)});
    }
    return jsonResponse({reports: await listReports(env, request)});
  } catch (error) {
    return handleError(error);
  }
}

export async function onRequestPatch({request, env}) {
  try {
    await requireReportAdmin(request, env);
    const body = await readJson(request);
    const id = String(body.id || new URL(request.url).searchParams.get("id") || "").trim();
    if (!id) {
      return jsonResponse({error: "Report id is required."}, 400);
    }
    let report = await updateReport(env, id, body);
    const mirrorResult = await applyMirrorActions(env, report, body);
    report = await loadReport(env, id);
    return jsonResponse({report, mirror: mirrorResult});
  } catch (error) {
    return handleError(error);
  }
}

export async function onRequestDelete({request, env}) {
  try {
    await requireReportAdmin(request, env);
    const url = new URL(request.url);
    const id = String(url.searchParams.get("id") || "").trim();
    if (!id) {
      return jsonResponse({error: "Report id is required."}, 400);
    }
    const report = await deleteReport(env, id);
    const mirrorResult = await applyMirrorActions(env, report, {
      githubState: "closed",
      comment: "Deleted from Mountlet report admin.",
    });
    return jsonResponse({ok: true, deleted: id, mirror: mirrorResult});
  } catch (error) {
    return handleError(error);
  }
}

async function applyMirrorActions(env, report, body) {
  const result = {};
  let issueNumber = Number(report.githubIssueNumber || 0);
  if (body.mirrorGithub && !issueNumber) {
    try {
      const sink = await mirrorReportToGitHub(env, report);
      await setReportGitHubMirror(env, report.id, sink);
      issueNumber = Number(sink.id || 0);
      result.github = {created: true, issueNumber, url: sink.url || ""};
    } catch (error) {
      const message = String(error.message || error).slice(0, 500);
      await setReportGitHubError(env, report.id, message);
      result.github = {created: false, error: message};
    }
  }
  if (!issueNumber) {
    return result;
  }
  if (body.comment) {
    result.comment = await commentOnGitHubIssue(env, issueNumber, body.comment);
  }
  if (body.githubState) {
    const state = String(body.githubState).toLowerCase();
    if (!["open", "closed"].includes(state)) {
      return {...result, githubStateError: "githubState must be open or closed."};
    }
    result.githubState = await updateGitHubIssueState(env, issueNumber, state);
  }
  return result;
}
