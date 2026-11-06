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
from server.accounting.db import SCHEMA_VERSION, connect, initialize_database, migrate_database, preflight
from server.automation.reports import state

FORMAT_VERSION=2

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
    if preflight(source)!='current':
        raise ValueError(f'Backup requires schema {SCHEMA_VERSION}; explicitly migrate old source first')
    with connect(source,read_only=True) as check:
        validate_database(check)
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
    write_private(dest/'manifest.json',{'format_version':FORMAT_VERSION,'database_schema':SCHEMA_VERSION,'min_restore_protocol':2,'created_at':datetime.now(UTC).isoformat(),
        'files':{name:sha(dest/name) for name in ('business.sqlite3','credentials.json')}})
    return dest

def validate_database(db, *, allow_legacy=False):
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or list(db.execute('PRAGMA foreign_key_check')):
        raise ValueError('Database integrity validation failed')
    schema_version=db.execute('PRAGMA user_version').fetchone()[0]
    schema_v2 = schema_version in (8,9,10,11)
    required={'users','sessions','expenses','refunds','expense_images','revisions','operations','change_requests','app_state','reports','report_tasks','push_subscriptions','notification_outbox','push_deliveries','model_usage','weekly_settings','categories','backup_runs','user_sync'}
    family_tables={'families','family_members','family_invites','family_actions','monthly_settings','schema_migrations'}
    tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if family_tables & tables or not allow_legacy:
        required |= family_tables
    if not required.issubset(tables) or not db.execute('SELECT 1 FROM app_state WHERE singleton=1').fetchone():
        raise ValueError('Unsupported database schema')
    if 'families' in tables:
        has_owner = 'owner_id' in {r['name'] for r in db.execute('PRAGMA table_info(families)')}
        if not has_owner and not allow_legacy:
            raise ValueError('Unsupported family owner schema')
        member_filter = ' AND m.active=1' if schema_v2 else ''
        if has_owner and db.execute(f'''SELECT 1 FROM families f WHERE f.archived=0 AND
            (f.owner_id IS NULL OR NOT EXISTS(SELECT 1 FROM family_members m WHERE m.family_id=f.id AND m.user_id=f.owner_id{member_filter})) LIMIT 1''').fetchone():
            raise ValueError('Invalid family owner membership')
    if schema_v2:
        validate_multi_family(db,tables)
    elif not allow_legacy:
        raise ValueError('Schema 8, 9 or 10 required')
    if schema_version in (9,10,11):
        from server.memos.service import validate_database as validate_memos
        validate_memos(db)
        from server.plans import validate_database as validate_plans
        validate_plans(db)
    if schema_version>=11:
        from server.ai.validation import validate_database as validate_ai
        validate_ai(db)
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

