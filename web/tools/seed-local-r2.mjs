import {spawnSync} from "node:child_process";
import {readdirSync} from "node:fs";
import {resolve} from "node:path";

const bucket = "mountlet-downloads-dev";
const releaseDir = resolve("web", "dev", "release");
const files = readdirSync(releaseDir, {withFileTypes: true})
  .filter((entry) => entry.isFile())
  .map((entry) => entry.name)
  .sort();

for (const file of files) {
  const result = spawnSync(
    "wrangler",
    ["r2", "object", "put", `${bucket}/${file}`, "--local", "--file", resolve(releaseDir, file)],
    {stdio: "inherit"}
  );
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

console.log(`Seeded ${files.length} local release object(s).`);
