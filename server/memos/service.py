"""Transactional memo data and stable attachment manifests, separate from money."""
from datetime import datetime, UTC
import hashlib
import json
import time
from pydantic import ValidationError
from fastapi import HTTPException
from server.accounting import families, images
from server.accounting.ledger import canonical_json
from server.memos import models

MAX_FILE_BYTES=20*1024*1024


def fail(code,status=409,**extra):
    raise HTTPException(status,detail={'code':code,**extra})


def source(db,user_id):
    captured=getattr(db,'source',None)
    if not captured or captured['user_id']!=user_id:
        fail('source_context_required')
    actual=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    if captured['restore_id']!=actual:fail('restore_changed',restore_id=actual)
    auth=getattr(db,'auth_session',None)
    if auth and not db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?',(*auth,int(time.time()))).fetchone():fail('login_required',401)
    families.require_scope(db,user_id,captured['scope'])
    return dict(captured)


def namespace(src):
    return src['family_id'] if src['scope']=='shared' else src['user_id']


def require_record(db,user_id,entity_id,*,category=False,include_deleted=False):
    src=source(db,user_id)
    table='memo_categories' if category else 'memos'
    row=db.execute(f'SELECT * FROM {table} WHERE id=?',(entity_id,)).fetchone()
    if not row or row['scope']!=src['scope'] or row['namespace']!=namespace(src) or (row['deleted'] and not include_deleted):
        fail('memo_not_found',404)
    if not category and row['deleted'] and row['author_id']!=user_id:
        fail('memo_not_found',404)
    return row


def attachment_meta(row):
    return {key:row[key] for key in ('id','memo_id','created_by','kind','name','mime_type','size_bytes','sha256','state')}


def descriptors(db,memo_id):
    return [{key:r[key] for key in ('id','kind','name','mime_type','size_bytes','sha256')} for r in db.execute("SELECT * FROM memo_attachments WHERE memo_id=? AND state!='deleted' ORDER BY id",(memo_id,))]


def category_dto(row):
    return {key:row[key] for key in ('id','scope','creator_id','name','version','position','created_at','updated_at')} | {'family_id':row['namespace'] if row['scope']=='shared' else None,'deleted':bool(row['deleted'])}


def memo_dto(db,row):
    result={key:row[key] for key in ('id','scope','author_id','updated_by','category_id','title','body_markdown','version','created_at','updated_at','kind')}
    result.update(family_id=row['namespace'] if row['scope']=='shared' else None,attachments=descriptors(db,row['id']))
    result.update({key:bool(row[key]) for key in ('completed','archived','deleted')})
    result['completed_at']=row['completed_at']
    result.update({key:json.loads(row[key+'_json']) if row[key+'_json'] else None for key in ('schedule','reminders','recurrence')})
    from .lifecycle import metadata
    result.update(metadata(db,row))
    from .calendar import current_occurrence,local_time
    from .events import rule_effective_at
    effective={rule['id']:rule_effective_at(db,result,rule) for rule in result['reminders']}
    current=current_occurrence(result,local_time(datetime.now(UTC)),effective)
    result['current_occurrence_at']=current['occurrence_at'] if current else None
    return result


def users_for(db,memos,categories):
    ids={r['author_id'] for r in memos}|{r['updated_by'] for r in memos}|{r['creator_id'] for r in categories}
    return [dict(db.execute('SELECT id,display_name FROM users WHERE id=?',(uid,)).fetchone()) for uid in sorted(ids)]


def snapshot(db,user_id):
    from . import collections,events,deletions
    src=source(db,user_id);ns=namespace(src)
    events.generate(db,datetime.now(UTC),scope=src['scope'],namespace=ns)
    categories=db.execute('SELECT * FROM memo_categories WHERE scope=? AND namespace=? ORDER BY position,created_at,id',(src['scope'],ns)).fetchall()
    memos=db.execute('SELECT * FROM memos WHERE scope=? AND namespace=? AND (deleted=0 OR author_id=?) ORDER BY created_at,id',(src['scope'],ns,user_id)).fetchall()
    attachments=[attachment_meta(r) for r in db.execute('SELECT a.* FROM memo_attachments a JOIN memos m ON m.id=a.memo_id WHERE m.scope=? AND m.namespace=? AND (m.deleted=0 OR m.author_id=?) ORDER BY a.id',(src['scope'],ns,user_id))]
    requests=[deletions.dto(r) for r in db.execute('SELECT r.* FROM memo_deletion_requests r JOIN memos m ON m.id=r.memo_id WHERE m.scope=? AND m.namespace=? ORDER BY r.created_at,r.id',(src['scope'],ns))]
    return {'source':src,'categories':[category_dto(r) for r in categories],'memos':[memo_dto(db,r) for r in memos],'attachments':attachments,'users':users_for(db,memos,categories),'focus':collections.read(db,user_id,'focus'),'focused_records':collections.active(db,user_id),'category_order':collections.read(db,user_id,'category_order'),'deletion_requests':requests,'messages':events.messages(db,user_id)}


