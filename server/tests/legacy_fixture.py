"""Test-only reconstruction of old schema; never accepts multi-family fixture data."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from types import ModuleType
from server.accounting.db import connect, expected_columns


FIXTURES = Path(__file__).resolve().parents[2] / 'tests/fixtures/legacy'


@lru_cache(maxsize=1)
def _manifest():
    return json.loads((FIXTURES / 'manifest.json').read_text())['snapshots']


def _snapshot(revision):
    matches = [(key, value) for key, value in _manifest().items()
               if value['source_commit'].startswith(revision)]
    if len(matches) != 1:
        raise ValueError(f'Unknown or ambiguous legacy fixture: {revision}')
    return matches[0]


def legacy_file(revision, relative_path):
    """Read exact historical source, verifying its independently recorded hash."""
    key, snapshot = _snapshot(revision)
    expected = snapshot['files'][relative_path]['sha256']
    data = (FIXTURES / key / relative_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Legacy fixture hash mismatch: {key}/{relative_path}')
    return data.decode('utf-8')


def copy_legacy(revision, destination):
    """Copy only manifest-listed source into an isolated test working directory."""
    _, snapshot = _snapshot(revision)
    destination = Path(destination)
    for relative_path, info in snapshot['files'].items():
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(legacy_file(revision, relative_path).encode('utf-8'))
        target.chmod(int(info['mode'], 8) & 0o777)
    return destination


def to_v1(path):
    with connect(path) as db:
        columns={r['name'] for r in db.execute('PRAGMA table_info(family_members)')}
        if 'active' not in columns:
            return
        if db.execute('SELECT 1 FROM family_members WHERE active=1 GROUP BY user_id HAVING count(*)>1').fetchone():
            raise ValueError('Cannot downgrade a multi-family fixture')
        expected=expected_columns(0)
        extra_tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} - set(expected)
        # Test preparation may discard only empty new-module tables. Preserve
        # the historical accounting rows that the migration assertions inspect.
        for table in extra_tables-{'legacy_relation_epochs'}:
            if db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                raise ValueError('Cannot downgrade a fixture with new-module data')
        db.execute('BEGIN IMMEDIATE')
        for table in extra_tables:
            db.execute(f'DROP TABLE {table}')
        db.execute('DROP INDEX family_members_active_family')
        db.execute('ALTER TABLE family_members RENAME TO fixture_members_v2')
        db.execute('CREATE TABLE family_members(user_id TEXT PRIMARY KEY REFERENCES users(id),family_id TEXT NOT NULL REFERENCES families(id))')
        db.execute('INSERT INTO family_members SELECT user_id,family_id FROM fixture_members_v2 WHERE active=1')
        db.execute('DROP TABLE fixture_members_v2')
        for table,old_columns in expected.items():
            for col in list(db.execute(f'PRAGMA table_info({table})')):
                if col['name'] not in old_columns:
                    db.execute(f"ALTER TABLE {table} DROP COLUMN {col['name']}")
        db.execute("DELETE FROM schema_migrations WHERE name IN ('multi-family-v2','memos-v1','plans-v1')")
        db.execute('PRAGMA user_version=0')
        db.commit()


@lru_cache(maxsize=1)
def old_backup_module():
    module=ModuleType('frozen_a_backups')
    code=legacy_file('298b15f','server/automation/backups.py')
    exec(compile(code,'frozen-A/server/automation/backups.py','exec'),module.__dict__)
    return module


def old_backup(database,destination):
    return old_backup_module().backup(database,destination)
