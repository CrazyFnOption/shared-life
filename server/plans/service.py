"""Atomic, source-bound manual plan mutations and original receipts."""
from datetime import UTC, datetime
import hashlib
import json
import time
from fastapi import HTTPException
from server.accounting import families, images
from .models import validate_data

TABLES={'wish':'plan_records','plan':'plan_records','stage':'plan_stages','task':'plan_tasks','deletion_request':'plan_deletion_requests'}
ATTACHMENT_KEYS=('id','kind','name','mime_type','size_bytes','sha256')

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def fail(code,status=409,**extra):raise HTTPException(status,detail={'code':code,**extra})
def now():return datetime.now(UTC).isoformat()
def source(db,user_id):
    captured=getattr(db,'source',None)
    if not captured or getattr(db,'protocol',0)!=2:fail('source_context_required')
    if captured['user_id']!=user_id:fail('account_changed')
    actual=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    if captured['restore_id']!=actual:fail('restore_changed',restore_id=actual)
    auth=getattr(db,'auth_session',None)
    if auth and not db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?',(*auth,int(time.time()))).fetchone():fail('login_required',401)
    families.check_context(db,user_id,required=True)
    families.require_scope(db,user_id,captured['scope'])
    return dict(captured)
def namespace(origin):return origin['family_id'] if origin['scope']=='shared' else origin['user_id']
def raw(db,entity,identity):
    row=db.execute(f'SELECT * FROM {TABLES[entity]} WHERE id=?',(identity,)).fetchone()
    if row is None:fail('plan_not_found',404)
    if entity in {'wish','plan'} and row['kind']!=entity:fail('plan_not_found',404)
    return dict(row)
def parent_record(db,entity,row):
    if entity in {'wish','plan'}:return row
    if entity in {'task','stage'}:return raw(db,'plan',row['plan_id'])
    target=raw(db,row['target_type'],row['target_id'])
    return parent_record(db,row['target_type'],target)
def require(db,user_id,entity,identity,*,allow_deleted=False):
    origin=source(db,user_id)
    row=raw(db,entity,identity)
    if entity=='deletion_request':
        require(db,user_id,row['target_type'],row['target_id'],allow_deleted=allow_deleted)
        return row
    parent=parent_record(db,entity,row)
    if parent['scope']!=origin['scope'] or parent['namespace']!=namespace(origin):fail('plan_not_found',404)
    # A deletion receipt may expose its own deleted target, never its deleted ancestors.
    if parent['deleted'] and not (allow_deleted and entity in {'wish','plan'}):fail('plan_not_found',404)
    if row.get('deleted') and not allow_deleted:fail('plan_not_found',404)
    if entity=='task':
        stage=raw(db,'stage',row['stage_id'])
        if stage['plan_id']!=row['plan_id'] or stage['deleted']:fail('plan_not_found',404)
    return row

def metadata(row):return {k:row[k] for k in ('id','parent_id','created_by','kind','name','mime_type','size_bytes','sha256','state')}
def attachments(db,identity):return [metadata(r) for r in db.execute("SELECT * FROM plan_attachments WHERE parent_id=? AND state!='deleted' ORDER BY id",(identity,))]
def dto(db,entity,row):
    item=dict(row)
    if entity in {'wish','plan'}:
        item['family_id']=item['namespace'] if item['scope']=='shared' else None
        item.pop('namespace')
        for field in ('realized','archived','deleted'):item[field]=bool(item[field])
        item['attachments']=attachments(db,item['id'])
    elif entity=='task':
        item['assignee_ids']=json.loads(item.pop('assignee_ids_json'))
        item['completed']=bool(item['completed']);item['deleted']=bool(item['deleted'])
        item['attachments']=attachments(db,item['id'])
    elif entity=='stage':item['deleted']=bool(item['deleted'])
    return item

def task_reference(db,user_id,task_id):
    row=require(db,user_id,'task',task_id)
    parent=require(db,user_id,'plan',row['plan_id'])
    if parent['archived']:fail('plan_not_found',404)
    return dto(db,'task',row)

def unique_id(db,identity):
    if any(db.execute(f'SELECT 1 FROM {table} WHERE id=?',(identity,)).fetchone() for table in set(TABLES.values())):fail('plan_id_exists')