def detail(db,user_id,memo_id):
    row=require_record(db,user_id,memo_id)
    cats=[require_record(db,user_id,row['category_id'],category=True)] if row['category_id'] else []
    return {'source':source(db,user_id),'memo':memo_dto(db,row),'attachments':[attachment_meta(r) for r in db.execute("SELECT * FROM memo_attachments WHERE memo_id=? AND state!='deleted' ORDER BY id",(memo_id,))],'users':users_for(db,[row],cats)}


def parse(model,data):
    try:
        return model.model_validate(data).model_dump(mode='json')
    except ValidationError:
        fail('invalid_memo_data',422)


def manifest(db,user_id,memo_id,items):
    ids=[x['id'] for x in items]
    if len(set(ids))!=len(ids):
        fail('duplicate_attachment',422)
    for item in items:
        old=db.execute('SELECT * FROM memo_attachments WHERE id=?',(item['id'],)).fetchone()
        if old:
            if old['memo_id']!=memo_id or old['state']=='deleted' or any(old[k]!=item[k] for k in ('kind','name','mime_type','size_bytes','sha256')):
                fail('attachment_id_reused')
        else:
            if item['kind']=='photo' and (item['size_bytes']>images.MAX_IMAGE_BYTES or item['mime_type'] not in images.FORMATS.values()):
                fail('invalid_photo_metadata',422)
            db.execute("INSERT INTO memo_attachments(id,memo_id,created_by,kind,name,mime_type,size_bytes,sha256,state) VALUES(?,?,?,?,?,?,?,?,'pending')",(item['id'],memo_id,user_id,item['kind'],item['name'],item['mime_type'],item['size_bytes'],item['sha256']))
    for row in db.execute("SELECT id FROM memo_attachments WHERE memo_id=? AND state!='deleted'",(memo_id,)).fetchall():
        if row['id'] not in ids:
            db.execute("UPDATE memo_attachments SET state='deleted' WHERE id=?",(row['id'],))


def receipt_lookup(db,user_id,op_id):
    src=source(db,user_id)
    old=db.execute('SELECT * FROM memo_operations WHERE user_id=? AND op_id=?',(user_id,op_id)).fetchone()
    if not old:
        fail('memo_operation_not_found',404)
    if old['source_restore_id']!=src['restore_id'] or old['source_scope']!=src['scope'] or old['family_id']!=src['family_id'] or (old['relation_epoch'] or 0)!=src['relation_epoch']:
        fail('memo_operation_not_found',404)
    if old['entity_type'] in ('focus','category_order'):
        from .collections import read
        if read(db,user_id,old['entity_type'])['id']!=old['entity_id']:fail('memo_operation_not_found',404)
    elif old['entity_type']=='deletion_request':
        from .deletions import require
        require(db,user_id,old['entity_id'])
    else:
        require_record(db,user_id,old['entity_id'],category=old['entity_type']=='category',include_deleted=True)
    stored=json.loads(old['receipt_json'])
    for effect in stored.get('effects',{}).get('memos',[]):
        require_record(db,user_id,effect['id'],include_deleted=True)
    return old


def submit(db,user_id,body):
    operation=body.model_dump(mode='json');entity_id=operation['entity_id']
    digest=hashlib.sha256(canonical_json(operation).encode()).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        src=source(db,user_id)
        if db.execute('SELECT 1 FROM memo_operations WHERE user_id=? AND op_id=?',(user_id,str(body.op_id))).fetchone():
            old=receipt_lookup(db,user_id,str(body.op_id))
            if old['request_hash']!=digest:fail('op_id_reused')
            result={'source':src,'receipt':json.loads(old['receipt_json'])};db.commit();return result
        effects={}
        if body.entity_type in ('focus','category_order'):
            from .collections import apply
            record=apply(db,user_id,body)
        elif body.entity_type=='deletion_request':
            from .deletions import apply
            record,effects=apply(db,user_id,body)
        else:
            record,effects=apply_record(db,user_id,body,src)
        receipt={'op_id':str(body.op_id),'entity_type':body.entity_type,'entity_id':entity_id,'version':record['version'],'record':record}
        if effects:receipt['effects']=effects
        db.execute('INSERT INTO memo_operations VALUES(?,?,?,?,?,?,?,?,?,?)',(user_id,str(body.op_id),body.entity_type,entity_id,digest,canonical_json(receipt),src['restore_id'],src['scope'],src['family_id'],src['relation_epoch'] if src['scope']=='shared' else None))
        db.commit();return {'source':src,'receipt':receipt}
    except BaseException:
        db.rollback();raise


