"""Fictional recovery correlation and safe diagnostics; no network or real records."""
import io
import json
from urllib.error import HTTPError,URLError
from uuid import uuid4
import pytest
from server.accounting.db import connect
from server.plans.test_service import world,bind,operation,content
from server.automation.providers import HTTPProvider,ProviderError,ProviderResult
from server.ai.models import Message
from server.ai import planning
from server.tests.test_automation import setup,seed,enable,Model


def uid():return str(uuid4())

def test_http_read_handles_content_length_completion_and_closed_socket():
    from http.server import BaseHTTPRequestHandler,HTTPServer
    import threading,time
    from urllib.request import urlopen
    from server.automation.providers import read_response
    class Reply(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Reply)
    thread=threading.Thread(target=server.handle_request);thread.start()
    try:
        with urlopen('http://127.0.0.1:'+str(server.server_port),timeout=3) as response:
            assert read_response(response,time.monotonic())==b'{}'
    finally:thread.join(timeout=3);server.server_close()

def test_http_read_uses_remaining_total_deadline(monkeypatch):
    from types import SimpleNamespace
    from server.automation.providers import read_response
    clock=[50.0];timeouts=[]
    class Reply:
        fp=SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))
        def read1(self,size):clock[0]+=100;return b'x'
    monkeypatch.setattr('server.automation.providers.time.monotonic',lambda:clock[0])
    with pytest.raises(TimeoutError):read_response(Reply(),0.0)
    assert timeouts==[250,150,50]

def test_240_second_request_is_still_running_and_its_reservation_remains_live(world):
    from datetime import datetime,UTC,timedelta
    from server.ai import budget
    path,(a,_,_),fid=world
    class Slow:
        call_limit=100
        def call(self,payload):
            with connect(path) as other:
                bind(other,a,fid)
                stamp=(datetime.now(UTC)-timedelta(seconds=240)).isoformat()
                other.execute('UPDATE ai_turns SET created_at=?',(stamp,))
                other.execute('UPDATE ai_costs SET created_at=?',(stamp,))
                result=planning.read(other,a,wish['id'],self)
                assert result['messages'][0]['status']=='running' and result['messages'][0]['error'] is None
                other.execute('BEGIN IMMEDIATE');budget.reserve(other,'monthly');other.commit()
                assert other.execute('SELECT status FROM ai_costs ORDER BY created_at LIMIT 1').fetchone()[0]=='reserved'
            return ProviderResult({'message':'虚构慢响应仍可保存','items':[]},{'input_tokens':10,'output_tokens':10,'web_search_calls':0})
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content())['receipt']['record']
        result=planning.send(db,a,wish['id'],Message(op_id=uid(),content='虚构慢请求',base_version=1),Slow(),path)
        assert result['status']=='ready' and db.execute("SELECT count(*) FROM ai_costs WHERE status='settled'").fetchone()[0]==1

def test_monthly_live_claim_is_not_reclaimed_at_240_seconds(setup):
    from datetime import datetime,UTC,timedelta
    from server.automation.worker import Worker
    path,client,_,_,clock,*_=setup
    class Metered(Model):
        metered=True
        def call(self,payload):return ProviderResult(super().call(payload),{'input_tokens':10,'output_tokens':10,'web_search_calls':0})
    first=Metered();second=Metered();seed(client);enable(client)
    clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    other=Worker(path,primary=second,clock=lambda:clock[0],call_limit=100)
    def concurrent_tick():
        clock[0]+=timedelta(seconds=240);other.tick()
        with connect(path) as db:
            assert db.execute('SELECT status FROM ai_costs').fetchone()[0]=='reserved'
    first.hook=concurrent_tick
    Worker(path,primary=first,clock=lambda:clock[0],call_limit=100).tick()
    assert len(first.calls)==1 and not second.calls
    with connect(path) as db:assert db.execute('SELECT status FROM ai_costs').fetchone()[0]=='settled'

