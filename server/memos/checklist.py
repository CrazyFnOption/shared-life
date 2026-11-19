"""Checklist state shares the memo's existing version and original occurrence."""
import json
from .calendar import local_time,reminder_instances
from .service import fail


def legacy_occurrence_key(record,day):
    from datetime import date,timedelta
    from .calendar import reminder_time
    if not record['reminders']:return 'whole'
    rule=record['reminders'][0];initial=reminder_time(rule,record['schedule'])
    origin=record['schedule']['start_date'] if rule['mode']=='relative' else record['recurrence']['start_date']
    return (initial+timedelta(days=(date.fromisoformat(day)-date.fromisoformat(origin)).days)).isoformat()


def completion_scope(record):
    return 'occurrence' if not record['completed'] and record['reminders'] and (record['recurrence'] or len(record['reminders'])>1) else 'whole'


def state(row):
    return json.loads(row['checklist_json'])


def save(db,memo_id,value):
    db.execute('UPDATE memos SET checklist_json=? WHERE id=?',(json.dumps(value,separators=(',',':')),memo_id))


def slot(db,record,requested):
    if completion_scope(record)=='whole':
        if requested:fail('invalid_occurrence',422)
        return 'whole'
    if not requested:fail('occurrence_required',422)
    at=local_time(requested).isoformat()
    if any(local_time(item['occurrence_at']).isoformat()==at for item in record['reminder_occurrences']):return at
    from .events import rule_effective_at
    effective={r['id']:rule_effective_at(db,record,r) for r in record['reminders']}
    instant=local_time(at)
    if not any(item['occurrence_at']==at for item in reminder_instances(record,instant.date(),instant.date(),effective,include_completed=True)):fail('invalid_occurrence',422)
    return at


def edit(db,row,items):
    if items is None:return
    if len({item['id'] for item in items})!=len(items):fail('duplicate_checklist_item',422)
    value=state(row) if row else {'items':[],'states':{}}
    value['items']=items
    ids={item['id'] for item in items}
    for entry in value['states'].values():
        if 'items_snapshot' not in entry:entry['checked_ids']=[item for item in entry['checked_ids'] if item in ids]
    return value


def check(db,row,record,data):
    key=slot(db,record,data.get('occurrence_at'))
    if row['archived'] or row['completed'] or any(item['occurrence_at']==key and item['archived_at'] for item in record['reminder_occurrences']):fail('memo_archived')
    value=state(row);entry=value['states'].setdefault(key,{'checked_ids':[]})
    items=entry.get('items_snapshot',value['items'])
    if data['item_id'] not in {item['id'] for item in items}:fail('checklist_item_not_found',422)
    checked=set(entry['checked_ids'])
    if data['checked']:checked.add(data['item_id'])
    else:checked.discard(data['item_id'])
    entry['checked_ids']=[item['id'] for item in items if item['id'] in checked]
    save(db,row['id'],value)


def freeze(db,row,key):
    value=state(row)
    entry=value['states'].setdefault(key,{'checked_ids':[]})
    entry.setdefault('items_snapshot',value['items'])
    save(db,row['id'],value)
