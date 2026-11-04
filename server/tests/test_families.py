"""Family lifecycle and tenant isolation through real HTTP handlers and SQLite."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, UTC
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.accounting.db import connect, initialize_database, migrate_database
from server.accounting.categories import DEFAULT_CATEGORIES
from server.accounting.ledger import canonical_json, digest
from server.tests.legacy_fixture import old_backup
from server.automation.backups import backup, restore
from server.automation.worker import Worker
from server.tests.test_api import operation, submit, update
from server.tests.test_refinement import image_url, upload
from server.tests.test_maintenance import action, propose, decide
from server.tests.test_automation import Model, Push


@pytest.fixture
def group(tmp_path):
    path=tmp_path/'families.sqlite3'
    app=create_app(path)
    clients=[]
    for i in range(5):
        c=TestClient(app)
        c.headers['X-App-Request']='shared-expense'
        response=c.post('/api/auth/register',json={'username':f'person-{i}','display_name':f'测试{i}','password':'fictional-password'})
        assert response.status_code==200,response.text
        c.headers['X-Expected-User']=response.json()['user']['id']
        refresh(c)
        clients.append(c)
    yield path,app,clients
    for c in clients:
        c.close()


def refresh(c):
    result=c.get('/api/sync')
    assert result.status_code==200,result.text
    data=result.json()
    c.headers.update({'X-Family-Id':data['family']['id'] if data['family'] else 'none','X-Membership-Version':str(data['membership_version']),'X-Restore-Id':data['restore_id']})
    return data


def create(c,name='测试家'):
    result=c.post('/api/families',json={'request_id':str(uuid4()),'name':name})
    assert result.status_code==200,result.text
    refresh(c)
    return result.json()['family']['id']


def invite(c,username):
    result=c.post('/api/family-invites',json={'request_id':str(uuid4()),'username':username})
    assert result.status_code==200,result.text
    return result.json()['id']


def join(c,iid):
    result=c.post(f'/api/family-invites/{iid}/accept',json={})
    assert result.status_code==200,result.text
    refresh(c)


def leave(c,fid):
    result=c.post(f'/api/families/{fid}/leave',json={})
    assert result.status_code==200,result.text
    return refresh(c)


def transfer(c,fid,target):
    result=c.post(f'/api/families/{fid}/owner',json={'user_id':target.headers['X-Expected-User']})
    assert result.status_code==200,result.text
    return result.json()


def test_solo_registration_validation_and_no_global_directory(group):
    _,_,clients=group
    a,b,*_=clients
    for c in clients:
        data=refresh(c)
        assert data['family'] is None and data['membership_version']==0
        assert data['expenses']==data['images']==[]
        assert data['categories']==[{'scope':'private','name':name} for name in sorted(DEFAULT_CATEGORIES)]
        assert [u['id'] for u in data['users']]==[c.headers['X-Expected-User']]
    assert a.post('/api/operations',json=operation()).status_code==404
    private=submit(a,operation('private'))
    assert b.get('/api/expenses/'+private['id']).status_code==404
    for payload in [dict(username='aa',display_name='甲',password='fictional-password'),dict(username='valid-user',display_name=' ',password='fictional-password'),dict(username='valid-user',display_name='甲',password='secret')]:
        result=a.post('/api/auth/register',json=payload)
        assert result.status_code==422 and payload['password'] not in result.text
    assert a.post('/api/auth/register',json={'username':'person-0','display_name':'甲','password':'fictional-password'}).status_code==409


def test_invites_three_members_reject_revoke_expire_and_archive(group):
    path,_,(a,b,c,d,e)=group
    fid=create(a)
    old=invite(a,'person-1')
    assert b.post(f'/api/family-invites/{old}/reject',json={}).json()['status']=='rejected'
    old=invite(a,'person-1')
    assert a.post(f'/api/family-invites/{old}/revoke',json={}).json()['status']=='revoked'
    old=invite(a,'person-1')
    with connect(path) as db:
        db.execute("UPDATE family_invites SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(old,))
    assert b.post(f'/api/family-invites/{old}/accept',json={}).status_code==409
    join(b,invite(a,'person-1'));join(c,invite(b,'person-2'))
    assert len(a.get('/api/family').json()['family']['members'])==3
    pending=invite(c,'person-3')
    leave(c,fid)
    assert d.post(f'/api/family-invites/{pending}/accept',json={}).status_code==409
    leave(b,fid);leave(a,fid)
    with connect(path) as db:
        assert db.execute('SELECT archived FROM families WHERE id=?',(fid,)).fetchone()[0]==1


def test_two_families_all_resources_and_departed_owner(group):
    _,_,(a,b,c,d,e)=group
    fa=create(a,'A');join(b,invite(a,'person-1'))
    fb=create(c,'B');join(d,invite(c,'person-3'))
    original=operation(category='仅A共同',purpose='A共同秘密')
    shared=submit(a,original);private=submit(a,operation('private',purpose='A私人秘密'))
    url=image_url(shared['id']);assert upload(a,url).status_code==200
    request,body=propose(b,shared)
    decision_id=str(uuid4())
    decision=decide(a,request,decision_id=decision_id);assert decision.status_code==200
    for outsider in (c,d,e):
        assert outsider.get('/api/expenses/'+shared['id']).status_code==404
        assert outsider.get(url).status_code==404
        assert outsider.get('/api/expenses/'+shared['id']+'/history').status_code==404
        assert outsider.post('/api/change-requests',json=body).status_code==404
        assert outsider.get('/api/change-requests').json()['requests']==[]
        assert 'A共同秘密' not in outsider.get('/api/export').text
        assert '仅A共同' not in outsider.get('/api/sync').text
    assert b.get('/api/expenses/'+private['id']).status_code==404
    old_headers=dict(a.headers)
    transfer(a,fa,b)
    data=leave(a,fa)
    assert len(data['expenses'])==1 and data['expenses'][0]['id']==private['id']
    assert a.get('/api/expenses/'+shared['id']).status_code==404
    assert a.get(url).status_code==404
    assert a.post('/api/operations',json=original).status_code==404
    assert decide(a,request,decision_id=decision_id).status_code==404
    assert a.delete(url).status_code==404
    assert 'A共同秘密' not in a.get('/api/export').text
    assert b.get('/api/expenses/'+shared['id']).status_code==200
    assert a.headers['X-Expected-User'] in {u['id'] for u in refresh(b)['users']}
    join(a,invite(c,'person-0'))
    assert a.headers['X-Expected-User'] not in {u['id'] for u in refresh(e)['users']}
    assert 'A共同秘密' not in a.get('/api/sync').text
    assert a.post('/api/operations',json=operation(),headers=old_headers).json()['detail']['code']=='family_changed'
    assert fa!=fb


def test_scope_move_cannot_replay_or_view_old_family_history(group):
    _,_,(a,b,c,_,_)=group
    fa=create(a);join(b,invite(a,'person-1'))
    original=operation(purpose='旧共同版本');expense=submit(a,original)
    private=submit(a,action(expense,'scope',scope='private'))
    private=submit(a,update(private,purpose='当前私人'))
    transfer(a,fa,b)
    leave(a,fa)
    fb=create(c);join(a,invite(c,'person-0'))
    shared=submit(a,action(private,'scope',scope='shared'))
    assert shared['family_id']==fb
    assert a.post('/api/operations',json=original).status_code==404
    history=a.get('/api/expenses/'+shared['id']+'/history').json()['revisions']
    assert history
    assert all(snapshot is None or snapshot['scope']=='private' or snapshot['family_id']==fb for row in history for snapshot in (row['before'],row['after']))
    assert not any(row['version']==1 for row in history)
    assert c.get('/api/expenses/'+shared['id']+'/history').status_code==404
    assert b.get('/api/expenses/'+shared['id']).status_code==404


def test_context_required_and_same_family_rejoin_rejects_old_receipt(group):
    _,_,(a,b,*_)=group
    fid=create(a);join(b,invite(a,'person-1'))
    body=operation();submit(a,body)
    original_version=a.headers['X-Membership-Version']
    del a.headers['X-Family-Id'];del a.headers['X-Membership-Version']
    assert a.post('/api/operations',json=body).json()['detail']['code']=='family_context_required'
    refresh(a);transfer(a,fid,b);leave(a,fid);join(a,invite(b,'person-0'))
    assert int(a.headers['X-Membership-Version'])>int(original_version)
    assert a.post('/api/operations',json=body).json()['detail']['code']=='family_changed'
    assert a.get('/api/expenses/'+body['expense_id']).status_code==200


def test_concurrent_accept_membership_is_atomic(group):
    _,app,(a,b,c,*_)=group
    create(a);create(b)
    ids=[invite(a,'person-2'),invite(b,'person-2')]
    def accept(iid):
        with TestClient(app) as peer:
            peer.cookies.update(c.cookies);peer.headers.update(c.headers)
            return peer.post(f'/api/family-invites/{iid}/accept',json={}).status_code
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(accept,ids))
    assert sorted(result)==[200,409]
    assert refresh(c)['membership_version']==1


def test_create_and_invite_idempotency_never_rejoins(group):
    _,_,(a,b,*_)=group
    body={'request_id':str(uuid4()),'name':'幂等家庭'}
    first=a.post('/api/families',json=body);assert first.status_code==200
    refresh(a)
    assert a.post('/api/families',json=body).json()==first.json()
    inv={'request_id':str(uuid4()),'username':'person-1'}
    first_inv=a.post('/api/family-invites',json=inv)
    assert a.post('/api/family-invites',json=inv).json()==first_inv.json()
    fid=first.json()['family']['id'];leave(a,fid)
    assert a.post('/api/families',json=body).status_code==409
    assert refresh(a)['family'] is None
    assert b.post('/api/family-invites/'+first_inv.json()['id']+'/accept',json={}).status_code==409


def test_settings_report_tasks_push_and_exports_are_family_scoped(group):
    path,app,(a,b,c,d,e)=group
    fa=create(a);join(b,invite(a,'person-1'));fb=create(c);join(d,invite(c,'person-3'))
    for actor,label in ((a,'A秘密'),(c,'B秘密')):
        submit(actor,operation(purpose=label,occurred_at='2026-09-10T00:00:00Z'))
        assert actor.put('/api/settings?scope=shared',json={'monthly':{'enabled':True,'time':'20:00'}}).status_code==200
    submit(a,operation('private',purpose='A私人',occurred_at='2026-09-10T00:00:00Z'))
    assert a.get('/api/settings?scope=private').json()['monthly']['enabled'] is False
    assert e.put('/api/settings?scope=shared',json={'monthly':{'enabled':True,'time':'20:00'}}).status_code==404
    model=Model();push=Push();clock=lambda:datetime(2026,10,4,13,tzinfo=UTC)
    # Existing HTTP workers use the real current time only for the enable start;
    # set the fictional schedule explicitly, then use injected providers only.
    with connect(path) as db:
        db.execute("UPDATE monthly_settings SET start='2026-09'")
    worker=Worker(path,primary=model,push=push,clock=clock,call_limit=20)
    for _ in range(8):worker.tick()
    calls=[call for call in model.calls if call['kind']=='items']
    assert {tuple(e['purpose'] for e in call['expenses']) for call in calls}=={('A秘密',),('B秘密',)}
    ar=a.get('/api/reports').json()['reports'];cr=c.get('/api/reports').json()['reports']
    assert len(ar)==len(cr)==1 and ar[0]['family_id']==fa and cr[0]['family_id']==fb
    assert c.get('/api/reports/'+ar[0]['id']).status_code==404
    assert c.post('/api/reports/'+ar[0]['id']+'/retry',json={}).status_code==404
    assert e.get('/api/reports').json()['reports']==[]
    with connect(path) as db:
        recipients={r['user_id'] for r in db.execute('SELECT user_id FROM notification_outbox WHERE report_id=?',(ar[0]['id'],))}
        assert recipients=={a.headers['X-Expected-User'],b.headers['X-Expected-User']}
    transfer(a,fa,b);leave(a,fa)
    assert a.get('/api/reports/'+ar[0]['id']).status_code==404
    with connect(path) as db:
        assert db.execute('SELECT status FROM notification_outbox WHERE user_id=? AND report_id=?',(a.headers['X-Expected-User'],ar[0]['id'])).fetchone()[0]=='cancelled'
    leave(b,fa)
    with connect(path) as db:
        assert not db.execute('SELECT 1 FROM report_tasks WHERE report_id=?',(ar[0]['id'],)).fetchone()
        assert db.execute("SELECT enabled FROM monthly_settings WHERE scope='shared' AND namespace=?",(fa,)).fetchone()[0]==0


def test_family_backup_restore_preserves_memberships_and_invalidates_sessions(group,tmp_path):
    path,_,(a,b,c,*_)=group
    fid=create(a);iid=invite(a,'person-1')
    submit(a,operation());create(c,'另家')
    saved=backup(path,tmp_path/'backup');target=tmp_path/'restored.sqlite3'
    restore(saved,target,saved/'credentials.json')
    with connect(target) as db:
        assert db.execute('SELECT count(*) FROM families').fetchone()[0]==2
        assert db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]==a.headers['X-Expected-User']
        assert db.execute('SELECT family_id FROM family_invites WHERE id=?',(iid,)).fetchone()[0]==fid
        assert not db.execute('SELECT 1 FROM sessions').fetchone()
        assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1


def test_unrecognized_legacy_shared_data_fails_without_reassigning(tmp_path):
    path=tmp_path/'unknown.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(Path('server/migrations/001_initial.sql').read_text())
        db.execute("INSERT INTO users VALUES('unknown','unknown','unused','未知')")
        db.execute("INSERT INTO expenses VALUES('old','unknown','shared',100,'旧数据','2026-01-01T00:00:00Z','',1)")
    with pytest.raises(RuntimeError,match='family_migration_required'):
        migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT purpose FROM expenses').fetchone()[0]=='旧数据'
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='families'").fetchone()


def test_pre_family_backup_restore_migrates_only_copy_and_keeps_receipt_hash(tmp_path):
    from server.accounting.db import password_hasher
    from server.automation.backups import sha
    path=tmp_path/'old.sqlite3'
    users=[str(uuid4()),str(uuid4())]
    body=operation(purpose='旧共同记录')
    from server.api.models import CreateOperation
    from server.accounting.ledger import request_payload,operation_hash
    payload=request_payload(CreateOperation.model_validate(body))
    expense=dict(payload['data'],id=payload['expense_id'],owner_id=users[0],version=1,voided=False,refunds=[],net_amount_cents=1234)
    original_receipt=canonical_json({'operation_id':payload['operation_id'],'expense':expense})
    original_hash=operation_hash(payload)
    # Build the actual pre-family schema from its original migration files.
    with sqlite3.connect(path) as db:
        for name in ('001_initial.sql','002_categories_images.sql','003_maintenance.sql','004_automation.sql'):
            db.executescript((Path('server/migrations')/name).read_text())
        db.execute("ALTER TABLE expenses ADD COLUMN category TEXT NOT NULL DEFAULT ''")
        db.execute('ALTER TABLE expenses ADD COLUMN voided INTEGER NOT NULL DEFAULT 0')
        db.execute('ALTER TABLE expenses ADD COLUMN rating INTEGER')
        db.execute("ALTER TABLE expenses ADD COLUMN rating_reason TEXT NOT NULL DEFAULT ''")
        for i,uid in enumerate(users):
            db.execute('INSERT INTO users VALUES(?,?,?,?)',(uid,f'demo-{chr(97+i)}',password_hasher.hash('fictional-password'),f'旧用户{i}'))
        db.execute("INSERT INTO app_state(singleton,restore_id,monthly_enabled,monthly_start) VALUES(1,'old-restore',1,'2026-09')")
        db.execute('INSERT INTO expenses(id,owner_id,scope,amount_cents,purpose,occurred_at,location,version) VALUES(:id,:owner_id,:scope,:amount_cents,:purpose,:occurred_at,:location,:version)',expense)
        db.execute('INSERT INTO operations VALUES(?,?,?,?)',(users[0],payload['operation_id'],original_hash,original_receipt))
        db.execute('INSERT INTO revisions VALUES(?,1,?,?,NULL,?)',(expense['id'],users[0],payload['operation_id'],canonical_json(expense)))
    saved=old_backup(path,tmp_path/'old-backup')
    hashes={p.name:sha(p) for p in saved.iterdir()}
    target=tmp_path/'upgraded.sqlite3'
    result=restore(saved,target,saved/'credentials.json')
    assert hashes=={p.name:sha(p) for p in saved.iterdir()}
    migrate_database(target)
    with connect(target) as db:
        fid=db.execute('SELECT family_id FROM expenses').fetchone()[0]
        assert fid and db.execute('SELECT count(*) FROM families').fetchone()[0]==1
        assert db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]==users[0]
        assert db.execute('SELECT count(*) FROM family_members').fetchone()[0]==2
        row=db.execute('SELECT * FROM operations').fetchone()
        assert row['request_hash']==original_hash and row['receipt_json']==original_receipt
        assert row['family_id']==fid
        assert db.execute('SELECT count(*) FROM monthly_settings WHERE enabled=1').fetchone()[0]==3
        assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1
    with TestClient(create_app(target)) as c:
        c.headers['X-App-Request']='shared-expense'
        assert c.post('/api/auth/login',json={'username':'demo-a','password':'fictional-password'}).status_code==200
        c.headers['X-Expected-User']=users[0]
        data=refresh(c)
        assert data['legacy_family_id']==fid and data['membership_version']==1
        assert c.post('/api/operations',json=body).status_code==200


def test_password_change_keeps_current_session_and_revokes_other_sessions(group):
    _,app,(a,*_)=group
    with TestClient(app) as other:
        other.headers['X-App-Request']='shared-expense'
        assert other.post('/api/auth/login',json={'username':'person-0','password':'fictional-password'}).status_code==200
        before=a.get('/api/auth/me').json()
        assert a.post('/api/auth/password',json={'old_password':'wrong-password','new_password':'new-fictional-password'}).status_code==400
        assert other.get('/api/auth/me').status_code==200
        assert a.post('/api/auth/password',json={'old_password':'fictional-password','new_password':'new-fictional-password'}).status_code==204
        assert a.get('/api/auth/me').json()==before
        assert other.get('/api/auth/me').status_code==401
        assert other.post('/api/auth/login',json={'username':'person-0','password':'fictional-password'}).status_code==401
        assert other.post('/api/auth/login',json={'username':'person-0','password':'new-fictional-password'}).status_code==200
    bad=a.post('/api/auth/password',json={'old_password':'sensitive-input','new_password':123})
    assert bad.status_code==422 and 'sensitive-input' not in bad.text
