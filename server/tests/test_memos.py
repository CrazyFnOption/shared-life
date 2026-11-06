"""C representative persistence, provenance, concurrency and recovery tests."""
import hashlib
import json
from uuid import uuid4
import pytest
import io,os,subprocess,sys
from server.tests.test_multi_family import home, headers, create, join, png, management
from server.accounting.db import SCHEMA_VERSION, connect, _initialize_legacy, _migrate_multi_family, migrate_database, preflight
from server.automation.backups import backup, restore
from server.tests.legacy_fixture import copy_legacy, legacy_file
from server.api.app import create_app
from datetime import date,datetime,timedelta
from server.memos import calendar,events


def op(client,entity_type,entity_id,kind,data,version=0,fid=None,op_id=None):
    return client.post('/api/v2/memos/operations',headers=headers(client,fid),json={'op_id':op_id or str(uuid4()),'entity_type':entity_type,'entity_id':entity_id,'kind':kind,'base_version':version,'data':data})


def saved(response):
    assert response.status_code==200,response.text
    return response.json()['receipt']['record']


def category(c,fid=None,name='资料'):
    return saved(op(c,'category',str(uuid4()),'create',{'name':name},fid=fid))


def memo(c,cat,fid=None,attachments=None):
    return saved(op(c,'memo',str(uuid4()),'create',{'category_id':cat['id'],'title':'共同资料','body_markdown':'# Markdown\n**原文保留**','attachments':attachments or []},fid=fid))


def test_memo_members_edit_complete_author_receipt_and_private(home):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid)
    cat=category(a,fid);m=memo(a,cat,fid)
    data={key:m[key] for key in ('category_id','title','body_markdown','attachments')};data['title']='成员更新'
    update_id=str(uuid4());r=op(b,'memo',m['id'],'update',data,1,fid,update_id);updated=saved(r)
    assert updated['author_id']==m['author_id'] and updated['updated_by']!=m['author_id'] and updated['version']==2
    assert op(b,'memo',m['id'],'update',data,1,fid,update_id).json()==r.json()
    assert op(b,'memo',m['id'],'update',data,1,fid).json()['detail']['code']=='memo_conflict'
    assert saved(op(b,'memo',m['id'],'complete',{'completed':True},2,fid))['completed']
    assert a.get('/api/v2/memos/records/'+m['id'],headers=headers(a,fid)).json()['memo']['completed']
    assert c.get('/api/v2/memos/records/'+m['id'],headers=headers(c)).status_code==404
    private=memo(a,category(a));assert b.get('/api/v2/memos/records/'+private['id'],headers=headers(b,fid)).status_code==404
    assert b.get('/api/v2/memos/operations/'+update_id,headers=headers(b)).status_code==404
    # A new process reads the committed original Markdown and completion fact.
    create_app(path)


