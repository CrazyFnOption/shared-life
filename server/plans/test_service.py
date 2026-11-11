"""Fictional module checks: collaboration, isolation, provenance and attachments."""
import hashlib
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from server.accounting.db import connect,initialize_database,create_user
from server.accounting import families
from server.plans import initialize,validate_database
from server.plans.models import Operation
from server.plans import service

def uid():return str(uuid4())
@pytest.fixture
def world(tmp_path):
    path=tmp_path/'plans.sqlite3';initialize_database(path)
    with connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_records'").fetchone():
            db.execute('BEGIN IMMEDIATE');initialize(db);db.commit()
    # create_user's preflight rejects D additions until shared schema wiring arrives;
    # seed accounts before installing D if necessary using the existing password hash.
    from server.accounting.db import password_hasher
    users=[uid(),uid(),uid()]
    with connect(path) as db:
        for i,user in enumerate(users):
            db.execute('INSERT INTO users(id,username,password_hash,display_name) VALUES(?,?,?,?)',(user,f'fiction-{i}',password_hasher.hash('fiction-only-password'),f'虚构{i}'))
        db.execute('BEGIN IMMEDIATE');family=families.create_explicit_family(db,users[:2]);db.commit()
        family_id=family['id'] if isinstance(family,dict) else families.active_ids(db,users[0])[0]
    return path,users,family_id

def bind(db,user,family=None):
    db.protocol=2;db.source=families.source(db,user,family);db.context_user=user

def operation(db,user,entity,kind,data,identity=None,base=0,op_id=None):
    return service.apply(db,user,Operation(op_id=op_id or uid(),entity_type=entity,entity_id=identity or uid(),kind=kind,base_version=base,data=data))

def content(title='虚构旅行',**extra):return {'title':title,'body_markdown':'# 一起准备','wish_kind':'未来旅行','target_date':None,'attachments':[],**extra}

def chain(db,user):
    wish=operation(db,user,'wish','create',content())['receipt']['record']
    plan=operation(db,user,'plan','from_wish',content('旅行计划',wish_kind='',source_wish_id=wish['id']))['receipt']['record']
    stage=operation(db,user,'stage','create',{'plan_id':plan['id'],'title':'准备','position':0})['receipt']['record']
    task=operation(db,user,'task','create',{'plan_id':plan['id'],'stage_id':stage['id'],'title':'确定日期','body_markdown':'查阅资料','assignee_ids':[user],'due_date':None,'position':0,'attachments':[]})['receipt']['record']
    return wish,plan,stage,task

def test_task_creation_time_is_persisted_and_survives_later_edits(world,monkeypatch):
    path,(a,_,_),fid=world
    created='2090-01-01T08:00:00+00:00';monkeypatch.setattr(service,'now',lambda:created)
    with connect(path) as db:
        bind(db,a,fid);_,plan,stage,task=chain(db,a)
        assert task['created_at']==created
    monkeypatch.setattr(service,'now',lambda:'2090-01-02T08:00:00+00:00')
    with connect(path) as db:
        bind(db,a,fid)
        assert service.sync(db,a)['tasks'][0]['created_at']==created
        data={'plan_id':plan['id'],'stage_id':stage['id'],'title':'稍后修改','body_markdown':'','assignee_ids':[],'due_date':None,'position':0,'attachments':[]}
        changed=operation(db,a,'task','update',data,task['id'],1)['receipt']['record']
        assert changed['created_at']==created

def test_shared_chain_replay_conflict_and_private_isolation(world):
    path,(a,b,c),fid=world
    with connect(path) as db:
        bind(db,a,fid);wish,plan,stage,task=chain(db,a)
        op_id=uid();result=operation(db,a,'task','complete',{'completed':True},task['id'],1,op_id)
        assert operation(db,a,'task','complete',{'completed':True},task['id'],1,op_id)==result
        with pytest.raises(HTTPException) as error:operation(db,a,'task','complete',{'completed':False},task['id'],1,op_id)
        assert error.value.detail['code']=='op_id_reused'
        bind(db,b,fid);snapshot=service.sync(db,b);assert len(snapshot['records'])==2 and snapshot['tasks'][0]['completed']
        with pytest.raises(HTTPException) as error:operation(db,b,'task','complete',{'completed':False},task['id'],1)
        assert error.value.detail['code']=='plan_conflict'
        operation(db,b,'task','complete',{'completed':False},task['id'],2)
        with pytest.raises(HTTPException) as error:operation(db,b,'plan','from_wish',content(wish_kind='',source_wish_id=wish['id']))
        assert error.value.detail['code']=='wish_already_planned'
        bind(db,a);private=operation(db,a,'wish','create',content('私人愿望'))['receipt']['record']
        bind(db,b)
        with pytest.raises(HTTPException):service.require(db,b,'wish',private['id'])
        bind(db,c)
        assert not service.sync(db,c)['records']
        validate_database(db)

