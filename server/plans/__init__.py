"""Scope-bound manual wishes and plans; no AI integration."""
from pathlib import Path

def initialize(db):
    for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
        if statement.strip():db.execute(statement)
    db.execute("INSERT INTO schema_migrations(name) VALUES('plans-v1')")

def rebind_restore(db,restore_id):
    db.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE status='pending'")
    db.execute('UPDATE plan_operations SET source_restore_id=?',(restore_id,))

def validate_database(db):
    from .service import validate_database as validate
    validate(db)
