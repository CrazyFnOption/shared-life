from datetime import datetime, timedelta, UTC
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from server.accounting.db import create_user, connect
from server.accounting.families import create_explicit_family
from server.api.app import create_app
from server.automation.worker import Worker, validate, groups
from server.automation.providers import ProviderError, PushError, HTTPProvider
from server.automation.backups import backup, restore
from server.tests.test_api import operation, submit, update

class Model:
    name='test-primary'; model='injected-only'
    def __init__(self, errors=()):
        self.calls=[];self.errors=list(errors);self.hook=None
    def call(self,payload):
        self.calls.append(payload)
        if self.hook:
            hook,self.hook=self.hook,None;hook()
        if self.errors:
            raise ProviderError(self.errors.pop(0))
        return {'items':[{'expense_id':e['id'],'analysis':'无评价记录，按一般理解。'} for e in payload['expenses']]} if payload['kind']=='items' else {'summary':'这是全部记录的定性复盘。'}

class Push:
    def __init__(self,error=None): self.calls=[];self.error=error
    def send(self,sub,payload):
        self.calls.append((sub,payload))
        if self.error: raise PushError(self.error)

@pytest.fixture
def setup(tmp_path):
    path=tmp_path/'db.sqlite3'
    a=create_user(path,'alice','fictional-a','甲');b=create_user(path,'bob','fictional-b','乙')
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        fid=create_explicit_family(db,[a["id"],b["id"]],"测试家庭")
        db.commit()
    clock=[datetime(2026,10,1,tzinfo=UTC)]
    model=Model();pusher=Push()
    worker=Worker(path,primary=model,push=pusher,clock=lambda:clock[0],call_limit=100)
    app=create_app(path,worker=worker)
    with TestClient(app) as client:
        assert client.post('/api/auth/login',json={'username':'alice','password':'fictional-a'},headers={'X-App-Request':'shared-expense'}).status_code==200
        client.headers.update({'X-App-Request':'shared-expense','X-Expected-User':a['id'],'X-Family-Id':fid,'X-Membership-Version':'1'})
        yield path,client,worker,model,clock,a,b,pusher

def enable(client):
    for scope in ('shared','private'):
        assert client.put('/api/settings?scope='+scope,json={'monthly':{'enabled':True,'time':'20:00'}}).status_code==200

def seed(client,scope='shared',**kwargs):
    return submit(client,operation(scope,occurred_at='2026-09-15T12:00:00+08:00',**kwargs))

def finish(worker,clock):
    clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    for _ in range(3): worker.tick()

def test_schedule_snapshot_isolation_and_no_current_month(setup):
    path,c,w,m,clock,a,b,p=setup
    shared=seed(c);private=seed(c,'private',purpose='隐私测试');submit(c,operation(purpose='当月不可提前'))
    enable(c);w.tick();assert not m.calls
    finish(w,clock)
    for _ in range(3): w.tick()
    reports=c.get('/api/reports').json()['reports'];assert len(reports)==1
    detail=c.get('/api/reports/'+reports[0]['id']).json()
    assert detail['status']=='ready' and detail['summary']['total_cents']==1234
    assert detail['expenses'][0]['rating'] == 3
    itemcalls=[x for x in m.calls if x['kind']=='items']
    assert {tuple(e['id'] for e in x['expenses']) for x in itemcalls}=={(shared['id'],),(private['id'],)}

def test_change_during_generation_discards_old_result_and_revokes(setup):
    path,c,w,m,clock,a,b,p=setup
    expense=seed(c);enable(c)
    m.hook=lambda:submit(c,update(expense,purpose='最新目的'))
    finish(w,clock)
    row=c.get('/api/reports').json()['reports'][0]
    assert row['status']=='stale' and row['content'] is None
    clock[0]=datetime(2026,12,10,tzinfo=UTC)
    for _ in range(3): w.tick()
    rid=row['id'];assert c.get('/api/reports/'+rid).json()['status']=='ready'
    current=c.get('/api/expenses/'+expense['id']).json()
    body={'operation_id':str(uuid4()),'kind':'scope','expense_id':expense['id'],'base_version':current['version'],'data':{'scope':'private'}}
    response=c.post('/api/operations',json=body)
    assert response.status_code==200,response.text
    detail=c.get('/api/reports/'+rid).json();assert detail['content'] is None and detail['expenses']==[]

def test_primary_twice_backup_once_and_budget_persists(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c);enable(c);m.errors=['transient','transient'];fallback=Model();fallback.name='test-backup';w.backup=fallback
    clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    w.tick();clock[0]+=timedelta(seconds=31);w.tick();w.tick()
    assert len(m.calls)==2 and len(fallback.calls)==1
    w.call_limit=3;w.tick();assert len(m.calls)==2
    with connect(path) as db: assert db.execute('SELECT sum(calls) FROM model_usage').fetchone()[0]==3
    restarted=Worker(path,primary=m,clock=lambda:clock[0],call_limit=3);restarted.tick();assert len(m.calls)==2

