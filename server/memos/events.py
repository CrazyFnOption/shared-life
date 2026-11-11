"""Durable memo reminder events and per-person message dismissal; no Push calls."""
from datetime import datetime,date,timedelta,UTC
import json
import hashlib
from uuid import uuid5,NAMESPACE_URL
from server.accounting import families
from .calendar import ZONE,anchor,local_time,dates,reminder_time


def signature(rule,recurrence,schedule=None):
    payload={'rule':rule,'recurrence':recurrence}
    if rule['mode']=='relative':payload['schedule']={key:schedule[key] for key in ('start_date','start_time')} if schedule else None
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def rule_effective_at(db,record,rule):
    """Only the latest continuous use of this rule can produce new reminders."""
    wanted=signature(rule,record.get('recurrence'),record.get('schedule'))
    effective=local_time(record['created_at']);previous=None
    receipts=[json.loads(row['receipt_json'])['record'] for row in db.execute("SELECT receipt_json FROM memo_operations WHERE entity_type='memo' AND entity_id=?",(record['id'],))]
    for saved in sorted(receipts,key=lambda item:item['version']):
        saved_rule=next((item for item in saved.get('reminders',[]) if item['id']==rule['id']),None)
        current=signature(saved_rule,saved.get('recurrence'),saved.get('schedule')) if saved_rule else None
        if current==wanted and current!=previous:effective=local_time(saved['updated_at'])
        previous=current
    return effective


def cancel_completed_events(db,memo_id,completed_at):
    """Completion resolves already due events for everyone, independent of ignore."""
    db.execute("UPDATE memo_reminder_events SET status='cancelled' WHERE memo_id=? AND status='ready' AND julianday(occurrence)<=julianday(?)",(memo_id,completed_at))
    from .notifications import cancel_invalid
    cancel_invalid(db,memo_id)


def cancel_changed_events(db,record):
    rules={rule['id']:rule for rule in record['reminders']}
    for row in db.execute("SELECT id,rule_id,rule_signature FROM memo_reminder_events WHERE memo_id=? AND status='ready'",(record['id'],)).fetchall():
        rule=rules.get(row['rule_id'])
        if not rule or row['rule_signature']!=signature(rule,record['recurrence'],record['schedule']):
            db.execute("UPDATE memo_reminder_events SET status='cancelled' WHERE id=?",(row['id'],))
    from .notifications import cancel_invalid
    cancel_invalid(db,record['id'])


