CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS families (id TEXT PRIMARY KEY,name TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS family_members (user_id TEXT PRIMARY KEY REFERENCES users(id),family_id TEXT NOT NULL REFERENCES families(id));
CREATE TABLE IF NOT EXISTS family_invites (id TEXT PRIMARY KEY,family_id TEXT NOT NULL REFERENCES families(id),inviter_id TEXT NOT NULL REFERENCES users(id),target_user_id TEXT NOT NULL REFERENCES users(id),status TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,decided_at TEXT);
CREATE TABLE IF NOT EXISTS family_actions (user_id TEXT NOT NULL REFERENCES users(id),request_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,result_id TEXT NOT NULL,PRIMARY KEY(user_id,request_id));
CREATE TABLE IF NOT EXISTS monthly_settings (scope TEXT NOT NULL,namespace TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,time TEXT NOT NULL DEFAULT '20:00',start TEXT,updated_by TEXT,PRIMARY KEY(scope,namespace));
