CREATE TABLE memo_categories (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 creator_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 40),
 version INTEGER NOT NULL CHECK(version>0), deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX memo_category_name ON memo_categories(scope,namespace,name) WHERE deleted=0;
CREATE TABLE memos (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 author_id TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id),
 category_id TEXT REFERENCES memo_categories(id), title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 body_markdown TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
 version INTEGER NOT NULL CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'memo' CHECK(kind IN ('memo','schedule')),
 schedule_json TEXT, reminders_json TEXT NOT NULL DEFAULT '[]', recurrence_json TEXT,
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 completed_at TEXT, last_cycle TEXT
);
CREATE INDEX memos_namespace ON memos(scope,namespace);
CREATE TABLE memo_attachments (
 id TEXT PRIMARY KEY, memo_id TEXT NOT NULL REFERENCES memos(id), created_by TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL CHECK(kind IN ('photo','file')), name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 255),
 mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>=0 AND size_bytes<=20971520),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), data BLOB,
 state TEXT NOT NULL CHECK(state IN ('pending','uploaded','deleted'))
);
CREATE TABLE memo_operations (
 user_id TEXT NOT NULL REFERENCES users(id), op_id TEXT NOT NULL, entity_type TEXT NOT NULL,
 entity_id TEXT NOT NULL, request_hash TEXT NOT NULL, receipt_json TEXT NOT NULL,
 source_restore_id TEXT NOT NULL, source_scope TEXT NOT NULL CHECK(source_scope IN ('private','shared')),
 family_id TEXT REFERENCES families(id), relation_epoch INTEGER,
 PRIMARY KEY(user_id,op_id)
);
CREATE TABLE memo_focus (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0), targets_json TEXT NOT NULL,
 UNIQUE(scope,namespace)
);
CREATE TABLE memo_category_orders (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0), category_ids_json TEXT NOT NULL,
 UNIQUE(scope,namespace)
);
CREATE TABLE memo_deletion_requests (
 id TEXT PRIMARY KEY, memo_id TEXT NOT NULL REFERENCES memos(id), requester_id TEXT NOT NULL REFERENCES users(id),
 author_id TEXT NOT NULL REFERENCES users(id), target_version INTEGER NOT NULL CHECK(target_version>0),
 reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','stale')),
 source_restore_id TEXT NOT NULL, requester_epoch INTEGER, author_epoch INTEGER,
 version INTEGER NOT NULL CHECK(version>0), created_at TEXT NOT NULL, decided_at TEXT
);
CREATE UNIQUE INDEX memo_pending_deletion ON memo_deletion_requests(memo_id,requester_id) WHERE status='pending';
CREATE TABLE memo_reminder_events (
 id TEXT PRIMARY KEY, memo_id TEXT NOT NULL REFERENCES memos(id), rule_id TEXT NOT NULL,
 occurrence TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 source_restore_id TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 relation_epoch INTEGER, record_version INTEGER NOT NULL, rule_signature TEXT NOT NULL, created_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('ready','cancelled')),
 UNIQUE(memo_id,rule_id,occurrence,user_id,source_restore_id)
);
CREATE TABLE memo_message_ignores (
 user_id TEXT NOT NULL REFERENCES users(id), event_id TEXT NOT NULL, source_restore_id TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL, ignored_at TEXT NOT NULL,
 PRIMARY KEY(user_id,event_id,source_restore_id)
);
