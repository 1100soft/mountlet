#!/usr/bin/env node

import assert from "node:assert/strict";

import {
  ensureNoticeSchema,
  normalizeNoticeAudience,
  normalizeNoticeInput,
  noticeAudience,
  publicNotice,
  requestedNoticeAudience,
} from "../../functions/_lib/notices.js";

assert.equal(normalizeNoticeAudience("PREVIEW"), "preview");
assert.equal(normalizeNoticeAudience("invalid", "local"), "local");
assert.equal(noticeAudience({url: "https://mountlet.app/api/notices"}), "production");
assert.equal(noticeAudience({url: "https://wip.mountlet.pages.dev/api/notices"}), "preview");
assert.equal(noticeAudience({url: "http://127.0.0.1:8788/api/notices"}), "local");
assert.equal(
  requestedNoticeAudience({url: "https://mountlet.app/api/notices?buildChannel=local"}),
  "local",
);
assert.equal(publicNotice({id: "legacy"}).audience, "preview");
assert.equal(normalizeNoticeInput({
  id: "release",
  title: "Release",
  message: "Available",
  audience: "all",
}).audience, "all");
assert.throws(
  () => normalizeNoticeInput({
    id: "bad",
    title: "Bad",
    message: "Bad",
    audience: "customers",
  }),
  /audience must be/,
);

const statements = [];
let audienceAdded = false;
const legacyEnv = {
  DB: {
    prepare(sql) {
      statements.push(sql);
      return {
        async all() {
          return {
            results: [
              {name: "id"},
              {name: "version"},
              {name: "title"},
              {name: "message"},
              {name: "status"},
            ],
          };
        },
        async run() {
          if (sql.includes("ADD COLUMN audience")) {
            audienceAdded = true;
          }
          return {success: true};
        },
      };
    },
  },
};
await ensureNoticeSchema(legacyEnv);
assert.equal(audienceAdded, true);
assert.ok(statements.some((sql) => sql.includes("DEFAULT 'preview'")));

console.log("Notice audience checks passed.");
