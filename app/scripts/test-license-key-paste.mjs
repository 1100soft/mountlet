import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/license_key_paste.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { bindLicenseKeyPaste } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

function controls(readText) {
  const key = Object.assign(new EventTarget(), {
    value: "", readOnly: false, isConnected: true, focused: false,
    focus() { this.focused = true; },
  });
  const paste = Object.assign(new EventTarget(), { disabled: false });
  const copy = { disabled: true };
  const activate = { disabled: true };
  const errors = [];
  key.addEventListener("input", () => {
    copy.disabled = activate.disabled = !key.value;
  });
  bindLicenseKeyPaste(key, paste, readText, async error => { errors.push(error); });
  return { key, paste, copy, activate, errors };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const click = button => button.dispatchEvent(new Event("click"));

// No browser clipboard or license service is present in this test environment.
const inactive = controls(async () => "  MNT-test-key\n");
click(inactive.paste);
await flush();
assert.equal(inactive.key.value, "MNT-test-key");
assert.equal(inactive.copy.disabled, false);
assert.equal(inactive.activate.disabled, false);
assert.equal(inactive.key.focused, true);
assert.equal(inactive.paste.disabled, false);

const empty = controls(async () => " \n");
empty.key.value = "old-key";
click(empty.paste);
await flush();
assert.equal(empty.key.value, "");
assert.equal(empty.copy.disabled, true);
assert.equal(empty.activate.disabled, true);

const failure = new Error("Clipboard unavailable");
const failed = controls(async () => { throw failure; });
failed.key.value = "keep-this-key";
click(failed.paste);
await flush();
assert.equal(failed.key.value, "keep-this-key");
assert.deepEqual(failed.errors, [failure]);
assert.equal(failed.paste.disabled, false);

let reads = 0;
const licensed = controls(async () => { reads += 1; return "replacement"; });
licensed.key.readOnly = true;
click(licensed.paste);
await flush();
assert.equal(reads, 0);

let finish;
reads = 0;
const pending = controls(() => { reads += 1; return new Promise(resolve => { finish = resolve; }); });
click(pending.paste);
click(pending.paste);
assert.equal(reads, 1);
pending.key.isConnected = false;
finish("stale-key");
await flush();
assert.equal(pending.key.value, "");
assert.equal(pending.key.focused, false);

console.log("License key paste checks passed.");
