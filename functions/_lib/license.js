const TOKEN_HEADER = {alg: "ES256", typ: "Mountlet-License"};
const BETA_TOKEN_SECONDS = 24 * 60 * 60;

export function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store"
    }
  });
}

export async function readJson(request) {
  try {
    return await request.json();
  } catch (_error) {
    throw new HttpError(400, "Invalid JSON body.");
  }
}

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function handleError(error) {
  if (error instanceof HttpError) {
    return jsonResponse({error: error.message}, error.status);
  }
  return jsonResponse({error: "Server error."}, 500);
}

export function requireEnv(env, key) {
  const value = env[key];
  if (!value) {
    throw new HttpError(500, `Missing ${key}.`);
  }
  return value;
}

export function nowIso() {
  return new Date().toISOString();
}

export function randomId(prefix) {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `${prefix}_${base64Url(bytes)}`;
}

export function generateLicenseKey(prefix = "MNT") {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  const raw = Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
  return `${prefix}-${raw.slice(0, 5)}-${raw.slice(5, 10)}-${raw.slice(10, 15)}-${raw.slice(15, 20)}`;
}

export function normalizeLicenseKey(value) {
  return String(value || "").trim().toUpperCase();
}

export async function licenseKeyHash(env, licenseKey) {
  const normalized = normalizeLicenseKey(licenseKey);
  if (!normalized) {
    throw new HttpError(400, "License key is required.");
  }
  const pepper = env.LICENSE_KEY_PEPPER || "";
  return sha256Hex(`${pepper}:${normalized}`);
}

export async function deviceHash(value) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    throw new HttpError(400, "Device fingerprint is required.");
  }
  return sha256Hex(normalized);
}

export async function signLicenseToken(env, payload) {
  const privateKey = await importPrivateKey(requireEnv(env, "LICENSE_SIGNING_PRIVATE_KEY"));
  const header = base64UrlJson(TOKEN_HEADER);
  const body = base64UrlJson(payload);
  const signed = new TextEncoder().encode(`${header}.${body}`);
  const signature = await crypto.subtle.sign({name: "ECDSA", hash: "SHA-256"}, privateKey, signed);
  return `${header}.${body}.${base64Url(new Uint8Array(signature))}`;
}

export async function verifyLicenseToken(env, token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) {
    throw new HttpError(401, "Invalid license token.");
  }
  const header = JSON.parse(textFromBase64Url(parts[0]));
  if (header.alg !== TOKEN_HEADER.alg) {
    throw new HttpError(401, "Unsupported license token.");
  }
  const publicKey = await importPublicKey(requireEnv(env, "LICENSE_SIGNING_PUBLIC_KEY"));
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  const signature = bytesFromBase64Url(parts[2]);
  const ok = await crypto.subtle.verify({name: "ECDSA", hash: "SHA-256"}, publicKey, signature, signed);
  if (!ok) {
    throw new HttpError(401, "Invalid license token signature.");
  }
  return JSON.parse(textFromBase64Url(parts[1]));
}

export async function loadActiveLicenseByKey(env, licenseKey) {
  const hash = await licenseKeyHash(env, licenseKey);
  const license = await env.DB.prepare("SELECT * FROM licenses WHERE license_key_hash = ?").bind(hash).first();
  if (!license || license.status !== "active") {
    throw new HttpError(404, "License not found or inactive.");
  }
  if (license.expires_at && Date.parse(license.expires_at) <= Date.now() && !license.stripe_subscription_id) {
    throw new HttpError(404, "License not found or inactive.");
  }
  return license;
}

export async function activeDeviceCount(env, licenseId) {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM devices WHERE license_id = ? AND deactivated_at IS NULL"
  ).bind(licenseId).first();
  return Number(row?.count || 0);
}

export function tokenPayload(license, device) {
  const licenseKind = license.license_kind || "paid";
  return {
    licenseId: license.id,
    deviceId: device.id,
    plan: license.plan || "Mountlet License",
    licenseKind,
    maxDevices: Number(license.max_devices || 0),
    deviceLabel: device.device_label || "",
    issuedAt: nowIso(),
    expiresAt: licenseKind === "beta" ? betaTokenExpiresAt() : license.expires_at || ""
  };
}

export function betaTokenExpiresAt() {
  return new Date(Date.now() + BETA_TOKEN_SECONDS * 1000).toISOString();
}

export async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function base64UrlJson(value) {
  return base64Url(new TextEncoder().encode(JSON.stringify(value)));
}

export function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function bytesFromBase64Url(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

export function textFromBase64Url(value) {
  return new TextDecoder().decode(bytesFromBase64Url(value));
}

async function importPrivateKey(pem) {
  const der = pemToBytes(pem);
  return crypto.subtle.importKey("pkcs8", der, {name: "ECDSA", namedCurve: "P-256"}, false, ["sign"]);
}

async function importPublicKey(pem) {
  const der = pemToBytes(pem);
  return crypto.subtle.importKey("spki", der, {name: "ECDSA", namedCurve: "P-256"}, false, ["verify"]);
}

function pemToBytes(pem) {
  const body = pem.replace(/-----BEGIN [^-]+-----/g, "").replace(/-----END [^-]+-----/g, "").replace(/\s+/g, "");
  return bytesFromBase64Url(body.replace(/\+/g, "-").replace(/\//g, "_"));
}
