"""Explicit plan proposals, captured against the exact generation snapshot."""
import json
from uuid import uuid4
from server.plans import service as plans
from .models import DraftItem

FIELDS={
    'plan':('title','body_markdown','target_date','open_questions_markdown'),
    'stage':('title','body_markdown','position'),
    'task':('title','body_markdown','stage_key','position','assignee_ids','due_date'),
}

def context(db,user,entry):
    wish=plan=None
    if entry['kind']=='wish':
        wish=entry
        row=db.execute("SELECT id FROM plan_records WHERE source_wish_id=? AND deleted=0",(entry['id'],)).fetchone()
        if row:plan=plans.require(db,user,'plan',row['id'])
    else:
        plan=entry
        if entry['source_wish_id']:
            row=plans.raw(db,'wish',entry['source_wish_id'])
            if not row['deleted']:wish=plans.require(db,user,'wish',row['id'])
    ids=[r['id'] for r in (wish,plan) if r]
    stages=[];tasks=[]
    if plan:
        stages=[plans.dto(db,'stage',r) for r in db.execute('SELECT * FROM plan_stages WHERE plan_id=? AND deleted=0 ORDER BY position,id',(plan['id'],))]
        tasks=[{k:v for k,v in plans.dto(db,'task',r).items() if k!='attachments'} for r in db.execute('SELECT * FROM plan_tasks WHERE plan_id=? AND deleted=0 ORDER BY position,id',(plan['id'],))]
    return {'ids':ids,'wish':wish,'plan':plan,'stages':stages,'tasks':tasks}

def values(kind,row):
    result={key:row['stage_id'] if key=='stage_key' else row[key] for key in FIELDS[kind] if key!='assignee_ids'}
    if kind=='task':result['assignee_ids']=row.get('assignee_ids',json.loads(row.get('assignee_ids_json','[]')))
    return result

def capture(items,ctx,user,allowed):
    """Never trust model-provided versions or before values."""
    by_id={item['id']:item for item in items}
    if len(by_id)!=len(items):raise ValueError('duplicate_item')
    rows={'plan':{ctx['plan']['id']:ctx['plan']} if ctx['plan'] else {},
          'stage':{s['id']:s for s in ctx['stages']},'task':{t['id']:t for t in ctx['tasks']}}
    if set(by_id)&set(rows['stage']):raise ValueError('ambiguous_stage_key')
    creates=[i for i in items if i['kind']=='plan' and i['operation']=='create']
    if len(creates)>1 or (creates and ctx['plan']):raise ValueError('duplicate_plan')
    targets=set()
    next_position={kind:max((r['position'] for r in rows[kind].values()),default=-1)+1 for kind in ('stage','task')}
    for item in items:
        kind= item['kind'];op=item['operation'];fields=list(item['changed_fields'])
        item.update(adopted=False,blocked_reason=None)
        if len(fields)!=len(set(fields)) or not set(fields)<=set(FIELDS[kind]):raise ValueError('invalid_fields')
        if op=='create':
            if item['target_id'] is not None:raise ValueError('invalid_target')
            item['base_version']=None
            fields=list(FIELDS[kind]);item['before']={}
            if kind in next_position and item['position'] is None:
                item['position']=next_position[kind];next_position[kind]+=1
            if kind=='plan' and item['open_questions_markdown'] is None:item['open_questions_markdown']=''
        else:
            row=rows[kind].get(item['target_id'])
            target=(kind,item['target_id'])
            if not row or target in targets:raise ValueError('invalid_target')
            targets.add(target)
            item['base_version']=row['version']
            if op=='delete':
                if kind=='plan':raise ValueError('invalid_delete')
                fields=list(FIELDS[kind])
                if kind=='task':
                    if row['completed']:item['blocked_reason']='task_completed'
                    elif row['author_id']!=user:item['blocked_reason']='creator_required'
            elif not fields:raise ValueError('empty_update')
            old=values(kind,row);item['snapshot']=old;item['before']={key:old[key] for key in fields}
        item['changed_fields']=fields
        item['after']=None if op=='delete' else {key:item[key] for key in fields}
        if item['after'] is not None:validate_after(kind,item['after'],allowed)
    for item in items:
        if item['kind']=='task' and item['operation']!='delete' and 'stage_key' in item['changed_fields']:
            stage=item['stage_key'];proposal=by_id.get(stage)
            if stage not in rows['stage'] and not (proposal and proposal['kind']=='stage' and proposal['operation']=='create'):raise ValueError('invalid_stage')
    if not ctx['plan'] and any(i['operation']=='create' and i['kind']!='plan' for i in items) and not creates:raise ValueError('missing_plan')
    return items