def apply_record(db,user_id,body,src):
    from . import collections,deletions
    entity_id=str(body.entity_id);kind=body.kind;category=body.entity_type=='category';table='memo_categories' if category else 'memos'
    existing=db.execute(f'SELECT * FROM {table} WHERE id=?',(entity_id,)).fetchone()
    if kind=='create':
        if existing:
            require_record(db,user_id,entity_id,category=category,include_deleted=True);fail('memo_id_reused')
        if body.base_version!=0:fail('invalid_base_version',422)
    else:
        existing=require_record(db,user_id,entity_id,category=category,include_deleted=kind=='restore')
        if existing['version']!=body.base_version:fail('memo_conflict',current={**(category_dto(existing) if category else memo_dto(db,existing)),'entity_type':body.entity_type},current_version=existing['version'])
        if existing['version']>=families.MAX_COUNTER:fail('version_exhausted')
    now=datetime.now(UTC).isoformat();ns=namespace(src);effects={}
    if category:
        if kind=='delete':
            data=parse(models.CategoryDelete,body.data);target=data['target_category_id']
            rows=db.execute('SELECT * FROM memos WHERE category_id=?',(entity_id,)).fetchall()
            if target==entity_id:fail('invalid_target_category',422)
            if target:require_record(db,user_id,target,category=True)
            if rows and not target:fail('target_category_required')
            for row in rows:
                db.execute('UPDATE memos SET category_id=?,version=version+1,updated_by=?,updated_at=? WHERE id=?',(target,user_id,now,row['id']))
                deletions.invalidate(db,row['id'])
            db.execute('UPDATE memo_categories SET deleted=1,version=version+1,updated_at=? WHERE id=?',(now,entity_id))
            effects['memos']=[memo_dto(db,db.execute('SELECT * FROM memos WHERE id=?',(r['id'],)).fetchone()) for r in rows if not r['deleted'] or r['author_id']==user_id]
            collections.catalog_changed(db,user_id)
        elif kind in ('create','update','rename'):
            data=parse(models.CategoryData,body.data)
            if db.execute('SELECT 1 FROM memo_categories WHERE scope=? AND namespace=? AND name=? AND deleted=0 AND id!=?',(src['scope'],ns,data['name'],entity_id)).fetchone():fail('category_name_exists')
            if kind=='create':
                position=db.execute('SELECT COALESCE(max(position),-1)+1 FROM memo_categories WHERE scope=? AND namespace=? AND deleted=0',(src['scope'],ns)).fetchone()[0]
                db.execute('INSERT INTO memo_categories(id,scope,namespace,creator_id,name,version,position,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?,?)',(entity_id,src['scope'],ns,user_id,data['name'],position,now,now))
                collections.catalog_changed(db,user_id)
            else:db.execute('UPDATE memo_categories SET name=?,version=version+1,updated_at=? WHERE id=?',(data['name'],now,entity_id))
        else:fail('unsupported_memo_operation',422)
        record=category_dto(db.execute('SELECT * FROM memo_categories WHERE id=?',(entity_id,)).fetchone())
        effects['categories']=[record];effects['category_order']=collections.read(db,user_id,'category_order')
        return record,effects
    if kind in ('create','update'):
        data=parse(models.MemoData,body.data)
        if existing and (existing['kind']!=data['kind'] or existing['archived']):fail('memo_archived' if existing['archived'] else 'memo_kind_immutable')
        if data['category_id']:require_record(db,user_id,data['category_id'],category=True)
        if data['kind']=='schedule' and not data['schedule']:fail('invalid_schedule',422)
        if data['schedule'] and data['recurrence'] and data['schedule']['start_date']!=data['recurrence']['start_date']:fail('recurrence_start_mismatch',422)
        ids=[r['id'] for r in data['reminders']]
        if len(set(ids))!=len(ids):fail('duplicate_reminder',422)
        allowed={user_id} if src['scope']=='private' else {r[0] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(src['family_id'],))}
        author=existing['author_id'] if existing else user_id
        for reminder in data['reminders']:
            if reminder['mode']=='relative' and not data['schedule']:fail('relative_reminder_requires_schedule',422)
            if not reminder['recipient_ids'] and not reminder['all_members']:reminder['recipient_ids']=[author]
            if not set(reminder['recipient_ids'])<=allowed:fail('reminder_recipient_unavailable',422)
        schedule=json.dumps(data['schedule']) if data['schedule'] else None
        recurrence=json.dumps(data['recurrence']) if data['recurrence'] else None
        if kind=='create':
            db.execute('INSERT INTO memos(id,scope,namespace,author_id,updated_by,category_id,title,body_markdown,version,created_at,updated_at,kind,schedule_json,reminders_json,recurrence_json) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)',(entity_id,src['scope'],ns,user_id,user_id,data['category_id'],data['title'],data['body_markdown'],now,now,data['kind'],schedule,json.dumps(data['reminders']),recurrence))
        else:
            db.execute('UPDATE memos SET category_id=?,title=?,body_markdown=?,kind=?,schedule_json=?,reminders_json=?,recurrence_json=?,updated_by=?,version=version+1,updated_at=? WHERE id=?',(data['category_id'],data['title'],data['body_markdown'],data['kind'],schedule,json.dumps(data['reminders']),recurrence,user_id,now,entity_id))
        if existing:
            from .lifecycle import state,save
            lifecycle=state(existing);changed=False
            if existing['kind']=='schedule' and existing['recurrence_json'] and len(json.loads(existing['reminders_json']))==1 and len(data['reminders'])>1:
                from .lifecycle import preserve_legacy_reminder_occurrences
                preserve_legacy_reminder_occurrences(memo_dto(db,existing),lifecycle);changed=True
            if existing['schedule_json']!=schedule or existing['recurrence_json']!=recurrence:
                lifecycle.pop('suppressed',None)
                # Preserve archived dates/timestamps when editing the future series.
                lifecycle['occurrences']={day:entry for day,entry in lifecycle.get('occurrences',{}).items() if entry.get('archived_at')};changed=True
            if changed:save(db,entity_id,lifecycle)
        manifest(db,user_id,entity_id,data['attachments'])
    elif kind in ('complete','archive'):
        from .lifecycle import state,save,occurrence_day,occurrence_schedule
        from .calendar import local_time
        data=parse(models.CompleteData if kind=='complete' else models.ArchiveData,body.data)
        lifecycle=state(existing);record=memo_dto(db,existing)
        slot=None
        if data.get('occurrence_at'):
            from .calendar import reminder_instances
            instant=local_time(data['occurrence_at']);key=instant.isoformat()
            previous=lifecycle.get('reminder_occurrences',{}).get(key)
            if previous:slot={'occurrence_at':key,'reminder_id':previous['reminder_id']}
            else:
                from .events import rule_effective_at
                effective={r['id']:rule_effective_at(db,record,r) for r in record['reminders']}
                slot=next((item for item in reminder_instances(record,instant.date(),instant.date(),effective,include_completed=True) if item['occurrence_at']==key),None)
                if slot is None:fail('invalid_occurrence',422)
        elif data.get('occurrence_date') and len(record['reminders'])>1:
            from datetime import date
            from .calendar import reminder_instances,reminder_time
            from .events import rule_effective_at
            day=date.fromisoformat(data['occurrence_date']);display_days=[]
            for reminder in record['reminders']:
                initial=reminder_time(reminder,record['schedule'])
                origin=date.fromisoformat(record['recurrence']['start_date']) if record['recurrence'] else date.fromisoformat(record['schedule']['start_date']) if reminder['mode']=='relative' else initial.date()
                display_days.append(day+(initial.date()-origin))
            effective={r['id']:rule_effective_at(db,record,r) for r in record['reminders']}
            all_candidates=[item for item in reminder_instances(record,min(display_days),max(display_days),effective,include_completed=True) if item['occurrence_date']==day.isoformat()]
            archived_keys={item['occurrence_at'] for item in record['reminder_occurrences'] if item['archived_at']}
            candidates=[item for item in all_candidates if item['occurrence_at'] not in archived_keys]
            due=[item for item in candidates if local_time(item['occurrence_at'])<=local_time(now)]
            slot=min(due or candidates,key=lambda item:item['occurrence_at']) if candidates else None
            if not (data['completed'] if kind=='complete' else data['archived']):
                valid_keys={item['occurrence_at'] for item in all_candidates}
                archived=[item for item in record['reminder_occurrences'] if item['occurrence_at'] in valid_keys and item['archived_at']]
                if archived:slot=max(archived,key=lambda item:item['occurrence_at'])
            if slot is None:fail('no_pending_occurrence',409)
        elif kind=='complete' and not data.get('occurrence_date') and not record['completed'] and (record['recurrence'] or len(record['reminders'])>1) and record['reminders']:
            from .calendar import current_occurrence
            from .events import rule_effective_at
            effective={r['id']:rule_effective_at(db,record,r) for r in record['reminders']}
            slot=current_occurrence(record,local_time(now),effective)
            if slot is None:fail('no_pending_occurrence',409)
        occurrence=occurrence_day(record,data.get('occurrence_date'),local_time(now)) if slot is None else None
        desired=data['completed'] if kind=='complete' else data['archived']
        if slot:
            entries=lifecycle.setdefault('reminder_occurrences',{});previous=entries.get(slot['occurrence_at'],{})
            entries[slot['occurrence_at']]={'reminder_id':slot['reminder_id'],'archived_at':(previous.get('archived_at') or now) if desired else None,
                                         'completed':desired if kind=='complete' else previous.get('completed',False) if desired else False}
        elif occurrence:
            entries=lifecycle.setdefault('occurrences',{});previous=entries.get(occurrence,{})
            entries[occurrence]={'archived_at':(previous.get('archived_at') or now) if desired else None,
                                 'completed':desired if kind=='complete' else previous.get('completed',False) if desired else False,
                                 'suppressed':not desired,'schedule':previous.get('schedule') or occurrence_schedule(record['schedule'],occurrence)}
        else:
            if desired:
                lifecycle.update(archived_at=lifecycle.get('archived_at') or record.get('archived_at') or now,reason=kind)
                lifecycle.pop('suppressed',None)
            else:
                lifecycle.pop('archived_at',None);lifecycle.pop('reason',None);lifecycle['suppressed']=True
            completed=desired if kind=='complete' else bool(existing['completed']) if desired else False
            db.execute('UPDATE memos SET completed=?,completed_at=?,archived=? WHERE id=?',
                       (int(completed),existing['completed_at'] or now if completed else None,int(desired),entity_id))
        save(db,entity_id,lifecycle)
        db.execute('UPDATE memos SET updated_by=?,version=version+1,updated_at=? WHERE id=?',(user_id,now,entity_id))
        if desired:
            if occurrence or slot:
                from .lifecycle import cancel_occurrence_events
                cancel_occurrence_events(db,entity_id)
            else:
                from .events import cancel_completed_events
                cancel_completed_events(db,entity_id,now)
    elif kind in ('delete','restore'):
        parse(models.Empty,body.data)
        if user_id!=existing['author_id']:fail('creator_required',403)
        db.execute('UPDATE memos SET deleted=?,updated_by=?,version=version+1,updated_at=? WHERE id=?',(int(kind=='delete'),user_id,now,entity_id))
    else:fail('unsupported_memo_operation',422)
    deletions.invalidate(db,entity_id)
    record=memo_dto(db,db.execute('SELECT * FROM memos WHERE id=?',(entity_id,)).fetchone())
    if kind=='update':
        from .events import cancel_changed_events
        cancel_changed_events(db,record)
    elif kind in ('archive','delete'):
        from .notifications import cancel_invalid
        cancel_invalid(db,entity_id)
    return record,effects