def generate(db,now,*,scope=None,namespace=None):
    from .service import memo_dto
    state=db.execute('SELECT restore_id,automation_paused FROM app_state WHERE singleton=1').fetchone()
    if state['automation_paused']:return
    now=now.astimezone(ZONE);today=now.date();restore=state['restore_id']
    sql="SELECT * FROM memos WHERE deleted=0 AND (archived=0 OR (completed=1 AND recurrence_json IS NOT NULL))"
    args=()
    if scope is not None:sql+=' AND scope=? AND namespace=?';args=(scope,namespace)
    for row in db.execute(sql,args).fetchall():
        if row['scope']=='shared':
            if not db.execute('SELECT 1 FROM families WHERE id=? AND archived=0',(row['namespace'],)).fetchone():continue
            members={r['user_id']:r['relation_epoch'] for r in db.execute('SELECT user_id,relation_epoch FROM family_members WHERE family_id=? AND active=1',(row['namespace'],))}
        else:members={row['namespace']:0}
        from .lifecycle import state as lifecycle_state,save as save_lifecycle
        record=memo_dto(db,row);recurrence=record['recurrence'];rules=record['reminders']
        # Also settle due events from an older program before a fixed-cycle reopen.
        if row['completed'] and row['completed_at']:cancel_completed_events(db,row['id'],row['completed_at'])
        if recurrence:
            origin=date.fromisoformat(recurrence['start_date'])
            initial=min((reminder_time(rule,record['schedule']) for rule in rules),default=anchor(origin))
            shift=initial.date()-origin
            recent=dates(recurrence,origin,today-shift)
            elapsed=[day for day in recent if initial+timedelta(days=(day-origin).days)<=now]
            cycle=elapsed[-1] if elapsed else None
            if cycle:
                boundary=initial+timedelta(days=(cycle-origin).days)
                completed_at=local_time(row['completed_at']) if row['completed_at'] else None
                # Finishing before today's reminder still completes this cycle.
                if row['completed'] and completed_at and boundary.date()>completed_at.date():
                    lifecycle=lifecycle_state(row);lifecycle.pop('archived_at',None);lifecycle.pop('reason',None);lifecycle.pop('suppressed',None)
                    save_lifecycle(db,row['id'],lifecycle)
                    db.execute('UPDATE memos SET completed=0,completed_at=NULL,archived=0,last_cycle=?,version=version+1,updated_at=? WHERE id=?',(cycle.isoformat(),now.isoformat(),row['id']))
                    from .deletions import invalidate
                    invalidate(db,row['id']);row=db.execute('SELECT * FROM memos WHERE id=?',(row['id'],)).fetchone();record=memo_dto(db,row)
                elif row['last_cycle']!=cycle.isoformat():db.execute('UPDATE memos SET last_cycle=? WHERE id=?',(cycle.isoformat(),row['id']))
        if record['archived'] or record['completed']:continue
        for rule in rules:
            effective=rule_effective_at(db,record,rule)
            if rule['mode']=='relative':
                schedule=record['schedule'];base=anchor(schedule['start_date'],schedule['start_time']);offset=timedelta(minutes=rule['offset_minutes'])
                initial=base-offset
            else:
                initial=local_time(rule['at']);offset=timedelta(0)
            if recurrence:
                origin=date.fromisoformat(recurrence['start_date'])
                # Fixed absolute time shifts with the planned date, not completion.
                shift=(initial.date()-origin).days
                planned=dates(recurrence,today-timedelta(days=shift),today-timedelta(days=shift))
                due=[initial+timedelta(days=(planned_day-origin).days) for planned_day in planned]
            else:due=[initial]
            for when in due:
                if row['kind']=='schedule' and recurrence:
                    occurrence=(when.date()-timedelta(days=shift)).isoformat()
                    if lifecycle_state(row).get('occurrences',{}).get(occurrence,{}).get('archived_at'):continue
                if when.date()!=today or when>now or when<effective:continue
                recipients=set(members) if rule['all_members'] else set(rule['recipient_ids'])
                for uid in sorted(recipients & members.keys()):
                    identity=str(uuid5(NAMESPACE_URL,f'shared-life:reminder:{restore}:{row["id"]}:{rule["id"]}:{when.isoformat()}:{uid}'))
                    db.execute("INSERT OR IGNORE INTO memo_reminder_events(id,memo_id,rule_id,occurrence,user_id,source_restore_id,scope,namespace,relation_epoch,record_version,rule_signature,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'ready')",(identity,row['id'],rule['id'],when.isoformat(),uid,restore,row['scope'],row['namespace'],members[uid] or None,row['version'],signature(rule,recurrence,record['schedule']),now.isoformat()))
                    from .notifications import enqueue
                    enqueue(db,identity,now.timestamp())


