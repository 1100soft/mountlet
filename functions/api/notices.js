import {jsonResponse} from "../_lib/license.js";
import {listPublishedNotices, noticeIsActive, publicNotice} from "../_lib/notices.js";

export async function onRequestGet({env}) {
  const stored = await listPublishedNotices(env);
  const configured = configuredNotices(env);
  const merged = new Map();
  for (const notice of [...configured, ...stored]) {
    const current = merged.get(notice.id);
    if (!current || Number(notice.version || 1) >= Number(current.version || 1)) {
      merged.set(notice.id, notice);
    }
  }
  return jsonResponse({
    ok: true,
    notices: [...merged.values()].sort((left, right) => String(right.updatedAt || "").localeCompare(String(left.updatedAt || ""))),
  });
}

function configuredNotices(env) {
  const text = String(env.MOUNTLET_NOTICES_JSON || env.NOTICES_JSON || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    const values = Array.isArray(parsed) ? parsed : parsed.notices;
    return (Array.isArray(values) ? values : [])
      .filter((notice) => notice && typeof notice === "object")
      .map((notice) => publicNotice(notice))
      .filter((notice) => notice.id && notice.title && notice.message && noticeIsActive(notice));
  } catch (_error) {
    return [];
  }
}
