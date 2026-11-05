"""Semantic validation for full C data, including retained recovery bytes."""
import hashlib,json
from server.accounting import images
from server.memos import models


def validate(db):
    def bad():raise ValueError('Invalid memo database')
    def ns_valid(scope,ns):
        return db.execute('SELECT 1 FROM '+('users' if scope=='private' else 'families')+' WHERE id=?',(ns,)).fetchone()
    people={r[0] for r in db.execute('SELECT id FROM users')}
    categories={r['id']:r for r in db.execute('SELECT * FROM memo_categories')}
    memos={r['id']:r for r in db.execute('SELECT * FROM memos')}
    restore=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    for cat in categories.values():
        if not ns_valid(cat['scope'],cat['namespace']) or cat['scope']=='private' and cat['creator_id']!=cat['namespace']:bad()
    for row in memos.values():
        if not ns_valid(row['scope'],row['namespace']) or row['scope']=='private' and row['author_id']!=row['namespace']:bad()
        if row['kind']=='memo':
            cat=categories.get(row['category_id'])
            if not cat or (cat['scope'],cat['namespace'])!=(row['scope'],row['namespace']) or cat['deleted']:bad()
        elif row['category_id'] is not None or not row['schedule_json']:bad()
        data={'category_id':row['category_id'],'title':row['title'],'body_markdown':row['body_markdown'],'kind':row['kind'],'schedule':json.loads(row['schedule_json']) if row['schedule_json'] else None,'reminders':json.loads(row['reminders_json']),'recurrence':json.loads(row['recurrence_json']) if row['recurrence_json'] else None,'attachments':[]}
        try:
            models.MemoData.model_validate(data)
            if 'archive_state_json' in row.keys():models.ArchiveState.model_validate(json.loads(row['archive_state_json']))
        except Exception:bad()
        rules=data['reminders']
        if len({r['id'] for r in rules})!=len(rules) or any(not set(r['recipient_ids'])<=people for r in rules):bad()
    for row in db.execute('SELECT * FROM memo_attachments'):
        if row['memo_id'] not in memos or row['created_by'] not in people:bad()
        try:models.Attachment.model_validate({k:row[k] for k in ('id','kind','name','mime_type','size_bytes','sha256')})
        except Exception:bad()
        if row['state']=='pending' and row['data'] is not None or row['state']=='uploaded' and row['data'] is None:bad()
        if row['data'] is not None:
            if len(row['data'])!=row['size_bytes'] or hashlib.sha256(row['data']).hexdigest()!=row['sha256']:bad()
            if row['kind']=='photo':
                try:images.validate_image(row['data'],row['mime_type'])
                except Exception:bad()
    for table,key in (('memo_focus','targets'),('memo_category_orders','category_ids')):
        for row in db.execute(f'SELECT * FROM {table}'):
            if not ns_valid(row['scope'],row['namespace']):bad()
            values=json.loads(row[key+'_json'])
            try:(models.FocusData if key=='targets' else models.CategoryOrderData).model_validate({key:values})
            except Exception:bad()
            if key=='category_ids':
                if len(set(values))!=len(values):bad()
                expected={r['id'] for r in categories.values() if not r['deleted'] and (r['scope'],r['namespace'])==(row['scope'],row['namespace'])}
                if set(values)!=expected:bad()
            else:
                if len({(r['kind'],r['id']) for r in values})!=len(values):bad()
                for target in values:
                    if target['kind']=='memo':parent=memos.get(target['id'])
                    else:
                        task=db.execute('SELECT plan_id FROM plan_tasks WHERE id=?',(target['id'],)).fetchone()
                        parent=db.execute('SELECT * FROM plan_records WHERE id=?',(task[0],)).fetchone() if task else None
                    if not parent or (parent['scope'],parent['namespace'])!=(row['scope'],row['namespace']):bad()
    requests={r['id']:r for r in db.execute('SELECT * FROM memo_deletion_requests')}
    for row in requests.values():
        memo=memos.get(row['memo_id'])
        if not memo or memo['scope']!='shared' or memo['author_id']!=row['author_id']:bad()
        if row['status']=='pending' and row['source_restore_id']!=restore:bad()
        for uid,key in ((row['requester_id'],'requester_epoch'),(row['author_id'],'author_epoch')):
            member=db.execute('SELECT relation_epoch FROM family_members WHERE user_id=? AND family_id=?',(uid,memo['namespace'])).fetchone()
            if not member or not isinstance(row[key],int) or not 0<row[key]<=member[0]:bad()
    from server.automation.backups import validate_relation_source
    for row in db.execute('SELECT * FROM memo_operations'):
        if row['source_restore_id']!=restore:bad()
        validate_relation_source(db,row['user_id'],row['source_scope'],row['family_id'],row['relation_epoch'])
        entity=row['entity_type'];ns=row['family_id'] if row['source_scope']=='shared' else row['user_id']
        if entity=='memo':obj=memos.get(row['entity_id'])
        elif entity=='category':obj=categories.get(row['entity_id'])
        elif entity in ('focus','category_order'):
            obj=db.execute('SELECT * FROM '+('memo_focus' if entity=='focus' else 'memo_category_orders')+' WHERE id=?',(row['entity_id'],)).fetchone()
        elif entity=='deletion_request':
            request=requests.get(row['entity_id']);obj=memos.get(request['memo_id']) if request else None
        else:bad()
        if not obj or (obj['scope'],obj['namespace'])!=(row['source_scope'],ns):bad()
        receipt=json.loads(row['receipt_json']);record=receipt.get('record',{})
        if receipt.get('op_id')!=row['op_id'] or receipt.get('entity_type')!=entity or receipt.get('entity_id')!=row['entity_id'] or record.get('id')!=row['entity_id']:bad()
        for effect in receipt.get('effects',{}).get('memos',[]):
            parent=memos.get(effect.get('id'))
            if not parent or (parent['scope'],parent['namespace'])!=(row['source_scope'],ns) or effect.get('scope')!=row['source_scope'] or effect.get('family_id')!=row['family_id']:bad()
        if entity!='deletion_request' and (record.get('scope')!=row['source_scope'] or record.get('family_id')!=row['family_id']):bad()
    for row in db.execute('SELECT * FROM memo_reminder_events'):
        memo=memos.get(row['memo_id'])
        if not memo or (memo['scope'],memo['namespace'])!=(row['scope'],row['namespace']) or row['user_id'] not in people:bad()
        if len(row['rule_signature'])!=64 or row['record_version']<1:bad()
        if row['status']=='ready' and row['source_restore_id']!=restore:bad()
        validate_relation_source(db,row['user_id'],row['scope'],row['namespace'] if row['scope']=='shared' else None,row['relation_epoch'])
    for row in db.execute('SELECT * FROM memo_message_ignores'):
        if not ns_valid(row['scope'],row['namespace']) or row['scope']=='private' and row['user_id']!=row['namespace']:bad()

    for row in db.execute("SELECT * FROM notification_outbox WHERE dedup_key LIKE 'memo:%'"):
        event=db.execute('SELECT * FROM memo_reminder_events WHERE id=?',(row['dedup_key'][5:],)).fetchone()
        if not event or row['protocol_version']!=2 or row['user_id']!=event['user_id'] or row['source_scope']!=event['scope'] or row['family_id']!=(event['namespace'] if event['scope']=='shared' else None) or row['relation_epoch']!=event['relation_epoch'] or row['source_restore_id']!=event['source_restore_id'] or row['data_version']!=event['rule_signature']:bad()
