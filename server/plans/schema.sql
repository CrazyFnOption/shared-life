CREATE TABLE plan_records (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('wish','plan')),
 scope TEXT NOT NULL CHECK(scope IN ('private','shared')), namespace TEXT NOT NULL,
 author_id TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200), body_markdown TEXT NOT NULL DEFAULT '',
 wish_kind TEXT NOT NULL DEFAULT '', target_date TEXT, source_wish_id TEXT REFERENCES plan_records(id),
 realized INTEGER NOT NULL DEFAULT 0 CHECK(realized IN (0,1)), archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)), version INTEGER NOT NULL CHECK(version>0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX plan_record_namespace ON plan_records(scope,namespace);
CREATE UNIQUE INDEX plan_wish_conversion ON plan_records(source_wish_id) WHERE source_wish_id IS NOT NULL AND deleted=0;
CREATE TABLE plan_stages (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan_records(id), title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 position INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL CHECK(version>0), deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
);
CREATE TABLE plan_tasks (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan_records(id), stage_id TEXT NOT NULL REFERENCES plan_stages(id),
 author_id TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id),
 title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200), body_markdown TEXT NOT NULL DEFAULT '',
 assignee_ids_json TEXT NOT NULL DEFAULT '[]', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
 position INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL CHECK(version>0), deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
);
CREATE INDEX plan_tasks_parent ON plan_tasks(plan_id,stage_id);
CREATE TABLE plan_attachments (
 id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL CHECK(kind IN ('photo','file')), name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 255),
 mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>=0 AND size_bytes<=20971520),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), data BLOB, state TEXT NOT NULL CHECK(state IN ('pending','uploaded','deleted'))
);
CREATE TABLE plan_deletion_requests (
 id TEXT PRIMARY KEY, target_type TEXT NOT NULL CHECK(target_type IN ('wish','plan','task')), target_id TEXT NOT NULL,
 requester_id TEXT NOT NULL REFERENCES users(id), author_id TEXT NOT NULL REFERENCES users(id),
 source_restore_id TEXT NOT NULL, source_scope TEXT NOT NULL CHECK(source_scope IN ('private','shared')),
 family_id TEXT REFERENCES families(id), requester_epoch INTEGER NOT NULL, author_epoch INTEGER NOT NULL,
 target_version INTEGER NOT NULL CHECK(target_version>0), reason TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','stale')), version INTEGER NOT NULL CHECK(version>0)
);
CREATE UNIQUE INDEX plan_pending_deletion ON plan_deletion_requests(target_id,requester_id) WHERE status='pending';
CREATE TABLE plan_operations (
 user_id TEXT NOT NULL REFERENCES users(id), op_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, operation_kind TEXT NOT NULL,
 request_hash TEXT NOT NULL, receipt_json TEXT NOT NULL,
 source_restore_id TEXT NOT NULL, source_scope TEXT NOT NULL CHECK(source_scope IN ('private','shared')),
 family_id TEXT REFERENCES families(id), relation_epoch INTEGER NOT NULL, PRIMARY KEY(user_id,op_id)
);
