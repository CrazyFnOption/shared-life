"""Two specific shared lists: memo focus references and memo category order."""
import json
from uuid import uuid5,NAMESPACE_URL
from fastapi import HTTPException
from server.memos import models


def read(db,user_id,kind):
    from .service import source,namespace
    src=source(db,user_id);ns=namespace(src)
    identity=str(uuid5(NAMESPACE_URL,f'shared-life:memos:{kind}:{src["scope"]}:{ns}'))
    table='memo_focus' if kind=='focus' else 'memo_category_orders'
    row=db.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone()
    key='targets' if kind=='focus' else 'category_ids'
    values=json.loads(row[key+'_json']) if row else []
    if kind=='category_order':
        current=[r[0] for r in db.execute('SELECT id FROM memo_categories WHERE scope=? AND namespace=? AND deleted=0 ORDER BY position,created_at,id',(src['scope'],ns))]
        values=[i for i in values if i in current]+[i for i in current if i not in values]
    return {'id':identity,'scope':src['scope'],'family_id':src['family_id'],'version':row['version'] if row else 0,key:values}


def resolve(db,user_id,target):
    from .service import require_record,memo_dto,fail
    if target['kind']=='memo':
        row=require_record(db,user_id,target['id'])
        if row['archived']:fail('memo_not_found',404)
        return memo_dto(db,row)
    from server.plans.service import task_reference
    return task_reference(db,user_id,target['id'])


def active(db,user_id):
    result=[]
    for target in read(db,user_id,'focus')['targets']:
        try:record=resolve(db,user_id,target)
        except HTTPException as error:
            if error.status_code==404:continue
            raise
        if not record.get('completed'):result.append({'target':target,'record':record})
    return result


def apply(db,user_id,operation):
    from .service import source,namespace,parse,fail
    kind=operation.entity_type;current=read(db,user_id,kind)
    if str(operation.entity_id)!=current['id']:fail('memo_not_found',404)
    if operation.kind!='update':fail('unsupported_memo_operation',422)
    if operation.base_version!=current['version']:fail('memo_conflict',current={**current,'entity_type':kind},current_version=current['version'])
    src=source(db,user_id);ns=namespace(src)
    if kind=='focus':
        data=parse(models.FocusData,operation.data);values=data['targets'];key='targets'
        keys=[(t['kind'],t['id']) for t in values]
        if len(set(keys))!=len(keys):fail('duplicate_focus_target',422)
        old={(t['kind'],t['id']) for t in current['targets']}
        for target in values:
            try:resolve(db,user_id,target)
            except HTTPException as error:
                if error.status_code!=404 or (target['kind'],target['id']) not in old:raise
        table='memo_focus'
    else:
        data=parse(models.CategoryOrderData,operation.data);values=data['category_ids'];key='category_ids';table='memo_category_orders'
        if len(set(values))!=len(values) or set(values)!=set(current[key]):fail('category_order_changed')
        for position,identity in enumerate(values):db.execute('UPDATE memo_categories SET position=? WHERE id=?',(position,identity))
    version=current['version']+1
    db.execute(f'INSERT INTO {table}(id,scope,namespace,version,{key}_json) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,{key}_json=excluded.{key}_json',(current['id'],src['scope'],ns,version,json.dumps(values,sort_keys=True)))
    return {**current,'version':version,key:values}


def catalog_changed(db,user_id):
    from .service import source,namespace
    current=read(db,user_id,'category_order');src=source(db,user_id)
    db.execute('INSERT INTO memo_category_orders(id,scope,namespace,version,category_ids_json) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,category_ids_json=excluded.category_ids_json',(current['id'],src['scope'],namespace(src),current['version']+1,json.dumps(current['category_ids'])))
