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

@pytest.mark.parametrize('failure,kind,reason,status',[
    (TimeoutError('PRIVATE_DIAGNOSTIC_MARKER'),'transient','timeout',None),
    (URLError(TimeoutError('PRIVATE_DIAGNOSTIC_MARKER')),'transient','timeout',None),
    (URLError(OSError('PRIVATE_DIAGNOSTIC_MARKER')),'transient','connection',None),
    (HTTPError('https://example.com/',503,'PRIVATE_DIAGNOSTIC_MARKER',{'x-request-id':'req_test'},None),'transient','http_status',503),
    (HTTPError('https://example.com/',401,'PRIVATE_DIAGNOSTIC_MARKER',{'x-request-id':'req_test'},None),'unavailable','http_status',401),
])
def test_transport_diagnostic_preserves_kind_without_exception_text(monkeypatch,failure,kind,reason,status):
    def transport(request,timeout):
        assert timeout==60
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
        assert planning.send(db,a,wish['id'],body,provider,path)==result and calls==[60]
        bind(db,b,fid);other=planning.read(db,b,wish['id'],provider)
        assert 'op_id' not in other['messages'][0]
        assert other['messages'][0]['status']=='failed' and calls==[60]
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
        clock[0]+=timedelta(seconds=181);worker.tick()
    else:worker.tick()
    with connect(path) as db:
        report=dict(db.execute('SELECT * FROM reports').fetchone());rid=report['id']
        task=dict(db.execute('SELECT * FROM report_tasks').fetchone())
        original=dict(db.execute("SELECT * FROM ai_costs WHERE status='unknown'").fetchone())
        assert report['error']==UNKNOWN_STEP_ERROR and report['status']=='failed'
        assert task['progress_json']==progress and task['next_attempt']==1e30 and task['claim_token'] is None
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
        assert current['progress_json']==progress and current['target_version']==task['target_version']
        assert dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(original['id'],)).fetchone())==original
        assert budget.snapshot(db,clock[0])['unknown_micro_usd']==original['reserved_micro_usd']
    assert client.post('/api/reports/'+rid+'/retry',json={}).status_code==200
    worker.tick();worker.tick()
    assert len(model.calls)==6
    with connect(path) as db:
        assert db.execute('SELECT status FROM reports WHERE id=?',(rid,)).fetchone()[0]=='ready'
        assert dict(db.execute('SELECT * FROM ai_costs WHERE id=?',(original['id'],)).fetchone())==original
