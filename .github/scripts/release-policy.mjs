import {execFileSync} from "node:child_process";
import {appendFileSync} from "node:fs";
import {pathToFileURL} from "node:url";

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

function contains(ref, sha) {
  // A missing branch or Git error must not silently classify a release.
  execFileSync("git", ["rev-parse", "--verify", ref], {stdio: "pipe"});
  try {
    execFileSync("git", ["merge-base", "--is-ancestor", sha, ref], {stdio: "pipe"});
    return true;
  } catch (error) {
    if (error.status === 1) return false;
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const ref = process.env.GITHUB_REF ?? "";
  const tag = ref.startsWith("refs/tags/");
  const sha = execFileSync("git", ["rev-parse", "HEAD^{commit}"], {encoding: "utf8"}).trim();
  const policy = releasePolicy({
    event: process.env.GITHUB_EVENT_NAME,
    ref,
    manualPreview: process.env.PUBLISH_APT_PREVIEW === "true",
    onWip: tag && contains("refs/remotes/origin/wip", sha),
    onMain: tag && contains("refs/remotes/origin/main", sha),
  });
  if (tag && !policy.upload) throw Error("Tag is neither an unreleased wip commit nor a stable vX.Y.Z release on main.");
  const output = `channel=${policy.channel}\nupload=${policy.upload}\npublish-apt=${policy.publishApt}\n`;
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, output);
  process.stdout.write(output);
}