def version(db,entity,row,base):
    # Both callers obtain row through require() in this same source-bound transaction.
    if row['version']!=base:fail('plan_conflict',current_version=row['version'],current={**dto(db,entity,row),'entity_type':entity})
def assert_writable_parent(db,user_id,entity,row):
    parent=parent_record(db,entity,row)
    if parent['archived']:fail('plan_archived')
def invalidate_requests(db,identity):db.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE target_id=? AND status='pending'",(identity,))
def bump(db,entity,row,changes,user_id):
    table=TABLES[entity]
    values=dict(changes,version=row['version']+1)
    if entity in {'wish','plan'}:values.update(updated_by=user_id,updated_at=now())
    if entity=='task':values['updated_by']=user_id
    db.execute(f"UPDATE {table} SET "+','.join(f'{key}=?' for key in values)+' WHERE id=?',(*values.values(),row['id']))
    invalidate_requests(db,row['id'])
    return raw(db,entity,row['id'])

def manifest(db,user_id,parent_id,files):
    ids=[file['id'] for file in files]
    if len(ids)!=len(set(ids)):fail('duplicate_attachment',422)
    for file in files:
        if file['kind']=='photo' and (file['mime_type'] not in images.FORMATS.values() or file['size_bytes']>images.MAX_IMAGE_BYTES):fail('unsupported_image',422)
        row=db.execute('SELECT * FROM plan_attachments WHERE id=?',(file['id'],)).fetchone()
        if row:
            if row['parent_id']!=parent_id:fail('attachment_not_found',404)
            if row['state']=='deleted' or any(row[key]!=file[key] for key in ATTACHMENT_KEYS):fail('attachment_id_reused')
        else:
            db.execute('INSERT INTO plan_attachments(id,parent_id,created_by,kind,name,mime_type,size_bytes,sha256,state) VALUES(?,?,?,?,?,?,?,?,?)',(file['id'],parent_id,user_id,file['kind'],file['name'],file['mime_type'],file['size_bytes'],file['sha256'],'pending'))
    for row in db.execute("SELECT id FROM plan_attachments WHERE parent_id=? AND state!='deleted'",(parent_id,)).fetchall():
        if row['id'] not in ids:db.execute("UPDATE plan_attachments SET state='deleted' WHERE id=?",(row['id'],))

def receipt_source(row,origin):
    return row['source_restore_id']==origin['restore_id'] and row['source_scope']==origin['scope'] and row['family_id']==origin['family_id'] and row['relation_epoch']==origin['relation_epoch']
def previous(db,user_id,op_id,request_hash=None):
    origin=source(db,user_id)
    row=db.execute('SELECT * FROM plan_operations WHERE user_id=? AND op_id=?',(user_id,op_id)).fetchone()
    if not row:return None
    if not receipt_source(row,origin):fail('plan_not_found',404)
    receipt=json.loads(row['receipt_json'])
    deletion_receipt=row['operation_kind']=='delete' or (row['operation_kind']=='decide' and receipt['record'].get('status')=='accepted')
    require(db,user_id,row['entity_type'],row['entity_id'],allow_deleted=deletion_receipt)
    if request_hash is not None and row['request_hash']!=request_hash:fail('op_id_reused')
    return receipt

def request_current(db,request):
    restore=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    if request['source_restore_id']!=restore:return False
    target=raw(db,request['target_type'],request['target_id'])
    parent=parent_record(db,request['target_type'],target)
    if parent['scope']!=request['source_scope'] or (parent['namespace'] if parent['scope']=='shared' else None)!=request['family_id']:return False
    if target['deleted'] or parent['deleted'] or target['version']!=request['target_version']:return False
    if request['source_scope']=='private':return request['requester_id']==request['author_id']==parent['namespace'] and request['requester_epoch']==request['author_epoch']==0
    family=db.execute('SELECT archived FROM families WHERE id=?',(request['family_id'],)).fetchone()
    if not family or family['archived']:return False
    for key,epoch_key in [('requester_id','requester_epoch'),('author_id','author_epoch')]:
        member=families.relation(db,request[key],request['family_id'])
        if not member or member['relation_epoch']!=request[epoch_key]:return False
    return True

