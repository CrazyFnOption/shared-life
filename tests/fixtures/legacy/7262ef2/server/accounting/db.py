"""SQLite connections and explicit local account initialization."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from argon2 import PasswordHasher


password_hasher = PasswordHasher()


class Connection(sqlite3.Connection):
    """Request metadata stays on its own connection, never global state."""
    family_headers = None
    context_user = None
    auth_session = None

    def execute(self, sql, parameters=(), /):
        result = super().execute(sql, parameters)
        if sql.strip().upper().startswith('BEGIN') and self.auth_session:
            from fastapi import HTTPException
            if not super().execute('SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?', (*self.auth_session,int(time.time()))).fetchone():
                raise HTTPException(401, detail='Login required')
        if sql.strip().upper().startswith('BEGIN') and self.context_user:
            from server.accounting.families import check_context
            check_context(self, self.context_user)
        return result


@contextmanager
def connect(db_path: str | Path, *, read_only=False):
    database = Path(db_path).resolve().as_uri() + "?mode=ro" if read_only else db_path
    connection = sqlite3.connect(database, uri=read_only, timeout=10, isolation_level=None, check_same_thread=False, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.create_function("app_now", 0, time.time)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
    with connect(path) as db:
        db.executescript("BEGIN;\n" + schema.read_text() + "\nCOMMIT;")
        db.execute("BEGIN IMMEDIATE")
        if "category" not in {row["name"] for row in db.execute("PRAGMA table_info(expenses)")}:
            db.execute("ALTER TABLE expenses ADD COLUMN category TEXT NOT NULL DEFAULT '' CHECK (length(category) <= 24)")
        additions = schema.with_name("002_categories_images.sql").read_text()
        for statement in additions.split(";"):
            if statement.strip():
                db.execute(statement)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(expenses)")}
        for name, definition in {
            "voided": "INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1))",
            "rating": "INTEGER CHECK (rating IS NULL OR (typeof(rating) = 'integer' AND rating BETWEEN 1 AND 5))",
            "rating_reason": "TEXT NOT NULL DEFAULT '' CHECK (length(rating_reason) <= 500)",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE expenses ADD COLUMN {name} {definition}")
        for statement in schema.with_name("003_maintenance.sql").read_text().split(";"):
            if statement.strip():
                db.execute(statement)
        for statement in schema.with_name("004_automation.sql").read_text().split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("INSERT OR IGNORE INTO app_state(singleton,restore_id) VALUES(1,?)", (str(uuid4()),))
        migrate_families(db, schema)
        db.commit()


def migrate_families(db, schema):
    import json
    from server.accounting.families import create_explicit_family
    for statement in schema.with_name('005_families.sql').read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    for table, columns in {
        'users': {'membership_version': 'INTEGER NOT NULL DEFAULT 0', 'legacy_family_id': 'TEXT'},
        'expenses': {'family_id': 'TEXT REFERENCES families(id)'},
        'operations': {'family_id': 'TEXT REFERENCES families(id)', 'expense_id': 'TEXT', 'membership_version': 'INTEGER'},
        'change_requests': {'family_id': 'TEXT REFERENCES families(id)'},
    }.items():
        existing = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    if db.execute("SELECT 1 FROM schema_migrations WHERE name='families-v1'").fetchone():
        return
    legacy_users = list(db.execute("SELECT id FROM users WHERE username IN ('demo-a','demo-b')"))
    legacy_ids = {r['id'] for r in legacy_users}
    shared_owners = {r['owner_id'] for r in db.execute("SELECT owner_id FROM expenses WHERE scope='shared'")}
    for row in db.execute('SELECT * FROM operations'):
        expense = json.loads(row['receipt_json']).get('expense', {})
        if expense.get('scope') == 'shared':
            shared_owners.add(expense['owner_id'])
    for row in db.execute('SELECT before_json,after_json FROM revisions'):
        for key in ('before_json','after_json'):
            old = json.loads(row[key]) if row[key] else {}
            if old.get('scope')=='shared':
                shared_owners.add(old['owner_id'])
    shared_data = bool(shared_owners or db.execute("SELECT 1 FROM categories WHERE scope='shared'").fetchone() or db.execute("SELECT 1 FROM reports WHERE scope='shared'").fetchone())
    if not shared_owners.issubset(legacy_ids) or (shared_data and len(legacy_ids) != 2):
        raise RuntimeError('family_migration_required: unrecognized legacy shared data; explicit mapping required')
    fid = create_explicit_family(db, sorted(legacy_ids)) if len(legacy_ids) == 2 else None
    if fid:
        db.execute('UPDATE users SET legacy_family_id=? WHERE id IN (?,?)', (fid,*sorted(legacy_ids)))
        db.execute("UPDATE expenses SET family_id=? WHERE scope='shared'", (fid,))
        db.execute("UPDATE categories SET namespace=? WHERE scope='shared'", (fid,))
        db.execute("UPDATE reports SET owner_id=? WHERE scope='shared'", (fid,))
        db.execute('UPDATE change_requests SET family_id=?', (fid,))
    for row in db.execute('SELECT * FROM operations').fetchall():
        expense = json.loads(row['receipt_json']).get('expense', {})
        revision = db.execute('SELECT before_json FROM revisions WHERE user_id=? AND operation_id=?',(row['user_id'],row['operation_id'])).fetchone()
        before = json.loads(revision['before_json']) if revision and revision['before_json'] else {}
        shared_origin = expense.get('scope')=='shared' or before.get('scope')=='shared'
        db.execute('UPDATE operations SET expense_id=?,family_id=?,membership_version=? WHERE user_id=? AND operation_id=?', (expense.get('id'), fid if shared_origin else None, 1 if shared_origin else None,row['user_id'],row['operation_id']))
    old = db.execute('SELECT * FROM app_state WHERE singleton=1').fetchone()
    namespaces = [('private',r['id']) for r in db.execute('SELECT id FROM users')]
    if fid:
        namespaces.append(('shared',fid))
    for scope, namespace in namespaces:
        db.execute('INSERT INTO monthly_settings(scope,namespace,enabled,time,start,updated_by) VALUES(?,?,?,?,?,?)', (scope,namespace,old['monthly_enabled'],old['monthly_time'],old['monthly_start'],old['updated_by']))
    migrate_family_report_versions(db)
    db.execute("INSERT INTO schema_migrations VALUES('families-v1')")


def migrate_family_report_versions(db):
    """Translate only versions proven current before adding family_id to snapshots."""
    from server.automation import reports
    for row in db.execute('SELECT * FROM reports').fetchall():
        snapshot = reports.snapshot(db, row['scope'], row['owner_id'], row['month'])
        legacy_snapshot = [{key: value for key, value in expense.items() if key != 'family_id'}
                           for expense in snapshot]
        legacy_version = reports.version(legacy_snapshot)
        current_version = reports.version(snapshot)
        if row['data_version'] == legacy_version:
            # This is a structural hash change, not a new model input. Preserve
            # the status/content, including stale reports already awaiting work.
            db.execute('UPDATE reports SET data_version=? WHERE id=?', (current_version, row['id']))
            db.execute("""UPDATE notification_outbox SET data_version=?,
                dedup_key=CASE WHEN dedup_key=? THEN ? ELSE dedup_key END
                WHERE report_id=? AND data_version=?""",
                (current_version, f"report:{row['id']}:{legacy_version}",
                 f"report:{row['id']}:{current_version}", row['id'], legacy_version))
        # Task targets can differ from the report version. Translate each only
        # when its own target matches the old current snapshot; keep progress.
        db.execute('UPDATE report_tasks SET target_version=? WHERE report_id=? AND target_version=?',
                   (current_version, row['id'], legacy_version))


def create_user(
    db_path: str | Path, username: str, password: str, display_name: str
) -> dict[str, str]:
    """Create one explicitly requested account; never overwrite an existing user."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}",username) or not 8 <= len(password) <= 128 or not 1 <= len(display_name.strip()) <= 40:
        raise ValueError("Invalid account name, display name or password length")
    display_name=display_name.strip()
    initialize_database(db_path)
    user = {"id": str(uuid4()), "display_name": display_name, "username": username}
    encoded_password = password_hasher.hash(password)
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
            (user["id"], username, encoded_password, display_name),
        )
        db.commit()
    return user
