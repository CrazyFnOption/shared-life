"""Fictional end-to-end checks for schema12 plan proposal semantics."""
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from server.accounting import db as database,families
from server.accounting.db import connect,create_user
from server.api.app import create_app
from server.ai.models import DraftItem
from server.automation.providers import HTTPProvider,ProviderResult
from server.automation.backups import validate_database,backup,restore
from server.memos.migration_guard import capture,verify

def uid():return str(uuid4())

def item(kind,operation='create',**fields):
    return DraftItem(id=uid(),kind=kind,operation=operation,title='虚构'+kind,body_markdown='具体说明',**fields).model_dump(mode='json')

class Fake:
    call_limit=100
    def __init__(self):self.items=[];self.calls=[]
    def call(self,payload):
        self.calls.append(payload)
        return ProviderResult({'message':'这份提案需确认后保存。','items':self.items},{'input_tokens':10,'output_tokens':10,'web_search_calls':0})

@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.setenv('SHARED_EXPENSE_ALLOW_MODEL_CALLS','0')
    monkeypatch.setenv('SHARED_EXPENSE_ALLOW_PLANNING_AI','0')
    path=tmp_path/'fiction.sqlite3'
    users=[create_user(path,name,'fiction-only',name) for name in ('fiction-a','fiction-b')]
    with connect(path) as db:fid=families.create_explicit_family(db,[u['id'] for u in users])
    fake=Fake();app=create_app(path,planning_provider=fake);clients=[]
    for user in users:
        client=TestClient(app);client.__enter__();client.headers['X-App-Request']='shared-expense'
        assert client.post('/api/auth/login',json={'username':user['username'],'password':'fiction-only'}).status_code==200
        client.headers.update({'X-App-Protocol':'2','X-Expected-User':user['id']})
        rid=client.get('/api/context').json()['restore_id']
        client.headers.update({'X-Restore-Id':rid,'X-Scope':'shared','X-Family-Id':fid,'X-Relation-Epoch':'1'})
        clients.append(client)
    yield path,fake,app,clients,users
    for client in clients:client.__exit__(None,None,None)

def op(client,kind,data,*,identity=None,version=0,action='create'):
    response=client.post('/api/v2/plans/operations',json={'op_id':uid(),'entity_type':kind,'entity_id':identity or uid(),'kind':action,'base_version':version,'data':data})
    assert response.status_code==200,response.text
    return response.json()['receipt']['record']

def wish(client):return op(client,'wish',{'title':'虚构愿望','body_markdown':'约束来自愿望','wish_kind':'未来旅行'})
def url(record):return '/api/v2/plans/records/'+record['id']+'/ai'
def send(client,record):
    response=client.post(url(record)+'/messages',json={'op_id':uid(),'content':'完善原方案','base_version':record['version'],'allow_web':False})
    assert response.status_code==200,response.text
    assert response.json()['status']=='ready',response.text
    return response.json()['drafts'][-1]

def adopt(client,record,draft,plan=None,ids=None,overrides=None):
    body={'op_id':uid(),'draft_id':draft['id'],'item_ids':ids or [i['id'] for i in draft['items']],'base_version':plan['version'] if plan else record['version'],'target_plan_id':plan['id'] if plan else None,'overrides':overrides or []}
    return client.post(url(record)+'/adopt',json=body),body

def chain(client):
    w=wish(client)
    p=op(client,'plan',{'source_wish_id':w['id'],'title':'原计划','body_markdown':'人工总方案','open_questions_markdown':'待确定日期'},action='from_wish')
    s=op(client,'stage',{'plan_id':p['id'],'title':'原阶段','body_markdown':'人工阶段正文','position':0})
    t=op(client,'task',{'plan_id':p['id'],'stage_id':s['id'],'title':'原任务','body_markdown':'人工操作标准','position':0,'assignee_ids':[]})
    return w,p,s,t