def stale_expired_requests(db,target_id):
    for request in db.execute("SELECT * FROM plan_deletion_requests WHERE target_id=? AND status='pending'",(target_id,)).fetchall():
        if not request_current(db,request):db.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE id=?",(request['id'],))

def check_assignees(db,user_id,ids,existing=()):
    origin=source(db,user_id)
    if len(ids)!=len(set(ids)):fail('duplicate_assignee',422)
    allowed={user_id} if origin['scope']=='private' else {r[0] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(origin['family_id'],))}
    if not (set(ids)-set(existing))<=allowed:fail('assignee_unavailable',422)

def apply(db,user_id,operation):
    try:data=validate_data(operation)
    except ValueError:fail('invalid_plan_operation',422)
    payload=operation.model_dump(mode='json');payload['data']=data
    request_hash=hashlib.sha256(canonical(payload).encode()).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        origin=source(db,user_id)
        old=previous(db,user_id,str(operation.op_id),request_hash)
        if old:db.commit();return {'source':origin,'receipt':old}
        entity,kind,identity,base=operation.entity_type,operation.kind,str(operation.entity_id),operation.base_version
        create=kind in {'create','from_wish'}
        row=None
        if create:
            if base!=0:fail('plan_conflict')
            unique_id(db,identity)
        else:
            row=require(db,user_id,entity,identity)
            version(db,entity,row,base)
        if entity in {'wish','plan'} and kind in {'create','from_wish','update'}:
            if entity=='wish' and (data['target_date'] is not None or data.get('source_wish_id')):fail('invalid_wish_fields',422)
            if entity=='plan' and data['wish_kind']:fail('invalid_plan_fields',422)
            if entity=='wish' and not data['wish_kind']:fail('wish_kind_required',422)
            if row:assert_writable_parent(db,user_id,entity,row)
            source_wish=None
            if kind=='from_wish':
                source_wish=require(db,user_id,'wish',data['source_wish_id'])
                if source_wish['archived']:fail('plan_archived')
                existing=db.execute('SELECT id FROM plan_records WHERE source_wish_id=? AND deleted=0',(source_wish['id'],)).fetchone()
                if existing:fail('wish_already_planned',plan_id=existing['id'])
            if create:
                timestamp=now()
                db.execute('INSERT INTO plan_records(id,kind,scope,namespace,author_id,updated_by,title,body_markdown,wish_kind,target_date,source_wish_id,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(identity,entity,origin['scope'],namespace(origin),user_id,user_id,data['title'],data['body_markdown'],data['wish_kind'],data['target_date'],source_wish['id'] if source_wish else None,1,timestamp,timestamp))
            else:bump(db,entity,row,{k:data[k] for k in ('title','body_markdown','wish_kind','target_date')},user_id)
            manifest(db,user_id,identity,data['attachments'])
        elif entity=='wish' and kind=='realized':bump(db,entity,row,{'realized':int(data['realized'])},user_id)
        elif entity in {'wish','plan'} and kind=='archive':bump(db,entity,row,{'archived':int(data['archived'])},user_id)
        elif entity=='stage' and kind in {'create','update'}:
            parent=require(db,user_id,'plan',data['plan_id']);assert_writable_parent(db,user_id,'plan',parent)
            if create:db.execute('INSERT INTO plan_stages(id,plan_id,title,position,version) VALUES(?,?,?,?,1)',(identity,data['plan_id'],data['title'],data['position']))
            else:
                if row['plan_id']!=data['plan_id']:fail('plan_not_found',404)
                bump(db,entity,row,{'title':data['title'],'position':data['position']},user_id)
        elif entity=='task' and kind in {'create','update'}:
            parent=require(db,user_id,'plan',data['plan_id']);assert_writable_parent(db,user_id,'plan',parent)
            stage=require(db,user_id,'stage',data['stage_id'])
            if stage['plan_id']!=parent['id']:fail('plan_not_found',404)
            check_assignees(db,user_id,data['assignee_ids'],json.loads(row['assignee_ids_json']) if row else ())
            fields={k:data[k] for k in ('title','body_markdown','stage_id','due_date','position')}
            fields['assignee_ids_json']=canonical(data['assignee_ids'])
            if create:
                fields.update(id=identity,plan_id=parent['id'],author_id=user_id,updated_by=user_id,version=1)
                db.execute('INSERT INTO plan_tasks('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values()))
            else:
                if row['plan_id']!=parent['id']:fail('plan_not_found',404)
                bump(db,entity,row,fields,user_id)
            manifest(db,user_id,identity,data['attachments'])
        elif entity=='task' and kind=='complete':
            assert_writable_parent(db,user_id,entity,row)
            bump(db,entity,row,{'completed':int(data['completed'])},user_id)
        elif kind=='delete' and entity in {'wish','plan','task','stage'}:
            if entity=='stage':
                assert_writable_parent(db,user_id,entity,row)
                if db.execute('SELECT 1 FROM plan_tasks WHERE stage_id=? AND deleted=0',(identity,)).fetchone():fail('stage_has_tasks')
            elif row['author_id']!=user_id:fail('creator_required',403)
            bump(db,entity,row,{'deleted':1},user_id)
            db.execute("UPDATE plan_attachments SET state='deleted' WHERE parent_id=?",(identity,))
        elif entity=='deletion_request' and kind=='create':
            target=require(db,user_id,data['target_type'],data['target_id']);version(db,data['target_type'],target,data['target_version'])
            if target['author_id']==user_id:fail('creator_can_delete',422)
            stale_expired_requests(db,target['id'])
            if db.execute("SELECT 1 FROM plan_deletion_requests WHERE target_id=? AND requester_id=? AND status='pending'",(target['id'],user_id)).fetchone():fail('deletion_request_exists')
            author_relation=families.relation(db,target['author_id'],origin['family_id']) if origin['scope']=='shared' else None
            if origin['scope']=='shared' and not author_relation:fail('creator_unavailable')
            db.execute("INSERT INTO plan_deletion_requests(id,target_type,target_id,requester_id,author_id,source_restore_id,source_scope,family_id,requester_epoch,author_epoch,target_version,reason,status,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',1)",(identity,data['target_type'],target['id'],user_id,target['author_id'],origin['restore_id'],origin['scope'],origin['family_id'],origin['relation_epoch'],author_relation['relation_epoch'] if author_relation else 0,target['version'],data['reason']))
        elif entity=='deletion_request' and kind=='decide':
            if row['author_id']!=user_id:fail('creator_required',403)
            if row['status']!='pending':fail('deletion_request_decided')
            if not request_current(db,row):
                db.execute("UPDATE plan_deletion_requests SET status='stale',version=version+1 WHERE id=?",(identity,))
                db.commit()
                fail('deletion_request_stale')
            target=require(db,user_id,row['target_type'],row['target_id']);version(db,row['target_type'],target,row['target_version'])
            if data['accepted']:
                bump(db,row['target_type'],target,{'deleted':1},user_id)
                db.execute("UPDATE plan_attachments SET state='deleted' WHERE parent_id=?",(target['id'],))
            db.execute('UPDATE plan_deletion_requests SET status=?,version=version+1 WHERE id=?',('accepted' if data['accepted'] else 'rejected',identity))
        else:fail('invalid_plan_operation',422)
        record=dto(db,entity,raw(db,entity,identity))
        receipt={'op_id':str(operation.op_id),'entity_type':entity,'entity_id':identity,'version':record['version'],'record':record}
        db.execute('INSERT INTO plan_operations(user_id,op_id,entity_type,entity_id,operation_kind,request_hash,receipt_json,source_restore_id,source_scope,family_id,relation_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(user_id,str(operation.op_id),entity,identity,kind,request_hash,canonical(receipt),origin['restore_id'],origin['scope'],origin['family_id'],origin['relation_epoch']))
        db.commit();return {'source':origin,'receipt':receipt}
    except BaseException:db.rollback();raise

def sync(db,user_id):
    db.execute('BEGIN')
    try:
        origin=source(db,user_id)
        rows=[dict(r) for r in db.execute('SELECT * FROM plan_records WHERE scope=? AND namespace=? AND deleted=0 ORDER BY created_at DESC,id',(origin['scope'],namespace(origin)))]
        records=[dto(db,r['kind'],r) for r in rows];parents={r['id'] for r in rows if r['kind']=='plan'}
        stages=[dto(db,'stage',r) for r in db.execute('SELECT * FROM plan_stages WHERE deleted=0 ORDER BY position,id') if r['plan_id'] in parents]
        stage_ids={r['id'] for r in stages}
        tasks=[dto(db,'task',r) for r in db.execute('SELECT * FROM plan_tasks WHERE deleted=0 ORDER BY position,id') if r['plan_id'] in parents and r['stage_id'] in stage_ids]
        targets={r['id'] for r in records+tasks}
        requests=[dict(r) for r in db.execute('SELECT * FROM plan_deletion_requests ORDER BY id') if r['target_id'] in targets]
        people={user_id}|{r['author_id'] for r in records+tasks}|{r['updated_by'] for r in records+tasks}|{r['requester_id'] for r in requests}|{uid for r in tasks for uid in r['assignee_ids']}
        if origin['scope']=='shared':people|={r[0] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(origin['family_id'],))}
        assignable={user_id} if origin['scope']=='private' else {r[0] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(origin['family_id'],))}
        users=[dict(r,assignable=r['id'] in assignable) for r in db.execute('SELECT id,display_name FROM users') if r['id'] in people]
        for request in requests:
            if request['status']=='pending' and not request_current(db,request):request.update(status='stale',version=request['version']+1)
        result={'source':origin,'records':records,'stages':stages,'tasks':tasks,'attachments':[file for r in records+tasks for file in r['attachments']],'deletion_requests':requests,'users':users}
        db.commit();return result
    except BaseException:db.rollback();raise