def test_invalid_item_ids_rejected(setup):
    payload={'kind':'items','expenses':[{'id':'x'}]}
    for output in ({'items':[]},{'items':[{'expense_id':'x','analysis':'x'},{'expense_id':'x','analysis':'x'}]},{'items':[{'expense_id':'wrong','analysis':'x'}]},{'items':[{'expense_id':'x','analysis':'x','net_amount':2}]}):
        with pytest.raises(ProviderError):validate(payload,output)

def test_bounded_batches_cover_all_items():
    items=[{'id':str(i),'purpose':'长文本'*500} for i in range(110)]
    chunks=groups(items,'expenses')
    assert sum(map(len,chunks))==110 and all(len(c)<=50 for c in chunks)
    assert all(len(json.dumps({'expenses':c},ensure_ascii=False,separators=(',',':')).encode())<=24000 for c in chunks)

def test_push_dedup_and_expired(setup):
    path,c,w,m,clock,a,b,p=setup
    pending=seed(c);enable(c)
    # Legacy unrated record keeps the weekly reminder/expiry path meaningful.
    with connect(path) as db:
        db.execute('UPDATE expenses SET rating=NULL WHERE id=?',(pending['id'],))
    endpoint='https://fcm.googleapis.com/fake-test'
    assert c.post('/api/push-subscriptions',json={'endpoint':endpoint,'keys':{'p256dh':'abc','auth':'def'}}).status_code==200
    finish(w,clock);w.tick();assert len(p.calls)==1
    assert p.calls[0][1]=={'title':'同在','body':'你有待办，请打开同在','url':'/'}
    assert c.put('/api/settings',json={'weekly':{'enabled':True,'weekday':6,'time':'19:00'}}).status_code==200
    p.error=410;clock[0]+=timedelta(seconds=301);w.tick()
    assert c.get('/api/settings').json()['push_subscription_state']=='expired'

def test_push_unknown_delivery_never_retried(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c);enable(c)
    c.post('/api/push-subscriptions',json={'endpoint':'https://fcm.googleapis.com/fake-test','keys':{'p256dh':'abc','auth':'def'}})
    def uncertain(sub,payload): p.calls.append((sub,payload));raise OSError('unknown network outcome')
    p.send=uncertain;finish(w,clock);clock[0]+=timedelta(hours=1);w.tick()
    assert len(p.calls)==1 and c.get('/api/settings').json()['notification_uncertain']==1

def test_backup_credentials_scrub_restore_and_old_header_rejection(setup,tmp_path):
    path,c,w,m,clock,a,b,p=setup
    seed(c);original=c.get('/api/sync').json()['restore_id']
    with connect(path) as db:
        hashes=[r[0] for r in db.execute('SELECT password_hash FROM users')]
        token=db.execute('SELECT token_hash FROM sessions').fetchone()[0]
    destination=backup(path,tmp_path/'snapshot')
    raw=(destination/'business.sqlite3').read_bytes()
    assert all(h.encode() not in raw for h in hashes) and token.encode() not in raw
    assert (destination.stat().st_mode&0o777)==0o700
    restored=tmp_path/'restored.sqlite3'
    result=restore(destination,restored,destination/'credentials.json')
    assert result['restore_id']!=original and result['automation_paused']
    with TestClient(create_app(restored)) as fresh:
        fresh.headers.update({'X-App-Request':'shared-expense'})
        assert fresh.post('/api/auth/login',json={'username':'alice','password':'fictional-a'}).status_code==200
        fresh.headers['X-Expected-User']=a['id']
        assert fresh.post('/api/operations',json=operation()).json()['detail']['code']=='restore_changed'
        fresh.headers['X-Restore-Id']=original
        assert fresh.post('/api/operations',json=operation()).status_code==409
        fresh.headers['X-Restore-Id']=result['restore_id']
        family=fresh.get('/api/sync').json()
        fresh.headers.update({'X-Family-Id':family['family']['id'],'X-Membership-Version':str(family['membership_version'])})
        assert fresh.post('/api/operations',json=operation()).status_code==200
    disabled=tmp_path/'disabled.sqlite3';restore(destination,disabled)
    with TestClient(create_app(disabled)) as fresh:
        assert fresh.post('/api/auth/login',json={'username':'alice','password':'fictional-a'},headers={'X-App-Request':'shared-expense'}).status_code==401