@pytest.mark.parametrize('failure,kind,reason,status',[
    (TimeoutError('PRIVATE_DIAGNOSTIC_MARKER'),'transient','timeout',None),
    (URLError(TimeoutError('PRIVATE_DIAGNOSTIC_MARKER')),'transient','timeout',None),
    (URLError(OSError('PRIVATE_DIAGNOSTIC_MARKER')),'transient','connection',None),
    (HTTPError('https://example.com/',503,'PRIVATE_DIAGNOSTIC_MARKER',{'x-request-id':'req_test'},None),'transient','http_status',503),
    (HTTPError('https://example.com/',401,'PRIVATE_DIAGNOSTIC_MARKER',{'x-request-id':'req_test'},None),'unavailable','http_status',401),
])
def test_transport_diagnostic_preserves_kind_without_exception_text(monkeypatch,failure,kind,reason,status):
    def transport(request,timeout):
        assert timeout==300
        raise failure
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    with pytest.raises(ProviderError) as caught:HTTPProvider('openai','gpt-6.1-sol','fictional').call({'kind':'planning','content':'虚构城市五日游','allow_web':True})
    error=caught.value
    assert error.kind==kind and error.diagnostics['reason']==reason and error.diagnostics['http_status']==status
    assert type(error.diagnostics['elapsed_ms'])==int and error.diagnostics['elapsed_ms']>=0
    assert 'PRIVATE_DIAGNOSTIC_MARKER' not in json.dumps(error.diagnostics) and str(error)==kind


def test_timeout_saved_once_get_exposes_only_authors_op_id_and_redacted_log(world,monkeypatch,caplog):
    path,(a,b,_),fid=world;monkeypatch.setenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','100');calls=[]
    def transport(request,timeout):calls.append(timeout);raise TimeoutError('PRIVATE_DIAGNOSTIC_MARKER')
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    provider=HTTPProvider('openai','gpt-6.1-sol','fictional-key')
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content('虚构海边旅行'))['receipt']['record']
        body=Message(op_id=uid(),content='虚构五天四晚，请仅提草案',base_version=1,allow_web=True)
        result=planning.send(db,a,wish['id'],body,provider,path)
        message=result['messages'][0]
        assert result['status']=='failed' and message['error']=='ai_transient' and message['op_id']==str(body.op_id)
        cost=db.execute('SELECT status,reserved_micro_usd,actual_micro_usd FROM ai_costs').fetchone()
        assert tuple(cost)==('unknown',5450000,None)
        assert result['budget']['unknown_micro_usd']==5450000 and result['budget']['has_unknown']
        assert not result['budget']['blocked']
        assert planning.send(db,a,wish['id'],body,provider,path)==result and calls==[300]
        bind(db,b,fid);other=planning.read(db,b,wish['id'],provider)
        assert 'op_id' not in other['messages'][0]
        assert other['messages'][0]['status']=='failed' and calls==[300]
    text=caplog.text
    assert '"reason":"timeout"' in text and '"elapsed_ms":' in text
    assert 'PRIVATE_DIAGNOSTIC_MARKER' not in text and '虚构五天四晚' not in text and 'fictional-key' not in text


def test_successful_get_correlates_current_author_and_leaves_assistant_unlabeled(world):
    path,(a,b,_),fid=world
    class Fake:
        call_limit=100
        def call(self,payload):return ProviderResult({'message':'虚构可行建议','items':[]},{'input_tokens':10,'output_tokens':10,'web_search_calls':0})
    with connect(path) as db:
        bind(db,a,fid);wish=operation(db,a,'wish','create',content())['receipt']['record'];body=Message(op_id=uid(),content='虚构规划',base_version=1)
        result=planning.send(db,a,wish['id'],body,Fake(),path)
        assert result['messages'][0]['op_id']==str(body.op_id) and 'op_id' not in result['messages'][1]
        bind(db,b,fid);result=planning.read(db,b,wish['id'],Fake())
        assert all('op_id' not in message for message in result['messages'])


def test_unknown_reservation_is_kept_and_new_requests_stop_at_remaining_cap(tmp_path):
    from server.accounting.db import initialize_database
    from server.ai import budget
    path=tmp_path/'unknown-budget.sqlite3';initialize_database(path)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');first=budget.reserve(db,'planning',web=True);budget.unknown(db,first);db.commit()
        original=dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(first,)).fetchone())
        allowed=0
        while True:
            db.execute('BEGIN IMMEDIATE')
            try:budget.reserve(db,'planning',web=True)
            except budget.BudgetError as error:
                db.rollback();assert error.code=='ai_budget_exceeded';break
            db.commit();allowed+=1
        state=budget.snapshot(db)
        assert allowed==8 and state['committed_micro_usd']==49_050_000 and state['remaining_micro_usd']==950_000
        assert state['unknown_micro_usd']==5_450_000 and state['reserved_micro_usd']==43_600_000
        assert state['confirmed_micro_usd']==0 and state['has_unknown']
        assert dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(first,)).fetchone())==original


