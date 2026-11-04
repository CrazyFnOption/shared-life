"""Report snapshots, authorization and atomic ledger invalidation."""
from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from server.accounting import ledger, families

ZONE = ZoneInfo('Asia/Shanghai')

def state(db):
    return dict(db.execute('SELECT * FROM app_state WHERE singleton=1').fetchone())

def month_of(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZONE).strftime('%Y-%m')

def previous_month(now):
    return (now.astimezone(ZONE).replace(day=1) - timedelta(days=1)).strftime('%Y-%m')

def snapshot(db, scope, owner, month):
    rows = db.execute("SELECT id FROM expenses WHERE scope=? AND voided=0 AND ((scope='shared' AND family_id=?) OR (scope='private' AND owner_id=?)) ORDER BY id", (scope, owner, owner))
    result = []
    for row in rows:
        expense = ledger.read_expense(db, row['id'])
        if month_of(expense['occurred_at']) == month:
            result.append(ledger.shared_expense(expense))
    return result

def summary(expenses):
    payers = {}
    for e in expenses:
        payers[e['owner_id']] = payers.get(e['owner_id'], 0) + e['net_amount_cents']
    return {'total_cents':sum(payers.values()), 'by_payer':[{'user_id':k,'amount_cents':v} for k,v in sorted(payers.items())], 'unrated_count':sum(e['rating'] is None for e in expenses)}

def version(expenses):
    return ledger.digest(expenses)

def queue(db, report_id, data_version, *, when=None):
    db.execute('''INSERT INTO report_tasks(report_id,target_version,restore_id,next_attempt)
        VALUES(?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET target_version=excluded.target_version,
        restore_id=excluded.restore_id,next_attempt=excluded.next_attempt,claim_token=NULL,lease_until=NULL,
        progress_json='{}',failures=0,rounds=0,retry_day=NULL''',
        (report_id,data_version,state(db)['restore_id'], time.time() if when is None else when))

def invalidate(db, before, after):
    affected = {(e['scope'], e['family_id'] if e['scope']=='shared' else e['owner_id'], month_of(e['occurred_at'])) for e in (before,after) if e}
    for scope, owner, month in affected:
        row = db.execute('SELECT * FROM reports WHERE scope=? AND owner_id=? AND month=?',(scope,owner,month)).fetchone()
        if not row:
            continue
        data_version = version(snapshot(db,scope,owner,month))
        db.execute("UPDATE reports SET status='stale',content_json=NULL,data_version=?,error=NULL WHERE id=?",(data_version,row['id']))
        db.execute("UPDATE notification_outbox SET status='cancelled' WHERE report_id=? AND status!='sent'",(row['id'],))
        queue(db,row['id'],data_version,when=db.execute("SELECT app_now()").fetchone()[0]+60)

def authorize(db, report_id, user_id):
    row = db.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone()
    if not row or (row['owner_id'] != (families.current_id(db,user_id) if row['scope']=='shared' else user_id)) or (getattr(db,'protocol',0)==2 and (not db.source or db.source['scope']!=row['scope'])):
        raise HTTPException(404,detail='Report not found')
    return dict(row)

def read(db, row, *, detail=False):
    expenses = snapshot(db,row['scope'],row['owner_id'],row['month'])
    current = version(expenses)
    fresh = row['data_version']==current
    result = {k:row[k] for k in ('id','scope','owner_id','month','status','data_version','generated_at','provider','model','error')}
    result['family_id'] = row['owner_id'] if row['scope']=='shared' else None
    result['owner_id'] = None if row['scope']=='shared' else row['owner_id']
    result['summary'] = summary(expenses)
    result['content'] = json.loads(row['content_json']) if detail and fresh and row['status']=='ready' and row['content_json'] else None
    if not fresh:
        result.update(status='stale', data_version=current)
    if detail:
        result['expenses'] = expenses
    return result

def due_at(month, clock):
    year, number = map(int,month.split('-'))
    year, number = (year+1,1) if number==12 else (year,number+1)
    first = datetime(year,number,1,tzinfo=ZONE)
    hour, minute = map(int,clock.split(':'))
    return (first+timedelta(days=(6-first.weekday())%7)).replace(hour=hour,minute=minute)