@pytest.mark.parametrize('known_usage',[True,False])
def test_http_provider_create_reopen_and_update_original_completed_task(world,monkeypatch,known_usage):
    path,fake,app,(client,other),users=world
    monkeypatch.setenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','100')
    app.state.planning_provider=HTTPProvider('openai','gpt-6.1-sol','fictional-not-a-key')
    plan_item=item('plan',open_questions_markdown='日期待定')
    stage_item=item('stage',position=0)
    task_item=item('task',stage_key=stage_item['id'],position=0)
    queued=[[plan_item,stage_item,task_item]];requests=[]
    class Reply:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return self.body
    def transport(request,timeout):
        request_body=json.loads(request.data);requests.append(request_body)
        instruction=request_body['instructions']
        assert 'travel by day, learning by week or phase' in instruction
        assert 'general matters by concrete steps' in instruction
        assert 'executable first draft immediately' in instruction
        assert '1-3 focused questions' in instruction
        assert 'other unknowns in open_questions_markdown without inventing dates' in instruction
        schema=request_body['text']['format']['schema']
        def strict(node):
            if isinstance(node,dict):
                if node.get('type')=='object':
                    assert node['additionalProperties'] is False
                    assert set(node['required'])==set(node['properties'])
                assert 'default' not in node
                for value in node.values():strict(value)
            elif isinstance(node,list):
                for value in node:strict(value)
        strict(schema)
        draft_schema=schema['$defs']['DraftItem']
        assert set(draft_schema['properties']['operation']['enum'])=={'create','update','delete'}
        assert {'anyOf'}<=draft_schema['properties']['target_id'].keys()
        response=Reply();wire={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'message':'保存前请核对','items':queued.pop(0)})}]}]}
        if known_usage:wire['usage']={'input_tokens':20,'output_tokens':20,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}
        response.body=json.dumps(wire).encode();return response
    monkeypatch.setattr('server.automation.providers.urlopen',transport)
    w=wish(client);draft=send(client,w)
    assert not draft['legacy'] and draft['items'][0]['before']=={}
    response,body=adopt(client,w,draft,overrides=[{'item_id':stage_item['id'],'changes':{'body_markdown':'确认时编辑的阶段说明'}}])
    assert response.status_code==200,response.text
    receipt=response.json();p=receipt['receipt']['record']
    assert client.post(url(w)+'/adopt',json=body).json()==receipt and len(requests)==1
    # A fresh app/client reads actual persisted fields and cross-entry discussion.
    reopened_app=create_app(path,planning_provider=app.state.planning_provider)
    with TestClient(reopened_app) as reopened:
        reopened.headers.update(client.headers);reopened.cookies.update(client.cookies)
        view=reopened.get(url(p)).json();current=view['current_plan'];t=current['tasks'][0]
        assert current['record']['body_markdown']=='具体说明'
        assert current['record']['open_questions_markdown']=='日期待定'
        assert current['stages'][0]['body_markdown']=='确认时编辑的阶段说明'
        assert view['drafts'][0]['record_id']==w['id'] and view['wish_id']==w['id']
        assert all(i['adopted'] for i in view['drafts'][0]['items'])
        completed=op(reopened,'task',{'completed':True},identity=t['id'],version=t['version'],action='complete')
        update=item('task','update',target_id=t['id'],base_version=999,changed_fields=['body_markdown'])
        queued.append([update]);new_draft=send(reopened,w)
        assert new_draft['items'][0]['base_version']==completed['version']
        assert new_draft['items'][0]['before']=={'body_markdown':'具体说明'}
        response,_=adopt(reopened,p,new_draft,p,overrides=[{'item_id':update['id'],'changes':{'body_markdown':'仅修改原任务做法'}}])
        assert response.status_code==200,response.text
        after=other.get(url(w)).json()['current_plan']['tasks']
        assert len(after)==1 and after[0]['id']==t['id']
        assert after[0]['completed'] and after[0]['title']==t['title'] and after[0]['body_markdown']=='仅修改原任务做法'
        assert after[0]['created_at']==t['created_at']
    payload=json.loads(requests[1]['input'][0]['content'][0]['text'])
    assert payload['wish']['id']==w['id'] and payload['plan']['id']==p['id']
    assert payload['tasks'][0]['completed'] and payload['history'][1]['proposal'][0]['adopted']
    with connect(path) as db:
        assert {r[0] for r in db.execute('SELECT status FROM ai_costs')}==({'settled'} if known_usage else {'unknown'})
        validate_database(db)

def test_stale_batch_is_atomic_but_unrelated_stage_can_be_selected(world):
    path,fake,app,(client,_),users=world;w,p,s,t=chain(client)
    fake.items=[item('stage','update',target_id=s['id'],changed_fields=['body_markdown']),item('task','update',target_id=t['id'],changed_fields=['body_markdown'])]
    draft=send(client,w)
    manual=op(client,'task',{'plan_id':p['id'],'stage_id':s['id'],'title':t['title'],'body_markdown':'生成后人工修改','position':0},identity=t['id'],version=1,action='update')
    response,_=adopt(client,w,draft,p)
    assert response.status_code==409 and response.json()['detail']['code']=='ai_draft_stale'
    state=client.get(url(p)).json()['current_plan']
    assert state['stages'][0]['body_markdown']=='人工阶段正文' and state['tasks'][0]['body_markdown']==manual['body_markdown']
    response,_=adopt(client,w,draft,p,ids=[fake.items[0]['id']])
    assert response.status_code==200,response.text
    latest=response.json()['receipt']['record']
    response,_=adopt(client,w,draft,latest,ids=[fake.items[1]['id']])
    assert response.status_code==409
    assert client.get(url(p)).json()['current_plan']['tasks'][0]['body_markdown']=='生成后人工修改'