def attachment_parent(db,user_id,parent_id,write=False):
    row=db.execute('SELECT kind FROM plan_records WHERE id=?',(parent_id,)).fetchone()
    entity=row['kind'] if row else 'task'
    parent=require(db,user_id,entity,parent_id)
    if write:assert_writable_parent(db,user_id,entity,parent)
    return parent

def put_attachment(db,user_id,parent_id,attachment_id,data,content_type):
    db.execute('BEGIN IMMEDIATE')
    try:
        origin=source(db,user_id);attachment_parent(db,user_id,parent_id)
        row=db.execute("SELECT * FROM plan_attachments WHERE id=? AND parent_id=? AND state!='deleted'",(attachment_id,parent_id)).fetchone()
        if not row:fail('attachment_not_found',404)
        if len(data)!=row['size_bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256'] or content_type!=row['mime_type']:fail('attachment_mismatch')
        if row['kind']=='photo':images.validate_image(data,content_type)
        if row['state']=='pending':db.execute("UPDATE plan_attachments SET data=?,state='uploaded' WHERE id=?",(data,attachment_id))
        result=metadata(db.execute('SELECT * FROM plan_attachments WHERE id=?',(attachment_id,)).fetchone())
        db.commit();return {'source':origin,'attachment':result}
    except BaseException:db.rollback();raise