def schedule(db, now):
    settings = state(db)
    if settings['automation_paused']:
        return
    for monthly in db.execute("SELECT * FROM monthly_settings WHERE enabled=1 AND start IS NOT NULL AND (scope='private' OR namespace IN (SELECT id FROM families WHERE archived=0))"):
        month = monthly['start']
        scope,owner = monthly['scope'],monthly['namespace']
        while due_at(month,monthly['time'])<=now:
            expenses = snapshot(db,scope,owner,month)
            if expenses:
                report_id = str(uuid4())
                inserted = db.execute("INSERT OR IGNORE INTO reports(id,scope,owner_id,month,status,data_version) VALUES(?,?,?,?,'pending',?)",(report_id,scope,owner,month,version(expenses))).rowcount
                if inserted:
                    queue(db,report_id,version(expenses),when=now.timestamp())
            year, number = map(int,month.split('-'))
            month = f'{year+1}-01' if number==12 else f'{year}-{number+1:02d}'
    local = now.astimezone(ZONE)
    monday = local.date()-timedelta(days=local.weekday())
    for row in db.execute('SELECT * FROM weekly_settings WHERE enabled=1'):
        hour,minute = map(int,row['time'].split(':'))
        due = datetime.combine(monday+timedelta(days=row['weekday']),datetime.min.time(),tzinfo=ZONE).replace(hour=hour,minute=minute)
        has_unrated = has_unrated_expenses(db,row['user_id'])
        if now>=due and has_unrated:
            enqueue_notification(db,row['user_id'],f'weekly:{monday}',now.timestamp())

def has_unrated_expenses(db,user_id):
    return db.execute("""SELECT 1 FROM expenses e WHERE e.owner_id=? AND e.voided=0 AND e.rating IS NULL AND
        (e.scope='private' OR EXISTS(SELECT 1 FROM family_members m JOIN families f ON f.id=m.family_id
         WHERE m.user_id=? AND m.family_id=e.family_id AND m.active=1 AND f.archived=0)) LIMIT 1""",(user_id,user_id)).fetchone()


def enqueue_notification(db,user_id,key,when,report_id=None,data_version=None):
    scope='account';fid=None;epoch=None
    if report_id:
        report=db.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone()
        if not report:
            return
        scope=report['scope']
        if scope=='shared':
            fid=report['owner_id'];member=families.relation(db,user_id,fid)
            if not member:
                return
            epoch=member['relation_epoch']
        elif report['owner_id']!=user_id:
            return
    db.execute("INSERT OR IGNORE INTO notification_outbox(id,user_id,dedup_key,next_attempt,report_id,data_version,source_scope,family_id,relation_epoch,source_restore_id,protocol_version,source_status) VALUES(?,?,?,?,?,?,?,?,?,?,2,'verified')",(str(uuid4()),user_id,key,when,report_id,data_version,scope,fid,epoch,state(db)['restore_id']))


def notification_valid(db,row):
    if row['dedup_key'].startswith('memo:'):
        from server.memos.notifications import valid
        return valid(db,row)
    if row['source_status']!='verified' or row['source_restore_id']!=state(db)['restore_id'] or state(db)['automation_paused']:
        return False
    if row['source_scope']=='shared':
        member=families.relation(db,row['user_id'],row['family_id'])
        if not member or member['relation_epoch']!=row['relation_epoch'] or not db.execute('SELECT 1 FROM families WHERE id=? AND archived=0',(row['family_id'],)).fetchone():
            return False
    elif row['source_scope']=='account':
        if not has_unrated_expenses(db,row['user_id']):
            return False
    elif row['source_scope']!='private':
        return False
    if row['report_id']:
        report=db.execute('SELECT * FROM reports WHERE id=?',(row['report_id'],)).fetchone()
        if not report or report['status']!='ready' or report['data_version']!=row['data_version'] or report['data_version']!=version(snapshot(db,report['scope'],report['owner_id'],report['month'])) or report['scope']!=row['source_scope'] or report['owner_id']!=(row['family_id'] if row['source_scope']=='shared' else row['user_id']):
            return False
    return True
