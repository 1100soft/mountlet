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
