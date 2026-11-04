"""Consistent SQLite backups with separate credentials and isolated restores."""
from datetime import datetime, UTC
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from PIL import Image
from server.accounting.db import connect, initialize_database
from server.automation.reports import state

FORMAT_VERSION=1

def sha(path):
    with open(path,'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def write_private(path,data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)

def backup(database,destination):
    source=Path(database);dest=Path(destination)
    if not source.is_file():
        raise ValueError('Source database does not exist')
    dest.mkdir(mode=0o700,parents=False,exist_ok=False)
    business=dest/'business.sqlite3'
    fd=os.open(business,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with connect(source,read_only=True) as original,connect(business) as copied:
        original.backup(copied)
        credentials={'schema_version':FORMAT_VERSION,'users':[dict(r) for r in copied.execute('SELECT id AS user_id,password_hash FROM users ORDER BY id')]}
        copied.execute('PRAGMA secure_delete=ON')
        copied.execute('BEGIN IMMEDIATE')
        copied.execute('DELETE FROM sessions')
        copied.execute('DELETE FROM push_subscriptions')
        copied.execute('DELETE FROM push_deliveries')
        copied.execute("UPDATE users SET password_hash=''")
        copied.commit()
        copied.execute('VACUUM')
    write_private(dest/'credentials.json',credentials)
    write_private(dest/'manifest.json',{'format_version':FORMAT_VERSION,'created_at':datetime.now(UTC).isoformat(),
        'files':{name:sha(dest/name) for name in ('business.sqlite3','credentials.json')}})
    return dest

def validate_database(db, *, allow_legacy=False):
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or list(db.execute('PRAGMA foreign_key_check')):
        raise ValueError('Database integrity validation failed')
    required={'users','sessions','expenses','refunds','expense_images','revisions','operations','change_requests','app_state','reports','report_tasks','push_subscriptions','notification_outbox','push_deliveries','model_usage','weekly_settings','categories','backup_runs','user_sync'}
    family_tables={'families','family_members','family_invites','family_actions','monthly_settings','schema_migrations'}
    tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if family_tables & tables or not allow_legacy:
        required |= family_tables
    if not required.issubset(tables) or not db.execute('SELECT 1 FROM app_state WHERE singleton=1').fetchone():
        raise ValueError('Unsupported database schema')
    if db.execute('''SELECT 1 FROM expenses e WHERE e.amount_cents < COALESCE((SELECT sum(amount_cents) FROM refunds WHERE expense_id=e.id AND voided=0),0) LIMIT 1''').fetchone():
        raise ValueError('Invalid refund balance')
    for row in db.execute('SELECT * FROM expense_images WHERE deleted=0'):
        data=row['data']
        if not data or len(data)!=row['size_bytes'] or hashlib.sha256(data).hexdigest()!=row['content_hash']:
            raise ValueError('Invalid image bytes')
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except Exception:
            raise ValueError('Unreadable image') from None

def restore(backup_dir,destination,credentials=None):
    source=Path(backup_dir);dest=Path(destination)
    if dest.exists():
        raise ValueError('Destination must be a new path')
    manifest=json.loads((source/'manifest.json').read_text())
    if manifest.get('format_version')!=FORMAT_VERSION or set(manifest.get('files',{}))!={'business.sqlite3','credentials.json'}:
        raise ValueError('Unsupported backup manifest')
    if not (source/'business.sqlite3').is_file() or sha(source/'business.sqlite3')!=manifest['files']['business.sqlite3']:
        raise ValueError('Backup file checksum mismatch')
    if credentials and (not Path(credentials).is_file() or sha(credentials)!=manifest['files']['credentials.json']):
        raise ValueError('Credential file checksum mismatch')
    with connect(source/'business.sqlite3',read_only=True) as original:
        validate_database(original, allow_legacy=True)
        users={r['id'] for r in original.execute('SELECT id FROM users')}
        credential_rows=[]
        if credentials:
            from argon2 import extract_parameters
            credential_data=json.loads(Path(credentials).read_text())
            credential_rows=credential_data.get('users',[])
            if credential_data.get('schema_version')!=FORMAT_VERSION or len(credential_rows)!=len(users) or {r.get('user_id') for r in credential_rows}!=users:
                raise ValueError('Credential users do not match restored users')
            for row in credential_rows:
                try:
                    extract_parameters(row['password_hash'])
                except Exception:
                    raise ValueError('Invalid credential password hash') from None
        fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with connect(dest) as copied:
            original.backup(copied)
            # Upgrade only the isolated copy. The signed source backup stays unchanged.
            initialize_database(dest)
            validate_database(copied)
            copied.execute('PRAGMA secure_delete=ON')
            copied.execute('BEGIN IMMEDIATE')
            copied.execute('DELETE FROM sessions');copied.execute('DELETE FROM push_subscriptions');copied.execute('DELETE FROM push_deliveries')
            copied.execute("UPDATE users SET password_hash=''")
            for row in credential_rows:
                copied.execute('UPDATE users SET password_hash=? WHERE id=?',(row['password_hash'],row['user_id']))
            restore_id=str(uuid4()); restored_at=datetime.now(UTC).isoformat()
            copied.execute('UPDATE app_state SET restore_id=?,restored_at=?,automation_paused=1 WHERE singleton=1',(restore_id,restored_at))
            copied.execute("UPDATE report_tasks SET restore_id=?,claim_token=NULL,lease_until=NULL,progress_json='{}',failures=0",(restore_id,))
            copied.execute("UPDATE reports SET status='stale',content_json=NULL WHERE status='running'")
            copied.execute("UPDATE notification_outbox SET status='cancelled' WHERE status!='sent'")
            copied.commit();copied.execute('VACUUM');validate_database(copied)
    return {'restore_id':restore_id,'restored_at':restored_at,'login_enabled':bool(credential_rows),'automation_paused':True}