def validate_multi_family(db,tables):
    if not {'legacy_relation_epochs','category_tombstones','category_changes','category_approvals','category_decisions'}.issubset(tables) or not db.execute("SELECT 1 FROM schema_migrations WHERE name='multi-family-v2'").fetchone():
        raise ValueError('Incomplete schema 8')
    if [(r['name'],r['pk']) for r in db.execute('PRAGMA table_info(family_members)') if r['pk']] != [('user_id',1),('family_id',2)]:
        raise ValueError('Invalid relationship key')
    def reject(sql,args=()):
        if db.execute(sql,args).fetchone():
            raise ValueError('Invalid multi-family provenance or relationship')
    reject("SELECT 1 FROM family_members WHERE typeof(relation_epoch)!='integer' OR relation_epoch<=0 OR active NOT IN (0,1)")
    reject('SELECT 1 FROM family_members m JOIN families f ON f.id=m.family_id WHERE m.active=1 AND f.archived!=0')
    reject("SELECT 1 FROM users u WHERE typeof(context_version)!='integer' OR context_version<1 OR typeof(default_version)!='integer' OR default_version<1 OR typeof(membership_version)!='integer' OR membership_version<0 OR (default_family_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM family_members m JOIN families f ON f.id=m.family_id WHERE m.user_id=u.id AND m.family_id=u.default_family_id AND m.active=1 AND f.archived=0))")
    reject("SELECT 1 FROM families WHERE typeof(revision)!='integer' OR revision<1")
    reject("SELECT 1 FROM legacy_relation_epochs l WHERE typeof(l.relation_epoch)!='integer' OR l.relation_epoch<1 OR typeof(l.membership_version)!='integer' OR l.membership_version<0 OR NOT EXISTS(SELECT 1 FROM family_members m WHERE m.user_id=l.user_id AND m.family_id=l.family_id AND l.relation_epoch<=m.relation_epoch)")
    reject("SELECT 1 FROM expenses e WHERE (scope='private' AND family_id IS NOT NULL) OR (scope='shared' AND family_id IS NULL)")
    for row in db.execute("SELECT * FROM operations WHERE source_status='verified'"):
        if row['protocol_version'] not in (1,2) or row['source_restore_id']!=state(db)['restore_id'] or row['source_scope'] not in ('private','shared') or not db.execute('SELECT 1 FROM expenses WHERE id=?',(row['expense_id'],)).fetchone():
            raise ValueError('Invalid operation source')
        validate_relation_source(db,row['user_id'],row['source_scope'],row['family_id'],row['relation_epoch'])
        receipt=json.loads(row['receipt_json'])
        expense=receipt.get('expense')
        if not isinstance(expense,dict) or expense.get('id')!=row['expense_id']:
            raise ValueError('Invalid receipt object')
        snapshots=[expense]
        if isinstance(receipt.get('request'),dict) and isinstance(receipt['request'].get('before'),dict):
            snapshots.append(receipt['request']['before'])
        for snapshot in snapshots:
            if snapshot.get('scope')=='shared':
                if row['source_scope']!='shared' or snapshot.get('family_id',row['family_id'])!=row['family_id']:
                    raise ValueError('Receipt source/body mismatch')
            elif snapshot.get('scope')!='private' or snapshot.get('owner_id')!=row['user_id']:
                raise ValueError('Receipt private owner mismatch')
    for row in db.execute("SELECT * FROM change_requests WHERE source_status='verified'"):
        if not row['source_restore_id']:
            raise ValueError('Invalid request restore domain')
        for uid,key in ((row['requester_id'],'requester_epoch'),(row['owner_id'],'owner_epoch')):
            validate_relation_source(db,uid,'shared',row['family_id'],row[key])
    for row in db.execute("SELECT * FROM category_changes WHERE source_status='verified'"):
        ctx=json.loads(row['relation_context_json'] or '{}')
        if ctx.get('protocol_version')!=2 or not ctx.get('restore_id') or ctx.get('family_id')!=(row['namespace'] if row['scope']=='shared' else None) or not isinstance(ctx.get('members'),dict):
            raise ValueError('Invalid category signature')
        if row['scope']=='shared':
            required={row['requester_id']} | {item['owner_id'] for item in json.loads(row['items_json'])}
            if set(ctx['members'])!=required:
                raise ValueError('Incomplete category signature')
            for uid,epoch in ctx['members'].items():
                validate_relation_source(db,uid,'shared',row['namespace'],epoch)
    for row in db.execute('SELECT * FROM reports'):
        target='families' if row['scope']=='shared' else 'users'
        if row['scope'] not in ('private','shared') or not db.execute(f'SELECT 1 FROM {target} WHERE id=?',(row['owner_id'],)).fetchone():
            raise ValueError('Invalid report namespace')
    for row in db.execute('SELECT * FROM report_tasks'):
        if row['restore_id']!=state(db)['restore_id'] or len(row['target_version'])!=64:
            raise ValueError('Invalid report task domain')
    for row in db.execute("SELECT * FROM notification_outbox WHERE source_status='verified'"):
        if row['protocol_version'] not in (1,2) or not row['source_restore_id'] or row['source_scope'] not in ('account','private','shared'):
            raise ValueError('Invalid outbox domain')
        validate_relation_source(db,row['user_id'],row['source_scope'],row['family_id'],row['relation_epoch'])
        if row['report_id']:
            report=db.execute('SELECT * FROM reports WHERE id=?',(row['report_id'],)).fetchone()
            if not report or report['scope']!=row['source_scope'] or report['owner_id']!=(row['family_id'] if row['source_scope']=='shared' else row['user_id']):
                raise ValueError('Invalid outbox report source')
        if row['status'] in ('pending','failed','uncertain') and row['source_restore_id']!=state(db)['restore_id']:
            raise ValueError('Invalid active outbox restore domain')