def test_memo_stable_upload_duplicate_and_manifest_tombstone(home):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid)
    data=png();aid=str(uuid4());meta={'id':aid,'kind':'photo','name':'照片.png','mime_type':'image/png','size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    m=memo(a,category(a,fid),fid,[meta]);url=f"/api/v2/memos/records/{m['id']}/attachments/{aid}"
    assert a.get(url,headers=headers(a,fid)).json()['detail']['code']=='attachment_not_uploaded'
    for unused in range(2):
        r=b.put(url,headers={**headers(b,fid),'Content-Type':'image/png'},content=data);assert r.status_code==200,r.text
    assert b.get(url,headers=headers(b,fid)).content==data
    assert a.get('/api/v2/memos/records/'+m['id'],headers=headers(a,fid)).json()['memo']['version']==1
    assert b.get(url,headers=headers(b)).status_code==404
    assert b.put(url,headers={**headers(b,fid),'Content-Type':'image/png'},content=b'bad').status_code==409
    updated={key:m[key] for key in ('category_id','title','body_markdown','attachments')};updated['attachments']=[]
    saved(op(b,'memo',m['id'],'update',updated,1,fid))
    assert b.put(url,headers={**headers(b,fid),'Content-Type':'image/png'},content=data).status_code==404
    assert b.get(url,headers=headers(b,fid)).status_code==404
    assert op(a,'memo',m['id'],'update',{**updated,'attachments':[meta]},2,fid).status_code==409
    with connect(path) as db:assert db.execute('SELECT data FROM memo_attachments WHERE id=?',(aid,)).fetchone()[0]==data


def test_memo_file_restore_and_b_migration_keep_old_bytes(home,tmp_path):
    path,app,(a,b,c)=home;m=memo(a,category(a));binary=b'\x00arbitrary file\xff';aid=str(uuid4())
    meta={'id':aid,'kind':'file','name':'notes.bin','mime_type':'application/octet-stream','size_bytes':len(binary),'sha256':hashlib.sha256(binary).hexdigest()}
    saved(op(a,'memo',m['id'],'update',{key:m[key] for key in ('category_id','title','body_markdown')}|{'attachments':[meta]},1))
    url=f"/api/v2/memos/records/{m['id']}/attachments/{aid}";assert a.put(url,headers={**headers(a),'Content-Type':'application/octet-stream'},content=binary).status_code==200
    original=a.get(url,headers=headers(a));assert 'attachment;' in original.headers['content-disposition']
    saved_backup=backup(path,tmp_path/'backup');target=tmp_path/'restored.sqlite3';restore(saved_backup,target,saved_backup/'credentials.json')
    with connect(target) as db:
        assert db.execute('SELECT data FROM memo_attachments WHERE id=?',(aid,)).fetchone()[0]==binary
        assert not db.execute('SELECT 1 FROM sessions').fetchone()
        assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1
    # A precise B schema8 with actual user credentials and an unrelated row.
    old=tmp_path/'schema8.sqlite3'
    with connect(old) as db:
        db.execute('BEGIN IMMEDIATE');_initialize_legacy(db);_migrate_multi_family(db);db.commit()
        before={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in ('users','app_state','schema_migrations')}
    assert preflight(old)=='previous';assert migrate_database(old);assert not migrate_database(old)
    with connect(old) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        for t in ('users','app_state'):assert [tuple(r) for r in db.execute(f'SELECT * FROM {t}')]==before[t]


def snapshot(c,fid=None):
    r=c.get('/api/v2/memos/sync',headers=headers(c,fid));assert r.status_code==200,r.text;return r.json()


def test_member_category_move_order_focus_and_creator_deletion(home):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid)
    first=category(a,fid);second=category(a,fid,'第二类');m=memo(a,first,fid)
    state=snapshot(a,fid);focus=state['focus'];order=state['category_order']
    saved(op(b,'focus',focus['id'],'update',{'targets':[{'kind':'memo','id':m['id']}]},focus['version'],fid))
    saved(op(b,'category_order',order['id'],'update',{'category_ids':[second['id'],first['id']]},order['version'],fid))
    renamed=saved(op(b,'category',first['id'],'rename',{'name':'全员可改'},1,fid))
    moved=op(b,'category',first['id'],'delete',{'target_category_id':second['id']},renamed['version'],fid)
    removed=saved(moved);assert removed['deleted']
    effect=moved.json()['receipt']['effects']['memos'][0]
    assert effect['id']==m['id'] and effect['category_id']==second['id'] and effect['author_id']==m['author_id']
    assert snapshot(a,fid)['focus']['targets']==[{'kind':'memo','id':m['id']}]
    archived=saved(op(b,'memo',m['id'],'archive',{'archived':True},effect['version'],fid));assert not snapshot(a,fid)['focused_records']
    reopened=saved(op(b,'memo',m['id'],'archive',{'archived':False},archived['version'],fid));assert len(snapshot(a,fid)['focused_records'])==1
    assert op(b,'memo',m['id'],'delete',{},reopened['version'],fid).status_code==403
    rid=str(uuid4());request=saved(op(b,'deletion_request',rid,'create',{'memo_id':m['id'],'target_version':reopened['version'],'reason':'重复资料'},fid=fid))
    messages=a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages'];message=next(x for x in messages if x['target_id']==rid)
    ignored=a.post('/api/v2/memos/messages/'+message['id']+'/ignore',headers=headers(a,fid),json={});assert ignored.status_code==200,ignored.text
    assert not a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages']
    hidden=a.get('/api/v2/memos/messages?include_ignored=true',headers=headers(a,fid)).json()['messages'];assert hidden[0]['ignored']
    assert snapshot(b,fid)['deletion_requests'][0]['status']=='pending'
    assert a.post('/api/v2/memos/messages/'+message['id']+'/restore',headers=headers(a,fid),json={}).status_code==200
    accepted=saved(op(a,'deletion_request',rid,'decide',{'accepted':True},request['version'],fid));assert accepted['status']=='accepted'
    assert b.get('/api/v2/memos/records/'+m['id'],headers=headers(b,fid)).status_code==404
    assert not snapshot(b,fid)['memos']
    assert not snapshot(a,fid)['focused_records']
    with connect(path) as db:assert db.execute('SELECT deleted FROM memos WHERE id=?',(m['id'],)).fetchone()[0]==1


