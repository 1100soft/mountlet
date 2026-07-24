import {HttpError} from "./license.js";

export const NOTICE_LEVELS = new Set(["info", "important", "critical"]);
export const NOTICE_STATUSES = new Set(["draft", "published", "archived"]);
export const NOTICE_AUDIENCES = new Set(["production", "preview", "local", "all"]);

export async function ensureNoticeSchema(env) {
  if (!env.DB) {
    throw new HttpError(500, "DB binding is missing.");
  }
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS notices (
      id TEXT PRIMARY KEY,
      version INTEGER NOT NULL DEFAULT 1,
      title TEXT NOT NULL,
      message TEXT NOT NULL,
      level TEXT NOT NULL DEFAULT 'info',
      type TEXT NOT NULL DEFAULT 'general',
      url TEXT NOT NULL DEFAULT '',
      starts_at TEXT NOT NULL DEFAULT '',
      ends_at TEXT NOT NULL DEFAULT '',
      audience TEXT NOT NULL DEFAULT 'preview',
      status TEXT NOT NULL DEFAULT 'draft',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
  `).run();
  await env.DB.prepare(`
    CREATE INDEX IF NOT EXISTS idx_notices_status_time
    ON notices(status, starts_at, ends_at)
  `).run();
  const info = await env.DB.prepare("PRAGMA table_info(notices)").all();
  const columns = new Set((info.results || []).map((row) => String(row.name || "")));
  if (!columns.has("audience")) {
    try {
      await env.DB.prepare(
        "ALTER TABLE notices ADD COLUMN audience TEXT NOT NULL DEFAULT 'preview'"
      ).run();
    } catch (error) {
      if (!String(error?.message || error).toLowerCase().includes("duplicate column")) {
        throw error;
      }
    }
  }
}

export async function inspectNoticeSchema(env) {
  if (!env.DB) {
    return {ok: false, error: "DB binding is missing."};
  }
  try {
    const info = await env.DB.prepare("PRAGMA table_info(notices)").all();
    const columns = new Set((info.results || []).map((row) => String(row.name || "")));
    const required = [
      "id", "version", "title", "message", "level", "type", "url",
      "starts_at", "ends_at", "audience", "status", "created_at", "updated_at"
    ];
    const missing = required.filter((column) => !columns.has(column));
    return {ok: columns.size > 0 && missing.length === 0, missing};
  } catch (error) {
    return {ok: false, error: String(error?.message || error || "Could not inspect notices.")};
  }
}

export function normalizeNoticeInput(value, {partial = false} = {}) {
  const source = value && typeof value === "object" ? value : {};
  const result = {};
  assignString(result, source, "id", {required: !partial, max: 80});
  assignString(result, source, "title", {required: !partial, max: 160});
  assignString(result, source, "message", {required: !partial, max: 4000});
  assignString(result, source, "url", {max: 1000, fallback: partial ? undefined : ""});
  assignString(result, source, "type", {max: 40, fallback: partial ? undefined : "general"});
  assignString(result, source, "startsAt", {
    aliases: ["starts_at"], max: 40, fallback: partial ? undefined : ""
  });
  assignString(result, source, "endsAt", {
    aliases: ["ends_at"], max: 40, fallback: partial ? undefined : ""
  });
  assignString(result, source, "status", {max: 16, fallback: partial ? undefined : "draft"});
  assignString(result, source, "level", {max: 16, fallback: partial ? undefined : "info"});
  assignString(result, source, "audience", {max: 16, fallback: partial ? undefined : ""});

  for (const field of ["title", "message"]) {
    if (Object.hasOwn(result, field) && !result[field]) {
      throw new HttpError(400, `${field} cannot be empty.`);
    }
  }

  if (Object.hasOwn(result, "id") && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(result.id)) {
    throw new HttpError(400, "id may contain letters, numbers, dots, underscores, and hyphens.");
  }
  if (Object.hasOwn(result, "level")) {
    result.level = result.level.toLowerCase();
    if (!NOTICE_LEVELS.has(result.level)) {
      throw new HttpError(400, "level must be info, important, or critical.");
    }
  }
  if (Object.hasOwn(result, "status")) {
    result.status = result.status.toLowerCase();
    if (!NOTICE_STATUSES.has(result.status)) {
      throw new HttpError(400, "status must be draft, published, or archived.");
    }
  }
  if (Object.hasOwn(result, "type")) {
    result.type = result.type.toLowerCase() || "general";
  }
  if (Object.hasOwn(result, "audience") && result.audience) {
    result.audience = result.audience.toLowerCase();
    if (!NOTICE_AUDIENCES.has(result.audience)) {
      throw new HttpError(400, "audience must be production, preview, local, or all.");
    }
  }
  for (const field of ["startsAt", "endsAt"]) {
    if (result[field] && !Number.isFinite(Date.parse(result[field]))) {
      throw new HttpError(400, `${field} must be an ISO date/time.`);
    }
  }
  if (result.url && !/^https:\/\//i.test(result.url)) {
    throw new HttpError(400, "url must use https.");
  }
  return result;
}

export function publicNotice(row) {
  const status = String(row.status || "published").toLowerCase();
  return {
    id: String(row.id || ""),
    version: String(row.version || 1),
    title: String(row.title || ""),
    message: String(row.message || ""),
    level: String(row.level || "info"),
    type: String(row.type || "general"),
    url: String(row.url || ""),
    startsAt: String(row.starts_at ?? row.startsAt ?? ""),
    endsAt: String(row.ends_at ?? row.endsAt ?? ""),
    audience: normalizeNoticeAudience(row.audience, "preview"),
    updatedAt: String(row.updated_at ?? row.updatedAt ?? ""),
    archived: status === "archived" || row.archived === true,
  };
}

export function adminNotice(row) {
  return {
    ...publicNotice(row),
    status: String(row.status || "draft"),
    createdAt: String(row.created_at ?? row.createdAt ?? ""),
    updatedAt: String(row.updated_at ?? row.updatedAt ?? ""),
  };
}

export function noticeIsActive(notice, now = Date.now()) {
  const startsAt = Date.parse(String(notice.startsAt || notice.starts_at || ""));
  const endsAt = Date.parse(String(notice.endsAt || notice.ends_at || ""));
  return (!Number.isFinite(startsAt) || now >= startsAt) && (!Number.isFinite(endsAt) || now <= endsAt);
}

export async function listPublicNotices(env, audience = "production") {
  if (!env.DB) {
    return [];
  }
  try {
    const result = await env.DB.prepare(
      `SELECT * FROM notices WHERE status IN ('published', 'archived')
       ORDER BY CASE status WHEN 'published' THEN 0 ELSE 1 END, updated_at DESC`
    ).all();
    return (result.results || [])
      .map(publicNotice)
      .filter((notice) => notice.audience === "all" || notice.audience === audience)
      .filter((notice) => notice.archived || noticeIsActive(notice));
  } catch (error) {
    if (String(error?.message || error).toLowerCase().includes("no such table")) {
      return [];
    }
    throw error;
  }
}

export function noticeAudience(request, fallback = "production") {
  let hostname = "";
  try {
    hostname = new URL(request?.url || "").hostname.toLowerCase();
  } catch (_error) {
    hostname = "";
  }
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1") {
    return "local";
  }
  if (hostname === "wip.mountlet.pages.dev" || hostname.includes("preview")) {
    return "preview";
  }
  return normalizeNoticeAudience(fallback, "production");
}

export function requestedNoticeAudience(request) {
  try {
    const requested = new URL(request.url).searchParams.get("buildChannel");
    return normalizeNoticeAudience(requested, noticeAudience(request));
  } catch (_error) {
    return noticeAudience(request);
  }
}

export function normalizeNoticeAudience(value, fallback = "production") {
  const normalized = String(value || "").trim().toLowerCase();
  return NOTICE_AUDIENCES.has(normalized) ? normalized : fallback;
}

// Retained for callers outside this repository that used the original name.
export const listPublishedNotices = listPublicNotices;

function assignString(result, source, key, {aliases = [], required = false, max = 0, fallback} = {}) {
  const sourceKey = [key, ...aliases].find((candidate) => Object.hasOwn(source, candidate));
  if (sourceKey === undefined) {
    if (required) {
      throw new HttpError(400, `${key} is required.`);
    }
    if (fallback !== undefined) {
      result[key] = fallback;
    }
    return;
  }
  const value = String(source[sourceKey] ?? "").trim();
  if (required && !value) {
    throw new HttpError(400, `${key} is required.`);
  }
  if (max && value.length > max) {
    throw new HttpError(400, `${key} is too long.`);
  }
  result[key] = value;
}
