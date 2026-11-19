"""Memo reminder bridge to the existing bounded WebPush outbox."""
import json
import re
from html import unescape
from urllib.parse import urlencode
from uuid import uuid4
from server.accounting import families


def enqueue(db,event_id,when):
    event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(event_id,)).fetchone()
    if not event or event['status']!='ready':return
    from .events import message_identity
    db.execute("INSERT OR IGNORE INTO notification_outbox(id,user_id,dedup_key,next_attempt,data_version,source_scope,family_id,relation_epoch,source_restore_id,protocol_version,source_status) VALUES(?,?,?,?,?,?,?,?,?,2,'verified')",(str(uuid4()),event['user_id'],'memo:'+message_identity(event),when,event['rule_signature'],event['scope'],event['namespace'] if event['scope']=='shared' else None,event['relation_epoch'],event['source_restore_id']))


def valid(db,outbox,*,include_ignored=False,for_delivery=True):
    from .events import signature
    state=db.execute('SELECT restore_id,automation_paused FROM app_state WHERE singleton=1').fetchone()
    if outbox['source_status']!='verified' or outbox['source_restore_id']!=state['restore_id'] or for_delivery and state['automation_paused']:return False
    if not outbox['dedup_key'].startswith('memo:') or outbox['source_scope'] not in ('private','shared'):return False
    from .events import parse_message_identity,message_identity
    event_id,revision=parse_message_identity(outbox['dedup_key'][5:])
    event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(event_id,)).fetchone()
    if event and event['delivery_revision']!=revision:return False
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
    if not include_ignored and db.execute('SELECT 1 FROM memo_message_ignores WHERE user_id=? AND event_id=? AND source_restore_id=?',(outbox['user_id'],message_identity(event),state['restore_id'])).fetchone():return False
    return True


def preview_text(markdown):
    """Short text only: images and link destinations never enter a push."""
    text=unescape(markdown)
    text=re.sub(r'(?m)^\s*\[[^\]]+\]:[^\n]*','',text)
    destination=r'\((?:[^()\n]|\([^()\n]*\))*\)'
    text=re.sub(r'!\[[^\]]*\]'+destination+r'|!\[[^\]]*\](?:\[[^\]]*\])?','',text)
    text=re.sub(r'\[([^\]]+)\]'+destination+r'|\[([^\]]+)\]\[[^\]]*\]',lambda m:m[1] or m[2],text)
    text=re.sub(r'<[^>]*>|(?:https?://|mailto:)\S+','',text)
    text=re.sub(r'(?m)^\s*(?:`{3,}|~{3,})[^\n]*|^\s{0,3}(?:#{1,6}\s+|>\s*|(?:[-+*]|\d+[.)])\s+)','',text)
    text=re.sub(r'\[[ xX]\]\s*|[*_~`]+','',text)
    return ' '.join(text.split())[:80]


def payload(db,outbox):
    # Reuse the delivery guard before reading any notification content.
    if not valid(db,outbox):return None
    from .events import parse_message_identity
    event_id,_=parse_message_identity(outbox['dedup_key'][5:])
    event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(event_id,)).fetchone()
    record=db.execute('SELECT title,body_markdown FROM memos WHERE id=?',(event['memo_id'],)).fetchone()
    from .calendar import local_time
    when=local_time(event['delivery_at'] or event['occurrence']).strftime('%m月%d日 %H:%M')
    summary=preview_text(record['body_markdown'])
    params={'notification':outbox['id'],'scope':outbox['source_scope'],'family_id':outbox['family_id'] or 'none','user_id':outbox['user_id'],'restore_id':outbox['source_restore_id'],'relation_epoch':outbox['relation_epoch'] or 0}
    return {'title':' '.join(record['title'].split())[:200],'body':when+(' · '+summary if summary else ''),'url':'/?'+urlencode(params)}


def cancel_invalid(db,memo_id):
    for row in db.execute("SELECT o.* FROM notification_outbox o JOIN memo_reminder_events e ON (o.dedup_key='memo:'||e.id OR o.dedup_key LIKE 'memo:'||e.id||':%') WHERE e.memo_id=? AND o.status IN ('pending','failed','uncertain')",(memo_id,)).fetchall():
        if not valid(db,row):db.execute("UPDATE notification_outbox SET status='cancelled' WHERE id=?",(row['id'],))


def resolve(db,user_id,outbox_id):
    from .service import source,fail
    from .events import messages
    src=source(db,user_id)
    row=db.execute('SELECT * FROM notification_outbox WHERE id=? AND user_id=?',(outbox_id,user_id)).fetchone()
    if not row:fail('notification_unavailable',404)
    if not row['dedup_key'].startswith('memo:'):
        from server.automation.notifications import resolve as review_resolve
        return review_resolve(db,user_id,row,src)
    if row['source_status']!='verified' or row['source_restore_id']!=src['restore_id'] or row['source_scope']!=src['scope'] or row['family_id']!=src['family_id'] or (row['relation_epoch'] or 0)!=src['relation_epoch']:fail('notification_unavailable',404)
    from .events import parse_message_identity,message_identity
    event_id,revision=parse_message_identity(row['dedup_key'][5:])
    event=db.execute('SELECT e.*,m.completed,m.archived,m.deleted,m.archive_state_json,m.kind,m.recurrence_json,m.schedule_json,m.reminders_json FROM memo_reminder_events e JOIN memos m ON m.id=e.memo_id WHERE e.id=? AND e.user_id=? AND m.scope=? AND m.namespace=?',(event_id,user_id,src['scope'],src['family_id'] or user_id)).fetchone()
    if event and not event['deleted']:
        from .lifecycle import event_is_archived
        rule=next((r for r in json.loads(event['reminders_json']) if r['id']==event['rule_id']),None)
        if event['completed'] or event['archived'] or rule and event_is_archived(event,event,rule):return {'source':src,'message':None,'notice':'本次提醒已完成或归档。'}
        if revision!=event['delivery_revision']:return {'source':src,'message':None,'notice':'本次已推后至'+event['delivery_at']+'。'}
    if not valid(db,row,include_ignored=True,for_delivery=False):
        event=db.execute('SELECT e.status,m.completed,m.archived,m.deleted FROM memo_reminder_events e JOIN memos m ON m.id=e.memo_id WHERE e.id=? AND e.user_id=? AND m.scope=? AND m.namespace=?',(event_id,user_id,src['scope'],src['family_id'] or user_id)).fetchone()
        if event and not event['deleted'] and (event['completed'] or event['archived']):return {'source':src,'message':None,'notice':'这条提醒对应事项已完成或归档，当前没有待处理提醒。'}
        fail('notification_unavailable',404)
    message=next((m for m in messages(db,user_id,include_ignored=True) if m['id']==row['dedup_key'][5:]),None)
    if not message:fail('notification_unavailable',404)
    return {'source':src,'message':message}
