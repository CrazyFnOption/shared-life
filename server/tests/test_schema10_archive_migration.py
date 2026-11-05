"""Schema 9 -> 10 preserves old values, receipts and retained attachment bytes."""
import hashlib
import json
import subprocess
import sys
from uuid import uuid4

import pytest

from server.accounting import db as database
from server.accounting.db import connect, initialize_database, migrate_database, preflight
from server.automation.backups import validate_database
from server.memos.migration_guard import capture, verify


@pytest.fixture
def schema9(tmp_path):
    path=tmp_path/'schema9.sqlite3'
    uid,cat,mid,aid=(str(uuid4()) for _ in range(4))
    binary=b'\x00fictional retained attachment\xff'
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        database._initialize_legacy(db)
        database._migrate_multi_family(db)
        database._migrate_memos(db)
        db.execute('INSERT INTO users(id,username,password_hash,display_name) VALUES(?,?,?,?)',
                   (uid,'fictional',database.password_hasher.hash('fictional-password'),'Fictional'))
        db.execute('INSERT INTO memo_categories(id,scope,namespace,creator_id,name,version,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',
                   (cat,'private',uid,uid,'资料','2026-11-03T00:00:00+00:00','2026-11-03T00:00:00+00:00'))
        db.execute('''INSERT INTO memos(id,scope,namespace,author_id,updated_by,category_id,title,
                   body_markdown,completed,version,created_at,updated_at,completed_at,last_cycle)
                   VALUES(?,?,?,?,?,?,?, ?,1,4,?,?,?,?)''',
                   (mid,'private',uid,uid,uid,cat,'Old completed memo','# Original\n**原文**',
                    '2026-11-03T00:00:00+00:00','2026-11-04T00:00:00+00:00','2026-11-04T00:00:00+00:00','2026-11-04'))
        db.execute('''INSERT INTO memo_attachments VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (aid,mid,uid,'file','retained.bin','application/octet-stream',len(binary),hashlib.sha256(binary).hexdigest(),binary,'deleted'))
        op_id=str(uuid4());restore_id=db.execute('SELECT restore_id FROM app_state').fetchone()[0]
        receipt=json.dumps({'op_id':op_id,'entity_type':'memo','entity_id':mid,'record':{'id':mid,'scope':'private','family_id':None,'completed':True,'last_cycle':'2026-11-04'}},ensure_ascii=False)
        db.execute('INSERT INTO memo_operations VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (uid,op_id,'memo',mid,'old-request-hash',receipt,restore_id,'private',None,None))
        db.commit()
        validate_database(db)
    return path


def test_9_to_10_preserves_every_old_column_and_startup_is_idempotent(schema9,tmp_path):
    with connect(schema9,read_only=True) as db:before=capture(db)
    assert preflight(schema9)=='previous'
    original=hashlib.sha256(schema9.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError,match='migration_required'):initialize_database(schema9)
    assert hashlib.sha256(schema9.read_bytes()).hexdigest()==original
    evidence=tmp_path/'evidence.json'
    result=subprocess.run([sys.executable,'-m','scripts.migrate_memos','--database',str(schema9),'--evidence',str(evidence)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    proof=json.loads(evidence.read_text())
    assert proof['schema']==10 and proof['original_rows_preserved'] and proof['input_schema']=='previous' and proof['input_schema_version']==9
    with connect(schema9,read_only=True) as db:
        verify(db,before)
        assert db.execute('SELECT archive_state_json,completed,last_cycle FROM memos').fetchone()[:]==('{}',1,'2026-11-04')
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    after=hashlib.sha256(schema9.read_bytes()).hexdigest()
    for _ in range(2):initialize_database(schema9);assert not migrate_database(schema9)
    assert preflight(schema9)=='current' and hashlib.sha256(schema9.read_bytes()).hexdigest()==after


@pytest.mark.parametrize('failure',['after_ddl','old_value_changed'])
def test_9_to_10_rolls_back_ddl_marker_version_and_old_values(schema9,monkeypatch,failure):
    with connect(schema9,read_only=True) as db:before=capture(db)
    original=database._migrate_memo_archive
    def damaged(db):
        original(db)
        if failure=='after_ddl':raise RuntimeError('injected interruption')
        db.execute("UPDATE memos SET title='unexpected migration mutation'")
    monkeypatch.setattr(database,'_migrate_memo_archive',damaged)
    with pytest.raises((RuntimeError,ValueError)):migrate_database(schema9)
    with connect(schema9,read_only=True) as db:
        verify(db,before)
        assert db.execute('PRAGMA user_version').fetchone()[0]==9
        assert 'archive_state_json' not in {r['name'] for r in db.execute('PRAGMA table_info(memos)')}
        assert not db.execute("SELECT 1 FROM schema_migrations WHERE name='memo-archive-v1'").fetchone()
    assert preflight(schema9)=='previous'


@pytest.mark.parametrize('alteration',[
    'PRAGMA user_version=11',
    "DELETE FROM schema_migrations WHERE name='memo-archive-v1'",
    'PRAGMA user_version=9',
    'ALTER TABLE memos ADD COLUMN unknown_future TEXT',
    "ALTER TABLE memos DROP COLUMN archive_state_json; ALTER TABLE memos ADD COLUMN archive_state_json TEXT DEFAULT '{}'",
])
def test_wrong_schema_is_rejected_without_writes(schema9,alteration):
    migrate_database(schema9)
    with connect(schema9) as db:db.executescript(alteration)
    original=hashlib.sha256(schema9.read_bytes()).hexdigest()
    for action in (preflight,initialize_database,migrate_database):
        with pytest.raises(RuntimeError,match='unsupported_database_schema'):action(schema9)
    assert hashlib.sha256(schema9.read_bytes()).hexdigest()==original
