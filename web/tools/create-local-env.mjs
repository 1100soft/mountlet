import {existsSync, mkdirSync, writeFileSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {generateKeyPairSync, randomBytes} from "node:crypto";
import {fileURLToPath} from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..");
const localDir = resolve(webRoot, ".local");
const envPath = resolve(repoRoot, ".dev.vars");
const privateKeyPath = resolve(localDir, "license-private.pem");
const publicKeyPath = resolve(localDir, "license-public.pem");

if (existsSync(envPath) && !process.argv.includes("--force")) {
  console.error(`${envPath} already exists. Use --force to replace it.`);
  process.exit(1);
}

mkdirSync(localDir, {recursive: true});

const {privateKey, publicKey} = generateKeyPairSync("ec", {namedCurve: "P-256"});
const privatePem = privateKey.export({type: "pkcs8", format: "pem"});
const publicPem = publicKey.export({type: "spki", format: "pem"});

writeFileSync(privateKeyPath, privatePem, {encoding: "utf8", mode: 0o600});
writeFileSync(publicKeyPath, publicPem, {encoding: "utf8", mode: 0o644});

const adminToken = randomBytes(24).toString("base64url");
const pepper = randomBytes(32).toString("base64url");
const escapedPrivate = String(privatePem).replace(/\n/g, "\\n");
const escapedPublic = String(publicPem).replace(/\n/g, "\\n");

writeFileSync(
  envPath,
  [
    `LICENSE_KEY_PEPPER="${pepper}"`,
    `LICENSE_ADMIN_TOKEN="${adminToken}"`,
    `LICENSE_SIGNING_PRIVATE_KEY="${escapedPrivate}"`,
    `LICENSE_SIGNING_PUBLIC_KEY="${escapedPublic}"`,
    "",
    "STRIPE_SECRET_KEY=\"sk_test_replace\"",
    "STRIPE_WEBHOOK_SECRET=\"whsec_replace\"",
    "",
    "# Optional license email delivery through Resend.",
    "# RESEND_API_KEY=\"re_replace\"",
    "# RESEND_FROM=\"Mountlet <licenses@example.com>\"",
    "# RESEND_REPLY_TO=\"support@example.com\"",
    "",
  ].join("\n"),
  {encoding: "utf8", mode: 0o600}
);

console.log(`Wrote ${envPath}`);
console.log(`Wrote ${privateKeyPath}`);
console.log(`Wrote ${publicKeyPath}`);
console.log("");
console.log("Local app activation env:");
console.log("export MOUNTLET_REQUIRE_LICENSE=1");
console.log("export MOUNTLET_LICENSE_API_URL=http://127.0.0.1:8788/api/license");
console.log(`export MOUNTLET_LICENSE_PUBLIC_KEY_FILE=${publicKeyPath}`);
console.log("");
console.log(`Admin token: ${adminToken}`);
