import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

async function load(name) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { googleCards, sameRemoteCard } = await load("google_remotes");
const drive = { id: "d", provider: "drive", name: "Account", googleGroup: "pair" };
const photos = { id: "p", provider: "gphotos", name: "Different alias", googleGroup: "pair" };
const unrelated = { id: "other", provider: "drive", name: "Account" };
assert.deepEqual(googleCards([photos, unrelated, drive]), [drive, unrelated]);
assert.equal(sameRemoteCard(drive, photos), true);
assert.equal(sameRemoteCard(drive, unrelated), false);
assert.equal(sameRemoteCard(drive, { ...photos, googleGroup: "another" }), false);
assert.equal(sameRemoteCard(drive, { ...photos, provider: "dropbox" }), false);
assert.equal(sameRemoteCard(undefined, undefined), false);
assert.equal(googleCards([{ ...drive, googleGroup: null }, { ...photos, googleGroup: null }]).length, 2);

const { resolveStartupLicense } = await load("license_startup");
const expired = { state: "expired", licenseKey: "fixture-beta" };
const licensed = { state: "licensed", licenseKey: "fixture-beta" };
let calls = 0, resolve;
const pending = resolveStartupLicense(expired, () => { ++calls; return new Promise(done => { resolve = done; }); });
assert.equal(calls, 1, "renewal starts immediately, without a dialog or timer");
let finished = false;
pending.then(() => { finished = true; });
await Promise.resolve();
assert.equal(finished, false, "startup waits for renewal before deciding expired UI");
resolve(licensed);
assert.equal(await pending, licensed);
assert.equal(await resolveStartupLicense(expired, async () => { throw Error("offline"); }), expired);
for (const state of [null, licensed, { state: "trial", licenseKey: "" }, { state: "expired", licenseKey: "" }]) {
  assert.equal(await resolveStartupLicense(state, () => { throw Error("must not renew"); }), state);
}
console.log("Google grouping and launch-time license renewal checks passed.");