def test_restore_rejects_corruption_and_existing_destination(setup,tmp_path):
    path,c,w,m,clock,a,b,p=setup
    directory=backup(path,tmp_path/'backup')
    with pytest.raises(ValueError):restore(directory,path)
    with open(directory/'business.sqlite3','ab') as stream:stream.write(b'corruption')
    with pytest.raises(ValueError):restore(directory,tmp_path/'bad.sqlite3')
    assert not (tmp_path/'bad.sqlite3').exists()

def test_export_formula_and_filter(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c,purpose='=1+2');seed(c,'private',purpose='@private')
    exported=c.get('/api/export?format=json&scope=shared').json()
    assert len(exported['expenses'])==1 and not exported['expenses'][0]['refunds']
    response=c.get('/api/export?format=csv&scope=all')
    assert response.content.startswith(b'\xef\xbb\xbf') and "'=1+2" in response.text and "'@private" in response.text
    assert response.headers['cache-control']=='no-store'

@pytest.mark.parametrize('name',['openai'])
def test_standard_http_provider_request_and_parser(name):
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):
            output='{"summary":"全部定性分析"}'
            return json.dumps({'status':'completed','output':[{'content':[{'type':'output_text','text':output}]}]} if name=='openai' else {'choices':[{'finish_reason':'stop','message':{'content':output}}]}).encode()
    captured=[]
    def transport(request,timeout):captured.append((request,timeout));return Reply()
    with patch('server.automation.providers.urlopen',transport):
        assert HTTPProvider(name,'gpt-6.1-sol','mock-key').call({'kind':'summary','analyses':['a']})=={'summary':'全部定性分析'}
    request,timeout=captured[0];body=json.loads(request.data)
    assert timeout==60 and body['model']=='gpt-6.1-sol'
    assert body['reasoning']=={'effort':'high'} and body['service_tier']=='default'
    assert body['text']['format']['type']=='json_schema' and 'tools' not in body
    assert request.full_url==('https://api.openai.com/v1/responses' if name=='openai' else 'https://api.deepseek.com/chat/completions')
    assert ('text' in body) if name=='openai' else body['response_format']=={'type':'json_object'}

def test_separated_credentials_backup_restores_disabled_or_explicit(setup,tmp_path):
    path,c,w,m,clock,a,b,p=setup
    directory=backup(path,tmp_path/'split-backup')
    credential=tmp_path/'protected-credentials.json'
    (directory/'credentials.json').rename(credential)
    assert not restore(directory,tmp_path/'disabled.sqlite3')['login_enabled']
    assert restore(directory,tmp_path/'enabled.sqlite3',credential)['login_enabled']
    credential.write_text('{}')
    with pytest.raises(ValueError):restore(directory,tmp_path/'invalid.sqlite3',credential)

def test_competing_worker_does_not_duplicate_claim(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c);enable(c);clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    other=Model();second=Worker(path,primary=other,clock=lambda:clock[0],call_limit=100)
    m.hook=second.tick
    w.tick();assert len(m.calls)==1 and not other.calls
    second.tick();assert len(other.calls)==1
    assert c.get('/api/reports').json()['reports'][0]['status']=='ready'

def test_crash_claim_recovery_charges_call_and_uses_bounded_fallback(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c);enable(c);clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    class SimulatedCrash(BaseException):pass
    def crash(payload):m.calls.append(payload);raise SimulatedCrash()
    m.call=crash
    for _ in range(2):
        with pytest.raises(SimulatedCrash):w.tick()
        clock[0]+=timedelta(seconds=121)
    fallback=Model();fallback.name='backup';w.backup=fallback
    w.tick();assert len(m.calls)==2 and len(fallback.calls)==1
    with connect(path) as db:assert db.execute('SELECT sum(calls) FROM model_usage').fetchone()[0]==3

def test_double_failure_only_three_rounds_without_manual_retry(setup):
    path,c,w,m,clock,a,b,p=setup
    seed(c);enable(c);clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    m.errors=['unavailable']*20
    fallback=Model(['unavailable']*20);fallback.name='backup';w.backup=fallback
    for _ in range(3):
        w.tick();w.tick();clock[0]+=timedelta(seconds=3601)
    assert len(m.calls)==3 and len(fallback.calls)==3
    clock[0]+=timedelta(days=1);w.tick();assert len(m.calls)==3
    row=c.get('/api/reports').json()['reports'][0];assert row['status']=='failed'
    assert c.post('/api/reports/'+row['id']+'/retry',json={}).status_code==200
    w.tick();assert len(m.calls)==4

