CREATE TABLE IF NOT EXISTS categories (
    scope TEXT NOT NULL CHECK (scope IN ('shared', 'private')),
    namespace TEXT NOT NULL,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 24),
    PRIMARY KEY (scope, namespace, name)
);
CREATE TABLE IF NOT EXISTS expense_images (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    mime_type TEXT,
    size_bytes INTEGER,
    content_hash TEXT,
    data BLOB,
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))
);
CREATE INDEX IF NOT EXISTS expense_images_expense ON expense_images(expense_id);
