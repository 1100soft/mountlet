#!/usr/bin/env node

import fs from "node:fs";
import process from "node:process";

const DEFAULT_SITE = "https://wip.mountlet.pages.dev";

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});

async function main() {
  const args = [...process.argv.slice(2)];
  const site = option(args, "--site") || envValue("REPORT_ADMIN_SITE") || DEFAULT_SITE;
  const command = args.shift() || "list";
  const token = envValue("REPORT_ADMIN_TOKEN") || envValue("LICENSE_ADMIN_TOKEN");
  if (!token) {
    throw new Error("Missing REPORT_ADMIN_TOKEN or LICENSE_ADMIN_TOKEN in the environment or .dev.vars.");
  }

  if (command === "list") {
    const status = args.shift() || "open";
    const data = await api(site, token, `/api/reports-admin?status=${encodeURIComponent(status)}&limit=50`);
    printReportList(data.reports || []);
    return;
  }

  if (command === "get") {
    const id = requireArg(args, "report id");
    const data = await api(site, token, `/api/reports-admin?id=${encodeURIComponent(id)}`);
    printJson(data.report || data);
    return;
  }

  if (command === "close") {
    const id = requireArg(args, "report id");
    const data = await api(site, token, "/api/reports-admin", {
      method: "PATCH",
      body: {
        id,
        status: "resolved",
        githubState: "closed",
        comment: option(args, "--comment") || "Report verified and closed.",
      },
    });
    printClosed(data.report || data);
    return;
  }

  if (command === "delete") {
    const id = requireArg(args, "report id");
    const data = await api(site, token, `/api/reports-admin?id=${encodeURIComponent(id)}`, {method: "DELETE"});
    printJson(data);
    return;
  }

  if (command === "mirror") {
    const id = requireArg(args, "report id");
    const data = await api(site, token, "/api/reports-admin", {
      method: "PATCH",
      body: {id, mirrorGithub: true},
    });
    printJson(data);
    return;
  }

  throw new Error([
    `Unknown command: ${command}`,
    "",
    "Usage:",
    "  npm run web:reports -- list [open|all|resolved]",
    "  npm run web:reports -- get <report-id>",
    "  npm run web:reports -- close <report-id> [--comment text]",
    "  npm run web:reports -- delete <report-id>",
    "  npm run web:reports -- mirror <report-id>",
    `  npm run web:reports -- --site ${DEFAULT_SITE} list`,
  ].join("\n"));
}

async function api(site, token, path, options = {}) {
  const response = await fetch(`${site.replace(/\/+$/, "")}${path}`, {
    method: options.method || "GET",
    headers: {
      authorization: `Bearer ${token}`,
      ...(options.body ? {"content-type": "application/json"} : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_error) {
    data = {raw: text};
  }
  if (!response.ok) {
    throw new Error(`Report admin returned ${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

function printReportList(reports) {
  if (reports.length === 0) {
    console.log("No reports.");
    return;
  }
  for (const report of reports) {
    const issue = report.githubIssueNumber ? `#${report.githubIssueNumber}` : "-";
    console.log([
      report.id,
      report.status,
      report.kind,
      issue,
      report.appVersion || "-",
      report.platform || "-",
      report.createdAt || "-",
    ].join("\t"));
  }
}

function printClosed(report) {
  console.log(JSON.stringify({
    id: report.id,
    status: report.status,
    githubIssueNumber: report.githubIssueNumber || null,
    githubIssueUrl: report.githubIssueUrl || "",
  }, null, 2));
}

function printJson(value) {
  console.log(JSON.stringify(value, null, 2));
}

function requireArg(args, label) {
  const value = args.shift();
  if (!value) {
    throw new Error(`Missing ${label}.`);
  }
  return value;
}

function option(args, name) {
  const index = args.indexOf(name);
  if (index === -1) {
    return "";
  }
  const value = args[index + 1] || "";
  args.splice(index, value ? 2 : 1);
  return value;
}

function envValue(name) {
  if (process.env[name]) {
    return process.env[name];
  }
  const localEnv = readLocalEnv();
  return localEnv[name] || "";
}

function readLocalEnv() {
  if (!fs.existsSync(".dev.vars")) {
    return {};
  }
  const values = {};
  const text = fs.readFileSync(".dev.vars", "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) {
      continue;
    }
    values[match[1]] = match[2].trim().replace(/^['"]|['"]$/g, "");
  }
  return values;
}
