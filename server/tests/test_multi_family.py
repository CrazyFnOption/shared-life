"""B developer checks: real handlers, frozen A source and isolated fictional restores."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from server.accounting import db as database, families, ledger
from server.accounting.db import connect, initialize_database, migrate_database
from server.api.app import create_app
from server.automation import reports
from server.automation.backups import backup, restore, sha, validate_database
from server.automation.worker import Worker
from server.tests.legacy_fixture import copy_legacy
from server.tests.test_api import operation


@pytest.fixture
def home(tmp_path):
    path=tmp_path/'fictional.sqlite3';app=create_app(path);clients=[]
    for name in ('alpha','beta','gamma'):
        c=TestClient(app);c.headers['X-App-Request']='shared-expense'
        response=c.post('/api/auth/register',json={'username':name,'display_name':name,'password':'fictional-password'})
        assert response.status_code==200,response.text
        c.headers.update({'X-App-Protocol':'2','X-Expected-User':response.json()['user']['id']})
        ctx=c.get('/api/context').json();c.headers['X-Restore-Id']=ctx['restore_id'];clients.append(c)
    yield path,app,clients
    for c in clients:c.close()


def ctx(c):
    r=c.get('/api/context');assert r.status_code==200,r.text
    return r.json()


def headers(c,fid=None):
    m=next((f for f in ctx(c)['families'] if f['id']==fid),None)
    return {'X-Scope':'shared' if fid else 'private','X-Family-Id':fid or 'none','X-Relation-Epoch':str(m['relation_epoch'] if m else 0)}


def create(c,name):
    r=c.post('/api/v2/families',json={'request_id':str(uuid4()),'name':name})
    assert r.status_code==200,r.text
    return next(f for f in r.json()['context']['families'] if f['name']==name)['id']


def join(owner,target,fid):
    r=owner.post('/api/v2/family-invites',headers=headers(owner,fid),json={'request_id':str(uuid4()),'username':target.get('/api/auth/me').json()['user']['username']})
    assert r.status_code==200,r.text
    r=target.post('/api/v2/family-invites/'+r.json()['id']+'/accept',json={})
    assert r.status_code==200,r.text


def submit(c,fid=None,body=None):
    body=body or operation('shared' if fid else 'private')
    r=c.post('/api/v2/operations',headers=headers(c,fid),json=body)
    assert r.status_code==200,r.text
    return r.json()['expense']


def management(c,fid,target=None):
    f=next(f for f in ctx(c)['families'] if f['id']==fid)
    body={'expected_family_revision':f['revision']}
    if target:
        body['target_relation_epoch']=next(m['relation_epoch'] for m in f['members'] if m['id']==target)
    return body


def scope(c,e,fid,scope):
    body={'operation_id':str(uuid4()),'expense_id':e['id'],'base_version':e['version'],'kind':'scope','data':{'scope':scope}}
    return submit(c,fid,body)


def png():
    data=io.BytesIO();Image.new('RGB',(4,4),'green').save(data,format='PNG');return data.getvalue()


def test_multi_family_private_scope_default_and_wire_boundaries(home):
    path,app,(a,b,c)=home
    fa=create(a,'甲');fb=create(b,'乙');join(b,a,fb)
    assert ctx(a)['default_scope']['scope']=='private'
    bodya=operation(purpose='甲家');bodyb=operation(purpose='乙家');bodyp=operation('private',purpose='私人')
    ea=submit(a,fa,bodya);eb=submit(a,fb,bodyb);ep=submit(a,None,bodyp)
    for fid,e in ((fa,ea),(fb,eb),(None,ep)):
        r=a.get('/api/v2/sync',headers=headers(a,fid));assert r.status_code==200,r.text
        assert [x['id'] for x in r.json()['expenses']]==[e['id']]
        assert r.headers['x-family-id']==(fid or 'none')
        assert r.headers['x-source-user']==a.headers['X-Expected-User']
    bad=a.post('/api/v2/operations',headers=headers(a,fb),json=bodya)
    assert bad.status_code==404 and '甲家' not in bad.text
    old=TestClient(app);old.cookies.update(a.cookies);old.headers.update({k:v for k,v in a.headers.items() if k.lower()!='x-app-protocol'})
    assert old.get('/api/sync').status_code==426
    assert old.post('/api/operations',json=bodyp).status_code==426
    assert a.post('/api/operations',headers=headers(a,fa),json=bodya).status_code==426
    before=ctx(a);rid=str(uuid4());payload={'request_id':rid,'base_version':before['default_scope']['version'],'scope':'shared','family_id':fb}
    assert a.put('/api/account/default-scope',json=payload).json()['context']['default_scope']['family_id']==fb
    assert a.put('/api/account/default-scope',json=payload).status_code==200
    assert a.put('/api/account/default-scope',json={**payload,'request_id':str(uuid4())}).status_code==409
    assert a.get('/api/v2/export?scope=all',headers=headers(a,fa)).json()['expenses'][0]['id'] in {ea['id'],ep['id']}
    exported=a.get('/api/v2/export?scope=all',headers=headers(a,fa)).json()
    assert {e['id'] for e in exported['expenses']}=={ea['id'],ep['id']}
    assert {e['id'] for e in a.get('/api/v2/export?scope=all',headers=headers(a)).json()['expenses']}=={ep['id']}


def test_target_only_revoke_default_receipt_images_and_rejoin(home):
    path,_,(a,b,c)=home
    fa=create(a,'甲');fb=create(b,'乙');join(a,c,fa);join(b,c,fb)
    body=operation(purpose='甲旧回执');ea=submit(c,fa,body);eb=submit(c,fb);ep=submit(c)
    image=str(uuid4());url=f"/api/v2/expenses/{ea['id']}/images/{image}"
    assert c.put(url,content=png(),headers={**headers(c,fa),'Content-Type':'image/png'}).status_code==200
    assert c.get(url,headers=headers(c,fb)).status_code==404
    saved=headers(c,fa);pref=ctx(c)['default_scope']['version']
    assert c.put('/api/account/default-scope',json={'request_id':str(uuid4()),'base_version':pref,'scope':'shared','family_id':fa}).status_code==200
    with connect(path) as db:
        reports.enqueue_notification(db,c.headers['X-Expected-User'],'weekly:fictional',0)
        for fid in (fa,fb):
            rid=str(uuid4());db.execute("INSERT INTO reports(id,scope,owner_id,month,status,data_version) VALUES(?,'shared',?,'2026-10','ready',?)",(rid,fid,'a'*64))
            reports.enqueue_notification(db,c.headers['X-Expected-User'],'report:'+rid,0,rid,'a'*64)
    r=c.post('/api/v2/families/'+fa+'/leave',headers=saved,json=management(c,fa));assert r.status_code==200,r.text
    assert ctx(c)['default_scope']['scope']=='private' and ctx(c)['default_invalidated_family_id']==fa
    assert c.get('/api/v2/sync',headers=saved).status_code==409
    assert c.get('/api/v2/expenses/'+eb['id'],headers=headers(c,fb)).status_code==200
    assert c.get('/api/v2/expenses/'+ep['id'],headers=headers(c)).status_code==200
    with connect(path) as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM notification_outbox')]
        assert next(r for r in rows if r['family_id']==fa)['status']=='cancelled'
        assert next(r for r in rows if r['family_id']==fb)['status']=='pending'
        assert next(r for r in rows if r['source_scope']=='account')['status']=='pending'
    join(a,c,fa);new=headers(c,fa);assert int(new['X-Relation-Epoch'])>int(saved['X-Relation-Epoch'])
    assert c.post('/api/v2/operations',headers=new,json=body).status_code==409
    assert c.get(url,headers=new).status_code==200


def test_scope_chain_receipts_history_dependencies_and_restore_get_gate(home):
    _,_,(a,b,c)=home;fa=create(a,'甲');fb=create(b,'乙');join(a,c,fa);join(b,c,fb)
    body=operation('private',purpose='跨范围链');e=submit(c,None,body)
    e=scope(c,e,fa,'shared');e=scope(c,e,fa,'private');e=scope(c,e,fb,'shared');e=scope(c,e,fb,'private')
    r=c.post('/api/v2/operations',headers=headers(c),json=body);assert r.status_code==200 and r.json()['expense']['version']==1
    history=c.get('/api/v2/expenses/'+e['id']+'/history',headers=headers(c)).json()
    assert {d['family_id'] for d in history['source_dependencies']}=={fa,fb}
    assert len(history['revisions'])==5
    assert c.get('/api/v2/sync',headers={**headers(c),'X-Restore-Id':'wrong'}).status_code==409
    assert b.get('/api/v2/expenses/'+e['id']+'/history',headers=headers(b,fb)).status_code==404
    c.post('/api/v2/families/'+fa+'/leave',headers=headers(c,fa),json=management(c,fa))
    history=c.get('/api/v2/expenses/'+e['id']+'/history',headers=headers(c)).json()
    assert {d['family_id'] for d in history['source_dependencies']}=={fb}
    assert len(history['revisions'])==3  # Independent private create and both still-authorized B transitions.


def test_category_signature_independent_of_other_memberships(home):
    path,_,(a,b,c)=home;fa=create(a,'甲');join(a,c,fa);e=submit(c,fa)
    r=a.post('/api/v2/category-changes/preview',headers=headers(a,fa),json={'kind':'reclassify','scope':'shared','expense_ids':[e['id']],'target_name':'餐饮'})
    assert r.status_code==200,r.text
    proposal={'id':str(uuid4()),'preview_id':r.json()['preview_id']}
    r=a.post('/api/v2/category-changes',headers=headers(a,fa),json=proposal);assert r.status_code==200,r.text
    change=r.json()['change']['id'];fb=create(c,'乙')
    r=c.post('/api/v2/category-changes/'+change+'/decision',headers=headers(c,fa),json={'decision_id':str(uuid4()),'decision':'accept'})
    assert r.status_code==200 and r.json()['change']['status']=='applied',r.text
    with connect(path) as db:assert ledger.read_expense(db,e['id'])['category']=='餐饮'


def test_transfer_atomic_stale_recipient_and_transaction_rollback(home):
    path,_,(a,b,c)=home;fa=create(a,'甲');join(a,b,fa);join(a,c,fa)
    target=b.headers['X-Expected-User'];old=management(a,fa,target)
    r=b.post('/api/v2/families/'+fa+'/leave',headers=headers(b,fa),json=management(b,fa));assert r.status_code==200
    r=a.post('/api/v2/families/'+fa+'/transfer-and-leave',headers=headers(a,fa),json={**old,'user_id':target});assert r.status_code in (404,409)
    assert next(f for f in ctx(a)['families'] if f['id']==fa)['owner_id']==a.headers['X-Expected-User']
    target=c.headers['X-Expected-User'];payload={**management(a,fa,target),'user_id':target}
    with connect(path) as db:db.execute("CREATE TRIGGER fail_leave BEFORE UPDATE OF active ON family_members BEGIN SELECT RAISE(ABORT,'fictional'); END")
    r=a.post('/api/v2/families/'+fa+'/transfer-and-leave',headers=headers(a,fa),json=payload);assert r.status_code==500
    assert len(ctx(a)['families'])==1 and ctx(a)['families'][0]['owner_id']==a.headers['X-Expected-User']
    with connect(path) as db:db.execute('DROP TRIGGER fail_leave')
    assert a.post('/api/v2/families/'+fa+'/transfer-and-leave',headers=headers(a,fa),json=payload).status_code==200
    assert ctx(a)['families']==[] and ctx(c)['families'][0]['owner_id']==target


@pytest.fixture(scope='module')
def frozen_a(tmp_path_factory):
    work=tmp_path_factory.mktemp('frozen-a')
    copy_legacy('298b15f',work)
    code='''
from pathlib import Path
import json, shutil
from fastapi.testclient import TestClient
from server.api.app import create_app
from server.accounting.db import connect
from server.accounting.families import create_explicit_family
from server.automation.backups import backup
from uuid import uuid4
p=Path('active.sqlite3');app=create_app(p);users=[];clients=[]
for name in ('alpha','beta'):
 c=TestClient(app);c.headers['X-App-Request']='shared-expense'
 uid=c.post('/api/auth/register',json={'username':name,'display_name':name,'password':'fictional-password'}).json()['user']['id'];users.append(uid);clients.append(c)
with connect(p) as db:
 db.execute('BEGIN IMMEDIATE');fid=create_explicit_family(db,users,'旧家');db.commit()
for uid,c in zip(users,clients):
 c.headers['X-Expected-User']=uid;r=c.get('/api/sync').json();c.headers.update({'X-Restore-Id':r['restore_id'],'X-Family-Id':fid,'X-Membership-Version':str(r['membership_version'])})
body={'operation_id':str(uuid4()),'kind':'create','expense_id':str(uuid4()),'base_version':0,
      'data':{'scope':'shared','amount_cents':1234,'purpose':'旧共同字节','occurred_at':'2026-10-30T12:00:00+08:00','location':''}}
r=clients[1].post('/api/operations',json=body);assert r.status_code==200,r.text
backup(p,'format1')
shutil.copyfile(p,'left.sqlite3');left=create_app('left.sqlite3');c=TestClient(left);c.cookies.update(clients[1].cookies);c.headers.update(clients[1].headers)
assert c.post('/api/families/'+fid+'/leave',json={}).status_code==200
Path('fixture.json').write_text(json.dumps({'users':users,'fid':fid,'body':body,'restore_id':r.headers.get('x-restore-id'),'receipt':r.json()}))
'''
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH' and not k.startswith('SHARED_EXPENSE_')}
    subprocess.run([sys.executable,'-c',code],cwd=work,env=env,check=True,capture_output=True,text=True)
    return work


def test_real_a_atomic_migration_preserves_old_columns_and_idempotence(frozen_a,tmp_path,monkeypatch):
    path=tmp_path/'a.sqlite3';shutil.copyfile(frozen_a/'active.sqlite3',path)
    with connect(path,read_only=True) as db:
        baseline={r['name']:([c['name'] for c in db.execute(f"PRAGMA table_info({r['name']})")],[tuple(x) for x in db.execute(f"SELECT * FROM {r['name']} ORDER BY rowid")]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    original=database.Connection.execute
    def fail(self,sql,parameters=(),/):
        if sql.strip().startswith('ALTER TABLE category_changes ADD COLUMN relation_context_json'):
            raise RuntimeError('fictional interrupted 008')
        return original(self,sql,parameters)
    with monkeypatch.context() as m:
        m.setattr(database.Connection,'execute',fail)
        with pytest.raises(RuntimeError):migrate_database(path)
    with connect(path,read_only=True) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==0 and 'active' not in {r['name'] for r in db.execute('PRAGMA table_info(family_members)')}
    with pytest.raises(RuntimeError,match='migration_required'):initialize_database(path)
    cli=subprocess.run([sys.executable,'-m','scripts.migrate_memos','--database',str(path)],check=True,capture_output=True,text=True)
    assert 'Migrated to schema 9' in cli.stdout
    with connect(path) as db:
        for table,(columns,rows) in baseline.items():
            actual=[tuple(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")]
            added_markers={'multi-family-v2','memos-v1','plans-v1'}
            assert rows==[r for r in actual if table!='schema_migrations' or r[0] not in added_markers]
        assert db.execute('SELECT default_family_id FROM users LIMIT 1').fetchone()[0]==json.loads((frozen_a/'fixture.json').read_text())['fid']
        validate_database(db)
    digest=sha(path);assert not migrate_database(path);initialize_database(path);assert sha(path)==digest


def test_a_departed_no_row_floor_reentry_holds_old_receipt(frozen_a,tmp_path):
    path=tmp_path/'left.sqlite3';shutil.copyfile(frozen_a/'left.sqlite3',path);meta=json.loads((frozen_a/'fixture.json').read_text());migrate_database(path)
    app=create_app(path);clients=[]
    for name in ('alpha','beta'):
        c=TestClient(app);c.headers['X-App-Request']='shared-expense';uid=c.post('/api/auth/login',json={'username':name,'password':'fictional-password'}).json()['user']['id'];c.headers.update({'X-App-Protocol':'2','X-Expected-User':uid});c.headers['X-Restore-Id']=ctx(c)['restore_id'];clients.append(c)
    a,b=clients;join(a,b,meta['fid']);new=headers(b,meta['fid']);assert int(new['X-Relation-Epoch'])>2
    assert all(x['membership_version']!=1 for x in ctx(b)['legacy_context_mappings'])
    r=b.post('/api/v2/operations',headers=new,json=meta['body']);assert r.status_code==409 and '旧共同字节' not in r.text


def test_format1_and_2_restore_and_old_real_restorer_rejection(frozen_a,tmp_path):
    old=frozen_a/'format1';restored=tmp_path/'from1.sqlite3';result=restore(old,restored,old/'credentials.json')
    assert result['automation_paused']
    with connect(restored) as db:validate_database(db);assert not db.execute('SELECT 1 FROM sessions').fetchone()
    saved=backup(restored,tmp_path/'format2');assert json.loads((saved/'manifest.json').read_text())['format_version']==2
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH' and not k.startswith('SHARED_EXPENSE_')}
    rejected=tmp_path/'must-not-exist.sqlite3'
    r=subprocess.run([sys.executable,'-m','scripts.restore','--backup',str(saved),'--destination',str(rejected)],cwd=frozen_a,env=env,capture_output=True,text=True)
    assert r.returncode!=0 and not rejected.exists()
    target=tmp_path/'from2.sqlite3';restore(saved,target,saved/'credentials.json')
    with connect(target) as db:validate_database(db);assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1


def test_future_and_corrupt_schema_startup_read_only(home):
    path,_,_=home
    with connect(path) as db:
        current_schema=db.execute('PRAGMA user_version').fetchone()[0]
        db.execute(f'PRAGMA user_version={current_schema+1}')
    before=sha(path)
    with pytest.raises(RuntimeError):create_app(path)
    assert before==sha(path)
    with connect(path) as db:db.execute(f'PRAGMA user_version={current_schema}');db.execute('ALTER TABLE operations DROP COLUMN source_restore_id')
    before=sha(path)
    with pytest.raises(RuntimeError):create_app(path)
    assert before==sha(path)


@pytest.mark.parametrize('race',['restore','session','relation'])
def test_transaction_rechecks_after_http_dependency(home,monkeypatch,race):
    import sqlite3
    path,_,(a,b,c)=home;fa=create(a,'甲');captured=headers(a,fa)
    original=database.Connection.execute;changed=False
    def inject(self,sql,parameters=(),/):
        nonlocal changed
        if not changed and sql.strip().upper().startswith('BEGIN') and self.context_user and self.protocol==2:
            changed=True
            if race=='restore':sqlite3.Connection.execute(self,"UPDATE app_state SET restore_id='new-fictional-domain'")
            elif race=='session':sqlite3.Connection.execute(self,'DELETE FROM sessions WHERE user_id=?',(self.context_user,))
            else:sqlite3.Connection.execute(self,'UPDATE family_members SET active=0,relation_epoch=relation_epoch+1 WHERE user_id=? AND family_id=?',(self.context_user,fa))
        return original(self,sql,parameters)
    monkeypatch.setattr(database.Connection,'execute',inject)
    r=a.post('/api/v2/operations',headers=captured,json=operation())
    assert r.status_code==(401 if race=='session' else 409),r.text
    with connect(path) as db:assert not db.execute('SELECT 1 FROM expenses').fetchone()


def test_late_push_result_never_revives_revoked_outbox(home):
    path,_,(a,b,c)=home;fa=create(a,'甲');join(a,c,fa);uid=c.headers['X-Expected-User'];rid=str(uuid4())
    with connect(path) as db:
        data_version=reports.version(reports.snapshot(db,'shared',fa,'2026-10'))
        db.execute("INSERT INTO reports(id,scope,owner_id,month,status,data_version) VALUES(?,'shared',?,'2026-10','ready',?)",(rid,fa,data_version))
        reports.enqueue_notification(db,uid,'report:'+rid,0,rid,data_version)
        db.execute("INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,'https://web.push.apple.com/fictional','{}')",(uid,))
    class RevokingPush:
        def send(self,*args):
            r=c.post('/api/v2/families/'+fa+'/leave',headers=headers(c,fa),json=management(c,fa));assert r.status_code==200,r.text
    worker=Worker(path,push=RevokingPush(),call_limit=0)
    worker._notifications(datetime.now(UTC))
    with connect(path) as db:
        assert db.execute('SELECT status FROM notification_outbox').fetchone()[0]=='cancelled'
        assert db.execute('SELECT status FROM push_deliveries').fetchone()[0]=='sent'  # Actual external fact retained.


def test_old_a_program_really_has_no_b_database_guard(home,frozen_a,tmp_path):
    path,_,(a,b,c)=home;fa=create(a,'甲');fb=create(b,'乙');join(b,a,fb)
    probe=tmp_path/'unsafe-a-copy.sqlite3';shutil.copyfile(path,probe)
    code='''
import sys,json
from server.accounting.db import initialize_database,connect
from server.accounting.families import remove_membership
p,uid,fid=sys.argv[1:]
initialize_database(p)
with connect(p) as db:
 before=db.execute('SELECT count(*) FROM family_members WHERE user_id=?',(uid,)).fetchone()[0]
 db.execute('BEGIN IMMEDIATE');remove_membership(db,uid,fid);db.commit()
 after=db.execute('SELECT count(*) FROM family_members WHERE user_id=?',(uid,)).fetchone()[0]
 print(json.dumps({'old_startup_succeeded':True,'before':before,'after':after}))
'''
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH' and not k.startswith('SHARED_EXPENSE_')}
    r=subprocess.run([sys.executable,'-c',code,str(probe),a.headers['X-Expected-User'],fa],cwd=frozen_a,env=env,capture_output=True,text=True,check=True)
    assert json.loads(r.stdout)=={'old_startup_succeeded':True,'before':2,'after':0}
    with connect(path) as db:assert db.execute('SELECT count(*) FROM family_members WHERE user_id=? AND active=1',(a.headers['X-Expected-User'],)).fetchone()[0]==2


@pytest.mark.parametrize('corrupt',['default','epoch','image','outbox','receipt'])
def test_backup_semantic_corruption_rejected_before_destination(home,tmp_path,corrupt):
    path,_,(a,b,c)=home;fa=create(a,'甲');e=submit(a,fa);uid=a.headers['X-Expected-User']
    saved=backup(path,tmp_path/'valid-backup');business=saved/'business.sqlite3'
    with connect(business) as db:
        if corrupt=='default':db.execute('UPDATE users SET default_family_id=? WHERE id=?',(fa,b.headers['X-Expected-User']))
        elif corrupt=='epoch':db.execute('UPDATE legacy_relation_epochs SET relation_epoch=9999')
        elif corrupt=='receipt':
            receipt=json.loads(db.execute('SELECT receipt_json FROM operations LIMIT 1').fetchone()[0]);receipt['expense']['family_id']=str(uuid4())
            db.execute('UPDATE operations SET receipt_json=?',(json.dumps(receipt),))
        elif corrupt=='image':db.execute("INSERT INTO expense_images(id,expense_id,mime_type,size_bytes,content_hash,data) VALUES(?,?,'image/png',3,'invalid',?)",(str(uuid4()),e['id'],b'bad'))
        else:
            db.execute("INSERT INTO notification_outbox(id,user_id,dedup_key,next_attempt,source_scope,family_id,relation_epoch,source_restore_id,protocol_version,source_status) VALUES(?,?,?,0,'shared',?,9999,?,2,'verified')",(str(uuid4()),uid,'bad',fa,ctx(a)['restore_id']))
    manifest=json.loads((saved/'manifest.json').read_text());manifest['files']['business.sqlite3']=sha(business);(saved/'manifest.json').write_text(json.dumps(manifest))
    dest=tmp_path/'must-not-exist.sqlite3'
    with pytest.raises(ValueError):restore(saved,dest)
    assert not dest.exists()



def test_held_category_signature_revoked_without_unfreezing(home):
    path,_,(a,b,c)=home;fa=create(a,'甲');join(a,c,fa);e=submit(c,fa)
    preview=a.post('/api/v2/category-changes/preview',headers=headers(a,fa),json={'kind':'reclassify','scope':'shared','expense_ids':[e['id']],'target_name':'餐饮'}).json()['preview_id']
    with connect(path) as db:
        db.execute("UPDATE category_changes SET relation_context_json=NULL,source_status='held' WHERE preview_id=?",(preview,))
    r=c.post('/api/v2/families/'+fa+'/leave',headers=headers(c,fa),json=management(c,fa));assert r.status_code==200,r.text
    with connect(path) as db:
        row=db.execute('SELECT status,source_status FROM category_changes WHERE preview_id=?',(preview,)).fetchone()
        assert tuple(row)==('stale','held')


def test_verified_receipt_foreign_restore_rejected_before_backup_and_restore(home,tmp_path):
    path,_,(a,b,c)=home
    body=operation('private',purpose='虚构错域完成回执');submit(a,None,body)
    saved=backup(path,tmp_path/'valid-before-corruption')
    with connect(path,read_only=True) as db:
        original=tuple(db.execute('SELECT receipt_json,request_hash FROM operations WHERE operation_id=?',(body['operation_id'],)).fetchone())
    for target in (path,saved/'business.sqlite3'):
        with connect(target) as db:
            db.execute("UPDATE operations SET source_restore_id='foreign-fictional-restore' WHERE operation_id=?",(body['operation_id'],))
            assert tuple(db.execute('SELECT receipt_json,request_hash FROM operations WHERE operation_id=?',(body['operation_id'],)).fetchone())==original
            assert db.execute('SELECT source_status FROM operations WHERE operation_id=?',(body['operation_id'],)).fetchone()[0]=='verified'
    replay=a.post('/api/v2/operations',headers=headers(a),json=body)
    assert replay.status_code==409 and replay.json()['detail']['code']=='source_unproven'
    with connect(path,read_only=True) as db:
        with pytest.raises(ValueError,match='Invalid operation source'):
            validate_database(db)
    rejected_backup=tmp_path/'must-not-create-backup'
    with pytest.raises(ValueError,match='Invalid operation source'):
        backup(path,rejected_backup)
    assert not rejected_backup.exists()
    manifest=json.loads((saved/'manifest.json').read_text())
    manifest['files']['business.sqlite3']=sha(saved/'business.sqlite3')
    (saved/'manifest.json').write_text(json.dumps(manifest))
    rejected_restore=tmp_path/'must-not-create-restore.sqlite3'
    with pytest.raises(ValueError,match='Invalid operation source'):
        restore(saved,rejected_restore,saved/'credentials.json')
    assert not rejected_restore.exists()


@pytest.mark.parametrize('held',[False,True],ids=['verified-current-domain','held-foreign-domain'])
def test_completed_receipt_restore_preserves_trust_and_original_bytes(home,tmp_path,held):
    path,_,(a,b,c)=home
    body=operation('private',purpose='虚构完成回执');submit(a,None,body)
    with connect(path) as db:
        if held:
            db.execute("UPDATE operations SET source_status='held',source_restore_id='foreign-fictional-restore' WHERE operation_id=?",(body['operation_id'],))
        original=tuple(db.execute('SELECT receipt_json,request_hash FROM operations WHERE operation_id=?',(body['operation_id'],)).fetchone())
    saved=backup(path,tmp_path/'restore-source');target=tmp_path/'restored.sqlite3'
    result=restore(saved,target,saved/'credentials.json')
    with connect(target,read_only=True) as db:
        row=db.execute('SELECT receipt_json,request_hash,source_status,source_restore_id FROM operations WHERE operation_id=?',(body['operation_id'],)).fetchone()
        assert tuple(row)[:2]==original
        assert row['source_status']==('held' if held else 'verified')
        assert row['source_restore_id']==('foreign-fictional-restore' if held else result['restore_id'])
    with TestClient(create_app(target)) as client:
        client.headers['X-App-Request']='shared-expense'
        logged=client.post('/api/auth/login',json={'username':'alpha','password':'fictional-password'})
        assert logged.status_code==200,logged.text
        client.headers.update({'X-App-Protocol':'2','X-Expected-User':logged.json()['user']['id'],'X-Restore-Id':result['restore_id']})
        replay=client.post('/api/v2/operations',headers=headers(client),json=body)
        assert replay.status_code==(409 if held else 200),replay.text
        if held:
            assert replay.json()['detail']['code']=='source_unproven'
        else:
            assert replay.json()==json.loads(original[0])
