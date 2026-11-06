"""Source-bound conversation and explicit, atomic append-only adoption."""
import hashlib
import json
import logging
import time
from uuid import uuid4
from fastapi import HTTPException
from server.accounting.db import connect
from server.plans import service as plans
from server.automation.providers import ProviderError
from . import budget
from .models import PlanningOutput

MAX_PAYLOAD_BYTES=200_000
MAX_MATERIAL_BYTES=2*1024*1024

def record(db,user,identity):
    row=db.execute('SELECT kind FROM plan_records WHERE id=?',(identity,)).fetchone()
    if not row:plans.fail('plan_not_found',404)
    return plans.require(db,user,row['kind'],identity)

def digest(value):return hashlib.sha256(plans.canonical(value).encode()).hexdigest()

def chosen(db,user,identity,ids):
    if len(ids)!=len(set(ids)):plans.fail('duplicate_attachment',422)
    result=[];total=0
    for aid in ids:
        row=db.execute("SELECT * FROM plan_attachments WHERE id=? AND parent_id=? AND state='uploaded'",(aid,identity)).fetchone()
        if not row:plans.fail('attachment_not_found',404)
        if row['mime_type'] not in ('image/png','image/jpeg','image/webp','application/pdf','text/plain'):plans.fail('ai_material_unsupported',422)
        total+=row['size_bytes']
        if total>MAX_MATERIAL_BYTES:plans.fail('ai_material_too_large',413)
        data=bytes(row['data'])
        if row['mime_type']=='text/plain':
            try:data.decode('utf-8')
            except UnicodeError:plans.fail('ai_material_unsupported',422)
        if row['mime_type']=='application/pdf' and not data.startswith(b'%PDF-'):plans.fail('ai_material_unsupported',422)
        result.append({'id':aid,'name':row['name'],'mime_type':row['mime_type'],'size_bytes':row['size_bytes'],'data':data})
    return result

def _view(db,user,identity,provider):
    origin=plans.source(db,user);row=record(db,user,identity)
    messages=[];turns=list(db.execute('SELECT t.*,u.display_name FROM ai_turns t JOIN users u ON u.id=t.user_id WHERE t.record_id=? ORDER BY t.created_at,t.rowid',(identity,)))
    latest=[]
    for turn in turns:
        materials=json.loads(turn['chosen_json']);latest=materials
        state=turn['status'];error=turn['error']
        # A crashed synchronous process never silently starts another paid request.
        if state=='running' and (budget.datetime.now(budget.UTC)-budget.datetime.fromisoformat(turn['created_at'])).total_seconds()>budget.PENDING_SECONDS:state='failed';error='ai_result_unknown'
        messages.append({'id':turn['id'],'role':'user','author_id':turn['user_id'],'author_name':turn['display_name'],'content':turn['content'],'created_at':turn['created_at'],'status':state,'error':error,'sources':[],'chosen_materials':materials,**({'op_id':turn['op_id']} if turn['user_id']==user else {})})
        if turn['response_json']:
            response=json.loads(turn['response_json'])
            messages.append({'id':turn['id']+'-reply','role':'assistant','author_id':None,'author_name':'AI','content':response['message'],'created_at':turn['created_at'],'status':state,'error':error,'sources':response.get('sources',[]),'chosen_materials':materials})
    drafts=[{'id':r['id'],'turn_id':r['turn_id'],'base_version':r['base_version'],'items':json.loads(r['items_json'])} for r in db.execute("SELECT d.* FROM ai_drafts d JOIN ai_turns t ON t.id=d.turn_id WHERE d.record_id=? AND t.status='ready' ORDER BY d.created_at,d.rowid",(identity,))]
    return {'source':origin,'record_id':identity,'configured':provider is not None,'status':('unconfigured' if provider is None else (messages[-1]['status'] if messages else 'ready')),'messages':messages,'drafts':drafts,'chosen_materials':latest,'budget':budget.snapshot(db)}

def read(db,user,identity,provider):
    db.execute('BEGIN')
    try:
        result=_view(db,user,identity,provider);db.commit();return result
    except BaseException:db.rollback();raise

