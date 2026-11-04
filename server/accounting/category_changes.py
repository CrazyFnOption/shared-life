"""Category-only proposals, fixed snapshots and unanimous atomic execution."""
from datetime import UTC, datetime
import json
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException

from server.accounting import families, ledger
from server.accounting.categories import category_state, ensure_catalog_category
from server.automation.reports import state


def now():
    return datetime.now(UTC).isoformat()


def namespace_for(db, user_id, scope):
    return families.require_scope(db,user_id,scope)


def source_items(db, scope, namespace, name):
    return [dict(r) for r in db.execute("""SELECT id,owner_id,version AS base_version,category
        FROM expenses WHERE scope=? AND category=? AND voided=0
        AND ((scope='shared' AND family_id=?) OR (scope='private' AND owner_id=?)) ORDER BY id""",
        (scope,name,namespace,namespace))]


def catalog_snapshot(db, scope, namespace, source, target):
    return {name:category_state(db,scope,namespace,name) for name in (source,target) if name}


def context_snapshot(db, scope, namespace, participants):
    result = {'protocol_version':2,'restore_id':state(db)['restore_id'],'family_id':namespace if scope=='shared' else None,'members':{}}
    if scope == 'shared':
        for uid in sorted(participants):
            if not families.relation(db,uid,namespace):
                raise HTTPException(409,detail={'code':'category_owner_unavailable'})
            result['members'][uid] = families.relation(db,uid,namespace)['relation_epoch']
    return result


def context_valid(db, row):
    if row['source_status']!='verified' or not row['relation_context_json']:
        return False
    context = json.loads(row['relation_context_json'])
    if state(db)['restore_id'] != context['restore_id']:
        return False
    for uid,version in context['members'].items():
        member = families.relation(db,uid,row['namespace'])
        if not member or member['relation_epoch'] != version:
            return False
    return True


def visible_to(db, row, user_id):
    if not row or not context_valid(db,row):
        return False
    if row['scope'] == 'private':
        if row['requester_id'] != user_id or (getattr(db,'protocol',0)==2 and (not db.source or db.source['scope']!='private')):
            return False
    elif not families.relation(db,user_id,row['namespace']) or (getattr(db,'protocol',0)==2 and (not db.source or db.source['scope']!='shared' or db.source['family_id']!=row['namespace'])):
        return False
    if row['requester_id'] != user_id and not db.execute('SELECT 1 FROM category_approvals WHERE change_id=? AND owner_id=?',(row['id'],user_id)).fetchone():
        return False
    # Never replay a historical proposal after any fixed item becomes private,
    # changes recorder, leaves this family or is withdrawn from shared visibility.
    for item in json.loads(row['items_json']):
        current = ledger.read_expense(db,item['id'])
        if not current or current['scope'] != row['scope'] or current['owner_id'] != item['owner_id']:
            return False
        if current['scope'] == 'shared' and (current['family_id'] != row['namespace'] or current['voided']):
            return False
        if not ledger.can_read(current,user_id,db):
            return False
    return True


def serialize(db, row, user_id):
    items = json.loads(row['items_json'])
    counts = {}
    for item in items:
        counts[item['owner_id']] = counts.get(item['owner_id'],0)+1
    others = [{'owner_id':uid,'display_name':db.execute('SELECT display_name FROM users WHERE id=?',(uid,)).fetchone()[0],'count':count}
              for uid,count in sorted(counts.items()) if uid != row['requester_id']]
    approvals = [dict(r,count=counts[r['owner_id']]) for r in db.execute('SELECT owner_id,status,decided_at FROM category_approvals WHERE change_id=? ORDER BY owner_id',(row['id'],))]
    return {key:row[key] for key in ('preview_id','id','requester_id','scope','kind','source_name','target_name','reason','rejection_reason','status','created_at','submitted_at','decided_at')} | {
        'merge':row['kind'] == 'rename' and json.loads(row['catalog_json']).get(row['target_name']) == 'active',
        'total_count':len(items),'own_count':counts.get(row['requester_id'],0),'other_owners':others,'approvals':approvals,
        'items':[item for item in items if row['requester_id'] == user_id or item['owner_id'] == user_id]}


