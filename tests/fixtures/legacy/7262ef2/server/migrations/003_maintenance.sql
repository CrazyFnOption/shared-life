CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents BETWEEN 1 AND 999999999),
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) <= 500),
    voided INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1)),
    version INTEGER NOT NULL CHECK (version > 0)
);
CREATE INDEX IF NOT EXISTS refunds_expense ON refunds(expense_id);
CREATE TABLE IF NOT EXISTS change_requests (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    requester_id TEXT NOT NULL REFERENCES users(id),
    owner_id TEXT NOT NULL REFERENCES users(id),
    base_version INTEGER NOT NULL CHECK (base_version > 0),
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    before_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'stale')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    withdrawn INTEGER NOT NULL DEFAULT 0 CHECK (withdrawn IN (0, 1)),
    request_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_requests_expense ON change_requests(expense_id);
