import {jsonResponse} from "../_lib/license.js";

export async function onRequestGet({env}) {
  const notices = parseNotices(env.MOUNTLET_NOTICES_JSON || env.NOTICES_JSON || "");
  return jsonResponse({
    ok: true,
    notices: activeNotices(notices),
  });
}

function parseNotices(value) {
  const text = String(value || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed;
    }
    if (Array.isArray(parsed.notices)) {
      return parsed.notices;
    }
  } catch (_error) {
    return [];
  }
  return [];
}

function activeNotices(notices) {
  const now = Date.now();
  return notices
    .filter((notice) => notice && typeof notice === "object")
    .filter((notice) => isActive(notice, now))
    .map((notice) => ({
      id: stringField(notice.id),
      title: stringField(notice.title),
      message: stringField(notice.message),
      level: normalizeLevel(notice.level),
      type: stringField(notice.type || "general"),
      url: stringField(notice.url),
      version: stringField(notice.version || "1"),
      startsAt: stringField(notice.startsAt || notice.starts_at),
      endsAt: stringField(notice.endsAt || notice.ends_at),
    }))
    .filter((notice) => notice.id && notice.title && notice.message);
}

function isActive(notice, now) {
  const startsAt = parseTime(notice.startsAt || notice.starts_at);
  const endsAt = parseTime(notice.endsAt || notice.ends_at);
  if (startsAt && now < startsAt) {
    return false;
  }
  if (endsAt && now > endsAt) {
    return false;
  }
  return true;
}

function parseTime(value) {
  const text = String(value || "").trim();
  if (!text) {
    return 0;
  }
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizeLevel(value) {
  const level = stringField(value).toLowerCase();
  return ["info", "important", "critical"].includes(level) ? level : "info";
}

function stringField(value) {
  return String(value || "").trim();
}
