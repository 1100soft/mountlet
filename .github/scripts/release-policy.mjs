import {execFile} from "node:child_process";
import {appendFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import {promisify} from "node:util";

const exec = promisify(execFile);

export function releasePolicy({event, ref, manualPreview = false, onWip = false, onMain = false}) {
  const tag = ref.startsWith("refs/tags/");
  const publishingEvent = event === "push" || event === "workflow_dispatch";
  const preview = ref === "refs/heads/wip" || (tag && onWip && !onMain);
  const stable = tag && onMain && /^refs\/tags\/v\d+\.\d+\.\d+$/.test(ref);
  return {
    channel: preview ? "preview" : "production",
    upload: publishingEvent && (preview || stable),
    publishApt: publishingEvent && (stable || (preview && (tag || manualPreview))),
  };
}

async function contains(ref, sha, cwd) {
  // A missing branch or Git error must not silently classify a release.
  await exec("git", ["rev-parse", "--verify", ref], {cwd});
  try {
    await exec("git", ["merge-base", "--is-ancestor", sha, ref], {cwd});
    return true;
  } catch (error) {
    if (error.code === 1) return false;
    throw error;
  }
}

export async function resolveReleasePolicy(env = process.env, cwd = process.cwd()) {
  const ref = env.GITHUB_REF ?? "";
  const tag = ref.startsWith("refs/tags/");
  const sha = (await exec("git", ["rev-parse", "HEAD^{commit}"], {cwd})).stdout.trim();
  const policy = releasePolicy({
    event: env.GITHUB_EVENT_NAME,
    ref,
    manualPreview: env.PUBLISH_APT_PREVIEW === "true",
    onWip: tag && await contains("refs/remotes/origin/wip", sha, cwd),
    onMain: tag && await contains("refs/remotes/origin/main", sha, cwd),
  });
  if (tag && !policy.upload) throw Error("Tag is neither an unreleased wip commit nor a stable vX.Y.Z release on main.");
  return policy;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const policy = await resolveReleasePolicy();
  const output = `channel=${policy.channel}\nupload=${policy.upload}\npublish-apt=${policy.publishApt}\n`;
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, output);
  process.stdout.write(output);
}