def test_move_reorder_delete_protections_and_selected_dependencies(world):
    path,fake,app,(client,other),users=world;w,p,s,t=chain(client)
    fake.items=[item('task','delete',target_id=t['id'])]
    blocked=send(other,p)
    assert blocked['items'][0]['blocked_reason']=='creator_required'
    response,_=adopt(other,p,blocked,p);assert response.status_code==403
    done=op(client,'task',{'completed':True},identity=t['id'],version=1,action='complete')
    blocked=send(client,p);assert blocked['items'][0]['blocked_reason']=='task_completed'
    response,_=adopt(client,p,blocked,p);assert response.status_code==409
    destination=item('stage',position=1)
    move=item('task','update',target_id=t['id'],stage_key=destination['id'],position=4,changed_fields=['stage_key','position'])
    delete=item('stage','delete',target_id=s['id'])
    fake.items=[destination,move,delete];draft=send(client,p)
    response,_=adopt(client,p,draft,p,ids=[move['id']]);assert response.status_code==422
    response,_=adopt(client,p,draft,p);assert response.status_code==200,response.text
    view=client.get(url(p)).json()['current_plan']
    assert len(view['tasks'])==1 and view['tasks'][0]['id']==t['id'] and view['tasks'][0]['completed']
    assert len(view['stages'])==1 and view['tasks'][0]['stage_id']==view['stages'][0]['id']
    assert view['tasks'][0]['position']==4
    with connect(path) as db:validate_database(db)

def test_private_and_unrelated_drafts_are_inaccessible(world):
    path,fake,app,(client,other),users=world
    client.headers.update({'X-Scope':'private','X-Family-Id':'none','X-Relation-Epoch':'0'})
    w=wish(client);fake.items=[item('plan',open_questions_markdown='')];draft=send(client,w)
    assert other.get(url(w)).status_code==404
    unrelated=wish(other)
    response,_=adopt(other,unrelated,draft);assert response.status_code==404

def test_old_drafts_and_operation_receipts_remain_readable_without_rewriting(world):
    path,fake,app,(client,_),users=world;w,p,s,t=chain(client)
    # Persist a legacy-shaped response through the actual send boundary.
    stage_id=uid()
    fake.items=[{'id':stage_id,'kind':'stage','title':'旧草案阶段','body_markdown':'旧草案正文','stage_key':None,'assignee_ids':[],'due_date':None}]
    draft=send(client,w)
    assert draft['legacy'] and draft['items'][0]['operation']=='create'
    with connect(path) as db:
        before=db.execute('SELECT items_json FROM ai_drafts WHERE id=?',(draft['id'],)).fetchone()[0]
        assert 'operation' not in json.loads(before)[0]
        previous=db.execute("SELECT * FROM plan_operations WHERE entity_type='stage' AND entity_id=?",(s['id'],)).fetchone()
        original_receipt=json.loads(previous['receipt_json'])
        # Pre-schema12 stage update carries no body; it must retain current body.
    changed=op(client,'stage',{'plan_id':p['id'],'title':'只改标题','position':0},identity=s['id'],version=s['version'],action='update')
    assert changed['body_markdown']=='人工阶段正文'
    changed_plan=op(client,'plan',{'title':p['title'],'body_markdown':p['body_markdown']},identity=p['id'],version=p['version'],action='update')
    assert changed_plan['open_questions_markdown']=='待确定日期'
    assert client.get(url(p)).json()['drafts'][0]['legacy']
    with connect(path) as db:
        assert db.execute('SELECT items_json FROM ai_drafts WHERE id=?',(draft['id'],)).fetchone()[0]==before
        assert json.loads(db.execute('SELECT receipt_json FROM plan_operations WHERE user_id=? AND op_id=?',(users[0]['id'],previous['op_id'])).fetchone()[0])==original_receipt
    # Legacy creation request with fields as they existed in schema11.
    import hashlib
    data={'plan_id':p['id'],'title':'旧客户端阶段','position':2}
    request={'op_id':uid(),'entity_type':'stage','entity_id':uid(),'kind':'create','base_version':0,'data':data}
    response=client.post('/api/v2/plans/operations',json=request);assert response.status_code==200,response.text
    with connect(path) as db:
        row=db.execute('SELECT request_hash FROM plan_operations WHERE user_id=? AND op_id=?',(users[0]['id'],request['op_id'])).fetchone()
        canonical=json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        assert row['request_hash']==hashlib.sha256(canonical.encode()).hexdigest()
    assert client.post('/api/v2/plans/operations',json=request).json()==response.json()
    update={'op_id':uid(),'entity_type':'stage','entity_id':changed['id'],'kind':'update','base_version':changed['version'],'data':{'plan_id':p['id'],'title':'仍保留正文','position':0}}
    response=client.post('/api/v2/plans/operations',json=update);assert response.status_code==200,response.text
    update['data']['body_markdown']=''
    rejected=client.post('/api/v2/plans/operations',json=update)
    assert rejected.status_code==409 and rejected.json()['detail']['code']=='op_id_reused'

