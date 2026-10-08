ALTER TABLE licenses ADD COLUMN billing_model TEXT NOT NULL DEFAULT 'lifetime';
ALTER TABLE licenses ADD COLUMN stripe_subscription_id TEXT NOT NULL DEFAULT '';
ALTER TABLE licenses ADD COLUMN subscription_status TEXT NOT NULL DEFAULT '';
ALTER TABLE licenses ADD COLUMN expires_at TEXT NOT NULL DEFAULT '';
