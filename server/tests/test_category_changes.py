"""Local fictional category proposals: privacy, fixed sets, atomicity and replay."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.accounting import ledger
from server.accounting.db import connect, initialize_database
from server.automation import reports
from server.automation.backups import backup, restore
from server.api.app import create_app
from server.tests.test_api import setup, operation, submit, update  # noqa: F401
from server.tests.test_families import group, create, invite, join, leave, refresh  # noqa: F401
from server.tests.test_maintenance import action, propose, decide as single_decide
from server.tests.test_refinement import image_bytes, image_url, upload


def preview(c,kind='delete',scope='shared',**fields):
    body={'kind':kind,'scope':scope,**fields}
    if kind in {'rename','delete'}:
        body.setdefault('source_name','餐饮')
    response=c.post('/api/category-changes/preview',json=body)
    assert response.status_code==200,response.text
    return response.json()


def send(c,p,body=None):
    body=body or {'id':str(uuid4()),'preview_id':p['preview_id']}
    response=c.post('/api/category-changes',json=body)
    assert response.status_code==200,response.text
    return response.json()['change'],body


def decision(c,change,value='accept',did=None):
    return c.post(f"/api/category-changes/{change['id']}/decision",json={'decision_id':did or str(uuid4()),'decision':value})


def current(c,e):
    return c.get('/api/expenses/'+e['id']).json()


@pytest.fixture
def trio(group):
    path,app,clients=group
    a,b,c,d,e=clients
    fid=create(a)
    join(b,invite(a,'person-1'))
    join(c,invite(a,'person-2'))
    other=create(d)
    join(e,invite(d,'person-4'))
    return path,app,clients,fid,other


def test_delete_builtin_own_scope_isolated_persistent_and_explicit_restore(setup):
    path,app,a,b,*_=setup
    shared=submit(a,operation(category='餐饮'))
    private=submit(a,operation('private',category='餐饮'))
    old_create=operation(category='餐饮')
    change,body=send(a,preview(a))
    assert change['status']=='applied' and change['own_count']==1 and not change['approvals']
    changed=current(a,shared)
    assert changed['version']==2 and changed['category']==''
    assert current(a,private)==private
    assert a.post('/api/category-changes',json=body).json()['change']==change
    assert a.post('/api/operations',json=old_create).json()['detail']['code']=='category_deleted'
    assert a.post('/api/categories',json={'scope':'shared','name':'餐饮'}).json()['detail']['code']=='category_deleted'
    initialize_database(path)
    assert {'scope':'shared','name':'餐饮'} not in refresh(b)['categories']
    # A successful historical ledger receipt stays byte-semantically original.
    with connect(path) as db:
        original=json.loads(db.execute('SELECT receipt_json FROM operations WHERE expense_id=? ORDER BY rowid LIMIT 1',(shared['id'],)).fetchone()[0])
    assert original['expense']['category']=='餐饮'
    assert a.post('/api/categories',json={'scope':'shared','name':'餐饮','restore_deleted':True}).status_code==200
    # Explicit restoration re-enables the same string label; old legal creates
    # can use it, while any stale update still fails its parent-version check.
    assert a.post('/api/operations',json=old_create).status_code==200
    old_edit=update(shared);old_edit['data']['category']='餐饮'
    assert a.post('/api/operations',json=old_edit).json()['detail']['code']=='version_conflict'


def test_multi_recorder_one_approval_each_and_unanimous_atomic_history_reports(trio):
    path,_,(a,b,c,d,_),fid,_=trio
    expenses=[submit(client,operation(category='餐饮')) for client in (a,b,b,c)]
    outside=submit(d,operation(category='餐饮'))
    p=preview(a)
    assert p['total_count']==4 and p['own_count']==1
    assert sorted(o['count'] for o in p['other_owners'])==[1,2]
    change,body=send(a,p)
    assert change['status']=='pending' and len(change['approvals'])==2
    assert len(refresh(b)['category_changes'][0]['items'])==2
    assert len(refresh(c)['category_changes'][0]['items'])==1
    assert refresh(d)['category_changes']==[]
    with connect(path) as db:
        snap=reports.snapshot(db,'shared',fid,'2026-10')
        db.execute("INSERT INTO reports(id,scope,owner_id,month,status,data_version,content_json) VALUES(?,'shared',?,'2026-10','ready',?,'{}')",(str(uuid4()),fid,reports.version(snap)))
    did=str(uuid4())
    first=decision(b,change,did=did)
    assert first.status_code==200 and first.json()['change']['status']=='pending'
    assert all(current(a,e)==e for e in expenses)
    last=decision(c,change)
    assert last.status_code==200 and last.json()['change']['status']=='applied'
    assert decision(b,change,did=did).json()==first.json()
    for client,expense in zip((a,b,b,c),expenses):
        changed=current(client,expense)
        assert changed==dict(expense,category='',version=2)
        history=client.get(f"/api/expenses/{expense['id']}/history").json()['revisions']
        assert len(history)==2 and history[-1]['after']==changed
    assert current(d,outside)==outside
    with connect(path) as db:
        assert db.execute('SELECT status,content_json FROM reports WHERE owner_id=?',(fid,)).fetchone()[:]==('stale',None)
        assert db.execute('SELECT count(*) FROM report_tasks').fetchone()[0]==1
    assert a.post('/api/category-changes',json=body).json()['change']['status']=='applied'


@pytest.mark.parametrize('kind,target,merge',[('delete','',False),('rename','新餐饮',False),('reclassify','交通',False),('rename','交通',True)])
def test_rejection_cancels_all_originals_for_every_kind(setup,kind,target,merge):
    _,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='餐饮'))
    fields={'target_name':target,'merge':merge}
    if kind=='reclassify':fields['expense_ids']=[own['id'],other['id']]
    change,_=send(a,preview(a,kind,**fields))
    assert decision(b,change,'reject').json()['change']['status']=='rejected'
    assert current(a,own)==own and current(a,other)==other
    assert {'scope':'shared','name':'餐饮'} in refresh(a)['categories']
    assert decision(b,change).status_code==409


@pytest.mark.parametrize('merge',[False,True])
@pytest.mark.parametrize('mutation',['update','private','void'])
def test_record_mutation_stales_entire_batch_without_extra_writes(setup,mutation,merge):
    path,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='餐饮'))
    change,_=send(a,preview(a,'rename',target_name='交通',merge=True) if merge else preview(a))
    changed=submit(b,update(other,purpose='修改后的虚构用途') if mutation=='update' else action(other,'scope',scope='private') if mutation=='private' else action(other,'void'))
    response=decision(b,change)
    assert response.status_code in {404,409}
    assert current(a,own)==own and current(b,other)==changed
    with connect(path) as db:
        assert db.execute('SELECT status FROM category_changes WHERE id=?',(change['id'],)).fetchone()[0]=='stale'
        assert db.execute('SELECT count(*) FROM revisions WHERE expense_id=?',(own['id'],)).fetchone()[0]==1
    if mutation!='update':
        assert refresh(a)['category_changes']==[]


@pytest.mark.parametrize('kind,merge',[('rename',False),('delete',False),('rename',True)])
def test_new_record_after_preview_stales_no_silent_expansion(setup,kind,merge):
    _,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='餐饮'))
    p=preview(a,kind,target_name=('交通' if merge else '改名后') if kind=='rename' else '',merge=merge)
    change,_=send(a,p)
    newest=submit(b,operation(category='餐饮'))
    result=decision(b,change)
    assert result.status_code==409 and result.json()['detail']['code']=='category_change_stale'
    assert all(current(a,e)==e for e in (own,other,newest))
    assert {'scope':'shared','name':'餐饮'} in refresh(a)['categories']


def test_preview_submit_stale_and_replay_ids(setup):
    _,_,a,b,*_=setup
    other=submit(b,operation(category='餐饮'))
    p=preview(a)
    edited=update(other,purpose='版本前进');edited['data']['category']='餐饮'
    submit(b,edited)
    assert a.post('/api/category-changes',json={'id':str(uuid4()),'preview_id':p['preview_id']}).json()['detail']['code']=='category_change_stale'
    change,body=send(a,preview(a))
    assert a.post('/api/category-changes',json=body).json()['change']==change
    assert a.post('/api/category-changes',json=dict(body,reason='另一个载荷')).json()['detail']['code']=='request_id_reused'
    assert a.post('/api/category-changes',json={'id':str(uuid4()),'preview_id':body['preview_id']}).status_code==409
    did=str(uuid4())
    response=decision(b,change,did=did)
    assert response.status_code==200
    assert decision(b,change,did=did).json()==response.json()
    assert decision(b,change,'reject',did).json()['detail']['code']=='decision_id_reused'


def test_batch_reclassify_exact_set_noop_filter_and_category_only_schema(setup):
    _,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='交通'))
    excluded=submit(b,operation(category='餐饮'))
    p=preview(a,'reclassify',target_name='交通',expense_ids=[own['id'],other['id']])
    assert p['total_count']==1 and p['own_count']==1
    change,_=send(a,p)
    assert change['status']=='applied' and current(a,own)['category']=='交通'
    assert current(a,other)==other and current(a,excluded)==excluded
    response=a.post('/api/category-changes/preview',json={'kind':'reclassify','scope':'shared','target_name':'交通','expense_ids':[other['id']]})
    assert response.json()['detail']['code']=='category_no_changes'
    for extra in ({'amount_cents':1},{'owner_id':other['owner_id']},{'version':8}):
        assert a.post('/api/category-changes/preview',json={'kind':'reclassify','scope':'shared','expense_ids':[excluded['id']],**extra}).status_code==422


@pytest.mark.parametrize('merge',[False,True])
def test_rename_custom_and_private_catalogs_are_independent(setup,merge):
    _,_,a,b,*_=setup
    for scope in ('private','shared'):
        assert a.post('/api/categories',json={'scope':scope,'name':'自定义'}).status_code==200
    private=submit(a,operation('private',category='自定义'))
    shared=submit(a,operation(category='自定义'))
    target='交通' if merge else '新名称'
    change,_=send(a,preview(a,'rename','private',source_name='自定义',target_name=target,merge=merge))
    assert change['status']=='applied' and current(a,private)==dict(private,category=target,version=2)
    assert current(a,shared)==shared and refresh(b)['category_changes']==[]
    assert a.post('/api/categories',json={'scope':'private','name':'自定义'}).json()['detail']['code']=='category_deleted'
    assert a.post('/api/categories',json={'scope':'shared','name':'自定义'}).status_code==200
    assert a.post('/api/category-changes/preview',json={'kind':'rename','scope':'shared','source_name':'自定义','target_name':'交通'}).json()['detail']['code']=='category_exists'
    empty,_=send(a,preview(a,'delete','private',source_name='其他'))
    assert empty['total_count']==0 and empty['status']=='applied'


@pytest.mark.parametrize('merge',[False,True])
def test_concurrent_final_approval_has_one_atomic_effect(setup,merge):
    path,_,a,b,*_=setup
    original=submit(b,operation(category='餐饮'))
    change,_=send(a,preview(a,'rename',target_name='交通',merge=True) if merge else preview(a))
    did=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:decision(b,change,did=did),range(2)))
    assert [r.status_code for r in responses]==[200,200]
    assert responses[0].json()==responses[1].json()
    assert current(b,original)['version']==2
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM revisions WHERE expense_id=?',(original['id'],)).fetchone()[0]==2


@pytest.mark.parametrize('merge',[False,True])
def test_failure_after_first_mutation_rolls_back_approvals_catalog_history(setup,monkeypatch,merge):
    path,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='餐饮'))
    target=submit(a,operation(category='交通'))
    change,_=send(a,preview(a,'rename',target_name='交通',merge=True) if merge else preview(a))
    original=ledger.store_revision
    def fail(db,*args):
        original(db,*args)
        raise sqlite3.OperationalError('fictional failure inside category transaction')
    with monkeypatch.context() as patch:
        patch.setattr(ledger,'store_revision',fail)
        assert decision(b,change).status_code==500
    assert current(a,own)==own and current(a,other)==other
    assert current(a,target)==target
    assert {'scope':'shared','name':'餐饮'} in refresh(a)['categories']
    with connect(path) as db:
        assert db.execute('SELECT status FROM category_approvals WHERE change_id=?',(change['id'],)).fetchone()[0]=='pending'
        assert db.execute('SELECT count(*) FROM category_decisions').fetchone()[0]==0
    assert decision(b,change).json()['change']['status']=='applied'


@pytest.mark.parametrize('merge',[False,True])
def test_departure_rejoin_cross_family_private_and_unknown_owner_never_leak(trio,merge):
    path,_,(a,b,c,d,_),fid,_=trio
    other=submit(b,operation(category='餐饮'))
    private=submit(b,operation('private',category='私人秘密'))
    change,body=send(a,preview(a,'rename',target_name='交通',merge=True) if merge else preview(a))
    for outsider in (d,):
        assert outsider.post('/api/category-changes',json=body).status_code==404
        assert decision(outsider,change).status_code==404
        assert outsider.post('/api/category-changes/preview',json={'kind':'reclassify','scope':'shared','expense_ids':[other['id']]}).status_code==404
    assert a.post('/api/category-changes/preview',json={'kind':'reclassify','scope':'private','expense_ids':[private['id']]}).status_code==404
    leave(b,fid)
    assert refresh(a)['category_changes']==[] and refresh(b)['category_changes']==[]
    assert decision(b,change).status_code==404
    assert a.post('/api/category-changes/preview',json={'kind':'delete','scope':'shared','source_name':'餐饮'}).json()['detail']['code']=='category_owner_unavailable'
    join(b,invite(a,'person-1'))
    assert refresh(a)['category_changes']==[] and decision(b,change).status_code==404
    with connect(path) as db:
        assert db.execute('SELECT status FROM category_changes WHERE id=?',(change['id'],)).fetchone()[0]=='stale'


@pytest.mark.parametrize('merge',[False,True])
def test_real_backup_restore_retains_tombstones_but_withdraws_old_context(setup,tmp_path,merge):
    path,_,a,b,*_=setup
    own=submit(a,operation('private',category='自定义'))
    send(a,preview(a,'delete','private',source_name='自定义'))
    original=submit(b,operation(category='餐饮'))
    pending,body=send(a,preview(a,'rename',target_name='交通',merge=True) if merge else preview(a))
    directory=backup(path,tmp_path/'backup')
    restored=tmp_path/'restored.sqlite3'
    result=restore(directory,restored,directory/'credentials.json')
    with TestClient(create_app(restored)) as client:
        assert client.post('/api/auth/login',json={'username':'alice','password':'fictional-a'},headers={'X-App-Request':'shared-expense'}).status_code==200
        client.headers.update(a.headers)
        assert client.post('/api/category-changes',json=body).json()['detail']['code']=='restore_changed'
        client.headers['X-Restore-Id']=result['restore_id']
        assert client.get('/api/category-changes').json()['changes']==[]
        assert client.post('/api/category-changes',json=body).status_code==404
        assert client.post('/api/categories',json={'scope':'private','name':'自定义'}).json()['detail']['code']=='category_deleted'
        assert current(client,original)==original
        assert current(client,own)['category']==''


def test_existing_single_request_approval_and_old_receipts_remain_compatible(setup):
    _,_,a,b,*_=setup
    original_body=operation(category='餐饮')
    original=submit(b,original_body)
    request,_=propose(a,original)
    batch,_=send(a,preview(a))
    assert decision(b,batch).json()['change']['status']=='applied'
    assert single_decide(b,request).json()['detail']['code']=='request_stale'
    assert b.post('/api/operations',json=original_body).json()['expense']==original


@pytest.mark.parametrize('kind,target',[('rename','改名分类'),('reclassify','交通')])
def test_shared_rename_and_reclassify_acceptance_change_only_category(setup,kind,target):
    _,_,a,b,*_=setup
    own=submit(a,operation(category='餐饮',purpose='保留用途',location='保留地点',rating=5,rating_reason='保留满意度'))
    other=submit(b,operation(category='餐饮',amount_cents=8765))
    fields={'target_name':target}
    if kind=='reclassify':fields['expense_ids']=[own['id'],other['id']]
    change,_=send(a,preview(a,kind,**fields))
    assert current(a,own)==own and current(a,other)==other
    assert decision(b,change).json()['change']['status']=='applied'
    assert current(a,own)==dict(own,category=target,version=2)
    assert current(a,other)==dict(other,category=target,version=2)
    names=refresh(a)['categories']
    assert ({'scope':'shared','name':'餐饮'} in names)==(kind=='reclassify')
    assert {'scope':'shared','name':target} in names


def test_unsent_old_update_cannot_revive_deleted_label(setup):
    _,_,a,b,*_=setup
    source=submit(a,operation(category='餐饮'))
    unrelated=submit(a,operation(category='交通'))
    old_update=update(unrelated);old_update['data']['category']='餐饮'
    send(a,preview(a))
    response=a.post('/api/operations',json=old_update)
    assert response.status_code==409 and response.json()['detail']['code']=='category_deleted'
    assert current(a,unrelated)==unrelated
    assert {'scope':'shared','name':'餐饮'} not in refresh(b)['categories']
    assert current(a,source)['category']==''


def test_different_member_versions_do_not_use_recorder_as_request_actor(trio):
    _,_,(a,b,c,*_),fid,_=trio
    leave(b,fid)
    join(b,invite(a,'person-1'))
    assert b.headers['X-Membership-Version'] != c.headers['X-Membership-Version']
    own=submit(a,operation(category='餐饮'))
    other=submit(b,operation(category='餐饮'))
    third=submit(c,operation(category='餐饮'))
    change,_=send(a,preview(a,'rename',target_name='新类别'))
    assert decision(b,change).json()['change']['status']=='pending'
    assert decision(c,change).json()['change']['status']=='applied'
    assert all(current(a,e)==dict(e,category='新类别',version=2) for e in (own,other,third))


def test_merge_unanimous_source_only_preserves_target_images_and_receipts(trio):
    path,_,(a,b,c,d,_),_,_=trio
    originals=[submit(client,operation(category='电器',amount_cents=8765)) for client in (a,b,b,c)]
    target=submit(b,operation(category='日用百货',amount_cents=4321))
    private=submit(a,operation('private',category='电器'))
    outside=submit(d,operation(category='电器'))
    withdrawn=submit(a,operation(category='电器'))
    withdrawn=submit(a,action(withdrawn,'void'))
    url=image_url(originals[1]['id'])
    meta=upload(b,url).json()
    p=preview(a,'rename',source_name='电器',target_name='日用百货',merge=True)
    assert p['merge'] is True and p['total_count']==4 and p['own_count']==1
    assert sorted(o['count'] for o in p['other_owners'])==[1,2]
    change,body=send(a,p)
    assert change['merge'] is True and change['status']=='pending' and len(change['approvals'])==2
    assert a.post('/api/category-changes',json=body).json()['change']==change
    assert len(refresh(b)['category_changes'][0]['items'])==2
    first_id,last_id=str(uuid4()),str(uuid4())
    first=decision(b,change,did=first_id)
    assert first.json()['change']['status']=='pending'
    assert all(current(a,e)==e for e in originals)
    # Existing target records can change during approval without expanding the batch.
    target=submit(b,update(target,purpose='目标记录的独立修改'))
    last=decision(c,change,did=last_id)
    assert last.json()['change']['status']=='applied' and last.json()['change']['merge'] is True
    assert decision(b,change,did=first_id).json()==first.json()
    assert decision(c,change,did=last_id).json()==last.json()
    assert a.post('/api/category-changes',json=body).json()['change']['status']=='applied'
    assert all(current(a,e)==dict(e,category='日用百货',version=2) for e in originals)
    assert current(b,target)==target and current(a,private)==private
    assert current(d,outside)==outside and current(a,withdrawn)==withdrawn
    assert a.get(url).content==image_bytes() and meta in refresh(b)['images']
    assert {'scope':'shared','name':'电器'} not in refresh(a)['categories']
    assert {'scope':'shared','name':'日用百货'} in refresh(a)['categories']
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM revisions WHERE expense_id=?',(target['id'],)).fetchone()[0]==2
        assert all(db.execute('SELECT count(*) FROM revisions WHERE expense_id=?',(e['id'],)).fetchone()[0]==2 for e in originals)


def test_merge_target_deletion_stales_whole_batch(setup):
    _,_,a,b,*_=setup
    own=submit(a,operation(category='电器'))
    other=submit(b,operation(category='电器'))
    change,_=send(a,preview(a,'rename',source_name='电器',target_name='交通',merge=True))
    send(a,preview(a,'delete',source_name='交通'))
    result=decision(b,change)
    assert result.status_code==409 and result.json()['detail']['code']=='category_change_stale'
    assert current(a,own)==own and current(a,other)==other
    assert {'scope':'shared','name':'电器'} in refresh(a)['categories']


@pytest.mark.parametrize('scope',['shared','private'])
def test_merge_empty_catalog_and_private_scope(setup,scope):
    _,_,a,b,*_=setup
    assert a.post('/api/categories',json={'scope':scope,'name':'电器'}).status_code==200
    private_other=submit(b,operation('private',category='电器'))
    change,_=send(a,preview(a,'rename',scope,source_name='电器',target_name='交通',merge=True))
    assert change['merge'] is True and change['status']=='applied' and change['total_count']==0
    assert {'scope':scope,'name':'电器'} not in refresh(a)['categories']
    assert current(b,private_other)==private_other
    assert refresh(b)['category_changes']==[]


def test_merge_validation_and_ordinary_rename_compatibility(setup):
    _,_,a,b,*_=setup
    base={'kind':'rename','scope':'shared','source_name':'餐饮','target_name':'交通'}
    assert a.post('/api/category-changes/preview',json=base).json()['detail']['code']=='category_exists'
    for extra,code in [({'target_name':'不存在'},'category_not_found'),({'source_name':'不存在'},'category_not_found')]:
        response=a.post('/api/category-changes/preview',json={**base,'merge':True,**extra})
        assert response.status_code==409 and response.json()['detail']['code']==code
    for extra in ({'target_name':'餐饮'},{'kind':'delete','target_name':''},{'kind':'reclassify','source_name':None,'expense_ids':[str(uuid4())]}):
        assert a.post('/api/category-changes/preview',json={**base,'merge':True,**extra}).status_code==422
    send(a,preview(a,'delete',source_name='交通'))
    for merge in (False,True):
        response=a.post('/api/category-changes/preview',json={**base,'merge':merge})
        assert response.status_code==409 and response.json()['detail']['code']=='category_deleted'
    p=preview(a,'rename',target_name='新餐饮')
    assert p['merge'] is False
    change,_=send(a,p)
    assert change['merge'] is False and change['status']=='applied'
