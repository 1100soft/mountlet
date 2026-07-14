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

CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  message TEXT NOT NULL,
  contact TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  runtime_log TEXT NOT NULL,
  rclone_log TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  github_issue_number INTEGER,
  github_issue_url TEXT NOT NULL DEFAULT '',
  github_sync_status TEXT NOT NULL DEFAULT '',
  github_sync_error TEXT NOT NULL DEFAULT '',
  email_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_status_created
ON reports(status, created_at);

CREATE INDEX IF NOT EXISTS idx_reports_github_issue
ON reports(github_issue_number);
