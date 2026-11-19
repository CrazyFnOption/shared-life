ALTER TABLE memos ADD COLUMN checklist_json TEXT NOT NULL DEFAULT '{"items":[],"states":{}}';
ALTER TABLE memo_reminder_events ADD COLUMN delivery_at TEXT;
ALTER TABLE memo_reminder_events ADD COLUMN delivery_revision INTEGER NOT NULL DEFAULT 0 CHECK(delivery_revision>=0);