def preview(db, user_id, body):
    db.execute('BEGIN IMMEDIATE')
    namespace = namespace_for(db,user_id,body.scope)
    catalog = catalog_snapshot(db,body.scope,namespace,body.source_name,body.target_name)
    if body.kind in {'rename','delete'} and catalog[body.source_name] != 'active':
        raise HTTPException(409,detail={'code':'category_deleted' if catalog[body.source_name] == 'deleted' else 'category_not_found'})
    if body.kind == 'rename':
        target_state = catalog[body.target_name]
        if body.merge and target_state != 'active':
            raise HTTPException(409,detail={'code':'category_deleted' if target_state == 'deleted' else 'category_not_found'})
        if not body.merge and target_state != 'missing':
            raise HTTPException(409,detail={'code':'category_deleted' if target_state == 'deleted' else 'category_exists'})
    if body.kind == 'reclassify' and body.target_name and catalog[body.target_name] != 'active':
        raise HTTPException(409,detail={'code':'category_deleted' if catalog[body.target_name] == 'deleted' else 'category_not_found'})
    if body.kind == 'reclassify':
        items = []
        for eid in sorted(str(eid) for eid in body.expense_ids):
            expense = ledger.read_expense(db,eid)
            if not ledger.can_read(expense,user_id,db) or expense['scope'] != body.scope or expense['voided']:
                raise HTTPException(404,detail='Expense not found')
            if expense['category'] != body.target_name:
                items.append({'id':eid,'owner_id':expense['owner_id'],'base_version':expense['version'],'category':expense['category']})
        if not items:
            raise HTTPException(409,detail={'code':'category_no_changes'})
    else:
        items = source_items(db,body.scope,namespace,body.source_name)
    context = context_snapshot(db,body.scope,namespace,{user_id} | {item['owner_id'] for item in items})
    preview_id = str(uuid4())
    db.execute('''INSERT INTO category_changes(preview_id,requester_id,scope,namespace,kind,source_name,target_name,items_json,context_json,catalog_json,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,'preview',?)''',
        (preview_id,user_id,body.scope,namespace,body.kind,body.source_name,body.target_name,ledger.canonical_json(items),ledger.canonical_json(context),ledger.canonical_json(catalog),now()))
    db.execute("UPDATE category_changes SET relation_context_json=?,source_status='verified' WHERE preview_id=?",(ledger.canonical_json(context),preview_id))
    result = serialize(db,db.execute('SELECT * FROM category_changes WHERE preview_id=?',(preview_id,)).fetchone(),user_id)
    db.commit()
    return result


def fresh(db, row):
    if not context_valid(db,row):
        return False
    if catalog_snapshot(db,row['scope'],row['namespace'],row['source_name'],row['target_name']) != json.loads(row['catalog_json']):
        return False
    items = json.loads(row['items_json'])
    for item in items:
        current = ledger.read_expense(db,item['id'])
        if not current or current['owner_id'] != item['owner_id'] or current['scope'] != row['scope'] or current['voided'] or current['version'] != item['base_version'] or current['category'] != item['category']:
            return False
        if current['scope'] == 'shared' and current['family_id'] != row['namespace']:
            return False
    # A rename/delete cannot orphan newly added uses of the old label. The
    # record set remains exactly the one shown in the preview, never expanded.
    if row['kind'] in {'rename','delete'} and source_items(db,row['scope'],row['namespace'],row['source_name']) != items:
        return False
    return True


def stale(db, row):
    db.execute("UPDATE category_changes SET status='stale',decided_at=? WHERE preview_id=? AND status IN ('preview','pending')",(now(),row['preview_id']))
    db.commit()
    raise HTTPException(409,detail={'code':'category_change_stale'})


def apply(db, row):
    if row['kind'] == 'rename':
        ensure_catalog_category(db,row['scope'],row['namespace'],row['target_name'])
    for item in json.loads(row['items_json']):
        before = ledger.read_expense(db,item['id'])
        after = ledger.changed_expense(before,'update',{'category':row['target_name']})
        operation_id = str(uuid5(UUID(row['id']),item['id']))
        ledger.persist_expense(db,after)
        receipt = {'operation_id':operation_id,'expense':after}
        ledger.store_receipt(db,row['requester_id'],operation_id,ledger.digest({'category_change':row['id'],'expense_id':item['id']}),receipt)
        ledger.store_revision(db,row['requester_id'],operation_id,before,after)
    if row['kind'] in {'rename','delete'}:
        db.execute('DELETE FROM categories WHERE scope=? AND namespace=? AND name=?',(row['scope'],row['namespace'],row['source_name']))
        db.execute('INSERT OR IGNORE INTO category_tombstones(scope,namespace,name) VALUES(?,?,?)',(row['scope'],row['namespace'],row['source_name']))
    db.execute("UPDATE category_changes SET status='applied',decided_at=? WHERE id=?",(now(),row['id']))