def get_attachment(db,user_id,parent_id,attachment_id):
    db.execute('BEGIN')
    try:
        attachment_parent(db,user_id,parent_id)
        row=db.execute("SELECT * FROM plan_attachments WHERE id=? AND parent_id=? AND state!='deleted'",(attachment_id,parent_id)).fetchone()
        if not row:fail('attachment_not_found',404)
        if row['state']!='uploaded':fail('attachment_not_uploaded')
        result=dict(row);db.commit();return result
    except BaseException:db.rollback();raise

def validate_database(db):
    """Reject corrupt references/bytes and provenance; never repair unknown data."""
    def bad():raise ValueError('invalid_plans_database')
    restore=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    people={r[0] for r in db.execute('SELECT id FROM users')};families_ids={r[0] for r in db.execute('SELECT id FROM families')}
    records={r['id']:dict(r) for r in db.execute('SELECT * FROM plan_records')}
    stages={r['id']:dict(r) for r in db.execute('SELECT * FROM plan_stages')}
    tasks={r['id']:dict(r) for r in db.execute('SELECT * FROM plan_tasks')}
    if set(records)&set(stages) or set(records)&set(tasks) or set(stages)&set(tasks):bad()
    for r in records.values():
        if r['author_id'] not in people or r['updated_by'] not in people or r['namespace'] not in (people if r['scope']=='private' else families_ids):bad()
        if r['scope']=='private' and r['namespace']!=r['author_id']:bad()
        if r['kind']=='wish' and (r['wish_kind'] not in {'生活愿景','未来旅行','想要的东西'} or r['source_wish_id'] or r['target_date']):bad()
        if r['source_wish_id']:
            wish=records.get(r['source_wish_id'])
            if not wish or wish['kind']!='wish' or (wish['scope'],wish['namespace'])!=(r['scope'],r['namespace']):bad()
    for r in stages.values():
        if r['plan_id'] not in records or records[r['plan_id']]['kind']!='plan':bad()
    for r in tasks.values():
        if r['stage_id'] not in stages or stages[r['stage_id']]['plan_id']!=r['plan_id'] or r['author_id'] not in people or r['updated_by'] not in people:bad()
        assignees=json.loads(r['assignee_ids_json'])
        if not isinstance(assignees,list) or len(set(assignees))!=len(assignees) or not set(assignees)<=people:bad()
    requests={r['id']:dict(r) for r in db.execute('SELECT * FROM plan_deletion_requests')}
    for r in requests.values():
        target=(tasks if r['target_type']=='task' else records).get(r['target_id'])
        if not target or target['author_id']!=r['author_id'] or r['requester_id'] not in people:bad()
        parent=records[target['plan_id']] if r['target_type']=='task' else target
        if r['source_scope']!=parent['scope'] or r['family_id']!=(parent['namespace'] if parent['scope']=='shared' else None):bad()
        if r['source_scope']=='private':
            if r['requester_id']!=r['author_id'] or r['requester_epoch']!=0 or r['author_epoch']!=0:bad()
        else:
            for person_key,epoch_key in [('requester_id','requester_epoch'),('author_id','author_epoch')]:
                member=families.relation(db,r[person_key],r['family_id'],active=False)
                if not member or not 0<r[epoch_key]<=member['relation_epoch']:bad()
        # Historical stale/decided requests retain their original restore provenance.
        if not isinstance(r['source_restore_id'],str) or not r['source_restore_id']:bad()
        if r['status']=='pending' and r['source_restore_id']!=restore:bad()
    for row in db.execute('SELECT * FROM plan_attachments'):
        if row['parent_id'] not in records and row['parent_id'] not in tasks:bad()
        if row['state']=='pending' and row['data'] is not None:bad()
        if row['state']=='uploaded' and row['data'] is None:bad()
        if row['data'] is not None:
            if len(row['data'])!=row['size_bytes'] or hashlib.sha256(row['data']).hexdigest()!=row['sha256']:bad()
            if row['kind']=='photo':images.validate_image(row['data'],row['mime_type'])
    restore=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    for row in db.execute('SELECT * FROM plan_operations'):
        receipt=json.loads(row['receipt_json']);entity=row['entity_type']
        if entity not in TABLES or row['user_id'] not in people or row['source_restore_id']!=restore:bad()
        allowed_kinds={'wish':{'create','update','realized','archive','delete'},'plan':{'create','from_wish','update','archive','delete'},'stage':{'create','update','delete'},'task':{'create','update','complete','delete'},'deletion_request':{'create','decide'}}
        if row['operation_kind'] not in allowed_kinds[entity]:bad()
        if row['operation_kind']=='delete' and receipt.get('record',{}).get('deleted') is not True:bad()
        if receipt.get('entity_id')!=row['entity_id'] or receipt.get('op_id')!=row['op_id'] or receipt.get('entity_type')!=entity or receipt.get('record',{}).get('id')!=row['entity_id']:bad()
        target=(tasks if entity=='task' else stages if entity=='stage' else requests if entity=='deletion_request' else records).get(row['entity_id'])
        if not target:bad()
        if entity=='deletion_request':target=(tasks if target['target_type']=='task' else records)[target['target_id']]
        parent=records[target['plan_id']] if 'plan_id' in target else target
        if row['source_scope']!=parent['scope'] or (row['family_id'] if parent['scope']=='shared' else row['user_id'])!=parent['namespace']:bad()
        if parent['scope']=='shared':
            relation=families.relation(db,row['user_id'],row['family_id'],active=False)
            if not relation or not 0<row['relation_epoch']<=relation['relation_epoch']:bad()
        elif row['family_id'] is not None or row['relation_epoch']!=0:bad()
