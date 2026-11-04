CREATE TABLE IF NOT EXISTS category_tombstones (
    scope TEXT NOT NULL,
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY(scope, namespace, name)
);
CREATE TABLE IF NOT EXISTS category_changes (
    preview_id TEXT PRIMARY KEY,
    id TEXT UNIQUE,
    requester_id TEXT NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL CHECK(scope IN ('private','shared')),
    namespace TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('rename','delete','reclassify')),
    source_name TEXT,
    target_name TEXT NOT NULL,
    items_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    catalog_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('preview','pending','applied','rejected','stale')),
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    decided_at TEXT,
    request_hash TEXT
);
CREATE TABLE IF NOT EXISTS category_approvals (
    change_id TEXT NOT NULL REFERENCES category_changes(id),
    owner_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected')),
    decided_at TEXT,
    PRIMARY KEY(change_id, owner_id)
);
CREATE TABLE IF NOT EXISTS category_decisions (
    user_id TEXT NOT NULL REFERENCES users(id),
    decision_id TEXT NOT NULL,
    change_id TEXT NOT NULL REFERENCES category_changes(id),
    request_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY(user_id, decision_id)
);