def submit(db, user_id, body):
    db.execute('BEGIN IMMEDIATE')
    payload = body.model_dump(mode='json')
    request_hash = ledger.digest(payload)
    previous = db.execute('SELECT * FROM category_changes WHERE id=?',(payload['id'],)).fetchone()
    if previous:
        if not visible_to(db,previous,user_id) or previous['requester_id'] != user_id:
            raise HTTPException(404,detail='Category change not found')
        if previous['request_hash'] != request_hash:
            raise HTTPException(409,detail={'code':'request_id_reused'})
        result = {'change':serialize(db,previous,user_id)}
        db.commit()
        return result
    row = db.execute('SELECT * FROM category_changes WHERE preview_id=? AND requester_id=?',(payload['preview_id'],user_id)).fetchone()
    if not row:
        raise HTTPException(404,detail='Category preview not found')
    if namespace_for(db,user_id,row['scope']) != row['namespace']:
        raise HTTPException(404,detail='Category preview not found')
    if row['id'] is not None:
        raise HTTPException(409,detail={'code':'category_preview_used'})
    if row['status'] != 'preview' or not fresh(db,row):
        stale(db,row)
    db.execute("UPDATE category_changes SET id=?,reason=?,request_hash=?,status='pending',submitted_at=? WHERE preview_id=?",(payload['id'],body.reason,request_hash,now(),row['preview_id']))
    owners = {item['owner_id'] for item in json.loads(row['items_json'])} - {user_id}
    for uid in sorted(owners):
        db.execute("INSERT INTO category_approvals(change_id,owner_id,status) VALUES(?,?,'pending')",(payload['id'],uid))
    row = db.execute('SELECT * FROM category_changes WHERE id=?',(payload['id'],)).fetchone()
    if not owners:
        apply(db,row)
    result = {'change':serialize(db,db.execute('SELECT * FROM category_changes WHERE id=?',(payload['id'],)).fetchone(),user_id)}
    db.commit()
    return result


def decide(db, user_id, change_id, body):
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM category_changes WHERE id=?',(change_id,)).fetchone()
    approval = db.execute('SELECT * FROM category_approvals WHERE change_id=? AND owner_id=?',(change_id,user_id)).fetchone()
    if not approval or not visible_to(db,row,user_id):
        raise HTTPException(404,detail='Category change not found')
    families.require_shared(db,user_id,row['namespace'])
    request_hash = ledger.digest({'change_id':change_id,**body.hash_payload()})
    decision_id = str(body.decision_id)
    previous = db.execute('SELECT * FROM category_decisions WHERE user_id=? AND decision_id=?',(user_id,decision_id)).fetchone()
    if previous:
        if previous['request_hash'] != request_hash:
            raise HTTPException(409,detail={'code':'decision_id_reused'})
        result = json.loads(previous['receipt_json'])
        db.commit()
        return result
    if row['status'] == 'stale' or (row['status'] == 'pending' and not fresh(db,row)):
        stale(db,row)
    if row['status'] != 'pending' or approval['status'] != 'pending':
        raise HTTPException(409,detail={'code':'category_change_decided'})
    db.execute('UPDATE category_approvals SET status=?,decided_at=? WHERE change_id=? AND owner_id=?',('accepted' if body.decision == 'accept' else 'rejected',now(),change_id,user_id))
    if body.decision == 'reject':
        db.execute("UPDATE category_changes SET status='rejected',rejection_reason=?,decided_at=? WHERE id=?",(body.rejection_reason,now(),change_id))
    elif not db.execute("SELECT 1 FROM category_approvals WHERE change_id=? AND status!='accepted'",(change_id,)).fetchone():
        apply(db,row)
    result = {'change':serialize(db,db.execute('SELECT * FROM category_changes WHERE id=?',(change_id,)).fetchone(),user_id)}
    db.execute('INSERT INTO category_decisions(user_id,decision_id,change_id,request_hash,receipt_json) VALUES(?,?,?,?,?)',(user_id,decision_id,change_id,request_hash,ledger.canonical_json(result)))
    db.commit()
    return result


def visible_changes(db, user_id):
    result = []
    for row in db.execute("SELECT * FROM category_changes WHERE id IS NOT NULL AND (requester_id=? OR id IN (SELECT change_id FROM category_approvals WHERE owner_id=?)) ORDER BY submitted_at,id",(user_id,user_id)).fetchall():
        if visible_to(db,row,user_id):
            if row['status'] == 'pending' and not fresh(db,row):
                db.execute("UPDATE category_changes SET status='stale',decided_at=? WHERE id=?",(now(),row['id']))
                row = db.execute('SELECT * FROM category_changes WHERE id=?',(row['id'],)).fetchone()
            result.append(serialize(db,row,user_id))
    return result


def invalidate_expense(db, expense_id):
    db.execute("""UPDATE category_changes SET status='stale',decided_at=?
        WHERE status IN ('preview','pending') AND EXISTS
        (SELECT 1 FROM json_each(category_changes.items_json) WHERE json_extract(value,'$.id')=?)""",(now(),expense_id))
