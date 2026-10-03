#!/usr/bin/env node

const args = process.argv.slice(2);
const command = args.shift() || "list";
const options = parseOptions(args);
const site = String(options.site || process.env.MOUNTLET_SITE_URL || "https://mountlet.app").replace(/\/$/, "");
const token = String(process.env.LICENSE_ADMIN_TOKEN || "").trim();

if (!token) fail("Set LICENSE_ADMIN_TOKEN in the environment.");
const endpoint = `${site}/api/notices/admin`;

if (command === "list") {
  printList((await request("GET")).notices || []);
} else if (command === "create") {
  const body = noticeBody(options, true);
  if (options.publish) body.status = "published";
  printJson(await request("POST", body));
} else if (command === "update") {
  const id = requireValue(options._[0], "notice id");
  printJson(await request("PATCH", {id, ...noticeBody(options, false)}));
} else if (command === "publish" || command === "archive") {
  const id = requireValue(options._[0], "notice id");
  printJson(await request("PATCH", {id, status: command === "publish" ? "published" : "archived"}));
} else if (command === "delete") {
  const id = requireValue(options._[0], "notice id");
  if (!options.yes) fail("Deletion is permanent. Add --yes, or use archive instead.");
  printJson(await request("DELETE", undefined, `?id=${encodeURIComponent(id)}`));
} else {
  fail("Use list, create, update, publish, archive, or delete.");
}

async function request(method, body, suffix = "") {
  const response = await fetch(`${endpoint}${suffix}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/json",
      ...(body ? {"content-type": "application/json"} : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (_error) {
    fail(`Server returned ${response.status}: ${text || "empty response"}`);
  }
  if (!response.ok) fail(`Server returned ${response.status}: ${payload.error || text}`);
  return payload;
}

function noticeBody(values, requireCore) {
  const body = {};
  const mappings = {
    id: "id", title: "title", message: "message", level: "level", type: "type",
    url: "url", starts: "startsAt", ends: "endsAt", status: "status", audience: "audience",
  };
  for (const [option, field] of Object.entries(mappings)) {
    if (values[option] !== undefined && values[option] !== true) body[field] = String(values[option]);
  }
  if (requireCore) {
    for (const field of ["id", "title", "message"]) requireValue(body[field], `--${field}`);
  }
  return body;
}

function parseOptions(values) {
  const parsed = {_: []};
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!value.startsWith("--")) {
      parsed._.push(value);
      continue;
    }
    const key = value.slice(2);
    const next = values[index + 1];
    if (next !== undefined && !next.startsWith("--")) {
      parsed[key] = next;
      index += 1;
    } else {
      parsed[key] = true;
    }
  }
  return parsed;
}

function printList(notices) {
  if (!notices.length) {
    console.log("No notices.");
    return;
  }
  for (const notice of notices) {
    console.log(
      `${notice.id}  v${notice.version}  ${notice.status}  ${notice.audience || "preview"}  `
      + `${notice.level}  ${notice.title}`
    );
  }
}

function printJson(value) {
  console.log(JSON.stringify(value, null, 2));
}

function requireValue(value, label) {
  if (value === undefined || value === null || String(value).trim() === "") fail(`${label} is required.`);
  return String(value).trim();
}

function fail(message) {
  console.error(message);
  process.exit(1);
}