def validate_relation_source(db,uid,scope,fid,epoch):
    if scope!='shared':
        if fid is not None or epoch is not None:
            raise ValueError('Unexpected private/account family')
        return
    member=db.execute('SELECT relation_epoch FROM family_members WHERE user_id=? AND family_id=?',(uid,fid)).fetchone()
    if not member or not isinstance(epoch,int) or epoch<=0 or epoch>member[0]:
        raise ValueError('Invalid relationship epoch')


def restore(backup_dir,destination,credentials=None):
    source=Path(backup_dir);dest=Path(destination)
    if dest.exists():
        raise ValueError('Destination must be a new path')
    manifest=json.loads((source/'manifest.json').read_text())
    version=manifest.get('format_version')
    if version not in (1,2) or set(manifest.get('files',{}))!={'business.sqlite3','credentials.json'}:
        raise ValueError('Unsupported backup manifest')
    if version==2 and (manifest.get('database_schema') not in (8,9,10,11) or manifest.get('min_restore_protocol')!=2):
        raise ValueError('Unsupported backup schema')
    if not (source/'business.sqlite3').is_file() or sha(source/'business.sqlite3')!=manifest['files']['business.sqlite3']:
        raise ValueError('Backup file checksum mismatch')
    if credentials and (not Path(credentials).is_file() or sha(credentials)!=manifest['files']['credentials.json']):
        raise ValueError('Credential file checksum mismatch')
    with connect(source/'business.sqlite3',read_only=True) as original:
        actual_version=original.execute('PRAGMA user_version').fetchone()[0]
        if actual_version != (manifest['database_schema'] if version==2 else 0):
            raise ValueError('Manifest/schema mismatch')
        preflight(source/'business.sqlite3')
        validate_database(original, allow_legacy=version==1)
        users={r['id'] for r in original.execute('SELECT id FROM users')}
        credential_rows=[]
        if credentials:
            from argon2 import extract_parameters
            credential_data=json.loads(Path(credentials).read_text())
            credential_rows=credential_data.get('users',[])
            if credential_data.get('schema_version')!=version or len(credential_rows)!=len(users) or {r.get('user_id') for r in credential_rows}!=users:
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
            if version==1 or actual_version<SCHEMA_VERSION:
                migrate_database(dest)
            else:
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
            copied.execute("UPDATE operations SET source_restore_id=? WHERE source_status='verified'",(restore_id,))
            copied.execute('UPDATE memo_operations SET source_restore_id=?',(restore_id,))
            from server.plans import rebind_restore as rebind_plans
            rebind_plans(copied,restore_id)
            copied.execute("UPDATE ai_turns SET status='stale',error='restore_changed' WHERE status='running'")
            copied.execute("UPDATE ai_costs SET status='unknown' WHERE status='reserved'")
            copied.execute("UPDATE memo_deletion_requests SET status='stale',version=version+1 WHERE status='pending'")
            copied.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE status='pending'")
            copied.execute("UPDATE memo_reminder_events SET status='cancelled'")
            copied.commit();copied.execute('VACUUM');validate_database(copied)
    return {'restore_id':restore_id,'restored_at':restored_at,'login_enabled':bool(credential_rows),'automation_paused':True}
