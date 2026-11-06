"""Format 2 round trips schema 10 and upgrades only an isolated schema 9 copy."""
import json
import pytest

from server.accounting.db import SCHEMA_VERSION,connect, migrate_database, preflight
from server.automation.backups import backup, restore, sha, validate_database, write_private
from server.tests.test_schema10_archive_migration import schema9
from server.tests.legacy_fixture import legacy_file


def test_format2_schema10_archive_and_attachment_roundtrip(schema9,tmp_path):
    migrate_database(schema9)
    archive={'archived_at':'2026-11-05T00:00:00+00:00','reason':'complete'}
    with connect(schema9) as db:
        db.execute('UPDATE memos SET archived=1,archive_state_json=?',(json.dumps(archive),))
        original=dict(db.execute('SELECT * FROM memos').fetchone())
        attachment=dict(db.execute('SELECT * FROM memo_attachments').fetchone())
        restore_id=db.execute('SELECT restore_id FROM app_state').fetchone()[0]
    saved=backup(schema9,tmp_path/'backup')
    manifest=json.loads((saved/'manifest.json').read_text())
    assert manifest['format_version']==2 and manifest['database_schema']==SCHEMA_VERSION and manifest['min_restore_protocol']==2
    signed=sha(saved/'business.sqlite3');dest=tmp_path/'restored.sqlite3'
    result=restore(saved,dest,saved/'credentials.json')
    assert result['login_enabled'] and result['automation_paused'] and result['restore_id']!=restore_id
    assert preflight(dest)=='current' and sha(saved/'business.sqlite3')==signed
    with connect(dest) as db:
        validate_database(db)
        assert dict(db.execute('SELECT * FROM memos').fetchone())==original
        assert dict(db.execute('SELECT * FROM memo_attachments').fetchone())==attachment
        assert not db.execute('SELECT 1 FROM sessions').fetchone()
        assert db.execute('SELECT source_restore_id FROM memo_operations').fetchone()[0]==result['restore_id']
    # The retained old B restorer refuses the new schema before target creation.
    older={};exec(compile(legacy_file('8b88854','server/automation/backups.py'),'frozen-B/backups.py','exec'),older)
    rejected=tmp_path/'old-program.sqlite3'
    with pytest.raises(ValueError,match='Unsupported backup schema'):older['restore'](saved,rejected)
    assert not rejected.exists()


def test_signed_schema9_backup_upgrades_only_isolated_copy(schema9,tmp_path):
    saved=tmp_path/'schema9-backup';saved.mkdir()
    with connect(schema9,read_only=True) as db,connect(saved/'business.sqlite3') as copied:
        db.backup(copied)
        credentials={'schema_version':2,'users':[dict(r) for r in copied.execute('SELECT id AS user_id,password_hash FROM users')]}
        copied.execute("UPDATE users SET password_hash=''")
    write_private(saved/'credentials.json',credentials)
    write_private(saved/'manifest.json',{'format_version':2,'database_schema':9,'min_restore_protocol':2,
        'files':{name:sha(saved/name) for name in ('business.sqlite3','credentials.json')}})
    signed=sha(saved/'business.sqlite3');original=sha(schema9)
    dest=tmp_path/'isolated-forward.sqlite3';result=restore(saved,dest,saved/'credentials.json')
    assert result['automation_paused'] and preflight(dest)=='current'
    assert sha(saved/'business.sqlite3')==signed and sha(schema9)==original
    with connect(dest) as db:
        validate_database(db)
        assert db.execute('SELECT archive_state_json,completed,last_cycle FROM memos').fetchone()[:]==('{}',1,'2026-11-04')
    with pytest.raises(ValueError,match='new path'):restore(saved,dest)


def test_restore_rejects_schema10_manifest_mismatch_before_target(schema9,tmp_path):
    migrate_database(schema9);saved=backup(schema9,tmp_path/'backup')
    manifest=json.loads((saved/'manifest.json').read_text());manifest['database_schema']=9
    (saved/'manifest.json').write_text(json.dumps(manifest))
    dest=tmp_path/'rejected.sqlite3'
    with pytest.raises(ValueError,match='Manifest/schema mismatch'):restore(saved,dest)
    assert not dest.exists()
