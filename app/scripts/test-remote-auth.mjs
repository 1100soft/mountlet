import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// Exercise the same TypeScript helpers used by both remote dialogs.
const source = await readFile(new URL("../src/remote_auth.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { authenticationFieldChanged, googleAccountValue, OAUTH_REMOTE_TYPES } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

assert.deepEqual([...OAUTH_REMOTE_TYPES].sort(), ["box", "drive", "dropbox", "gphotos", "onedrive", "pcloud"]);
assert.equal(googleAccountValue(" user "), "user@gmail.com");
assert.equal(googleAccountValue(" user@example.com "), "user@example.com");
assert.equal(googleAccountValue(" "), "");

for (const [next, previous, secret, expected] of [
  ["••••••", "stored-secret", true, false],
  ["", "stored-secret", true, false],
  ["new-secret", "stored-secret", true, true],
  ["new-client", "old-client", false, true],
  [" client ", "client", false, false],
  ["", "FALSE", false, false],
  ["false", "", false, false],
  ["true", "false", false, true],
  ["", "client", false, true],
]) {
  assert.equal(authenticationFieldChanged(next, previous, secret), expected);
}

console.log("Remote authentication checks passed.");
