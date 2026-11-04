CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL CHECK (scope IN ('shared', 'private')),
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents BETWEEN 1 AND 999999999),
    purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 1 AND 500),
    occurred_at TEXT NOT NULL,
    location TEXT NOT NULL CHECK (length(location) <= 200),
    version INTEGER NOT NULL CHECK (version > 0)
);

CREATE TABLE IF NOT EXISTS operations (
    user_id TEXT NOT NULL REFERENCES users(id),
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY (user_id, operation_id)
);

CREATE TABLE IF NOT EXISTS revisions (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    version INTEGER NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    operation_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    PRIMARY KEY (expense_id, version),
    FOREIGN KEY (user_id, operation_id) REFERENCES operations(user_id, operation_id)
);
