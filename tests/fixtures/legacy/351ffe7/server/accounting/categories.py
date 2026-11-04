"""Small shared/private category catalogs; names are stable within each scope."""

import sqlite3


def ensure_category(db: sqlite3.Connection, user_id: str, scope: str, name: str) -> dict:
    db.execute(
        "INSERT OR IGNORE INTO categories (scope, namespace, name) VALUES (?, ?, ?)",
        (scope, "" if scope == "shared" else user_id, name),
    )
    return {"scope": scope, "name": name}


def visible_categories(db: sqlite3.Connection, user_id: str) -> list[dict]:
    return [dict(row) for row in db.execute(
        "SELECT scope, name FROM categories WHERE scope = 'shared' OR namespace = ? ORDER BY scope, name",
        (user_id,),
    )]
