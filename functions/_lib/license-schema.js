export const REQUIRED_LICENSE_SCHEMA = {
  licenses: [
    "id",
    "license_key_hash",
    "status",
    "plan",
    "license_kind",
    "billing_model",
    "max_devices",
    "stripe_subscription_id",
    "subscription_status",
    "expires_at",
    "created_at",
    "updated_at",
  ],
  devices: [
    "id",
    "license_id",
    "device_hash",
    "device_label",
    "platform",
    "app_version",
    "activated_at",
    "last_seen_at",
    "deactivated_at",
  ],
  payments: [
    "id",
    "stripe_session_id",
    "stripe_customer_id",
    "license_id",
    "kind",
    "quantity",
    "license_key",
    "created_at",
  ],
};

const UPGRADE_COLUMNS = {
  licenses: {
    license_kind: "TEXT NOT NULL DEFAULT 'paid'",
    billing_model: "TEXT NOT NULL DEFAULT 'lifetime'",
    stripe_subscription_id: "TEXT NOT NULL DEFAULT ''",
    subscription_status: "TEXT NOT NULL DEFAULT ''",
    expires_at: "TEXT NOT NULL DEFAULT ''",
  },
  payments: {
    stripe_customer_id: "TEXT NOT NULL DEFAULT ''",
  },
};

export async function inspectLicenseSchema(env) {
  if (!env.DB) {
    return {ok: false, error: "DB binding is missing."};
  }
  try {
    const tables = {};
    for (const [table, columns] of Object.entries(REQUIRED_LICENSE_SCHEMA)) {
      const info = await env.DB.prepare(`PRAGMA table_info(${table})`).all();
      const names = new Set((info.results || []).map((row) => String(row.name || "")));
      const missing = columns.filter((column) => !names.has(column));
      tables[table] = {
        ok: missing.length === 0 && names.size > 0,
        missing,
      };
    }
    return {
      ok: Object.values(tables).every((table) => table.ok),
      tables,
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error?.message || error || "Could not inspect license database."),
    };
  }
}

export async function ensureLicenseSchema(env) {
  if (!env.DB) {
    throw new Error("DB binding is missing.");
  }
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS licenses (
      id TEXT PRIMARY KEY,
      license_key_hash TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'active',
      plan TEXT NOT NULL DEFAULT 'Mountlet License',
      license_kind TEXT NOT NULL DEFAULT 'paid',
      billing_model TEXT NOT NULL DEFAULT 'lifetime',
      max_devices INTEGER NOT NULL DEFAULT 3,
      stripe_subscription_id TEXT NOT NULL DEFAULT '',
      subscription_status TEXT NOT NULL DEFAULT '',
      expires_at TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
  `).run();
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS devices (
      id TEXT PRIMARY KEY,
      license_id TEXT NOT NULL,
      device_hash TEXT NOT NULL,
      device_label TEXT NOT NULL DEFAULT '',
      platform TEXT NOT NULL DEFAULT '',
      app_version TEXT NOT NULL DEFAULT '',
      activated_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      deactivated_at TEXT,
      FOREIGN KEY (license_id) REFERENCES licenses(id)
    )
  `).run();
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS payments (
      id TEXT PRIMARY KEY,
      stripe_session_id TEXT NOT NULL UNIQUE,
      stripe_customer_id TEXT NOT NULL DEFAULT '',
      license_id TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'new_license',
      quantity INTEGER NOT NULL DEFAULT 1,
      license_key TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (license_id) REFERENCES licenses(id)
    )
  `).run();
  await addMissingColumns(env);
  await env.DB.prepare(`
    CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_license_device
    ON devices(license_id, device_hash)
  `).run();
  await env.DB.prepare(`
    CREATE INDEX IF NOT EXISTS idx_devices_license_active
    ON devices(license_id, deactivated_at)
  `).run();
  return await inspectLicenseSchema(env);
}

async function addMissingColumns(env) {
  for (const [table, columns] of Object.entries(UPGRADE_COLUMNS)) {
    const info = await env.DB.prepare(`PRAGMA table_info(${table})`).all();
    const names = new Set((info.results || []).map((row) => String(row.name || "")));
    for (const [column, definition] of Object.entries(columns)) {
      if (!names.has(column)) {
        await env.DB.prepare(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`).run();
      }
    }
  }
}
