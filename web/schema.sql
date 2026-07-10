CREATE TABLE IF NOT EXISTS licenses (
  id TEXT PRIMARY KEY,
  license_key_hash TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  plan TEXT NOT NULL DEFAULT 'Mountlet License',
  license_kind TEXT NOT NULL DEFAULT 'paid',
  max_devices INTEGER NOT NULL DEFAULT 3,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

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
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_license_device
ON devices(license_id, device_hash);

CREATE INDEX IF NOT EXISTS idx_devices_license_active
ON devices(license_id, deactivated_at);

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
);
