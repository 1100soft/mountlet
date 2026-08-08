import {handleError, HttpError, jsonResponse, nowIso, readJson, requireEnv} from "../../_lib/license.js";
import {
  adminNotice,
  ensureNoticeSchema,
  normalizeNoticeInput,
  noticeAudience,
} from "../../_lib/notices.js";

export async function onRequestGet({request, env}) {
  try {
    authorize(request, env);
    await ensureNoticeSchema(env);
    const result = await env.DB.prepare("SELECT * FROM notices ORDER BY updated_at DESC").all();
    return jsonResponse({ok: true, notices: (result.results || []).map(adminNotice)});
  } catch (error) {
    return handleError(error);
  }
}

export async function onRequestPost({request, env}) {
  try {
    authorize(request, env);
    await ensureNoticeSchema(env);
    const notice = normalizeNoticeInput(await readJson(request));
    notice.audience = notice.audience || noticeAudience(request);
    const now = nowIso();
    await env.DB.prepare(`
      INSERT INTO notices (
        id, version, title, message, level, type, url, starts_at, ends_at, audience,
        status, created_at, updated_at
      ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      notice.id, notice.title, notice.message, notice.level, notice.type, notice.url,
      notice.startsAt, notice.endsAt, notice.audience, notice.status, now, now
    ).run();
    const row = await env.DB.prepare("SELECT * FROM notices WHERE id = ?").bind(notice.id).first();
    return jsonResponse({ok: true, notice: adminNotice(row)}, 201);
  } catch (error) {
    if (String(error?.message || error).includes("UNIQUE constraint failed")) {
      return jsonResponse({error: "A notice with that id already exists."}, 409);
    }
    return handleError(error);
  }
}

export async function onRequestPatch({request, env}) {
  try {
    authorize(request, env);
    await ensureNoticeSchema(env);
    const body = normalizeNoticeInput(await readJson(request), {partial: true});
    if (!body.id) {
      throw new HttpError(400, "id is required.");
    }
    const current = await env.DB.prepare("SELECT * FROM notices WHERE id = ?").bind(body.id).first();
    if (!current) {
      throw new HttpError(404, "Notice not found.");
    }
    const next = {
      title: body.title ?? current.title,
      message: body.message ?? current.message,
      level: body.level ?? current.level,
      type: body.type ?? current.type,
      url: body.url ?? current.url,
      startsAt: body.startsAt ?? current.starts_at,
      endsAt: body.endsAt ?? current.ends_at,
      audience: body.audience ?? current.audience ?? "preview",
      status: body.status ?? current.status,
    };
    await env.DB.prepare(`
      UPDATE notices SET version = version + 1, title = ?, message = ?, level = ?, type = ?, url = ?,
        starts_at = ?, ends_at = ?, audience = ?, status = ?, updated_at = ? WHERE id = ?
    `).bind(
      next.title, next.message, next.level, next.type, next.url,
      next.startsAt, next.endsAt, next.audience, next.status, nowIso(), body.id
    ).run();
    const row = await env.DB.prepare("SELECT * FROM notices WHERE id = ?").bind(body.id).first();
    return jsonResponse({ok: true, notice: adminNotice(row)});
  } catch (error) {
    return handleError(error);
  }
}

export async function onRequestDelete({request, env}) {
  try {
    authorize(request, env);
    await ensureNoticeSchema(env);
    const url = new URL(request.url);
    const id = String(url.searchParams.get("id") || "").trim();
    if (!id) {
      throw new HttpError(400, "id is required.");
    }
    const current = await env.DB.prepare("SELECT * FROM notices WHERE id = ?").bind(id).first();
    if (!current) {
      throw new HttpError(404, "Notice not found.");
    }
    if (current.level === "critical" || current.type === "price") {
      throw new HttpError(409, "Critical notices can be archived but not deleted.");
    }
    if (current.status === "published") {
      throw new HttpError(409, "Archive a published notice before deleting it.");
    }
    await env.DB.prepare("DELETE FROM notices WHERE id = ?").bind(id).run();
    return jsonResponse({ok: true, deleted: id});
  } catch (error) {
    return handleError(error);
  }
}

function authorize(request, env) {
  const expected = requireEnv(env, "LICENSE_ADMIN_TOKEN");
  const provided = request.headers.get("authorization") || "";
  if (provided !== `Bearer ${expected}`) {
    throw new HttpError(401, "Unauthorized.");
  }
}
