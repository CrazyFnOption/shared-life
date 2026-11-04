"""Category catalogs are owned by one person or one explicit family."""
import sqlite3
from server.accounting import families


def ensure_category(db: sqlite3.Connection, user_id: str, scope: str, name: str) -> dict:
    namespace = families.require_shared(db, user_id) if scope == 'shared' else user_id
    db.execute("INSERT OR IGNORE INTO categories (scope,namespace,name) VALUES(?,?,?)", (scope,namespace,name))
    return {"scope":scope,"name":name}


def visible_categories(db: sqlite3.Connection, user_id: str) -> list[dict]:
    return [dict(row) for row in db.execute("SELECT scope,name FROM categories WHERE (scope='shared' AND namespace=?) OR (scope='private' AND namespace=?) ORDER BY scope,name", (families.current_id(db,user_id),user_id))]
