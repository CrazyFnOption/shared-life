CREATE TABLE IF NOT EXISTS app_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), restore_id TEXT NOT NULL, restored_at TEXT,
 automation_paused INTEGER NOT NULL DEFAULT 0, monthly_enabled INTEGER NOT NULL DEFAULT 0,
 monthly_time TEXT NOT NULL DEFAULT '20:00', monthly_start TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS weekly_settings (
 user_id TEXT PRIMARY KEY REFERENCES users(id), enabled INTEGER NOT NULL DEFAULT 0,
 weekday INTEGER NOT NULL DEFAULT 6, time TEXT NOT NULL DEFAULT '19:00'
);
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '', month TEXT NOT NULL,
 status TEXT NOT NULL, data_version TEXT NOT NULL, generated_at TEXT, provider TEXT, model TEXT,
 error TEXT, content_json TEXT, UNIQUE(scope,owner_id,month)
);
CREATE TABLE IF NOT EXISTS report_tasks (
 report_id TEXT PRIMARY KEY REFERENCES reports(id), target_version TEXT NOT NULL, restore_id TEXT NOT NULL,
 next_attempt REAL NOT NULL, claim_token TEXT, lease_until REAL, progress_json TEXT NOT NULL DEFAULT '{}',
 failures INTEGER NOT NULL DEFAULT 0, rounds INTEGER NOT NULL DEFAULT 0, retry_day TEXT,
 config_sig TEXT
);
CREATE TABLE IF NOT EXISTS model_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS push_subscriptions (
 owner_id TEXT NOT NULL REFERENCES users(id), endpoint TEXT NOT NULL, keys_json TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(owner_id,endpoint)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), dedup_key TEXT NOT NULL,
 report_id TEXT REFERENCES reports(id), data_version TEXT, status TEXT NOT NULL DEFAULT 'pending',
 next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id,dedup_key)
);
CREATE TABLE IF NOT EXISTS push_deliveries (
 outbox_id TEXT NOT NULL REFERENCES notification_outbox(id), endpoint TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(outbox_id,endpoint)
);
CREATE TABLE IF NOT EXISTS backup_runs (day TEXT PRIMARY KEY, status TEXT NOT NULL, destination TEXT);
CREATE TABLE IF NOT EXISTS user_sync (
 user_id TEXT PRIMARY KEY REFERENCES users(id), last_synced_at TEXT NOT NULL
);