def test_deletion_request_relationship_change_and_restore_do_not_reauthorize(home,tmp_path):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid);m=memo(a,category(a,fid),fid)
    request=saved(op(b,'deletion_request',str(uuid4()),'create',{'memo_id':m['id'],'target_version':1},fid=fid))
    r=b.post('/api/v2/families/'+fid+'/leave',headers=headers(b,fid),json=management(b,fid));assert r.status_code==200,r.text
    join(a,b,fid)
    assert op(a,'deletion_request',request['id'],'decide',{'accepted':True},1,fid).status_code==409
    another=saved(op(b,'deletion_request',str(uuid4()),'create',{'memo_id':m['id'],'target_version':1},fid=fid))
    saved_backup=backup(path,tmp_path/'source');target=tmp_path/'new.sqlite3';restore(saved_backup,target,saved_backup/'credentials.json')
    with connect(target) as db:
        assert db.execute('SELECT status FROM memo_deletion_requests WHERE id=?',(another['id'],)).fetchone()[0]=='stale'


def test_schedule_half_hour_cross_day_optional_end_and_lunar_dates(home):
    path,app,(a,b,c)=home
    schedule={'start_date':'2026-11-04','end_date':'2026-11-06','start_time':'22:30','end_time':'00:00'}
    sid=str(uuid4());data={'kind':'schedule','category_id':None,'title':'跨日日程','body_markdown':'','attachments':[],'schedule':schedule}
    item=saved(op(a,'memo',sid,'create',data));assert item['kind']=='schedule'
    r=a.get('/api/v2/memos/calendar?from_date=2026-11-04&to_date=2026-11-06',headers=headers(a));assert r.status_code==200,r.text
    assert [x['date'] for x in r.json()['occurrences']]==['2026-11-04','2026-11-05']
    completed=saved(op(a,'memo',sid,'complete',{'completed':True},1))
    assert completed['completed'] and completed['archived'] and completed['version']==2
    assert op(a,'memo',sid,'complete',{'completed':False},1).status_code==409
    reopened=saved(op(a,'memo',sid,'complete',{'completed':False},2))
    assert not reopened['completed'] and not reopened['archived'] and reopened['version']==3
    invalid={**data,'schedule':{**schedule,'start_time':'22:15'}};assert op(a,'memo',str(uuid4()),'create',invalid).status_code==422
    single={**data,'schedule':{**schedule,'end_date':None,'end_time':None}};assert saved(op(a,'memo',str(uuid4()),'create',single))['schedule']['end_date'] is None
    rule={'frequency':'monthly','interval':1,'calendar':'solar','start_date':'2026-01-31','until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}
    assert calendar.dates(rule,date(2026,2,1),date(2026,3,31))==[date(2026,2,28),date(2026,3,31)]
    lunar={**rule,'frequency':'yearly','calendar':'lunar','start_date':'2026-02-17','lunar_month':1,'lunar_day':1}
    assert calendar.dates(lunar,date(2026,1,1),date(2026,12,31))==[date(2026,2,17)]
    leap={**lunar,'start_date':'2023-03-22','lunar_month':2,'lunar_day':30,'lunar_leap':True}
    expected=calendar.Lunar.fromYmd(2024,2,30).getSolar().toYmd()
    assert calendar.dates(leap,date(2024,1,1),date(2024,12,31))[0].isoformat()==expected


def test_multiple_reminders_dedup_fixed_repeat_and_no_duplicate_record(home):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid);cat=category(a,fid)
    now=datetime(2026,2,17,12,tzinfo=calendar.ZONE)
    rule={'frequency':'daily','interval':1,'calendar':'solar','start_date':'2026-02-17','until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}
    reminders=[{'id':str(uuid4()),'mode':'absolute','at':'2026-02-17T09:00:00+08:00','offset_minutes':0,'recipient_ids':[],'all_members':True},{'id':str(uuid4()),'mode':'absolute','at':'2026-02-17T10:00:00+08:00','offset_minutes':0,'recipient_ids':[],'all_members':False}]
    item=saved(op(a,'memo',str(uuid4()),'create',{'category_id':cat['id'],'title':'循环同一条','body_markdown':'','attachments':[],'reminders':reminders,'recurrence':rule},fid=fid))
    assert item['reminders'][1]['recipient_ids']==[item['author_id']]
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');events.generate(db,now);events.generate(db,now);db.commit()
        assert db.execute('SELECT count(*) FROM memo_reminder_events').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM memos').fetchone()[0]==1
        db.execute('BEGIN IMMEDIATE');events.generate(db,now+timedelta(days=1));db.commit()
        assert db.execute('SELECT count(*) FROM memo_reminder_events').fetchone()[0]==6
        assert db.execute('SELECT count(*) FROM memos').fetchone()[0]==1
        db.execute("UPDATE memos SET completed=1,completed_at='2026-02-18T23:00:00+08:00' WHERE id=?",(item['id'],))
        db.execute('BEGIN IMMEDIATE');events.generate(db,now+timedelta(days=2));db.commit()
        assert not db.execute('SELECT completed FROM memos WHERE id=?',(item['id'],)).fetchone()[0]


def test_plan_task_focus_references_original_and_full_module_restore(home,tmp_path):
    path,app,(a,b,c)=home;fid=create(a,'family');join(a,b,fid)
    def plan_op(entity,kind,data,identity=None,version=0,client=a):
        r=client.post('/api/v2/plans/operations',headers=headers(client,fid),json={'op_id':str(uuid4()),'entity_type':entity,'entity_id':identity or str(uuid4()),'kind':kind,'base_version':version,'data':data});return saved(r)
    binary=b'plan and memo attachment';sha=hashlib.sha256(binary).hexdigest()
    pfile={'id':str(uuid4()),'kind':'file','name':'plan.bin','mime_type':'application/octet-stream','size_bytes':len(binary),'sha256':sha}
    wish=plan_op('wish','create',{'title':'旅行愿望','wish_kind':'未来旅行','attachments':[pfile]})
    url=f"/api/v2/plans/records/{wish['id']}/attachments/{pfile['id']}";assert a.put(url,headers={**headers(a,fid),'Content-Type':'application/octet-stream'},content=binary).status_code==200
    plan=plan_op('plan','from_wish',{'title':'旅行计划','source_wish_id':wish['id']})
    stage=plan_op('stage','create',{'plan_id':plan['id'],'title':'准备','position':0})
    task=plan_op('task','create',{'plan_id':plan['id'],'stage_id':stage['id'],'title':'订票','position':0})
    focus=snapshot(a,fid)['focus'];saved(op(b,'focus',focus['id'],'update',{'targets':[{'kind':'plan_task','id':task['id']}]},focus['version'],fid))
    assert snapshot(a,fid)['focused_records'][0]['record']['id']==task['id']
    plan_op('task','complete',{'completed':True},task['id'],task['version'],b)
    assert not snapshot(a,fid)['focused_records'];assert snapshot(a,fid)['focus']['targets'][0]['id']==task['id']
    mfile={**pfile,'id':str(uuid4())};m=memo(a,category(a,fid),fid,[mfile]);murl=f"/api/v2/memos/records/{m['id']}/attachments/{mfile['id']}"
    assert a.put(murl,headers={**headers(a,fid),'Content-Type':'application/octet-stream'},content=binary).status_code==200
    saved_backup=backup(path,tmp_path/'full');target=tmp_path/'full-restored.sqlite3';restore(saved_backup,target,saved_backup/'credentials.json')
    with connect(target) as db:
        assert db.execute('SELECT data FROM plan_attachments WHERE id=?',(pfile['id'],)).fetchone()[0]==binary
        assert db.execute('SELECT data FROM memo_attachments WHERE id=?',(mfile['id'],)).fetchone()[0]==binary
        assert db.execute('SELECT completed FROM plan_tasks WHERE id=?',(task['id'],)).fetchone()[0]==1
        assert json.loads(db.execute('SELECT targets_json FROM memo_focus').fetchone()[0])[0]['id']==task['id']
    # A real frozen B restorer rejects schema9 metadata before making a target.
    frozen=legacy_file('8b88854','server/automation/backups.py')
    scope={};exec(compile(frozen,'frozen-B/backups.py','exec'),scope)
    rejected=tmp_path/'must-not-exist.sqlite3'
    with pytest.raises(ValueError,match='Unsupported backup schema'):scope['restore'](saved_backup,rejected)
    assert not rejected.exists()
    frozen_db=legacy_file('8b88854','server/accounting/db.py')
    older={};exec(compile(frozen_db,'frozen-B/db.py','exec'),older)
    original_sha=hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError,match='unsupported_database_schema'):older['initialize_database'](target)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==original_sha


