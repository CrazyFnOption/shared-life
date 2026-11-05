"""Memo reminder bridge to the existing bounded WebPush outbox."""
import json
from urllib.parse import urlencode
from uuid import uuid4
from server.accounting import families


def enqueue(db,event_id,when):
    event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(event_id,)).fetchone()
    if not event or event['status']!='ready':return
    db.execute("INSERT OR IGNORE INTO notification_outbox(id,user_id,dedup_key,next_attempt,data_version,source_scope,family_id,relation_epoch,source_restore_id,protocol_version,source_status) VALUES(?,?,?,?,?,?,?,?,?,2,'verified')",(str(uuid4()),event['user_id'],'memo:'+event_id,when,event['rule_signature'],event['scope'],event['namespace'] if event['scope']=='shared' else None,event['relation_epoch'],event['source_restore_id']))


def valid(db,outbox,*,include_ignored=False,for_delivery=True):
    from .events import signature
    state=db.execute('SELECT restore_id,automation_paused FROM app_state WHERE singleton=1').fetchone()
    if outbox['source_status']!='verified' or outbox['source_restore_id']!=state['restore_id'] or for_delivery and state['automation_paused']:return False
    if not outbox['dedup_key'].startswith('memo:') or outbox['source_scope'] not in ('private','shared'):return False
    event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(outbox['dedup_key'][5:],)).fetchone()
    if not event or event['status']!='ready' or event['user_id']!=outbox['user_id'] or event['source_restore_id']!=outbox['source_restore_id'] or event['scope']!=outbox['source_scope'] or event['rule_signature']!=outbox['data_version']:return False
    if event['scope']=='private':
        if event['namespace']!=outbox['user_id'] or outbox['family_id'] is not None or outbox['relation_epoch'] is not None:return False
    else:
        member=families.relation(db,outbox['user_id'],event['namespace'])
        if outbox['family_id']!=event['namespace'] or outbox['relation_epoch']!=event['relation_epoch'] or not member or member['relation_epoch']!=event['relation_epoch'] or not db.execute('SELECT 1 FROM families WHERE id=? AND archived=0',(event['namespace'],)).fetchone():return False
    record=db.execute('SELECT * FROM memos WHERE id=?',(event['memo_id'],)).fetchone()
    if not record or record['deleted'] or record['archived'] or record['completed'] or (record['scope'],record['namespace'])!=(event['scope'],event['namespace']):return False
    rule=next((r for r in json.loads(record['reminders_json']) if r['id']==event['rule_id']),None)
    recurrence=json.loads(record['recurrence_json']) if record['recurrence_json'] else None
    schedule=json.loads(record['schedule_json']) if record['schedule_json'] else None
    if not rule or event['rule_signature']!=signature(rule,recurrence,schedule):return False
    from .lifecycle import event_is_archived
    if event_is_archived(record,event,rule):return False
    if not rule['all_members'] and event['user_id'] not in rule['recipient_ids']:return False
    if not include_ignored and db.execute('SELECT 1 FROM memo_message_ignores WHERE user_id=? AND event_id=? AND source_restore_id=?',(outbox['user_id'],event['id'],state['restore_id'])).fetchone():return False
    return True


def payload(outbox):
    params={'notification':outbox['id'],'scope':outbox['source_scope'],'family_id':outbox['family_id'] or 'none','user_id':outbox['user_id'],'restore_id':outbox['source_restore_id'],'relation_epoch':outbox['relation_epoch'] or 0}
    return {'title':'同在','body':'你有新的提醒，请打开同在查看','url':'/?'+urlencode(params)}


def cancel_invalid(db,memo_id):
    for row in db.execute("SELECT o.* FROM notification_outbox o JOIN memo_reminder_events e ON o.dedup_key='memo:'||e.id WHERE e.memo_id=? AND o.status IN ('pending','failed','uncertain')",(memo_id,)).fetchall():
        if not valid(db,row):db.execute("UPDATE notification_outbox SET status='cancelled' WHERE id=?",(row['id'],))


def resolve(db,user_id,outbox_id):
    from .service import source,fail
    from .events import messages
    src=source(db,user_id)
    row=db.execute('SELECT * FROM notification_outbox WHERE id=? AND user_id=?',(outbox_id,user_id)).fetchone()
    if not row or row['source_scope']!=src['scope'] or row['family_id']!=src['family_id'] or (row['relation_epoch'] or 0)!=src['relation_epoch'] or not valid(db,row,include_ignored=True,for_delivery=False):fail('notification_unavailable',404)
    message=next((m for m in messages(db,user_id,include_ignored=True) if m['id']==row['dedup_key'][5:]),None)
    if not message:fail('notification_unavailable',404)
    return {'source':src,'message':message}