def send(db,user,identity,body,provider,db_path):
    data=body.model_dump(mode='json');request_hash=digest(data);turn_id=str(uuid4());cost_id=None
    db.execute('BEGIN IMMEDIATE')
    try:
        origin=plans.source(db,user);parent=record(db,user,identity);plans.assert_writable_parent(db,user,parent['kind'],parent)
        previous=db.execute('SELECT * FROM ai_turns WHERE user_id=? AND op_id=?',(user,str(body.op_id))).fetchone()
        if previous:
            if previous['record_id']!=identity or json.loads(previous['source_json'])!=origin:plans.fail('plan_not_found',404)
            if previous['request_hash']!=request_hash:plans.fail('op_id_reused')
            result=_view(db,user,identity,provider);db.commit();return result
        plans.version(db,parent['kind'],parent,body.base_version)
        materials=chosen(db,user,identity,[str(v) for v in body.attachment_ids])
        metadata=[{k:v for k,v in m.items() if k!='data'} for m in materials]
        history=[]
        for turn in db.execute("SELECT content,response_json FROM ai_turns WHERE record_id=? AND status='ready' ORDER BY created_at,rowid",(identity,)):
            history.append({'role':'user','content':turn['content']})
            if turn['response_json']:history.append({'role':'assistant','content':json.loads(turn['response_json'])['message']})
        stages=[];tasks=[]
        if parent['kind']=='plan':
            stages=[plans.dto(db,'stage',r) for r in db.execute('SELECT * FROM plan_stages WHERE plan_id=? AND deleted=0',(identity,))]
            tasks=[{k:v for k,v in plans.dto(db,'task',r).items() if k!='attachments'} for r in db.execute('SELECT * FROM plan_tasks WHERE plan_id=? AND deleted=0',(identity,))]
        assignable=[dict(r) for r in db.execute('SELECT id,display_name FROM users WHERE id=?',(user,))] if parent['scope']=='private' else [dict(r) for r in db.execute('SELECT u.id,u.display_name FROM users u JOIN family_members m ON m.user_id=u.id WHERE m.family_id=? AND m.active=1',(origin['family_id'],))]
        payload={'kind':'planning','record':{k:parent[k] for k in ('id','kind','title','body_markdown','target_date')},'stages':stages,'tasks':tasks,'history':history,'content':body.content,'allow_web':body.allow_web,'assignable_users':assignable,'chosen_materials':metadata}
        if len(plans.canonical(payload).encode())+sum(len(m['data']) for m in materials if m['mime_type']=='text/plain')>MAX_PAYLOAD_BYTES:plans.fail('ai_input_too_large',413)
        payload['materials']=materials
        error=None
        if db.execute('SELECT automation_paused FROM app_state WHERE singleton=1').fetchone()[0]:error='ai_paused'
        elif provider is None:error='ai_unconfigured'
        else:
            try:
                cost_id=budget.reserve(db,'planning',web=body.allow_web)
                day=budget.datetime.now(budget.UTC).astimezone(budget.ZONE).date().isoformat()
                import os
                call_limit=max(0,int(os.getenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','0')))
                # Explicit fake injection has no external spend; it can declare a test limit.
                call_limit=getattr(provider,'call_limit',call_limit)
                db.execute('INSERT OR IGNORE INTO model_usage(day,calls) VALUES(?,0)',(day,))
                if db.execute('SELECT calls FROM model_usage WHERE day=?',(day,)).fetchone()[0]>=call_limit:
                    db.execute('DELETE FROM ai_costs WHERE id=?',(cost_id,));cost_id=None;error='ai_daily_limit'
                else:db.execute('UPDATE model_usage SET calls=calls+1 WHERE day=?',(day,))
            except budget.BudgetError as exc:error=exc.code
        db.execute('INSERT INTO ai_turns(id,record_id,user_id,op_id,request_hash,source_json,base_version,content,chosen_json,allow_web,status,error,cost_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(turn_id,identity,user,str(body.op_id),request_hash,plans.canonical(origin),body.base_version,body.content,plans.canonical(metadata),int(body.allow_web),'failed' if error else 'running',error,cost_id,plans.now()))
        db.commit()
    except BaseException:db.rollback();raise
    if error:return read(db,user,identity,provider)
    usage=None;output=None;sources=[];started=time.monotonic();diagnostics={}
    try:
        # No DB transaction is held across network I/O; the fake has this same interface.
        response=provider.call(payload);usage=getattr(response,'usage',None);diagnostics=getattr(response,'diagnostics',{})
        if usage is None and isinstance(response,dict):usage=response.get('usage')
        raw={k:v for k,v in response.items() if k not in ('usage','sources')}
        output=PlanningOutput.model_validate(raw).model_dump(mode='json')
        ids=[item['id'] for item in output['items']]
        stage_ids={item['id'] for item in output['items'] if item['kind']=='stage'}
        allowed={u['id'] for u in assignable}
        if len(ids)!=len(set(ids)) or any((i['kind']=='task' and i['stage_key'] not in stage_ids) or (i['kind']=='stage' and i['stage_key'] is not None) or not set(i['assignee_ids'])<=allowed for i in output['items']):raise ProviderError('invalid',usage)
        sources=getattr(response,'sources',None) or (response.get('sources',[]) if isinstance(response,dict) else [])
        sources=[s for s in sources if isinstance(s,dict) and isinstance(s.get('url'),str) and s['url'].startswith(('https://','http://'))][:30] if body.allow_web else []
    except ProviderError as exc:
        error='ai_'+exc.kind;usage=exc.usage or usage;diagnostics=exc.diagnostics
    except Exception:
        error='ai_invalid' if usage else 'ai_result_unknown'
        diagnostics={**diagnostics,'reason':'output_validation'}
    if error:
        # Only fixed categories, timings and opaque IDs; never exception text, content or headers.
        allowed={'timeout','connection','tls','http_status','invalid_response','response_too_large','incomplete_response','unexpected_service_tier','output_validation'}
        from server.automation.providers import safe_identifier
        reason=diagnostics.get('reason') if diagnostics.get('reason') in allowed else 'provider_error'
        status=diagnostics.get('http_status');elapsed=diagnostics.get('elapsed_ms')
        detail={'turn_id':turn_id,'kind':error,'reason':reason,'http_status':status if type(status)==int and 100<=status<=599 else None,
                'elapsed_ms':elapsed if type(elapsed)==int and elapsed>=0 else max(0,round((time.monotonic()-started)*1000)),
                'request_id':safe_identifier(diagnostics.get('request_id')),'response_id':safe_identifier(diagnostics.get('response_id'))}
        logging.getLogger(__name__).warning('Planning AI failed %s',json.dumps(detail,separators=(',',':')))

    # Settle the external fact even if restore/session/member permission changed.
    with connect(db_path) as settlement:
        settlement.execute('BEGIN IMMEDIATE')
        settled=budget.settle(settlement,cost_id,usage,web_allowed=body.allow_web)
        if not settled:error=error or 'ai_budget_unknown'
        settlement.commit()
    try:
        db.execute('BEGIN IMMEDIATE')
        live_origin=plans.source(db,user);live=record(db,user,identity)
        if db.execute('SELECT automation_paused FROM app_state WHERE singleton=1').fetchone()[0]:plans.fail('ai_paused')
        if live_origin!=origin or live['version']!=body.base_version or live['archived']:plans.fail('ai_draft_stale')
        # Selected attachments may have been removed while the response was running.
        chosen(db,user,identity,[str(v) for v in body.attachment_ids])
        if error:db.execute("UPDATE ai_turns SET status='failed',error=? WHERE id=?",(error,turn_id))
        else:
            output['sources']=sources
            db.execute("UPDATE ai_turns SET status='ready',response_json=? WHERE id=?",(plans.canonical(output),turn_id))
            if output['items']:
                items=[dict(item,adopted=False) for item in output['items']]
                db.execute('INSERT INTO ai_drafts(id,turn_id,record_id,base_version,items_json,created_at) VALUES(?,?,?,?,?,?)',(str(uuid4()),turn_id,identity,body.base_version,plans.canonical(items),plans.now()))
        db.commit()
    except BaseException:
        db.rollback()
        with connect(db_path) as cleanup:cleanup.execute("UPDATE ai_turns SET status='stale',error='ai_draft_stale' WHERE id=?",(turn_id,))
        raise
    return read(db,user,identity,provider)

def adopt(db,user,identity,body):
    data=body.model_dump(mode='json');request_hash=digest(data)
    db.execute('BEGIN IMMEDIATE')
    try:
        origin=plans.source(db,user);parent=record(db,user,identity);plans.assert_writable_parent(db,user,parent['kind'],parent)
        old=db.execute('SELECT * FROM ai_adoptions WHERE user_id=? AND op_id=?',(user,str(body.op_id))).fetchone()
        if old:
            if old['record_id']!=identity or json.loads(old['source_json'])!=origin:plans.fail('plan_not_found',404)
            if old['request_hash']!=request_hash:plans.fail('op_id_reused')
            receipt=json.loads(old['receipt_json']);plans.require(db,user,'plan',receipt['plan_id']);db.commit();return {'source':origin,'receipt':receipt}
        draft=db.execute('SELECT d.*,t.source_json,t.status FROM ai_drafts d JOIN ai_turns t ON t.id=d.turn_id WHERE d.id=? AND d.record_id=?',(str(body.draft_id),identity)).fetchone()
        if not draft:plans.fail('ai_draft_not_found',404)
        if draft['status']!='ready' or json.loads(draft['source_json'])['restore_id']!=origin['restore_id']:plans.fail('ai_draft_stale')
        target=None
        if parent['kind']=='plan':
            if body.target_plan_id and str(body.target_plan_id)!=identity:plans.fail('plan_not_found',404)
            target=parent
        else:
            row=db.execute("SELECT id FROM plan_records WHERE source_wish_id=? AND deleted=0",(identity,)).fetchone()
            if row:
                if not body.target_plan_id or str(body.target_plan_id)!=row['id']:plans.fail('wish_already_planned',plan_id=row['id'])
                target=plans.require(db,user,'plan',row['id'])
            elif body.target_plan_id:plans.fail('plan_not_found',404)
        if target:
            plans.assert_writable_parent(db,user,'plan',target);plans.version(db,'plan',target,body.base_version)
        else:plans.version(db,parent['kind'],parent,body.base_version)
        items=json.loads(draft['items_json']);by_id={i['id']:i for i in items};selected=[str(v) for v in body.item_ids]
        if len(selected)!=len(set(selected)) or any(i not in by_id for i in selected):plans.fail('ai_invalid_selection',422)
        # Auto-include the minimal required stage containers, shown by frontend preview.
        for item_id in list(selected):
            item=by_id[item_id]
            if item['kind']=='task' and not by_id[item['stage_key']]['adopted'] and item['stage_key'] not in selected:selected.append(item['stage_key'])
        fresh=[by_id[i] for i in selected if not by_id[i]['adopted']]
        if not fresh:plans.fail('ai_already_adopted')
        # Draft version locks the original record. Prior adoption may legitimately bump target.
        if parent['version']!=draft['base_version'] and not any(i['adopted'] for i in items):plans.fail('ai_draft_stale')
        if target is None:
            plan_id=str(uuid4());stamp=plans.now()
            db.execute("INSERT INTO plan_records(id,kind,scope,namespace,author_id,updated_by,title,body_markdown,wish_kind,source_wish_id,version,created_at,updated_at) VALUES(?,'plan',?,?,?,?,?,?,'',?,1,?,?)",(plan_id,parent['scope'],parent['namespace'],user,user,parent['title'],parent['body_markdown'],identity,stamp,stamp))
            target=plans.raw(db,'plan',plan_id)
        plan_id=target['id'];position=db.execute('SELECT COALESCE(max(position),-1)+1 FROM plan_stages WHERE plan_id=?',(plan_id,)).fetchone()[0]
        for item in sorted(fresh,key=lambda i:i['kind']!='stage'):
            eid=str(uuid4());plans.unique_id(db,eid)
            if item['kind']=='stage':
                db.execute('INSERT INTO plan_stages(id,plan_id,title,position,version) VALUES(?,?,?,?,1)',(eid,plan_id,item['title'],position));position+=1
            else:
                stage=by_id[item['stage_key']]
                if not stage.get('entity_id'):plans.fail('ai_invalid_selection',422)
                stage_row=plans.require(db,user,'stage',stage['entity_id'])
                if stage_row['plan_id']!=plan_id:plans.fail('ai_draft_stale')
                plans.check_assignees(db,user,item['assignee_ids'])
                db.execute('INSERT INTO plan_tasks(id,plan_id,stage_id,author_id,updated_by,title,body_markdown,assignee_ids_json,due_date,position,version) VALUES(?,?,?,?,?,?,?,?,?,?,1)',(eid,plan_id,stage['entity_id'],user,user,item['title'],item['body_markdown'],plans.canonical(item['assignee_ids']),item['due_date'],position));position+=1
            item.update(adopted=True,entity_id=eid)
        # Appending increments the plan version; existing stage/task rows are untouched.
        target=plans.bump(db,'plan',target,{},user)
        db.execute('UPDATE ai_drafts SET items_json=? WHERE id=?',(plans.canonical(items),str(body.draft_id)))
        receipt={'plan_id':plan_id,'adopted_item_ids':[i['id'] for i in fresh],'record':plans.dto(db,'plan',target)}
        db.execute('INSERT INTO ai_adoptions(user_id,op_id,record_id,request_hash,source_json,receipt_json) VALUES(?,?,?,?,?,?)',(user,str(body.op_id),identity,request_hash,plans.canonical(origin),plans.canonical(receipt)))
        db.commit();return {'source':origin,'receipt':receipt}
    except BaseException:db.rollback();raise