def test_real_007_to_full_9_preserves_all_original_rows_and_atomic_failure(tmp_path,monkeypatch):
    from server.memos.migration_guard import capture,verify
    from server.accounting import db as database
    frozen=tmp_path/'actual-007';frozen.mkdir()
    copy_legacy('0a56d79',frozen)
    target=tmp_path/'007.sqlite3'
    code='''
from server.accounting.db import initialize_database,create_user,connect
from server.accounting.families import create_explicit_family
from server.api.app import create_app
from fastapi.testclient import TestClient
from uuid import uuid4
from pathlib import Path
import io,sys
from PIL import Image
p=Path(sys.argv[1]);initialize_database(p)
users=[create_user(p,name,'fictional-password',name) for name in ('fiction-a','fiction-b')]
with connect(p) as db:
 db.execute('BEGIN IMMEDIATE');create_explicit_family(db,[u['id'] for u in users],owner_id=users[0]['id']);db.commit()
c=TestClient(create_app(p));c.headers['X-App-Request']='shared-expense'
u=c.post('/api/auth/login',json={'username':'fiction-a','password':'fictional-password'}).json()['user'];c.headers['X-Expected-User']=u['id']
s=c.get('/api/sync').json();c.headers.update({'X-Restore-Id':s['restore_id'],'X-Family-Id':s['family']['id'],'X-Membership-Version':str(s['membership_version'])})
for scope in ('private','shared'):
 eid=str(uuid4());r=c.post('/api/operations',json={'operation_id':str(uuid4()),'expense_id':eid,'base_version':0,'kind':'create','data':{'scope':scope,'amount_cents':12345,'purpose':'frozen fictional record','occurred_at':'2026-11-04T08:00:00+08:00','location':'','category':'资料'}});assert r.status_code==200,r.text
 image=io.BytesIO();Image.new('RGB',(2,2),'green').save(image,format='PNG')
 r=c.put('/api/expenses/'+eid+'/images/'+str(uuid4()),content=image.getvalue(),headers={'Content-Type':'image/png'});assert r.status_code==200,r.text
'''
    r=subprocess.run([sys.executable,'-c',code,str(target)],cwd=frozen,env={**os.environ,'PYTHONPATH':str(frozen)},capture_output=True,text=True);assert r.returncode==0,r.stderr+r.stdout
    with connect(target,read_only=True) as db:
        assert 'rejection_reason' in {r['name'] for r in db.execute('PRAGMA table_info(change_requests)')}
        assert 'rejection_reason' in {r['name'] for r in db.execute('PRAGMA table_info(category_changes)')}
        before=capture(db)
    import shutil
    failpath=tmp_path/'rollback.sqlite3';shutil.copy2(target,failpath)
    actual_migrate=database._migrate_memos
    def fail_after_new_tables(db):actual_migrate(db);raise RuntimeError('injected migration interruption')
    monkeypatch.setattr(database,'_migrate_memos',fail_after_new_tables)
    with pytest.raises(RuntimeError,match='injected'):migrate_database(failpath)
    with connect(failpath,read_only=True) as db:
        verify(db,before);assert db.execute('PRAGMA user_version').fetchone()[0]==0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='memos'").fetchone()
    monkeypatch.setattr(database,'_migrate_memos',actual_migrate)
    evidence=tmp_path/'migration.json'
    r=subprocess.run([sys.executable,'-m','scripts.migrate_memos','--database',str(target),'--evidence',str(evidence)],capture_output=True,text=True);assert r.returncode==0,r.stderr
    assert json.loads(evidence.read_text())['original_rows_preserved']
    assert not migrate_database(target)
    with connect(target,read_only=True) as db:
        verify(db,before);assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert {r[0] for r in db.execute('SELECT name FROM schema_migrations')}=={'families-v1','family-owners-v1','multi-family-v2','memos-v1','plans-v1','memo-archive-v1','ai-v2'}