def messages(db,user_id,*,include_ignored=False):
    from .service import source,namespace
    src=source(db,user_id);ns=namespace(src);result=[]
    ignored={r[0] for r in db.execute('SELECT event_id FROM memo_message_ignores WHERE user_id=? AND source_restore_id=? AND scope=? AND namespace=?',(user_id,src['restore_id'],src['scope'],ns))}
    for row in db.execute("SELECT e.*,m.title,m.version,m.kind,m.completed,m.archived,m.deleted,m.reminders_json,m.recurrence_json,m.schedule_json,m.archive_state_json FROM memo_reminder_events e JOIN memos m ON m.id=e.memo_id WHERE e.user_id=? AND e.scope=? AND e.namespace=? AND e.source_restore_id=? AND e.status='ready'",(user_id,src['scope'],ns,src['restore_id'])):
        if row['archived'] or row['deleted'] or row['completed'] or (row['relation_epoch'] or 0)!=src['relation_epoch'] or row['rule_id'] not in {r['id'] for r in json.loads(row['reminders_json'])}:continue
        rule=next(r for r in json.loads(row['reminders_json']) if r['id']==row['rule_id'])
        recurrence=json.loads(row['recurrence_json']) if row['recurrence_json'] else None
        schedule=json.loads(row['schedule_json']) if row['schedule_json'] else None
        if row['rule_signature']!=signature(rule,recurrence,schedule):continue
        from .lifecycle import event_is_archived
        if event_is_archived(row,row,rule):continue
        result.append({'id':row['id'],'kind':'reminder','target_type':'memo','target_id':row['memo_id'],'record_id':row['memo_id'],'title':row['title'],'created_at':row['created_at'],'source':src})
    for row in db.execute("SELECT r.*,m.title FROM memo_deletion_requests r JOIN memos m ON m.id=r.memo_id WHERE r.author_id=? AND r.status='pending' AND m.scope=? AND m.namespace=? AND m.deleted=0 AND m.version=r.target_version AND r.source_restore_id=?",(user_id,src['scope'],ns,src['restore_id'])):
        requester=families.relation(db,row['requester_id'],src['family_id']) if src['scope']=='shared' else None
        if not requester or requester['relation_epoch']!=row['requester_epoch'] or row['author_epoch']!=src['relation_epoch']:continue
        result.append({'id':f'memo-deletion:{row["id"]}:{row["version"]}','kind':'memo_deletion_request','target_type':'deletion_request','target_id':row['id'],'record_id':row['memo_id'],'title':row['title'],'created_at':row['created_at'],'source':src})
    result.extend(ledger_messages(db,user_id,src))
    from server.plans import service as plans
    for row in db.execute("SELECT * FROM plan_deletion_requests WHERE author_id=? AND status='pending'",(user_id,)).fetchall():
        try:
            target=plans.require(db,user_id,row['target_type'],row['target_id'])
        except Exception as error:
            from fastapi import HTTPException
            if isinstance(error,HTTPException) and error.status_code in (404,409):continue
            raise
        if target['version']!=row['target_version']:continue
        if row['source_restore_id']!=src['restore_id'] or row['source_scope']!=src['scope'] or row['family_id']!=src['family_id'] or row['author_epoch']!=src['relation_epoch']:continue
        requester=families.relation(db,row['requester_id'],src['family_id']) if src['scope']=='shared' else None
        if not requester or requester['relation_epoch']!=row['requester_epoch']:continue
        # D schema's own request provenance check is additionally required at decide.
        result.append({'id':f'plan-deletion:{row["id"]}:{row["version"]}','kind':'plan_deletion_request','target_type':'plan_deletion_request','target_id':row['id'],'record_id':row['target_id'],'title':target['title'],'created_at':row.get('created_at','') if isinstance(row,dict) else '', 'source':src})
    return [{**r,'ignored':r['id'] in ignored} for r in sorted(result,key=lambda r:(r['created_at'],r['id']),reverse=True) if include_ignored or r['id'] not in ignored]


def ignore(db,user_id,event_id):
    from .service import source,namespace,fail
    src=source(db,user_id)
    if not any(r['id']==event_id for r in messages(db,user_id,include_ignored=True)):fail('message_not_found',404)
    db.execute('INSERT OR IGNORE INTO memo_message_ignores(user_id,event_id,source_restore_id,scope,namespace,ignored_at) VALUES(?,?,?,?,?,?)',(user_id,event_id,src['restore_id'],src['scope'],namespace(src),datetime.now(UTC).isoformat()))
    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE user_id=? AND dedup_key=? AND source_restore_id=? AND status IN ('pending','failed','uncertain')",(user_id,'memo:'+event_id,src['restore_id']))
    return {'source':src,'id':event_id,'ignored':True}


