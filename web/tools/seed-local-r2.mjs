import {spawnSync} from "node:child_process";
import {resolve} from "node:path";

const result = spawnSync(
  process.execPath,
  [
    resolve("web", "tools", "upload-release-r2.mjs"),
    "mountlet-downloads-dev",
    resolve("web", "dev", "release"),
    "--version",
    "0.0.0-dev",
  ],
  {stdio: "inherit"}
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status || 0);
