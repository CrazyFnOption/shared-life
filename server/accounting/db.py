"""SQLite connections and explicit local account initialization."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from functools import lru_cache
import time
from uuid import uuid4

from argon2 import PasswordHasher


password_hasher = PasswordHasher()


class Connection(sqlite3.Connection):
    """Request metadata stays on its own connection, never global state."""
    family_headers = None
    context_user = None
    auth_session = None
    protocol = 0
    source = None
    expected_restore = None
    legacy_discovery = False

    def execute(self, sql, parameters=(), /):
        result = super().execute(sql, parameters)
        if sql.strip().upper().startswith('BEGIN') and self.auth_session:
            from fastapi import HTTPException
            if not super().execute('SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?', (*self.auth_session,int(time.time()))).fetchone():
                raise HTTPException(401, detail='Login required')
        if sql.strip().upper().startswith('BEGIN') and self.expected_restore is not None:
            from fastapi import HTTPException
            actual = super().execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
            if actual != self.expected_restore:
                raise HTTPException(409, detail={'code':'restore_changed','restore_id':actual})
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


SCHEMA_VERSION = 9
MAX_COUNTER = 9223372036854775807


def preflight(db_path):
    """Read before mkdir, migration, HTTP, worker or business writes."""
    path = Path(db_path)
    if not path.exists():
        return 'new'
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('unsupported_database_schema')
    with connect(path, read_only=True) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users','expenses','operations','revisions'}.issubset(tables):
            raise RuntimeError('unsupported_database_schema')
        marker = 'schema_migrations' in tables and db.execute("SELECT 1 FROM schema_migrations WHERE name='multi-family-v2'").fetchone()
        if version in (8,9) and marker:
            expected = expected_columns(version)
            if tables != set(expected) or any({r['name'] for r in db.execute(f'PRAGMA table_info({table})')} != columns for table,columns in expected.items()):
                raise RuntimeError('unsupported_database_schema')
            required = {'users': {'context_version','default_family_id','default_version'},
                        'family_members': {'user_id','family_id','active','relation_epoch'},
                        'operations': {'source_status','source_restore_id','relation_epoch'},
                        'notification_outbox': {'source_scope','source_restore_id','source_status'}}
            for table, columns in required.items():
                if not columns.issubset({r['name'] for r in db.execute(f'PRAGMA table_info({table})')}):
                    raise RuntimeError('unsupported_database_schema')
            if [(r['name'],r['pk']) for r in db.execute('PRAGMA table_info(family_members)') if r['pk']] != [('user_id',1),('family_id',2)]:
                raise RuntimeError('unsupported_database_schema')
            if version==9 and {r[0] for r in db.execute("SELECT name FROM schema_migrations")}!={'families-v1','family-owners-v1','multi-family-v2','memos-v1','plans-v1'}:
                raise RuntimeError('unsupported_database_schema')
            return 'current' if version==SCHEMA_VERSION else 'previous'
        if version != 0 or marker or 'legacy_relation_epochs' in tables:
            raise RuntimeError('unsupported_database_schema')
        if 'schema_migrations' in tables and any(r[0] not in {'families-v1','family-owners-v1'} for r in db.execute('SELECT name FROM schema_migrations')):
            raise RuntimeError('unsupported_database_schema')
        allowed = {'users','sessions','expenses','operations','revisions','categories','expense_images','refunds','change_requests','app_state','weekly_settings','reports','report_tasks','model_usage','push_subscriptions','notification_outbox','push_deliveries','backup_runs','user_sync','schema_migrations','families','family_members','family_invites','family_actions','monthly_settings','category_tombstones','category_changes','category_approvals','category_decisions'}
        expected = expected_columns(0)
        if not tables.issubset(allowed) or any(not {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}.issubset(expected[table]) for table in tables):
            raise RuntimeError('unsupported_database_schema')
        if 'family_members' in tables and [r['name'] for r in db.execute('PRAGMA table_info(family_members)') if r['pk']] != ['user_id']:
            raise RuntimeError('unsupported_database_schema')
        # Only real v1 columns are accepted. Future schema must not be mistaken for old.
        known_additions = {'context_version','default_family_id','default_version','default_invalidated_family_id'}
        if known_additions & {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
            raise RuntimeError('unsupported_database_schema')
        return 'legacy'


@lru_cache(maxsize=4)
def expected_columns(version):
    with connect(':memory:') as reference:
        reference.execute('BEGIN')
        _initialize_legacy(reference)
        if version in (8,9):
            _migrate_multi_family(reference)
        if version==9:
            _migrate_memos(reference)
        return {r['name']:frozenset(c['name'] for c in reference.execute(f"PRAGMA table_info({r['name']})")) for r in reference.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def initialize_database(db_path: str | Path) -> None:
    status = preflight(db_path)
    if status == 'current':
        return
    if status in ('legacy','previous'):
        raise RuntimeError('migration_required: run python -m scripts.migrate_memos --database <path>')
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            _initialize_legacy(db)
            _migrate_multi_family(db)
            _migrate_memos(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _initialize_legacy(db):
    schema = Path(__file__).parent.parent / 'migrations' / '001_initial.sql'
    for statement in schema.read_text().split(';'):
        if statement.strip():
            db.execute(statement)
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
    migrate_family_owners(db)
    for statement in schema.with_name('006_category_changes.sql').read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    # These two column additions share this transaction and can be retried.
    statements = [s for s in schema.with_name('007_rejection_reasons.sql').read_text().split(';') if s.strip()]
    for table, statement in zip(('change_requests', 'category_changes'), statements, strict=True):
        if 'rejection_reason' not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(statement)


def migrate_database(db_path):
    status = preflight(db_path)
    if status == 'new':
        raise ValueError('Migration source must exist')
    if status == 'current':
        from server.automation.backups import validate_database
        with connect(db_path, read_only=True) as db:
            validate_database(db)
        return False
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            from server.memos.migration_guard import capture,verify
            before=capture(db)
            if status == 'legacy':
                _initialize_legacy(db)
                _migrate_multi_family(db)
            _migrate_memos(db)
            from server.automation.backups import validate_database
            validate_database(db)
            verify(db,before)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return True


def _migrate_memos(db):
    sql=Path(__file__).parent.parent / 'migrations' / '009_memos.sql'
    for statement in sql.read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    from server.plans import initialize as initialize_plans
    initialize_plans(db)
    db.execute("INSERT INTO schema_migrations(name) VALUES('memos-v1')")
    db.execute('PRAGMA user_version=9')


def _migrate_multi_family(db):
    import json
    from server.accounting.ledger import canonical_json
    sql = Path(__file__).parent.parent / 'migrations' / '008_multi_family.sql'
    for statement in sql.read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    restore_id = db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    for row in db.execute('SELECT * FROM operations').fetchall():
        fid = row['family_id']
        epoch = None
        if fid:
            mapping = db.execute('SELECT relation_epoch FROM legacy_relation_epochs WHERE user_id=? AND family_id=? AND membership_version=?',(row['user_id'],fid,row['membership_version'])).fetchone()
            epoch = mapping[0] if mapping else None
        db.execute('UPDATE operations SET source_restore_id=?,source_scope=?,relation_epoch=?,source_status=? WHERE user_id=? AND operation_id=?',
                   (restore_id,'shared' if fid else 'private',epoch,'verified' if not fid or epoch else 'held',row['user_id'],row['operation_id']))
    for row in db.execute('SELECT * FROM family_invites').fetchall():
        member = db.execute('SELECT relation_epoch FROM family_members WHERE user_id=? AND family_id=? AND active=1',(row['inviter_id'],row['family_id'])).fetchone()
        if row['status']=='pending' and member:
            db.execute('UPDATE family_invites SET inviter_epoch=? WHERE id=?',(member[0],row['id']))
    for row in db.execute('SELECT * FROM change_requests').fetchall():
        members = [db.execute('SELECT relation_epoch FROM family_members WHERE user_id=? AND family_id=? AND active=1',(uid,row['family_id'])).fetchone() for uid in (row['requester_id'],row['owner_id'])]
        if not row['withdrawn'] and all(members):
            db.execute("UPDATE change_requests SET source_restore_id=?,requester_epoch=?,owner_epoch=?,source_status='verified' WHERE id=?",(restore_id,members[0][0],members[1][0],row['id']))
    for row in db.execute('SELECT * FROM category_changes').fetchall():
        old = json.loads(row['context_json'])
        mapped = {}
        valid = old.get('restore_id')==restore_id
        for uid,version in old.get('members',{}).items():
            m = db.execute('SELECT relation_epoch FROM legacy_relation_epochs WHERE user_id=? AND family_id=? AND membership_version=?',(uid,row['namespace'],version)).fetchone()
            if not m:
                valid=False
            else:
                mapped[uid]=m[0]
        if valid:
            context={'protocol_version':2,'restore_id':restore_id,'family_id':row['namespace'] if row['scope']=='shared' else None,'members':mapped}
            db.execute("UPDATE category_changes SET relation_context_json=?,source_status='verified' WHERE preview_id=?",(canonical_json(context),row['preview_id']))
    for row in db.execute('SELECT * FROM notification_outbox').fetchall():
        report=db.execute('SELECT * FROM reports WHERE id=?',(row['report_id'],)).fetchone() if row['report_id'] else None
        scope='unknown';fid=None;epoch=None
        if report:
            scope=report['scope']
            if scope=='shared':
                fid=report['owner_id']
                m=db.execute('SELECT relation_epoch FROM family_members WHERE user_id=? AND family_id=? AND active=1',(row['user_id'],fid)).fetchone()
                epoch=m[0] if m else None
            elif report['owner_id']!=row['user_id']:
                scope='unknown'
        elif row['dedup_key'].startswith('weekly:'):
            scope='account'
        verified=scope!='unknown' and (scope!='shared' or epoch is not None)
        db.execute('UPDATE notification_outbox SET source_scope=?,family_id=?,relation_epoch=?,source_restore_id=?,source_status=? WHERE id=?',(scope,fid,epoch,restore_id,'verified' if verified else 'held',row['id']))
    db.execute("INSERT INTO schema_migrations(name) VALUES('multi-family-v2')")
    db.execute('PRAGMA user_version=8')

def migrate_families(db, schema):
    import json
    from server.accounting.families import create_explicit_family
    for statement in schema.with_name('005_families.sql').read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    for table, columns in {
        'families': {'owner_id': 'TEXT REFERENCES users(id)'},
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
    demo_owner = db.execute("SELECT id FROM users WHERE username='demo-a'").fetchone()
    fid = create_explicit_family(db, sorted(legacy_ids), owner_id=demo_owner['id']) if len(legacy_ids) == 2 else None
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


def migrate_family_owners(db):
    """Choose an owner only from recorded creation/join evidence, atomically."""
    if db.execute("SELECT 1 FROM schema_migrations WHERE name='family-owners-v1'").fetchone():
        return
    for family in db.execute('SELECT * FROM families WHERE owner_id IS NULL').fetchall():
        fid = family['id']
        members = {r['user_id'] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=?', (fid,))}
        if not members and family['archived']:
            continue
        creators = {r['user_id'] for r in db.execute("SELECT user_id FROM family_actions WHERE kind='create' AND result_id=?", (fid,))} & members
        owner = next(iter(creators)) if len(creators) == 1 else None
        if not owner:
            demo = db.execute("""SELECT u.id FROM users u JOIN family_members m ON m.user_id=u.id
                WHERE m.family_id=? AND u.username='demo-a' AND
                (u.legacy_family_id=? OR (
                    NOT EXISTS(SELECT 1 FROM family_actions WHERE kind='create' AND result_id=?)
                    AND EXISTS(SELECT 1 FROM families WHERE id=? AND name='演示家庭')
                    AND EXISTS(SELECT 1 FROM users b JOIN family_members bm ON bm.user_id=b.id WHERE bm.family_id=? AND b.username='demo-b')))""", (fid,fid,fid,fid,fid)).fetchone()
            owner = demo['id'] if demo else None
        if not owner:
            # Prefer the accepted timestamp; old fixtures may omit decided_at.
            joined = db.execute("""SELECT i.target_user_id FROM family_invites i
                JOIN family_members m ON m.user_id=i.target_user_id AND m.family_id=i.family_id
                WHERE i.family_id=? AND i.status='accepted'
                ORDER BY COALESCE(i.decided_at,i.created_at),i.created_at,i.id LIMIT 1""", (fid,)).fetchone()
            owner = joined['target_user_id'] if joined else None
        if not owner:
            raise RuntimeError(f'family_owner_migration_required: no recorded current owner for family {fid}')
        db.execute('UPDATE families SET owner_id=? WHERE id=?', (owner,fid))
    db.execute("INSERT INTO schema_migrations VALUES('family-owners-v1')")


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
