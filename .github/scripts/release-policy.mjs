import {execFile} from "node:child_process";
import {appendFileSync, readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
import {promisify} from "node:util";

const exec = promisify(execFile);
const tagPattern = /^v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(?:-preview\.([1-9][0-9]*))?$/;

export function releasePolicy({event, ref = "", version, onWip = false, onMain = false}) {
  const validation = {channel: "validation", upload: false, publishApt: false, releaseVersion: "", debianVersion: ""};
  if (!ref.startsWith("refs/tags/") || ref.startsWith("refs/tags/cerberus/source/")) return validation;
  if (event !== "push") throw Error("Releases require a pushed release tag; manual/PR runs only validate branches.");
  const tag = ref.slice("refs/tags/".length);
  const match = tag.match(tagPattern);
  if (!match) throw Error("Use vX.Y.Z on main or vX.Y.Z-preview.N on unreleased wip (N is a positive integer).");
  const [, core, preview] = match;
  if (core !== version) throw Error(`Tag core ${core} does not match application version ${version}.`);
  if (preview ? (!onWip || onMain) : !onMain) throw Error("Tag format and branch ancestry disagree; preview requires wip excluding main, stable requires main.");
  return {
    channel: preview ? "preview" : "production", upload: true, publishApt: true,
    releaseVersion: tag.slice(1), debianVersion: preview ? `${core}~preview.${preview}` : core,
  };
}

/** Check ordering once, before concurrent builds can finish in a different order. */
export async function validatePreviewOrder(policy, entries) {
  if (policy.channel !== 'preview') return;
  const published = entries.filter(entry => ['mountlet-preview','mountlet-lean-preview'].includes(entry.Package) && entry.Architecture === 'amd64');
  if (!['mountlet-preview','mountlet-lean-preview'].every(name => published.some(entry => entry.Package === name && entry.Version))) {
    throw Error('Cannot establish preview ordering: expected both existing Mountlet preview families in the public APT index.');
  }
  // Existing exact identities are replays, still gated by immutable file hashes.
  if (published.some(entry => entry.Version === policy.debianVersion)) return;
  for (const entry of published) {
    try {
      await exec('dpkg', ['--compare-versions', policy.debianVersion, 'gt', entry.Version]);
    } catch (error) {
      if (error.code === 1) throw Error(`Preview ${policy.debianVersion} must be newer than published ${entry.Package} ${entry.Version}; increase the tag sequence/core.`);
      throw error;
    }
  }
}

export async function readAptIndex() {
  const response = await fetch('https://apt.1100soft.com/dists/stable/main/binary-amd64/Packages?release-policy=' + Date.now(), {
    headers: {'User-Agent':'1100-CI-Mountlet-policy/1.0','Cache-Control':'no-cache'}, signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw Error(`Cannot establish preview ordering from public APT index: HTTP ${response.status}`);
  const entries = (await response.text()).trim().split(/\n\n+/).map(block => Object.fromEntries(block.split('\n').filter(line => /^[A-Za-z][A-Za-z0-9-]*: /.test(line)).map(line => {
    const colon = line.indexOf(': ');
    return [line.slice(0,colon), line.slice(colon+2)];
  })));
  if (!entries.some(entry => entry.Package && entry.Version && entry.Architecture)) throw Error('Invalid public APT index response.');
  return entries;
}

async function contains(ref, sha, cwd) {
  await exec("git", ["rev-parse", "--verify", ref], {cwd});
  try { await exec("git", ["merge-base", "--is-ancestor", sha, ref], {cwd}); return true; }
  catch (error) { if (error.code === 1) return false; throw error; }
}

export async function resolveReleasePolicy(env = process.env, cwd = process.cwd(), dependencies = {readAptIndex}) {
  const ref = env.GITHUB_REF ?? "";
  if (!ref.startsWith("refs/tags/") || ref.startsWith("refs/tags/cerberus/source/")) return releasePolicy({event: env.GITHUB_EVENT_NAME, ref});
  const sha = (await exec("git", ["rev-parse", "HEAD^{commit}"], {cwd})).stdout.trim();
  const tagged = (await exec("git", ["rev-parse", `${ref}^{commit}`], {cwd})).stdout.trim();
  const eventSha = env.GITHUB_SHA ? (await exec("git", ["rev-parse", `${env.GITHUB_SHA}^{commit}`], {cwd})).stdout.trim() : sha;
  if (tagged !== sha || eventSha !== sha) throw Error("Checkout, event SHA and release tag must identify the same commit.");
  const version = JSON.parse(readFileSync(`${cwd}/app/package.json`, "utf8")).version;
  const policy = releasePolicy({event: env.GITHUB_EVENT_NAME, ref, version,
    onWip: await contains("refs/remotes/origin/wip", sha, cwd),
    onMain: await contains("refs/remotes/origin/main", sha, cwd)});
  if (policy.channel === 'preview') await validatePreviewOrder(policy, await dependencies.readAptIndex());
  return policy;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const policy = await resolveReleasePolicy();
  const matrix = JSON.parse(readFileSync(new URL("./package-matrix.json", import.meta.url), "utf8"));
  const output = {
    channel: policy.channel, upload: policy.upload, "publish-apt": policy.publishApt,
    "release-version": policy.releaseVersion, "debian-version": policy.debianVersion,
    "linux-matrix": JSON.stringify({include: matrix.include.filter(row => row.platform.startsWith("linux-"))}),
    "other-matrix": JSON.stringify({include: matrix.include.filter(row => !row.platform.startsWith("linux-"))}),
    "source-epoch": (await exec("git", ["show", "-s", "--format=%ct", "HEAD"])).stdout.trim(),
  };
  const text = Object.entries(output).map(([key, value]) => `${key}=${value}\n`).join("");
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, text);
  process.stdout.write(text);
}
