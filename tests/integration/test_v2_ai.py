"""V2 fictional-only HTTP and persistence checks; no key or real API calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,UTC
import hashlib
import json
from io import BytesIO
from uuid import uuid4
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from server.accounting.db import initialize_database,create_user,connect,preflight,migrate_database
from server.accounting import families
from server.api.app import create_app
from server.automation.providers import ProviderResult,ProviderError
from server.automation.backups import backup,restore,validate_database
from server.ai import budget


def uid():return str(uuid4())
class Fake:
    call_limit=100
    def __init__(self):self.calls=[];self.hook=None;self.failure=False
    def call(self,payload):
        self.calls.append(payload)
        if self.hook:self.hook()
        if self.failure:raise ProviderError('transient')
        stage=uid()
        return ProviderResult({'message':'先确认出行日期，再查询交通。尚未联网核实。','items':[
            {'id':stage,'kind':'stage','title':'准备','body_markdown':'','stage_key':None,'assignee_ids':[],'due_date':None},
            {'id':uid(),'kind':'task','title':'确定日期','body_markdown':'完成标准：确认双方时间','stage_key':stage,'assignee_ids':[],'due_date':None}]},
            {'input_tokens':100,'input_tokens_details':{'cached_tokens':0},'output_tokens':100,'web_search_calls':0})

@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.setenv('SHARED_EXPENSE_ALLOW_MODEL_CALLS','0');monkeypatch.setenv('SHARED_EXPENSE_ALLOW_PLANNING_AI','0')
    path=tmp_path/'fiction.sqlite3';fake=Fake()
    a=create_user(path,'alice','fiction-only','甲');b=create_user(path,'bob','fiction-only','乙')
    with connect(path) as db:fid=families.create_explicit_family(db,[a['id'],b['id']])
    app=create_app(path,planning_provider=fake)
    clients=[]
    for user in (a,b):
        c=TestClient(app);c.__enter__();clients.append(c)
        c.headers['X-App-Request']='shared-expense'
        assert c.post('/api/auth/login',json={'username':user['username'],'password':'fiction-only'}).status_code==200
        c.headers.update({'X-App-Protocol':'2','X-Expected-User':user['id']})
        context=c.get('/api/context').json();rid=context['restore_id']
        c.headers.update({'X-Restore-Id':rid,'X-Scope':'shared','X-Family-Id':fid,'X-Relation-Epoch':'1'})
    yield path,fake,clients[0],clients[1],a,b,fid,app
    for c in clients:c.__exit__(None,None,None)

def wish(c,attachments=None,kind='wish'):
    response=c.post('/api/v2/plans/operations',json={'op_id':uid(),'entity_type':kind,'entity_id':uid(),'kind':'create','base_version':0,'data':{'title':'虚构旅行','body_markdown':'仅中文虚构资料','wish_kind':'未来旅行' if kind=='wish' else '', 'attachments':attachments or []}})
    assert response.status_code==200,response.text
    return response.json()['receipt']['record']

def send(c,w,**extra):
    body={'op_id':uid(),'content':'请给可执行方案','attachment_ids':[],'base_version':w['version'],**extra}
    r=c.post('/api/v2/plans/records/'+w['id']+'/ai/messages',json=body)
    return r,body

@pytest.mark.parametrize('case,reason', [('missing_usage','missing_usage'),('null_cache_write','invalid_usage'),('expired','reservation_already_unknown')])
def test_valid_http_planning_keeps_reply_and_adoptable_draft_when_fee_unknown(world,monkeypatch,caplog,case,reason):
    from server.automation.providers import HTTPProvider
    path,_,client,_,_,_,_,app=world
    monkeypatch.setenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','100')
    app.state.planning_provider=HTTPProvider('openai','gpt-6.1-sol','FICTIONAL_PRIVATE_KEY')
    output=dict(Fake().call({}));usage={'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}
    if case=='null_cache_write':usage['input_tokens_details']['cache_write_tokens']=None
    wire={'id':'resp_fictional','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(output),'annotations':[]}]}]}
    if case!='missing_usage':wire['usage']=usage
    calls=[]
    class Reply:
        headers={'x-request-id':'req_fictional'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return json.dumps(wire).encode()
    def transport(request,timeout):
        calls.append(timeout)
        if case=='expired':
            with connect(path) as db:db.execute("UPDATE ai_costs SET status='unknown' WHERE status='reserved'")
        return Reply()
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    record=wish(client);response,body=send(client,record,content='PRIVATE_CONTENT_MARKER')
    assert response.status_code==200;data=response.json();url='/api/v2/plans/records/'+record['id']+'/ai'
    assert data['status']=='ready' and all(m['cost_status']=='unknown' and m['error'] is None for m in data['messages'])
    assert len(data['drafts'])==1 and any(m['role']=='assistant' for m in data['messages'])
    assert client.get(url).json()==data and client.post(url+'/messages',json=body).json()==data and calls==[300]
    draft=data['drafts'][0];task=next(i for i in draft['items'] if i['kind']=='task')
    adoption={'op_id':uid(),'draft_id':draft['id'],'item_ids':[task['id']],'base_version':1,'target_plan_id':None}
    adopted=client.post(url+'/adopt',json=adoption);assert adopted.status_code==200
    assert client.post(url+'/adopt',json=adoption).json()==adopted.json() and calls==[300]
    with connect(path) as db:
        assert tuple(db.execute('SELECT status,reserved_micro_usd,actual_micro_usd FROM ai_costs').fetchone())==('unknown',5450000,None)
        assert db.execute('SELECT count(*) FROM plan_tasks').fetchone()[0]==1
        validate_database(db)
    assert 'Planning AI cost unconfirmed' in caplog.text and '"reason":"'+reason+'"' in caplog.text
    assert 'req_fictional' in caplog.text and 'resp_fictional' in caplog.text
    assert 'PRIVATE_CONTENT_MARKER' not in caplog.text and 'FICTIONAL_PRIVATE_KEY' not in caplog.text

@pytest.mark.parametrize('failure',['provider','validation'])
def test_true_http_provider_or_output_failure_does_not_publish_reply(world,monkeypatch,failure):
    from server.automation.providers import HTTPProvider
    path,_,client,_,_,_,_,app=world;monkeypatch.setenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','100')
    app.state.planning_provider=HTTPProvider('openai','gpt-6.1-sol','fictional-key')
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return json.dumps({'id':'resp_fictional','status':'completed','usage':{'input_tokens':100,'output_tokens':20},'output':[{'content':[{'type':'output_text','text':json.dumps({'message':'虚构','items':[{'id':'invalid UUID'}]})}]}]}).encode()
    calls=[]
    def transport(request,timeout):
        calls.append(timeout)
        if failure=='provider':raise TimeoutError('PRIVATE_FAILURE_MARKER')
        return Reply()
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    response,_=send(client,wish(client));data=response.json()
    assert response.status_code==200 and data['status']=='failed' and not data['drafts']
    assert not any(m['role']=='assistant' for m in data['messages']) and calls==[300]

def test_named_shared_persistence_adoption_replay_and_isolation(world,monkeypatch):
    path,fake,a,b,au,bu,fid,app=world;w=wish(a);url='/api/v2/plans/records/'+w['id']+'/ai'
    response,body=send(a,w);assert response.status_code==200,response.text
    data=response.json();assert data['status']=='ready' and data['messages'][0]['author_name']=='甲'
    assert a.post(url+'/messages',json=body).json()==data and len(fake.calls)==1
    assert b.get(url).json()['drafts']==data['drafts']
    draft=data['drafts'][0];task=next(i for i in draft['items'] if i['kind']=='task')
    adoption={'op_id':uid(),'draft_id':draft['id'],'item_ids':[task['id']],'base_version':1,'target_plan_id':None}
    from server.plans import service as plans
    created='2090-01-01T08:00:00+00:00';monkeypatch.setattr(plans,'now',lambda:created)
    result=b.post(url+'/adopt',json=adoption);assert result.status_code==200,result.text
    receipt=result.json();assert len(receipt['receipt']['adopted_item_ids'])==2
    assert list(receipt['receipt']['task_created_at'].values())==[created]
    monkeypatch.setattr(plans,'now',lambda:'2090-01-02T08:00:00+00:00')
    assert b.post(url+'/adopt',json=adoption).json()==receipt
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM plan_tasks').fetchone()[0]==1
        validate_database(db)
    # New client/app instance reads persisted conversation and adoption flags.
    app2=create_app(path,planning_provider=fake)
    with TestClient(app2) as reopened:
        reopened.cookies.update(a.cookies);reopened.headers.update(a.headers)
        assert all(i['adopted'] for i in reopened.get(url).json()['drafts'][0]['items'])
        assert reopened.get('/api/v2/plans/sync').json()['tasks'][0]['created_at']==created
    b.headers.update({'X-Scope':'private','X-Family-Id':'none','X-Relation-Epoch':'0'})
    assert b.get(url).status_code==404

@pytest.mark.parametrize('mime',['text/plain','application/pdf','image/png'])
def test_explicit_selected_materials_only(world,mime):
    path,fake,a,b,au,bu,fid,app=world
    if mime=='image/png':
        stream=BytesIO();Image.new('RGB',(2,2)).save(stream,format='PNG');data=stream.getvalue()
    else:data='中文材料'.encode() if mime=='text/plain' else b'%PDF-1.4\nfictional minimal test bytes'
    descriptor={'id':uid(),'kind':'photo' if mime.startswith('image') else 'file','name':'虚构材料','mime_type':mime,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    w=wish(a,[descriptor]);url='/api/v2/plans/records/'+w['id']
    assert a.put(url+'/attachments/'+descriptor['id'],content=data,headers={'Content-Type':mime}).status_code==200
    r,_=send(a,w,attachment_ids=[descriptor['id']]);assert r.status_code==200,r.text
    assert fake.calls[-1]['materials'][0]['data']==data
    assert r.json()['chosen_materials'][0]['id']==descriptor['id']
    other=wish(a)
    r,_=send(a,other,attachment_ids=[descriptor['id']]);assert r.status_code==404
    assert len(fake.calls)==1


def test_failure_retains_input_unknown_charge_and_no_duplicate_call(world):
    path,fake,a,b,*_=world;w=wish(a);fake.failure=True
    r,body=send(a,w);assert r.status_code==200 and r.json()['messages'][0]['error']=='ai_transient'
    assert r.json()['budget']['has_unknown'] and not r.json()['budget']['blocked']
    r2=a.post('/api/v2/plans/records/'+w['id']+'/ai/messages',json=body)
    assert r2.json()==r.json() and len(fake.calls)==1
    fake.failure=False
    r3,_=send(a,w);assert r3.json()['status']=='ready' and len(fake.calls)==2
    assert r3.json()['budget']['unknown_micro_usd']==5_450_000
    assert r3.json()['budget']['confirmed_micro_usd']>0

@pytest.mark.parametrize('change',['restore','session','version','member'])
def test_inflight_changed_context_never_publishes(world,change):
    path,fake,a,b,au,bu,fid,app=world;w=wish(a)
    def hook():
        with connect(path) as db:
            if change=='restore':db.execute('UPDATE app_state SET restore_id=?',(uid(),))
            elif change=='session':db.execute('DELETE FROM sessions WHERE user_id=?',(au['id'],))
            elif change=='version':db.execute('UPDATE plan_records SET version=version+1 WHERE id=?',(w['id'],))
            else:families.remove_membership(db,au['id'],fid)
    fake.hook=hook;r,_=send(a,w)
    assert r.status_code in (401,403,409),r.text
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM ai_drafts').fetchone()[0]==0
        assert db.execute('SELECT status FROM ai_costs').fetchone()[0]=='settled'
        assert db.execute('SELECT status FROM ai_turns').fetchone()[0]=='stale'


def test_unconfigured_keeps_manual_operations_and_input(world):
    path,fake,a,*rest=world;app=rest[-1];app.state.planning_provider=None;w=wish(a)
    r,_=send(a,w);assert r.status_code==200 and r.json()['status']=='unconfigured'
    assert r.json()['messages'][0]['error']=='ai_unconfigured'
    assert not fake.calls


def test_budget_atomic_across_features_and_month_boundary(tmp_path):
    path=tmp_path/'budget.sqlite3';initialize_database(path)
    moment=datetime(2026,10,31,15,59,tzinfo=UTC)
    def reserve(index):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            try:result=budget.reserve(db,'planning' if index%2 else 'monthly',now=moment);db.commit();return result
            except budget.BudgetError:db.rollback();return None
    with ThreadPoolExecutor(max_workers=12) as pool:ids=list(pool.map(reserve,range(15)))
    with connect(path) as db:
        state=budget.snapshot(db,moment);assert state['committed_micro_usd']<=50_000_000
        assert 0<len([v for v in ids if v])<15
        assert budget.snapshot(db,datetime(2026,10,31,16,0,tzinfo=UTC))['committed_micro_usd']==0
        db.execute('BEGIN IMMEDIATE');assert not budget.settle(db,next(v for v in ids if v),None);db.commit()
        assert budget.snapshot(db,moment)['has_unknown']


def test_schema10_explicit_migration_retains_values_and_backup(tmp_path):
    from server.accounting.db import _initialize_legacy,_migrate_multi_family,_migrate_memos,_migrate_memo_archive
    from server.memos.migration_guard import capture,verify
    path=tmp_path/'schema10.sqlite3'
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');_initialize_legacy(db);_migrate_multi_family(db);_migrate_memos(db);_migrate_memo_archive(db);db.commit()
        before=capture(db)
    assert preflight(path)=='previous'
    with pytest.raises(RuntimeError,match='migration_required'):initialize_database(path)
    assert migrate_database(path)
    with connect(path) as db:verify(db,before);validate_database(db)
    directory=backup(path,tmp_path/'backup');destination=tmp_path/'restored.sqlite3';restore(directory,destination)
    assert preflight(destination)=='current'
    with connect(destination) as db:validate_database(db)


def test_restored_planning_preserves_history_but_blocks_new_paid_calls(world,tmp_path):
    path,fake,a,b,au,bu,fid,app=world;w=wish(a);assert send(a,w)[0].json()['status']=='ready'
    directory=backup(path,tmp_path/'restorable');destination=tmp_path/'restored-planning.sqlite3'
    result=restore(directory,destination,directory/'credentials.json')
    with TestClient(create_app(destination,planning_provider=fake)) as c:
        c.headers['X-App-Request']='shared-expense'
        assert c.post('/api/auth/login',json={'username':'alice','password':'fiction-only'}).status_code==200
        c.headers.update({'X-App-Protocol':'2','X-Expected-User':au['id'],'X-Restore-Id':result['restore_id'],'X-Scope':'shared','X-Family-Id':fid,'X-Relation-Epoch':'1'})
        assert c.get('/api/v2/plans/records/'+w['id']+'/ai').json()['messages']
        r,_=send(c,w);assert r.status_code==200,r.text
        assert r.json()['messages'][-1]['error']=='ai_paused' and len(fake.calls)==1
        with connect(destination) as db:assert db.execute('SELECT count(*) FROM ai_costs').fetchone()[0]==1


def test_openai_planning_strict_schema_tools_materials_and_usage(monkeypatch):
    from server.automation.providers import HTTPProvider
    stage=uid();reply={'message':'草案','items':[{'id':stage,'kind':'stage','title':'准备','body_markdown':'','stage_key':None,'assignee_ids':[],'due_date':None}]}
    wire={'status':'completed','service_tier':'default','tool_usage':{'web_search':{'num_requests':1}},'usage':{'input_tokens':1000,'input_tokens_details':{'cached_tokens':500},'output_tokens':150,'output_tokens_details':{'reasoning_tokens':100}},'output':[
        {'type':'web_search_call','action':{'sources':[{'url':'https://example.com/official','title':'公开源'}]}},
        {'type':'message','content':[{'type':'output_text','text':json.dumps(reply),'annotations':[{'type':'url_citation','url':'https://example.com/official','title':'被引用的官方资料'}]}]}]}
    bodies=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,count):return json.dumps(wire).encode()
    def transport(request,timeout):bodies.append(json.loads(request.data));return Response()
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    provider=HTTPProvider('openai','gpt-6.1-sol','fictional-key')
    materials=[{'name':'中文.txt','mime_type':'text/plain','data':'中文'.encode()},{'name':'虚构.pdf','mime_type':'application/pdf','data':b'%PDF-1.4'},{'name':'虚构.png','mime_type':'image/png','data':b'fiction-image'}]
    output=provider.call({'kind':'planning','content':'帮我规划','allow_web':True,'materials':materials})
    body=bodies[-1];assert body['reasoning']=={'effort':'high'} and body['service_tier']=='default' and body['store'] is False
    assert body['max_tool_calls']==2 and body['tools']==[{'type':'web_search'}]
    def strict(node):
        if isinstance(node,dict):
            assert 'default' not in node
            if node.get('type')=='object':assert node['additionalProperties'] is False and set(node['required'])==set(node['properties'])
            for value in node.values():strict(value)
        elif isinstance(node,list):
            for value in node:strict(value)
    strict(body['text']['format']['schema'])
    content=body['input'][0]['content'];assert [i['type'] for i in content]==['input_text','input_text','input_file','input_image']
    assert output.usage['web_search_calls']==1 and output.sources[0]['checked_at'] and output.sources[0]['verified']
    # Reasoning is already inside output_tokens; cache receives its own rate.
    assert budget.usage_cost(output.usage,budget.pricing(),web_allowed=True)==12_550
    provider.call({'kind':'summary','analyses':['仅虚构财务分析']})
    assert 'tools' not in bodies[-1] and 'max_tool_calls' not in bodies[-1]
    with pytest.raises(ProviderError):HTTPProvider('deepseek','anything','fictional').call({'kind':'summary'})


def test_key_file_requires_explicit_enable_and_owner_only(tmp_path,monkeypatch):
    from server.automation.providers import configured_provider
    key=tmp_path/'fictional-key';key.write_text('fictional-not-a-key');key.chmod(0o600)
    monkeypatch.setenv('SHARED_EXPENSE_OPENAI_KEY_FILE',str(key));monkeypatch.setenv('SHARED_EXPENSE_ALLOW_PLANNING_AI','0')
    assert configured_provider('planning') is None
    monkeypatch.setenv('SHARED_EXPENSE_ALLOW_PLANNING_AI','1')
    assert configured_provider('planning').model=='gpt-6.1-sol'
    key.chmod(0o644);assert configured_provider('planning') is None
    key.chmod(0o600);link=tmp_path/'link';link.symlink_to(key);monkeypatch.setenv('SHARED_EXPENSE_OPENAI_KEY_FILE',str(link));assert configured_provider('planning') is None


@pytest.mark.parametrize('details',[
    {'cache_write_tokens':4951},
    {'cache_creation_tokens':4951},
    {'cache_write_tokens':4951,'cache_creation_tokens':4951},
])
def test_cache_write_real_field_and_legacy_alias_charge_once(details):
    usage={'input_tokens':17094,'input_tokens_details':details,'output_tokens':2506,'web_search_calls':2}
    assert budget.usage_cost(usage,budget.pricing(),web_allowed=True)==81724


@pytest.mark.parametrize('details',[
    {'cache_write_tokens':4951,'cache_creation_tokens':0},
    {'cache_write_tokens':None},
    {'cache_write_tokens':True,'cache_creation_tokens':1},
    ['not a usage object'],
])
def test_ambiguous_cache_write_retains_reservation_and_counts_toward_new_calls(tmp_path,details):
    path=tmp_path/'cache-unknown.sqlite3';initialize_database(path)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');cost=budget.reserve(db,'monthly');db.commit()
        db.execute('BEGIN IMMEDIATE')
        assert not budget.settle(db,cost,{'input_tokens':17094,'input_tokens_details':details,'output_tokens':2506})
        db.commit();assert budget.snapshot(db)['has_unknown'] and not budget.snapshot(db)['blocked']
        before=budget.snapshot(db)
        db.execute('BEGIN IMMEDIATE');new=budget.reserve(db,'planning');db.commit()
        assert new!=cost and budget.snapshot(db)['committed_micro_usd']>before['committed_micro_usd']
        assert budget.snapshot(db)['unknown_micro_usd']==before['unknown_micro_usd']


def test_only_output_text_citations_become_verified_sources(monkeypatch):
    from server.automation.providers import HTTPProvider
    wire={'status':'completed','usage':{'input_tokens':100,'output_tokens':20},'output':[
        {'type':'web_search_call','action':{'sources':[{'url':'https://example.com/search-only','title':'未引用的候选'}]}},
        {'type':'message','content':[
            {'type':'refusal','annotations':[{'type':'url_citation','url':'https://example.com/not-output-text'}]},
            {'type':'output_text','text':json.dumps({'message':'已引用的来源','items':[]}),
             'annotations':[{'type':'url_citation','url':'https://example.com/cited','title':'实际引用'},
                            {'type':'url_citation','url':'https://example.com/cited','title':'重复引用'},
                            {'type':'file_citation','url':'https://example.com/not-web'},
                            {'type':'url_citation','url':'javascript:bad'}]}]}]}
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,count):return json.dumps(wire).encode()
    monkeypatch.setattr('server.automation.providers.urlopen',lambda *args,**kwargs:Reply())
    provider=HTTPProvider('openai','gpt-6.1-sol','fictional-only')
    output=provider.call({'kind':'planning','content':'虚构','allow_web':True})
    assert [s['url'] for s in output.sources]==['https://example.com/cited']
    assert output.sources[0]['verified'] and output.sources[0]['checked_at']
    assert provider.call({'kind':'planning','content':'虚构','allow_web':False}).sources==[]


@pytest.mark.parametrize('reported',[0,1,2,'missing',None,True,'2',-1,3,1.5])
def test_http_web_billing_uses_official_request_count_and_preserves_unknown_result(world,monkeypatch,caplog,reported):
    from server.automation.providers import HTTPProvider
    path,_,client,_,_,_,_,app=world;monkeypatch.setenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','100')
    app.state.planning_provider=HTTPProvider('openai','gpt-6.1-sol','fictional-key')
    output=dict(Fake().call({}))
    wire={'id':'resp_billable','status':'completed','usage':{'input_tokens':100,'output_tokens':20},'output':[
        {'id':'ws_search','type':'web_search_call','status':'completed','action':{'type':'search','query':'PRIVATE_TOOL_MARKER'}},
        {'id':'ws_open','type':'web_search_call','status':'completed','action':{'type':'open_page','url':'https://example.com/PRIVATE_TOOL_MARKER'}},
        {'id':'ws_find','type':'web_search_call','status':'failed','action':{'type':'find_in_page','pattern':'PRIVATE_TOOL_MARKER'}},
        {'type':'message','content':[{'type':'output_text','text':json.dumps(output),'annotations':[]}]}]}
    if reported!='missing':wire['tool_usage']={'web_search':{'num_requests':reported,'num_requests_discounted':1}}
    calls=[]
    class Reply:
        headers={'x-request-id':'req_billable'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return json.dumps(wire).encode()
    def transport(request,timeout):
        body=json.loads(request.data);assert body['max_tool_calls']==2
        calls.append(timeout);return Reply()
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    record=wish(client);response,body=send(client,record)
    result=response.json();assert response.status_code==200 and result['status']=='ready' and len(result['drafts'])==1
    url='/api/v2/plans/records/'+record['id']+'/ai'
    assert client.get(url).json()==result and client.post(url+'/messages',json=body).json()==result and calls==[300]
    settled=type(reported)==int and 0<=reported<=2
    with connect(path) as db:
        cost=dict(db.execute('SELECT * FROM ai_costs').fetchone());usage=json.loads(cost['usage_json'])
        assert cost['status']==('settled' if settled else 'unknown')
        assert cost['actual_micro_usd']==400+reported*10000 if settled else cost['actual_micro_usd'] is None
        assert cost['reserved_micro_usd']==5450000 and usage['web_search_observed_items']==3
        assert usage['web_search_calls']==(reported if type(reported)==int and reported>=0 else None)
        if settled:
            metadata=usage['_web_search']
            assert metadata['observed_items']==3 and metadata['reported_billable_searches']==reported
            assert [item['action_type'] for item in metadata['calls']]==['search','open_page','find_in_page']
        validate_database(db)
    assert all(m['cost_status']==('settled' if settled else 'unknown') for m in result['messages'])
    assert 'PRIVATE_TOOL_MARKER' not in caplog.text
    if not settled:assert '"observed_items":3' in caplog.text and 'req_billable' in caplog.text


def test_official_web_count_has_precedence_over_output_item_count(monkeypatch):
    from server.automation.providers import HTTPProvider
    # A valid explicit count is authoritative even when tool output is absent.
    wire={'status':'completed','tool_usage':{'web_search':{'num_requests':2}},'usage':{'input_tokens':100,'output_tokens':20},'output':[{'content':[{'type':'output_text','text':json.dumps({'message':'虚构','items':[]})}]}]}
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return json.dumps(wire).encode()
    monkeypatch.setattr('server.automation.providers.urlopen',lambda *args,**kwargs:Reply())
    result=HTTPProvider('openai','gpt-6.1-sol','fictional').call({'kind':'planning','content':'虚构','allow_web':True})
    assert result.usage['web_search_calls']==2 and result.diagnostics['web_search']['observed_items']==0
    wire.pop('tool_usage')
    result=HTTPProvider('openai','gpt-6.1-sol','fictional').call({'kind':'planning','content':'虚构','allow_web':False})
    assert result.usage['web_search_calls']==0 and budget.usage_cost(result.usage,budget.pricing(),web_allowed=False)==400