def validate_after(kind,after,allowed):
    if not set(after)<=set(FIELDS[kind]):raise ValueError('invalid_fields')
    for key,value in after.items():
        if key in ('title','body_markdown','open_questions_markdown') and not isinstance(value,str):raise ValueError('invalid_text')
        if key=='title' and not value.strip():raise ValueError('invalid_title')
        if key=='position' and type(value)!=int:raise ValueError('invalid_position')
        if key=='stage_key' and not value:raise ValueError('invalid_stage')
        if key=='assignee_ids' and (not isinstance(value,list) or len(value)!=len(set(value)) or not set(value)<=allowed):raise ValueError('invalid_assignees')

def adopt(db,user,parent,target,draft,items,body):
    """Apply only selected fields, atomically; caller owns transaction/receipt."""
    selected=[str(v) for v in body.item_ids];by_id={i['id']:i for i in items}
    if len(selected)!=len(set(selected)) or any(i not in by_id for i in selected):plans.fail('ai_invalid_selection',422)
    fresh=[by_id[i] for i in selected if not by_id[i]['adopted']]
    if not fresh:plans.fail('ai_already_adopted')
    overrides={str(o.item_id):o.changes.model_dump(mode='json',exclude_unset=True) for o in body.overrides}
    if len(overrides)!=len(body.overrides) or not set(overrides)<={i['id'] for i in fresh}:plans.fail('ai_invalid_selection',422)
    allowed={user} if parent['scope']=='private' else {r[0] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(parent['namespace'],))}
    for item in fresh:
        changes=overrides.get(item['id'],{})
        if changes:
            if item['operation']=='delete' or not set(changes)<=set(FIELDS[item['kind']]):plans.fail('ai_invalid_selection',422)
            item.update(changes)
            item['changed_fields']=list(dict.fromkeys([*item['changed_fields'],*changes]))
            # before remains the captured snapshot, including newly edited fields.
            snapshot=item.get('snapshot',{})
            if item['operation']=='update':
                for key in changes:
                    if key not in item['before']:item['before'][key]=snapshot.get(key)
            item['after']={key:item[key] for key in item['changed_fields']}
        try:
            if item['after'] is not None:validate_after(item['kind'],item['after'],allowed)
            DraftItem.model_validate({k:v for k,v in item.items() if k in DraftItem.model_fields})
        except ValueError:plans.fail('ai_invalid_selection',422)
        if item['operation']!='create':
            row=plans.require(db,user,item['kind'],item['target_id'])
            if (row['id'] if item['kind']=='plan' else row['plan_id'])!=(target['id'] if target else None):plans.fail('ai_draft_stale')
            if row['version']!=item['base_version']:plans.fail('ai_draft_stale',entity_id=row['id'],current_version=row['version'])
            if item['operation']=='delete' and item['kind']=='task':
                if row['completed']:plans.fail('task_completed')
                if row['author_id']!=user:plans.fail('creator_required',403)
                item['blocked_reason']=None
    creates=[i for i in fresh if i['kind']=='plan' and i['operation']=='create']
    if creates and target:plans.fail('ai_draft_stale')
    if target is None:
        if len(creates)!=1:plans.fail('ai_invalid_selection',422)
        proposal=creates[0];plan_id=str(uuid4());plans.unique_id(db,plan_id);stamp=plans.now()
        data=proposal['after']
        db.execute("INSERT INTO plan_records(id,kind,scope,namespace,author_id,updated_by,title,body_markdown,target_date,open_questions_markdown,source_wish_id,version,created_at,updated_at) VALUES(?,'plan',?,?,?,?,?,?,?,?,?,1,?,?)",(plan_id,parent['scope'],parent['namespace'],user,user,data['title'],data['body_markdown'],data['target_date'],data['open_questions_markdown'],parent['id'],stamp,stamp))
        target=plans.raw(db,'plan',plan_id);proposal.update(adopted=True,entity_id=plan_id)
    plan_id=target['id'];task_created_at={}
    for item in fresh:
        if item['kind']=='task' and item['operation']!='delete' and 'stage_key' in item['changed_fields']:
            key=item['stage_key'];dependency=by_id.get(key)
            if dependency and dependency['kind']=='stage' and dependency['operation']=='create':
                if not dependency['adopted'] and dependency not in fresh:plans.fail('ai_invalid_selection',422)
            else:
                stage=plans.require(db,user,'stage',key)
                if stage['plan_id']!=plan_id:plans.fail('ai_draft_stale')
    # Create stages before tasks; move/delete tasks before deleting stage containers.
    ordered=sorted(fresh,key=lambda i: (3 if i['kind']=='stage' and i['operation']=='delete' else 0 if i['kind']=='stage' else 2 if i['kind']=='task' else 1))
    for item in ordered:
        if item['adopted']:continue
        kind=item['kind'];op=item['operation'];data=dict(item['after'] or {})
        if 'stage_key' in data:
            stage=by_id.get(data['stage_key']);data['stage_id']=stage['entity_id'] if stage and stage['kind']=='stage' and stage['operation']=='create' else data['stage_key'];data.pop('stage_key')
            actual=plans.require(db,user,'stage',data['stage_id'])
            if actual['plan_id']!=plan_id:plans.fail('ai_draft_stale')
        if 'assignee_ids' in data:
            existing=() if op=='create' else json.loads(plans.raw(db,'task',item['target_id'])['assignee_ids_json'])
            plans.check_assignees(db,user,data['assignee_ids'],existing)
            data['assignee_ids_json']=plans.canonical(data.pop('assignee_ids'))
        if op=='create':
            identity=str(uuid4());plans.unique_id(db,identity)
            data.update(id=identity,plan_id=plan_id,version=1)
            if kind=='task':
                data.update(author_id=user,updated_by=user);task_created_at[identity]=plans.now()
            db.execute('INSERT INTO '+plans.TABLES[kind]+'('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',tuple(data.values()))
        else:
            identity=item['target_id'];row=plans.raw(db,kind,identity)
            if op=='delete':
                if kind=='stage' and db.execute('SELECT 1 FROM plan_tasks WHERE stage_id=? AND deleted=0',(identity,)).fetchone():plans.fail('stage_has_tasks')
                data={'deleted':1}
                if kind=='task':db.execute("UPDATE plan_attachments SET state='deleted' WHERE parent_id=?",(identity,))
            plans.bump(db,kind,row,data,user)
        item.update(adopted=True,entity_id=identity)
    # Child changes are visible on the plan without changing completion or untouched fields.
    if not any(i['kind']=='plan' and i['operation']=='update' for i in fresh):target=plans.bump(db,'plan',plans.raw(db,'plan',plan_id),{},user)
    else:target=plans.raw(db,'plan',plan_id)
    db.execute('UPDATE ai_drafts SET items_json=? WHERE id=?',(plans.canonical(items),draft['id']))
    return {'plan_id':plan_id,'adopted_item_ids':[i['id'] for i in fresh],'record':plans.dto(db,'plan',target),'task_created_at':task_created_at}