def test_home_messages_scope_invite_financial_pending_and_ignore_preserves_business(home):
    from server.tests.test_api import operation as expense_operation
    from server.tests.test_multi_family import submit as ledger_submit
    path,app,(a,b,c)=home;fid=create(b,'受邀家');own=create(a,'另一家')
    username=a.get('/api/auth/me').json()['user']['username']
    invited=b.post('/api/v2/family-invites',headers=headers(b,fid),json={'request_id':str(uuid4()),'username':username});assert invited.status_code==200,invited.text
    invitation=invited.json()['id'];event='family-invite:'+invitation
    private=a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages'];assert any(x['id']==event and x['source']['scope']=='private' for x in private)
    assert not a.get('/api/v2/memos/messages',headers=headers(a,own)).json()['messages']
    assert a.post('/api/v2/memos/messages/'+event+'/ignore',headers=headers(a),json={}).status_code==200
    with connect(path) as db:assert db.execute('SELECT status FROM family_invites WHERE id=?',(invitation,)).fetchone()[0]=='pending'
    assert a.post('/api/v2/memos/messages/'+event+'/restore',headers=headers(a),json={}).status_code==200
    assert a.post('/api/v2/family-invites/'+invitation+'/accept',json={}).status_code==200
    assert not a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages']
    expense=ledger_submit(a,fid,expense_operation(category='餐饮'))
    rid=str(uuid4());r=b.post('/api/v2/change-requests',headers=headers(b,fid),json={'id':rid,'expense_id':expense['id'],'base_version':1,'kind':'update','data':{key:expense[key] for key in ('amount_cents','purpose','occurred_at','location')},'reason':'测试'});assert r.status_code==200,r.text
    pending=a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages'];assert any(x['id']=='expense-change:'+rid for x in pending)
    assert not a.get('/api/v2/memos/messages',headers=headers(a,own)).json()['messages']
    assert a.post('/api/v2/memos/messages/expense-change:'+rid+'/ignore',headers=headers(a,fid),json={}).status_code==200
    assert a.get('/api/v2/change-requests',headers=headers(a,fid)).json()['requests'][0]['status']=='pending'
    response=a.post('/api/v2/change-requests/'+rid+'/decision',headers=headers(a,fid),json={'decision_id':str(uuid4()),'decision':'reject'});assert response.status_code==200,response.text
    assert not any(x['target_id']==rid for x in a.get('/api/v2/memos/messages?include_ignored=true',headers=headers(a,fid)).json()['messages'])
    preview=b.post('/api/v2/category-changes/preview',headers=headers(b,fid),json={'kind':'rename','scope':'shared','source_name':'餐饮','target_name':'餐食'});assert preview.status_code==200,preview.text
    change=str(uuid4());sent=b.post('/api/v2/category-changes',headers=headers(b,fid),json={'id':change,'preview_id':preview.json()['preview_id']});assert sent.status_code==200,sent.text
    msgs=a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages'];assert any(x['kind']=='expense_category_change' and x['target_id']==change for x in msgs)