@pytest.mark.parametrize(('code','kind'),[
    ('insufficient_quota','unavailable'),
    ('credit_balance_exhausted','unavailable'),
    ('rate_limit_error','transient'),
])
def test_standard_http_quota_error_is_classified_without_body_leak(code,kind):
    import io
    import traceback
    from urllib.error import HTTPError
    body=json.dumps({'error':{'code':code,'message':'private mock body'}}).encode()
    failure=HTTPError('https://api.openai.com/v1/responses',429,'rate',{},io.BytesIO(body))
    with patch('server.automation.providers.urlopen',side_effect=failure):
        with pytest.raises(ProviderError) as caught:
            HTTPProvider('openai','gpt-6.1-sol','mock-key').call({'kind':'summary','analyses':[]})
    assert caught.value.kind==kind
    assert str(caught.value)==kind
    assert 'private mock body' not in ''.join(traceback.format_exception(caught.value))
    assert caught.value.__suppress_context__ and caught.value.__cause__ is None

def test_sync_records_per_user_last_connection_not_upload_completeness(setup):
    path,c,w,m,clock,a,b,p=setup
    response=c.get('/api/sync');assert response.status_code==200
    users={u['id']:u for u in response.json()['users']}
    assert users[a['id']]['last_synced_at']==clock[0].isoformat()
    assert users[b['id']]['last_synced_at'] is None
    clock[0]+=timedelta(minutes=5)
    response=c.get('/api/sync');users={u['id']:u for u in response.json()['users']}
    assert users[a['id']]['last_synced_at']==clock[0].isoformat()
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM user_sync').fetchone()[0]==1

def test_many_long_refunds_keep_model_input_bounded_and_report_detail_complete(setup):
    path,c,w,m,clock,a,b,p=setup
    expense=seed(c,amount_cents=2000)
    for _ in range(16):
        expense=submit(c,{'operation_id':str(uuid4()),'kind':'refund_create','expense_id':expense['id'],
            'base_version':expense['version'],'data':{'refund_id':str(uuid4()),'amount_cents':10,
            'occurred_at':'2026-10-01T12:00:00+08:00','reason':'测'*500}})
    enable(c);finish(w,clock)
    report=c.get('/api/reports').json()['reports'][0]
    assert report['status']=='ready'
    payload=next(call for call in m.calls if call['kind']=='items')
    item=payload['expenses'][0]
    assert set(item)=={'id','owner_id','purpose','occurred_at','category','rating','rating_reason','amount_cents','refund_total_cents','net_amount_cents'}
    assert (item['amount_cents'],item['refund_total_cents'],item['net_amount_cents'])==(2000,160,1840)
    assert len(json.dumps(payload,ensure_ascii=False).encode())<24000
    detail=c.get('/api/reports/'+report['id']).json()
    assert len(detail['expenses'][0]['refunds'])==16


def test_preparation_failure_parks_one_task_and_allows_notifications_and_other_report(setup):
    from server.automation.worker import model_expense
    path,c,w,m,clock,a,b,p=setup
    shared=seed(c);seed(c,'private');enable(c)
    c.put('/api/settings',json={'weekly':{'enabled':True,'weekday':6,'time':'19:00'}})
    c.post('/api/push-subscriptions',json={'endpoint':'https://fcm.googleapis.com/prep-test','keys':{'p256dh':'abc','auth':'def'}})
    clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    def broken_one(expense):
        if expense['id']==shared['id']:raise ProviderError('invalid')
        return model_expense(expense)
    with patch('server.automation.worker.model_expense',broken_one):
        for _ in range(5):w.tick()
    assert c.get('/api/reports').json()['reports'][0]['status']=='failed'
    assert c.get('/api/reports?scope=private').json()['reports'][0]['status']=='ready'
    assert p.calls and all('private' not in json.dumps(payload) for _,payload in p.calls)
    assert len(m.calls)==2
    with connect(path) as db:
        task=db.execute('SELECT * FROM report_tasks').fetchone()
        assert task['next_attempt']==1e30 and task['claim_token'] is None


def test_restored_paused_database_never_auto_backups_but_manual_backup_works(setup,tmp_path,monkeypatch):
    path,c,w,m,clock,a,b,p=setup
    source=backup(path,tmp_path/'source')
    restored=tmp_path/'restored-pause.sqlite3'
    restore(source,restored,source/'credentials.json')
    automatic=tmp_path/'automatic';automatic.mkdir()
    monkeypatch.setenv('SHARED_EXPENSE_BACKUP_DIR',str(automatic))
    worker=Worker(restored,primary=m,push=p,clock=lambda:clock[0],call_limit=100)
    worker.tick();worker._backup(clock[0])
    assert list(automatic.iterdir())==[]
    with connect(restored) as db:assert db.execute('SELECT count(*) FROM backup_runs').fetchone()[0]==0
    assert not m.calls and not p.calls
    assert (backup(restored,tmp_path/'manual')/'manifest.json').is_file()