@pytest.mark.parametrize('outcome',['timeout','crashed_claim'])
def test_unknown_monthly_step_stops_automatic_replay_but_other_reports_and_manual_retry_work(setup,outcome):
    from datetime import datetime,UTC,timedelta
    from server.automation.worker import Worker,UNKNOWN_STEP_ERROR
    from server.ai import budget
    path,client,_,_,clock,*_=setup
    class Crash(BaseException):pass
    class Metered(Model):
        metered=True
        key='fictional-before'
        def call(self,payload):
            result=super().call(payload)
            if len(self.calls)==2:
                if outcome=='crashed_claim':raise Crash()
                raise ProviderError('transient')
            return ProviderResult(result,{'input_tokens':10,'output_tokens':10,'web_search_calls':0})
    model=Metered();seed(client);enable(client)
    clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    worker=Worker(path,primary=model,clock=lambda:clock[0],call_limit=100)
    worker.tick()  # Keep the completed item analysis before the uncertain summary step.
    with connect(path) as db:progress=db.execute('SELECT progress_json FROM report_tasks').fetchone()[0]
    if outcome=='crashed_claim':
        with pytest.raises(Crash):worker.tick()
        clock[0]+=timedelta(seconds=361);worker.tick()
    else:worker.tick()
    with connect(path) as db:
        report=dict(db.execute('SELECT * FROM reports').fetchone());rid=report['id']
        task=dict(db.execute('SELECT * FROM report_tasks').fetchone())
        original=dict(db.execute("SELECT * FROM ai_costs WHERE status='unknown'").fetchone())
        assert report['error']==UNKNOWN_STEP_ERROR and report['status']=='failed'
        expected_progress=dict(json.loads(progress),cost_status='unknown')
        assert json.loads(task['progress_json'])==expected_progress and task['next_attempt']==1e30 and task['claim_token'] is None
    # Config/key changes must not revive this same paid step or erase its progress.
    model.key='fictional-after';worker=Worker(path,primary=model,clock=lambda:clock[0],call_limit=100)
    for _ in range(3):clock[0]+=timedelta(hours=1);worker.tick()
    assert len(model.calls)==2
    seed(client,'private',purpose='另一份虚构月报')
    worker.tick();worker.tick()
    assert len(model.calls)==4
    with connect(path) as db:
        assert db.execute("SELECT status FROM reports WHERE scope='private'").fetchone()[0]=='ready'
        current=dict(db.execute('SELECT * FROM report_tasks WHERE report_id=?',(rid,)).fetchone())
        assert json.loads(current['progress_json'])==expected_progress and current['target_version']==task['target_version']
        assert dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(original['id'],)).fetchone())==original
        assert budget.snapshot(db,clock[0])['unknown_micro_usd']==original['reserved_micro_usd']
    assert client.post('/api/reports/'+rid+'/retry',json={}).status_code==200
    worker.tick();worker.tick()
    assert len(model.calls)==6
    with connect(path) as db:
        assert db.execute('SELECT status FROM reports WHERE id=?',(rid,)).fetchone()[0]=='ready'
        assert dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(original['id'],)).fetchone())==original


@pytest.mark.parametrize('unknown_step',['items','summary'])
def test_valid_monthly_output_survives_unknown_cost_without_automatic_paid_continuation(setup,unknown_step):
    from datetime import datetime,UTC,timedelta
    from server.automation.worker import Worker
    path,client,_,_,clock,*_=setup
    class Metered(Model):
        metered=True
        def call(self,payload):
            output=super().call(payload)
            usage=None if payload['kind']==unknown_step else {'input_tokens':10,'output_tokens':10,'web_search_calls':0}
            return ProviderResult(output,usage,diagnostics={'http_status':200,'request_id':'req_monthly','response_id':'resp_monthly'})
    model=Metered();seed(client);enable(client);clock[0]=datetime(2026,10,4,13,tzinfo=UTC)
    worker=Worker(path,primary=model,clock=lambda:clock[0],call_limit=100)
    worker.tick()
    if unknown_step=='summary':worker.tick()
    report=client.get('/api/reports').json()['reports'][0]
    detail=client.get('/api/reports/'+report['id']).json()
    assert detail['cost_status']=='unknown'
    if unknown_step=='summary':
        assert detail['status']=='ready' and detail['error'] is None
        assert detail['content']['summary']=='这是全部记录的定性复盘。' and len(detail['content']['items'])==1
    else:
        assert detail['status']=='failed'
        with connect(path) as db:
            progress=json.loads(db.execute('SELECT progress_json FROM report_tasks').fetchone()[0])
            assert progress['index']==1 and len(progress['items'])==1 and progress['cost_status']=='unknown'
    before=len(model.calls)
    for _ in range(3):clock[0]+=timedelta(hours=1);worker.tick()
    assert len(model.calls)==before
    with connect(path) as db:
        cost=db.execute("SELECT reserved_micro_usd,actual_micro_usd FROM ai_costs WHERE status='unknown'").fetchone()
        assert tuple(cost)==(5430000,None)