def test_creator_delete_request_archive_and_focus(world):
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);wish,plan,stage,task=chain(db,a)
        bind(db,b,fid)
        with pytest.raises(HTTPException) as error:operation(db,b,'task','delete',{},task['id'],1)
        assert error.value.status_code==403
        request=operation(db,b,'deletion_request','create',{'target_type':'task','target_id':task['id'],'target_version':1,'reason':'重复任务'})['receipt']['record']
        bind(db,a,fid);operation(db,a,'deletion_request','decide',{'accepted':True},request['id'],1)
        with pytest.raises(HTTPException):service.task_reference(db,a,task['id'])
        operation(db,a,'plan','archive',{'archived':True},plan['id'],1)
        assert service.sync(db,a)['records'][0]['kind'] in {'wish','plan'}
        operation(db,a,'plan','archive',{'archived':False},plan['id'],2)
        operation(db,a,'plan','delete',{},plan['id'],3)
        replacement=operation(db,a,'plan','from_wish',content('重新规划',wish_kind='',source_wish_id=wish['id']))['receipt']['record']
        assert replacement['id']!=plan['id']
        validate_database(db)

def test_manifest_pending_hash_and_deleted_late_upload(world):
    path,(a,b,_),fid=world;payload=b'fictional document';attachment_id=uid()
    descriptor={'id':attachment_id,'kind':'file','name':'资料.txt','mime_type':'text/plain','size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content(attachments=[descriptor]))['receipt']['record']
        assert wish['attachments'][0]['state']=='pending'
        with pytest.raises(HTTPException) as error:service.put_attachment(db,a,wish['id'],attachment_id,b'bad','text/plain')
        assert error.value.detail['code']=='attachment_mismatch'
        operation(db,a,'wish','archive',{'archived':True},wish['id'],1)
        uploaded=service.put_attachment(db,a,wish['id'],attachment_id,payload,'text/plain')
        assert service.put_attachment(db,a,wish['id'],attachment_id,payload,'text/plain')==uploaded
        bind(db,b,fid);assert service.get_attachment(db,b,wish['id'],attachment_id)['data']==payload
        operation(db,b,'wish','archive',{'archived':False},wish['id'],2)
        operation(db,b,'wish','update',content(attachments=[]),wish['id'],3)
        with pytest.raises(HTTPException):service.put_attachment(db,b,wish['id'],attachment_id,payload,'text/plain')
        assert db.execute('SELECT data FROM plan_attachments WHERE id=?',(attachment_id,)).fetchone()[0]==payload
        validate_database(db)

def test_relation_epoch_rejects_replay(world):
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content())['receipt']['record'];origin=dict(db.source)
        db.execute('UPDATE family_members SET relation_epoch=relation_epoch+1 WHERE user_id=? AND family_id=?',(a,fid))
        db.source=origin
        with pytest.raises(HTTPException) as error:service.require(db,a,'wish',wish['id'])
        assert error.value.detail['code']=='relation_changed'

@pytest.mark.parametrize('changed_person',['requester','author','requester_without_hook'])
def test_pending_deletion_is_stale_after_either_party_rejoins(world,changed_person):
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content())['receipt']['record']
        bind(db,b,fid);request=operation(db,b,'deletion_request','create',{'target_type':'wish','target_id':wish['id'],'target_version':1})['receipt']['record']
        changed=a if changed_person=='author' else b
        if changed_person=='requester_without_hook':
            db.execute('UPDATE family_members SET relation_epoch=relation_epoch+2 WHERE user_id=? AND family_id=?',(changed,fid))
        else:families.remove_membership(db,changed,fid);families.add_members(db,fid,[changed])
        bind(db,a,fid)
        assert service.sync(db,a)['deletion_requests'][0]['status']=='stale'
        with pytest.raises(HTTPException) as error:operation(db,a,'deletion_request','decide',{'accepted':True},request['id'],1)
        assert error.value.detail['code'] in {'deletion_request_stale','plan_conflict'}
        assert service.raw(db,'deletion_request',request['id'])['status']=='stale'
        assert not service.raw(db,'wish',wish['id'])['deleted']
        validate_database(db)

