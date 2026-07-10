import {readFileSync} from "node:fs";
import {dirname, resolve} from "node:path";

const apiBase = process.env.MOUNTLET_LOCAL_API || "http://127.0.0.1:8788/api/license";
const repoRoot = resolve(dirname(new URL(import.meta.url).pathname), "..", "..");
const devVars = readDevVars(resolve(repoRoot, ".dev.vars"));
const adminToken = process.env.LICENSE_ADMIN_TOKEN || process.env.MOUNTLET_LOCAL_ADMIN_TOKEN || devVars.LICENSE_ADMIN_TOKEN;

if (!adminToken) {
  console.error("Set LICENSE_ADMIN_TOKEN or MOUNTLET_LOCAL_ADMIN_TOKEN to the value in web/.dev.vars.");
  process.exit(1);
}

const deviceFingerprint = `local-smoke-${Date.now()}`;

const created = await post(
  `${apiBase}/admin/create`,
  {
    licenseKind: "beta",
    plan: "Local smoke",
    maxDevices: 2,
  },
  {authorization: `Bearer ${adminToken}`}
);

const activated = await post(`${apiBase}/activate`, {
  licenseKey: created.licenseKey,
  deviceFingerprint,
  deviceLabel: "Local smoke device",
  platform: process.platform,
  appVersion: "local",
});

const devices = await post(`${apiBase}/devices`, {token: activated.token});
await post(`${apiBase}/deactivate`, {token: activated.token});
const reactivated = await post(`${apiBase}/activate`, {
  licenseKey: created.licenseKey,
  deviceFingerprint,
  deviceLabel: "Local smoke device reactivated",
  platform: process.platform,
  appVersion: "local",
});
const devicesAfterReactivation = await post(`${apiBase}/devices`, {token: reactivated.token});

console.log(JSON.stringify({
  licenseKey: created.licenseKey,
  licenseKind: created.licenseKind,
  maxDevices: created.maxDevices,
  tokenReturned: Boolean(activated.token),
  deviceCount: devices.devices?.length || 0,
  deactivated: true,
  reactivated: Boolean(reactivated.token),
  deviceCountAfterReactivation: devicesAfterReactivation.devices?.length || 0,
}, null, 2));

async function post(url, body, headers = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (_error) {
    throw new Error(`${url} returned non-JSON response: ${text}`);
  }
  if (!response.ok || data.error) {
    throw new Error(`${url} failed: ${data.error || response.statusText}`);
  }
  return data;
}

function readDevVars(path) {
  try {
    return Object.fromEntries(
      readFileSync(path, "utf8")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith("#") && line.includes("="))
        .map((line) => {
          const [key, ...rest] = line.split("=");
          return [key.trim(), rest.join("=").trim().replace(/^"|"$/g, "")];
        })
    );
  } catch (_error) {
    return {};
  }
}