def revoke(db,user_id,family_id):
    db.execute("UPDATE memo_deletion_requests SET status='stale',version=version+1 WHERE status='pending' AND (requester_id=? OR author_id=?) AND memo_id IN (SELECT id FROM memos WHERE scope='shared' AND namespace=?)",(user_id,user_id,family_id))
    db.execute("UPDATE memo_reminder_events SET status='cancelled' WHERE user_id=? AND scope='shared' AND namespace=?",(user_id,family_id))
    # Public membership hook also invalidates D requests, without altering D business code.
    db.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE status='pending' AND (requester_id=? OR author_id=?) AND (target_id IN (SELECT id FROM plan_records WHERE scope='shared' AND namespace=?) OR target_id IN (SELECT t.id FROM plan_tasks t JOIN plan_records p ON p.id=t.plan_id WHERE p.scope='shared' AND p.namespace=?))",(user_id,user_id,family_id,family_id))


def restore_message(db,user_id,event_id):
    from .service import source,namespace,fail
    src=source(db,user_id)
    if not any(r['id']==event_id for r in messages(db,user_id,include_ignored=True)):fail('message_not_found',404)
    db.execute('DELETE FROM memo_message_ignores WHERE user_id=? AND event_id=? AND source_restore_id=? AND scope=? AND namespace=?',(user_id,event_id,src['restore_id'],src['scope'],namespace(src)))
    return {'source':src,'id':event_id,'ignored':False}


def ledger_messages(db,user_id,src):
    from server.accounting import requests,category_changes
    result=[]
    if src['scope']=='shared':
        for row in db.execute("SELECT r.*,e.purpose,e.version,e.scope,e.voided FROM change_requests r JOIN expenses e ON e.id=r.expense_id WHERE r.owner_id=? AND r.family_id=? AND r.status='pending'",(user_id,src['family_id'])):
            if not requests.context_valid(db,row) or row['version']!=row['base_version'] or row['scope']!='shared' or row['voided']:continue
            result.append({'id':f'expense-change:{row["id"]}','kind':'expense_change_request','target_type':'expense_change_request','target_id':row['id'],'record_id':row['expense_id'],'title':row['purpose'],'created_at':row['created_at'],'source':src})
    for row in db.execute("SELECT c.* FROM category_changes c JOIN category_approvals a ON a.change_id=c.id WHERE c.status='pending' AND a.status='pending' AND a.owner_id=?",(user_id,)):
        if not category_changes.visible_to(db,row,user_id) or not category_changes.fresh(db,row):continue
        result.append({'id':f'expense-category:{row["id"]}:{user_id}','kind':'expense_category_change','target_type':'expense_category_change','target_id':row['id'],'record_id':row['id'],'title':row['source_name'] or '分类调整','created_at':row['submitted_at'] or row['created_at'],'source':src})
    if src['scope']=='private':
        now=datetime.now(UTC).isoformat()
        for row in db.execute("SELECT i.*,f.name,f.archived FROM family_invites i JOIN families f ON f.id=i.family_id WHERE i.target_user_id=? AND i.status='pending' AND i.expires_at>?",(user_id,now)):
            inviter=families.relation(db,row['inviter_id'],row['family_id'])
            if row['archived'] or not inviter or inviter['relation_epoch']!=row['inviter_epoch'] or families.relation(db,user_id,row['family_id']):continue
            result.append({'id':f'family-invite:{row["id"]}','kind':'family_invite','target_type':'family_invite','target_id':row['id'],'record_id':row['id'],'title':row['name'],'created_at':row['created_at'],'source':src})
    return result
