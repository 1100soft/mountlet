import {readFileSync} from "node:fs";

const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const tauriConfig = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const cargoToml = readFileSync(new URL("../src-tauri/Cargo.toml", import.meta.url), "utf8");
const cargoVersion = cargoToml.match(/\[package\][\s\S]*?\nversion\s*=\s*"([^"]+)"/)?.[1];

const versions = new Map([
  ["package.json", packageJson.version],
  ["Cargo.toml", cargoVersion],
  ["tauri.conf.json", tauriConfig.version],
]);
const unique = new Set(versions.values());
if (unique.size !== 1 || unique.has(undefined)) {
  throw new Error(`Mountlet versions do not match: ${[...versions].map(([file, version]) => `${file}=${version ?? "missing"}`).join(", ")}`);
}

const releaseTag = process.env.RELEASE_TAG || "";
if (releaseTag && releaseTag !== `v${packageJson.version}`) {
  throw new Error(`Release tag ${releaseTag} does not match Mountlet version ${packageJson.version}.`);
}

console.log(`Mountlet version ${packageJson.version} is consistent${releaseTag ? ` with ${releaseTag}` : ""}.`);
