"""Owner authorization, atomic revocation and evidence-based legacy upgrades."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.accounting.db import connect, initialize_database, migrate_database
from server.accounting import families
from server.tests.legacy_fixture import to_v1, old_backup, copy_legacy
from server.automation.backups import backup, restore, validate_database
from server.automation import reports
from server.automation.worker import Worker
from server.tests.test_api import operation, submit
from server.tests.test_families import group, create, invite, join, leave, refresh, transfer
from server.tests.test_maintenance import propose
from server.tests.test_refinement import image_url, upload


def endpoint(fid, action, target=None):
    return f'/api/families/{fid}/members/{target}/remove' if action=='remove' else f'/api/families/{fid}/{action}'


def test_transfer_and_leave_revokes_owner_in_one_request(group):
    path,_,(a,b,c,*_)=group
    fid=create(a);join(b,invite(a,'person-1'))
    shared=submit(a,operation());private=submit(a,operation('private'))
    pending=invite(a,'person-2')
    before=refresh(a)['membership_version'];other_before=refresh(b)['membership_version']
    bid=b.headers['X-Expected-User']
    result=a.post(endpoint(fid,'transfer-and-leave'),json={'user_id':bid})
    assert result.status_code==200
    assert result.json()=={'family':None,'membership_version':before+1}
    remaining=refresh(b)
    assert remaining['family']['owner_id']==bid
    assert [m['id'] for m in remaining['family']['members']]==[bid]
    assert remaining['membership_version']==other_before
    assert b.get('/api/expenses/'+shared['id']).status_code==200
    refresh(a)
    assert a.get('/api/expenses/'+shared['id']).status_code==404
    assert a.get('/api/expenses/'+private['id']).status_code==200
    assert c.post(f'/api/family-invites/{pending}/accept',json={}).status_code==409
    # A lost success response cannot transfer ownership again on retry.
    assert a.post(endpoint(fid,'transfer-and-leave'),json={'user_id':bid}).status_code==404
    assert refresh(b)['family']['owner_id']==bid
    with connect(path) as db:
        validate_database(db)


def test_transfer_and_leave_rejects_invalid_target_without_changing_family(group):
    _,_,(a,b,c,d,*_)=group
    fid=create(a);join(b,invite(a,'person-1'));join(d,invite(a,'person-3'));leave(d,fid)
    original=refresh(a)
    aid,bid,cid,did=[x.headers['X-Expected-User'] for x in (a,b,c,d)]
    assert b.post(endpoint(fid,'transfer-and-leave'),json={'user_id':aid}).status_code==403
    for target,code in [(aid,409),(cid,404),(did,404)]:
        assert a.post(endpoint(fid,'transfer-and-leave'),json={'user_id':target}).status_code==code
        assert refresh(a)['family']==original['family']
        assert refresh(a)['membership_version']==original['membership_version']


def test_transfer_and_leave_rolls_back_both_changes_on_failure(group,monkeypatch):
    _,_,(a,b,*_)=group
    fid=create(a);join(b,invite(a,'person-1'))
    original=refresh(a)
    remove=families.remove_membership
    def fail_after_removal(db,user_id,family_id):
        remove(db,user_id,family_id)
        raise sqlite3.OperationalError('fictional failure before commit')
    monkeypatch.setattr(families,'remove_membership',fail_after_removal)
    result=a.post(endpoint(fid,'transfer-and-leave'),json={'user_id':b.headers['X-Expected-User']})
    assert result.status_code==500
    assert refresh(a)['family']==original['family']
    assert refresh(a)['membership_version']==original['membership_version']


def test_owner_permissions_transfer_leave_and_remove(group):
    path,_,(a,b,c,d,e)=group
    fa=create(a);join(b,invite(a,'person-1'));join(e,invite(b,'person-4'))
    fb=create(c);join(d,invite(c,'person-3'))
    aid,bid,cid=(x.headers['X-Expected-User'] for x in (a,b,c))
    assert refresh(a)['family']['owner_id']==aid
    versions={x.headers['X-Expected-User']:refresh(x)['membership_version'] for x in (a,b,e)}
    for actor in (b,e):
        for action,body in [('remove',{}),('owner',{'user_id':bid}),('dissolve',{})]:
            result=actor.post(endpoint(fa,action,bid),json=body)
            assert result.status_code==403 and result.json()['detail']['code']=='family_owner_required'
    for action,body in [('remove',{}),('owner',{'user_id':bid}),('dissolve',{})]:
        assert c.post(endpoint(fa,action,bid),json=body).status_code==404
    assert a.post(endpoint(fa,'remove',aid),json={}).json()['detail']['code']=='use_leave'
    assert a.post(endpoint(fa,'owner'),json={'user_id':aid}).status_code==409
    assert a.post(endpoint(fa,'owner'),json={'user_id':cid}).status_code==404
    assert a.post(endpoint(fa,'remove',cid),json={}).status_code==404
    result=a.post(endpoint(fa,'leave'),json={})
    assert result.status_code==409 and result.json()['detail']['code']=='owner_transfer_or_dissolve_required'
    transfer(a,fa,b)
    for actor in (a,b,e):
        data=refresh(actor)
        assert data['family']['owner_id']==bid and data['membership_version']==versions[actor.headers['X-Expected-User']]
    assert a.post(endpoint(fa,'dissolve'),json={}).status_code==403
    assert a.post(endpoint(fa,'owner'),json={'user_id':aid}).status_code==403
    leave(a,fa)
    assert len(refresh(b)['family']['members'])==2
    assert refresh(c)['family']['id']==fb
    assert b.post(endpoint(fa,'remove',e.headers['X-Expected-User']),json={}).status_code==200
    assert refresh(e)['family'] is None
    leave(b,fa)
    with connect(path) as db:
        assert db.execute('SELECT archived FROM families WHERE id=?',(fa,)).fetchone()[0]==1
        validate_database(db)


def test_remove_revokes_own_shared_history_and_keeps_private(group):
    path,_,(a,b,c,*_)=group
    fid=create(a);join(b,invite(a,'person-1'))
    body=operation(purpose='被移出者共同');shared=submit(b,body)
    private=submit(b,operation('private',purpose='私人保留'))
    url=image_url(shared['id']);assert upload(b,url).status_code==200
    request,_=propose(a,shared)
    pending=invite(b,'person-2');old=dict(b.headers)
    before=refresh(b)['membership_version']
    result=a.post(endpoint(fid,'remove',b.headers['X-Expected-User']),json={})
    assert result.status_code==200 and result.json()['family']['id']==fid
    data=refresh(b)
    assert data['family'] is None and data['membership_version']==before+1
    assert [e['id'] for e in data['expenses']]==[private['id']]
    for route in ('/api/expenses/'+shared['id'],url,'/api/expenses/'+shared['id']+'/history'):
        assert b.get(route).status_code==404
    assert b.post('/api/operations',json=body).status_code==404
    assert b.post('/api/operations',json=operation(),headers=old).json()['detail']['code']=='family_changed'
    assert c.post(f'/api/family-invites/{pending}/accept',json={}).status_code==409
    assert a.get('/api/expenses/'+shared['id']).status_code==200
    assert '被移出者共同' not in b.get('/api/export').text
    submit(b,operation('private'))
    with connect(path) as db:
        row=db.execute('SELECT status,withdrawn FROM change_requests WHERE id=?',(request['id'],)).fetchone()
        assert tuple(row)==('stale',1)
    join(b,invite(a,'person-1'))
    assert b.post('/api/operations',json=body).json()['detail']['code']=='family_changed'


def test_dissolve_all_members_cancels_only_its_work_and_preserves_records(group):
    path,_,(a,b,c,d,e)=group
    fa=create(a);join(b,invite(a,'person-1'));fb=create(c);join(d,invite(c,'person-3'))
    shared_body=operation();shared=submit(b,shared_body)
    private=submit(b,operation('private'));other=submit(c,operation())
    url=image_url(shared['id']);assert upload(b,url).status_code==200
    request,_=propose(a,shared)
    pending=invite(b,'person-4');other_pending=invite(c,'person-4')
    before={x.headers['X-Expected-User']:refresh(x)['membership_version'] for x in (a,b,c,d)}
    report_ids=[]
    with connect(path) as db:
        for scope,owner,uid in [('shared',fa,b.headers['X-Expected-User']),('shared',fb,c.headers['X-Expected-User']),('private',b.headers['X-Expected-User'],b.headers['X-Expected-User'])]:
            rid=str(uuid4());report_ids.append(rid)
            db.execute("INSERT INTO reports(id,scope,owner_id,month,status,data_version) VALUES(?,?,?,'2026-10','running','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')",(rid,scope,owner))
            reports.queue(db,rid,'a'*64)
            reports.enqueue_notification(db,uid,'report:'+rid,0,rid,'a'*64)
            db.execute("INSERT INTO monthly_settings(scope,namespace,enabled,start) VALUES(?,?,1,'2026-10')",(scope,owner))
        original=[tuple(r) for r in db.execute('SELECT * FROM expenses ORDER BY id')]
    result=a.post(endpoint(fa,'dissolve'),json={})
    assert result.status_code==200 and result.json()['family'] is None
    for actor in (a,b):
        data=refresh(actor)
        assert data['family'] is None and data['membership_version']==before[actor.headers['X-Expected-User']]+1
        assert actor.get('/api/expenses/'+shared['id']).status_code==404
        assert actor.get(url).status_code==404
        assert actor.get('/api/reports/'+report_ids[0]).status_code==404
        assert actor.post('/api/operations',json=shared_body).status_code==404
    assert b.get('/api/expenses/'+private['id']).status_code==200
    assert c.get('/api/expenses/'+other['id']).status_code==200
    for actor in (c,d):
        data=refresh(actor)
        assert data['family']['id']==fb and data['membership_version']==before[actor.headers['X-Expected-User']]
    assert e.post(f'/api/family-invites/{pending}/accept',json={}).status_code==409
    assert next(i for i in e.get('/api/family-invites').json()['received'] if i['id']==other_pending)['status']=='pending'
    with connect(path) as db:
        assert original==[tuple(r) for r in db.execute('SELECT * FROM expenses ORDER BY id')]
        assert db.execute('SELECT count(*) FROM family_members WHERE family_id=? AND active=1',(fa,)).fetchone()[0]==0
        assert db.execute('SELECT status,withdrawn FROM change_requests WHERE id=?',(request['id'],)).fetchone()[:]==('stale',1)
        assert {r['report_id'] for r in db.execute('SELECT report_id FROM report_tasks')}==set(report_ids[1:])
        assert db.execute('SELECT status FROM notification_outbox WHERE report_id=?',(report_ids[0],)).fetchone()[0]=='cancelled'
        assert {r['status'] for r in db.execute('SELECT status FROM notification_outbox WHERE report_id!=?',(report_ids[0],))}=={'pending'}
        assert db.execute("SELECT enabled FROM monthly_settings WHERE namespace=?",(fa,)).fetchone()[0]==0
        validate_database(db)


def test_concurrent_transfer_and_remove_cannot_orphan_owner(group):
    path,app,(a,b,*_)=group
    fid=create(a);join(b,invite(a,'person-1'));bid=b.headers['X-Expected-User']
    def perform(action):
        with TestClient(app) as peer:
            peer.cookies.update(a.cookies);peer.headers.update(a.headers)
            return peer.post(endpoint(fid,action,bid),json={'user_id':bid} if action=='owner' else {}).status_code
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(perform,['owner','remove']))
    assert sorted(result) in ([200,403],[200,404])
    with connect(path) as db:
        validate_database(db)
        owner=db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]
        assert db.execute('SELECT 1 FROM family_members WHERE family_id=? AND active=1 AND user_id=?',(fid,owner)).fetchone()


def test_concurrent_accept_and_dissolve_cannot_revive_family(group):
    path,app,(a,b,*_)=group
    fid=create(a);iid=invite(a,'person-1')
    def perform(index):
        actor=(a,b)[index]
        with TestClient(app) as peer:
            peer.cookies.update(actor.cookies);peer.headers.update(actor.headers)
            return peer.post(endpoint(fid,'dissolve') if index==0 else f'/api/family-invites/{iid}/accept',json={}).status_code
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(perform,[0,1]))
    assert result[0]==200 and result[1] in (200,409)
    assert refresh(a)['family'] is None and refresh(b)['family'] is None
    with connect(path) as db:
        assert not db.execute('SELECT 1 FROM family_members WHERE family_id=? AND active=1',(fid,)).fetchone()
        validate_database(db)


def test_dissolve_during_model_call_does_not_restore_task_or_send_notification(group):
    path,_,(a,b,*_)=group
    fid=create(a);join(b,invite(a,'person-1'))
    submit(a,operation(occurred_at='2026-09-10T00:00:00Z'))
    assert a.put('/api/settings?scope=shared',json={'monthly':{'enabled':True,'time':'20:00'}}).status_code==200
    with connect(path) as db:
        db.execute("UPDATE monthly_settings SET start='2026-09'")
    class DissolvingModel:
        name='mock';model='mock'
        def call(self,payload):
            assert a.post(endpoint(fid,'dissolve'),json={}).status_code==200
            return {'items':[{'expense_id':e['id'],'analysis':'mock'} for e in payload['expenses']]}
    worker=Worker(path,primary=DissolvingModel(),clock=lambda:datetime(2026,10,4,13,tzinfo=UTC),call_limit=10)
    worker.tick();worker.tick()
    with connect(path) as db:
        assert not db.execute('SELECT 1 FROM report_tasks').fetchone()
        assert not db.execute('SELECT 1 FROM notification_outbox').fetchone()
        assert refresh(b)['family'] is None


def old_owner_schema(path):
    to_v1(path)
    with connect(path) as db:
        db.execute("DELETE FROM schema_migrations WHERE name='family-owners-v1'")
        db.execute('ALTER TABLE families DROP COLUMN owner_id')


def test_owner_migration_creation_evidence_and_earliest_remaining_join(group):
    path,_,(a,b,c,d,e)=group
    fa=create(a);join(c,invite(a,'person-2'));join(b,invite(a,'person-1'))
    fb=create(d);join(e,invite(d,'person-4'))
    with connect(path) as db:
        # This was valid before owner management: the creator could leave first.
        db.execute('DELETE FROM family_members WHERE user_id=?',(a.headers['X-Expected-User'],))
    old_owner_schema(path)
    migrate_database(path);migrate_database(path)
    with connect(path) as db:
        assert db.execute('SELECT owner_id FROM families WHERE id=?',(fa,)).fetchone()[0]==c.headers['X-Expected-User']
        assert db.execute('SELECT owner_id FROM families WHERE id=?',(fb,)).fetchone()[0]==d.headers['X-Expected-User']
        validate_database(db)


def test_unknown_owner_migration_rolls_back_without_guessing(group):
    path,_,(a,*_)=group
    fid=create(a)
    with connect(path) as db:
        db.execute('DELETE FROM family_actions')
    old_owner_schema(path)
    with pytest.raises(RuntimeError,match='family_owner_migration_required'):
        migrate_database(path)
    with connect(path) as db:
        assert 'owner_id' not in {r['name'] for r in db.execute('PRAGMA table_info(families)')}
        assert db.execute('SELECT family_id FROM family_members').fetchone()[0]==fid


def test_demo_accounts_in_another_family_do_not_override_join_evidence(group):
    path,_,(a,b,c,*_)=group
    fid=create(a,'演示家庭');join(c,invite(a,'person-2'));join(b,invite(a,'person-1'))
    with connect(path) as db:
        db.execute('DELETE FROM family_members WHERE user_id=?',(a.headers['X-Expected-User'],))
        db.execute("UPDATE users SET username='demo-a' WHERE id=?",(b.headers['X-Expected-User'],))
        db.execute("UPDATE users SET username='demo-b' WHERE id=?",(c.headers['X-Expected-User'],))
    old_owner_schema(path)
    migrate_database(path)
    with connect(path) as db:
        assert db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]==c.headers['X-Expected-User']


@pytest.mark.parametrize('revision',['351ffe7','7262ef2',None],ids=['351ffe7','7262ef2','current-seed'])
def test_real_release_seed_and_backup_owner_migration(tmp_path,revision):
    root=Path(__file__).resolve().parents[2]
    old=root
    if revision:
        old=tmp_path/revision;old.mkdir()
        copy_legacy(revision,old)
    path=tmp_path/'old.sqlite3'
    env=os.environ.copy();env.pop('PYTHONPATH',None)
    subprocess.run([sys.executable,'-m','scripts.seed_demo','--database',str(path)],cwd=old,env=env,check=True,capture_output=True,text=True)
    saved=(old_backup if revision else backup)(path,tmp_path/'backup');restored=tmp_path/'restored.sqlite3'
    restore(saved,restored,saved/'credentials.json')
    migrate_database(path);migrate_database(path)
    for candidate in (path,restored):
        with connect(candidate) as db:
            assert db.execute('SELECT username FROM users WHERE id=(SELECT owner_id FROM families)').fetchone()[0]=='demo-a'
            assert db.execute('SELECT count(*) FROM family_members').fetchone()[0]==2
            validate_database(db)