@pytest.fixture
def schema11(tmp_path):
    path=tmp_path/'schema11.sqlite3'
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');database._initialize_legacy(db);database._migrate_multi_family(db);database._migrate_memos(db);database._migrate_memo_archive(db);database._migrate_ai(db)
        person=uid();plan=uid();stage=uid();task=uid();stamp='2026-11-17T00:00:00+00:00'
        db.execute('INSERT INTO users(id,username,password_hash,display_name) VALUES(?,?,?,?)',(person,'fiction',database.password_hasher.hash('fiction-only'),'虚构'))
        db.execute("INSERT INTO plan_records(id,kind,scope,namespace,author_id,updated_by,title,body_markdown,version,created_at,updated_at) VALUES(?,'plan','private',?,?,?,'旧计划','原正文',4,?,?)",(plan,person,person,person,stamp,stamp))
        db.execute("INSERT INTO plan_stages(id,plan_id,title,position,version) VALUES(?,?,'旧阶段',0,2)",(stage,plan))
        db.execute("INSERT INTO plan_tasks(id,plan_id,stage_id,author_id,updated_by,title,body_markdown,completed,version) VALUES(?,?,?,?,?,'原任务','旧任务正文',1,3)",(task,plan,stage,person,person))
        binary=b'fictional retained bytes';import hashlib
        db.execute("INSERT INTO plan_attachments VALUES(?,?,?,'file','old.bin','application/octet-stream',?,?,?,'uploaded')",(uid(),task,person,len(binary),hashlib.sha256(binary).hexdigest(),binary))
        db.commit();validate_database(db)
    return path

def test_schema11_migration_preserves_old_values_reopens_backup_and_is_idempotent(schema11,tmp_path):
    with connect(schema11) as db:before=capture(db)
    assert database.preflight(schema11)=='previous'
    with pytest.raises(RuntimeError,match='migration_required'):database.initialize_database(schema11)
    assert database.migrate_database(schema11)
    with connect(schema11) as db:
        verify(db,before)
        assert db.execute('PRAGMA user_version').fetchone()[0]==database.SCHEMA_VERSION
        assert db.execute('SELECT body_markdown FROM plan_stages').fetchone()[0]==''
        assert db.execute('SELECT open_questions_markdown FROM plan_records').fetchone()[0]==''
        validate_database(db)
    assert not database.migrate_database(schema11)
    archive=backup(schema11,tmp_path/'backups');dest=tmp_path/'restored.sqlite3'
    restore(archive,dest)
    assert database.preflight(dest)=='current'
    with connect(dest) as db:
        assert db.execute('SELECT completed,version FROM plan_tasks').fetchone()[:]==(1,3)
        validate_database(db)

def test_schema11_migration_failure_rolls_back_ddl_marker_and_values(schema11,monkeypatch):
    with connect(schema11) as db:before=capture(db)
    original=database._migrate_plan_bodies
    def fail(db):
        original(db);db.execute("UPDATE plan_records SET title='unexpected'")
    monkeypatch.setattr(database,'_migrate_plan_bodies',fail)
    with pytest.raises(ValueError,match='Migration changed original rows'):database.migrate_database(schema11)
    assert database.preflight(schema11)=='previous'
    with connect(schema11) as db:
        verify(db,before)
        assert db.execute('PRAGMA user_version').fetchone()[0]==11
        assert 'body_markdown' not in {r['name'] for r in db.execute('PRAGMA table_info(plan_stages)')}
