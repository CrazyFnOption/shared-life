"""Schema 10 archive state; fixed-cycle dates remain in last_cycle.

Receipts stay immutable. A restored occurrence stays open until rescheduled.
"""
import json
from datetime import date, timedelta
from .calendar import anchor, dates, local_time


def state(row):
    return json.loads(row['archive_state_json'])


def save(db,memo_id,value):
    db.execute('UPDATE memos SET archive_state_json=? WHERE id=?',(json.dumps(value,separators=(',',':')),memo_id))


def occurrence_schedule(schedule,occurrence):
    shifted=dict(schedule);shift=date.fromisoformat(occurrence)-date.fromisoformat(schedule['start_date'])
    shifted['start_date']=occurrence
    if schedule.get('end_date'):shifted['end_date']=(date.fromisoformat(schedule['end_date'])+shift).isoformat()
    return shifted


def occurrence_day(record,requested,now):
    # New unified completion addresses the memo. Explicit old queued payloads
    # still address their original schedule occurrence.
    if not requested:return None
    from .service import fail
    if record['kind']=='schedule' and any(item['start_date']==requested for item in record.get('archived_occurrences',[])):return requested
    if not record['recurrence']:fail('occurrence_requires_recurrence',422)
    if record['kind']!='schedule':fail('occurrence_requires_schedule',422)
    day=date.fromisoformat(requested)
    if day not in dates(record['recurrence'],day,day):fail('invalid_occurrence',422)
    return requested


def legacy_archive_time(db,row):
    """Recover a historic transition from immutable receipts, never updated_at."""
    at=None;was_archived=False
    receipts=[]
    for item in db.execute("SELECT receipt_json FROM memo_operations WHERE entity_type='memo' AND entity_id=?",(row['id'],)):
        record=json.loads(item[0]).get('record',{})
        receipts.append(record)
    for record in sorted(receipts,key=lambda r:r.get('version',0)):
        archived=bool(record.get('archived'))
        if archived and not was_archived:at=record.get('archived_at') or record.get('updated_at')
        if not archived:at=None
        was_archived=archived
    return at


def metadata(db,row):
    value=state(row)
    archived=bool(row['archived']) or row['kind']=='memo' and bool(row['completed'])
    at=value.get('archived_at')
    if archived and not at:
        at=row['completed_at'] if row['completed'] else legacy_archive_time(db,row)
    return {'archived':archived,'archived_at':at,'auto_archive_suppressed':bool(value.get('suppressed')),
            'archived_occurrences':[{'start_date':day,**entry} for day,entry in sorted(value.get('occurrences',{}).items())]}


def event_is_archived(row,event,rule):
    if row['kind']!='schedule' or not row['recurrence_json']:return False
    schedule=json.loads(row['schedule_json']);recurrence=json.loads(row['recurrence_json'])
    initial=anchor(schedule['start_date'],schedule['start_time'])-timedelta(minutes=rule['offset_minutes']) if rule['mode']=='relative' else local_time(rule['at'])
    shift=initial.date()-date.fromisoformat(recurrence['start_date'])
    occurrence=(local_time(event['occurrence']).date()-shift).isoformat()
    return bool(state(row).get('occurrences',{}).get(occurrence,{}).get('archived_at'))


def cancel_occurrence_events(db,memo_id):
    row=db.execute('SELECT * FROM memos WHERE id=?',(memo_id,)).fetchone()
    rules={rule['id']:rule for rule in json.loads(row['reminders_json'])}
    for event in db.execute("SELECT * FROM memo_reminder_events WHERE memo_id=? AND status='ready'",(memo_id,)).fetchall():
        if event['rule_id'] in rules and event_is_archived(row,event,rules[event['rule_id']]):
            db.execute("UPDATE memo_reminder_events SET status='cancelled' WHERE id=?",(event['id'],))
    from .notifications import cancel_invalid
    cancel_invalid(db,memo_id)