def require_attachment(db,user_id,memo_id,attachment_id):
    require_record(db,user_id,memo_id)
    row=db.execute('SELECT * FROM memo_attachments WHERE id=? AND memo_id=?',(attachment_id,memo_id)).fetchone()
    if not row or row['state']=='deleted':
        fail('memo_attachment_not_found',404)
    return row


def upload(db,user_id,memo_id,attachment_id,data,mime_type):
    db.execute('BEGIN IMMEDIATE')
    try:
        row=require_attachment(db,user_id,memo_id,attachment_id)
        if row['size_bytes']!=len(data) or row['sha256']!=hashlib.sha256(data).hexdigest() or row['mime_type']!=mime_type:
            fail('attachment_mismatch')
        if row['kind']=='photo':
            images.validate_image(data,mime_type)
        if row['state']=='uploaded' and row['data']!=data:
            fail('attachment_mismatch')
        db.execute("UPDATE memo_attachments SET data=?,state='uploaded' WHERE id=?",(data,attachment_id))
        result={'source':source(db,user_id),'attachment':attachment_meta(db.execute('SELECT * FROM memo_attachments WHERE id=?',(attachment_id,)).fetchone())}
        db.commit();return result
    except BaseException:
        db.rollback();raise


def validate_database(db):
    from .validation import validate
    validate(db)
