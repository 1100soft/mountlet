import {readFileSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const varsPath = resolve(root, "web/site-vars.json");
const vars = JSON.parse(readFileSync(varsPath, "utf8"));

const generated = {
  root: [
    `- Production website: ${vars.productionSiteUrl}`,
    `- Source repository: ${vars.githubUrl}`,
  ].join("\n"),
  app: [
    `- Paid downloads and license purchases: ${vars.productionSiteUrl}`,
    `- Default license API: ${vars.productionSiteUrl}${vars.licenseApiPath}`,
    `- Override license API: \`${vars.env.licenseApiUrl}\``,
    `- Override public purchase site: \`${vars.env.licenseSiteUrl}\``,
  ].join("\n"),
  web: [
    `- Production website: ${vars.productionSiteUrl}`,
    `- Production license API: ${vars.productionSiteUrl}${vars.licenseApiPath}`,
    `- Relocated app API override: \`${vars.env.licenseApiUrl}\``,
    `- Relocated purchase-site override: \`${vars.env.licenseSiteUrl}\``,
    `- Resend API key: \`${vars.env.resendApiKey}\``,
    `- Resend sender: \`${vars.env.resendFrom}\``,
    `- Optional Resend reply-to: \`${vars.env.resendReplyTo}\``,
    `- Stripe secret key: \`${vars.env.stripeSecretKey}\``,
    `- Stripe webhook secret: \`${vars.env.stripeWebhookSecret}\``,
  ].join("\n"),
};

const targets = [
  ["README.md", "root"],
  ["app/README.md", "app"],
  ["web/README.md", "web"],
];

for (const [relativePath, key] of targets) {
  const path = resolve(root, relativePath);
  const original = readFileSync(path, "utf8");
  const next = replaceGeneratedBlock(original, generated[key]);
  if (next !== original) {
    writeFileSync(path, next, "utf8");
  }
}

function replaceGeneratedBlock(text, content) {
  const start = "<!-- mountlet-vars:start -->";
  const end = "<!-- mountlet-vars:end -->";
  const pattern = new RegExp(`${escapeRegExp(start)}[\\s\\S]*?${escapeRegExp(end)}`);
  const block = `${start}\n${content}\n${end}`;
  if (!pattern.test(text)) {
    throw new Error(`Missing generated docs markers: ${start} ... ${end}`);
  }
  return text.replace(pattern, block);
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