def test_restore_stales_pending_requests_without_rebinding_their_signature(world):
    from server.plans import rebind_restore
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content())['receipt']['record']
        bind(db,b,fid);request=operation(db,b,'deletion_request','create',{'target_type':'wish','target_id':wish['id'],'target_version':1})['receipt']['record']
        before=db.source['restore_id'];replacement=uid();db.context_user=None;db.source=None
        db.execute('UPDATE app_state SET restore_id=?',(replacement,));rebind_restore(db,replacement)
        validate_database(db);bind(db,a,fid)
        stored=service.raw(db,'deletion_request',request['id'])
        assert stored['status']=='stale' and stored['source_restore_id']==before
        assert db.execute('SELECT DISTINCT source_restore_id FROM plan_operations').fetchall()[0][0]==replacement
        with pytest.raises(HTTPException):operation(db,a,'deletion_request','decide',{'accepted':True},request['id'],stored['version'])
        assert not service.raw(db,'wish',wish['id'])['deleted']

def test_deleted_parent_hides_child_receipts_and_own_delete_replay_is_narrow(world):
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);_,plan,stage,task=chain(db,a)
        task_id=uid();create_id=uid();update_id=uid()
        bind(db,b,fid)
        data={'plan_id':plan['id'],'stage_id':stage['id'],'title':'他人任务','body_markdown':'CHILD_SECRET_AFTER_DELETE','assignee_ids':[],'due_date':None,'position':1,'attachments':[]}
        operation(db,b,'task','create',data,task_id,0,create_id)
        operation(db,b,'task','update',dict(data,title='已更新任务'),task_id,1,update_id)
        # Explicit deletion can acknowledge its own target while its ancestors remain readable.
        delete_id=uid();deleted=operation(db,b,'task','delete',{},task_id,2,delete_id)
        assert operation(db,b,'task','delete',{},task_id,2,delete_id)==deleted
        with pytest.raises(HTTPException) as error:service.previous(db,b,create_id)
        assert error.value.status_code==404
        bind(db,a,fid);parent_delete_id=uid();parent_delete=operation(db,a,'plan','delete',{},plan['id'],1,parent_delete_id)
        assert operation(db,a,'plan','delete',{},plan['id'],1,parent_delete_id)==parent_delete
        bind(db,b,fid)
        for old_id in (create_id,update_id,delete_id):
            with pytest.raises(HTTPException) as error:service.previous(db,b,old_id)
            assert error.value.status_code==404
        validate_database(db)

@pytest.mark.parametrize('kind',['file','photo'])
def test_tombstones_preserved_bytes_are_validated(world,kind):
    from io import BytesIO
    from PIL import Image
    path,(a,_,_),fid=world
    if kind=='photo':
        image=BytesIO();Image.new('RGB',(2,2),'green').save(image,format='PNG');payload=image.getvalue();mime='image/png'
    else:payload=b'original-restorable-bytes';mime='application/octet-stream'
    with connect(path) as db:
        bind(db,a,fid);aid=uid();pending_id=uid()
        descriptor={'id':aid,'kind':kind,'name':'fiction.bin','mime_type':mime,'size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        pending=dict(descriptor,id=pending_id)
        wish=operation(db,a,'wish','create',content(attachments=[descriptor,pending]))['receipt']['record']
        validate_database(db)  # pending+NULL is valid
        service.put_attachment(db,a,wish['id'],aid,payload,mime)
        operation(db,a,'wish','update',content(attachments=[]),wish['id'],1)
        validate_database(db)  # both uploaded+retained and never-uploaded+NULL tombstones are valid
        db.execute('UPDATE plan_attachments SET data=? WHERE id=?',(b'corrupt',aid))
        with pytest.raises(ValueError,match='invalid_plans_database'):validate_database(db)
        db.execute('UPDATE plan_attachments SET data=? WHERE id=?',(payload,aid));validate_database(db)

def test_departed_assignee_can_be_preserved_but_never_newly_assigned(world):
    path,(a,b,_),fid=world
    with connect(path) as db:
        bind(db,a,fid);_,plan,stage,task=chain(db,a)
        data={'plan_id':plan['id'],'stage_id':stage['id'],'title':'分配任务','body_markdown':'','assignee_ids':[b],'due_date':None,'position':0,'attachments':[]}
        operation(db,a,'task','update',data,task['id'],1)
        families.remove_membership(db,b,fid)
        operation(db,a,'task','update',dict(data,title='仅改正文'),task['id'],2)
        snapshot=service.sync(db,a)
        assert next(u for u in snapshot['users'] if u['id']==b)['assignable'] is False
        with pytest.raises(HTTPException) as error:operation(db,a,'task','create',data)
        assert error.value.detail['code']=='assignee_unavailable'
        operation(db,a,'task','update',dict(data,assignee_ids=[]),task['id'],3)
        with pytest.raises(HTTPException):operation(db,a,'task','update',data,task['id'],4)
        validate_database(db)
