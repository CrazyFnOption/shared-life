"""Per-family persistent relationships and transaction-bound request sources."""
from datetime import datetime, UTC, timedelta
import json
from uuid import uuid4
from fastapi import HTTPException

MAX_COUNTER = 9223372036854775807


def is_v2_schema(db):
    return 'active' in {r['name'] for r in db.execute('PRAGMA table_info(family_members)')}


def relation(db, user_id, fid, *, active=True):
    if not fid:
        return None
    return db.execute('SELECT * FROM family_members WHERE user_id=? AND family_id=?' + (' AND active=1' if active else ''), (user_id,fid)).fetchone()


def active_ids(db, user_id):
    return [r['family_id'] for r in db.execute('SELECT m.family_id FROM family_members m JOIN families f ON f.id=m.family_id WHERE m.user_id=? AND m.active=1 AND f.archived=0 ORDER BY m.family_id',(user_id,))]


def legacy_gate(db, user_id):
    ids = active_ids(db,user_id)
    if len(ids)>1:
        raise HTTPException(426,detail={'code':'client_upgrade_required','min_protocol':2})
    return ids[0] if ids else None


def current_id(db, user_id):
    if not is_v2_schema(db):
        row=db.execute('SELECT family_id FROM family_members WHERE user_id=?',(user_id,)).fetchone()
        return row[0] if row else None
    source=getattr(db,'source',None)
    if getattr(db,'protocol',0)==2 and source and source['user_id']==user_id:
        return source['family_id'] if source['scope']=='shared' else None
    return legacy_gate(db,user_id)


def source(db, user_id, fid=None):
    restore_id=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    member=relation(db,user_id,fid) if fid else None
    return {'protocol_version':2,'user_id':user_id,'restore_id':restore_id,'scope':'shared' if fid else 'private','family_id':fid,'relation_epoch':member['relation_epoch'] if member else 0}


def check_context(db, user_id, *, required=False):
    if getattr(db,'protocol',0)==2:
        captured=getattr(db,'source',None)
        if captured is None:
            if required:
                raise HTTPException(409,detail={'code':'source_context_required'})
            return None
        if captured['user_id']!=user_id:
            raise HTTPException(409,detail={'code':'account_changed'})
        if captured['scope']=='private':
            return None
        member=relation(db,user_id,captured['family_id'])
        family=db.execute('SELECT archived FROM families WHERE id=?',(captured['family_id'],)).fetchone()
        if not member or member['relation_epoch']!=captured['relation_epoch'] or not family or family['archived']:
            raise HTTPException(409,detail={'code':'relation_changed'})
        return captured['family_id']
    if not is_v2_schema(db):
        return current_id(db,user_id)
    fid=legacy_gate(db,user_id)
    headers=getattr(db,'family_headers',None)
    if headers is None or getattr(db,'legacy_discovery',False):
        return fid
    supplied,version=headers
    if supplied is None or version is None:
        if required:
            raise HTTPException(409,detail={'code':'family_context_required'})
        if supplied is None and version is None:
            return fid
        raise HTTPException(409,detail={'code':'family_changed'})
    actual=db.execute('SELECT membership_version FROM users WHERE id=?',(user_id,)).fetchone()[0]
    if supplied!=(fid or 'none') or version!=str(actual):
        raise HTTPException(409,detail={'code':'family_changed'})
    return fid


def require_shared(db,user_id,family_id=None):
    fid=check_context(db,user_id,required=True)
    if not fid or (family_id is not None and family_id!=fid):
        raise HTTPException(404,detail='Family not found')
    return fid


def require_scope(db,user_id,scope):
    if getattr(db,'protocol',0)==2:
        captured=getattr(db,'source',None)
        if not captured or captured['scope']!=scope:
            raise HTTPException(409,detail={'code':'source_context_required'})
    return require_shared(db,user_id) if scope=='shared' else user_id


def visible(db,expense,user_id,*,any_scope=False):
    if not expense:
        return False
    if expense['scope']=='private':
        allowed=expense['owner_id']==user_id
    else:
        allowed=bool(relation(db,user_id,expense['family_id']) and db.execute('SELECT 1 FROM families WHERE id=? AND archived=0',(expense['family_id'],)).fetchone() and (expense['owner_id']==user_id or not expense['voided']))
    if not allowed or any_scope or getattr(db,'protocol',0)!=2:
        return allowed
    captured=getattr(db,'source',None)
    return bool(captured and captured['scope']==expense['scope'] and (expense['scope']=='private' or captured['family_id']==expense['family_id']))


def bump(db,table,column,where,parameters):
    if db.execute(f'SELECT 1 FROM {table} WHERE {where} AND {column}>=?',(*parameters,MAX_COUNTER)).fetchone():
        raise HTTPException(409,detail={'code':'version_exhausted'})
    db.execute(f'UPDATE {table} SET {column}={column}+1 WHERE {where}',parameters)


def bump_context(db,user_ids):
    for uid in set(user_ids):
        bump(db,'users','context_version','id=?',(uid,))


def family_changed(db,fid,extra=()):
    bump(db,'families','revision','id=?',(fid,))
    ids=[r['user_id'] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(fid,))]
    bump_context(db,[*ids,*extra])


def bump_membership(db,uid):
    bump(db,'users','membership_version','id=?',(uid,))
    version=db.execute('SELECT membership_version FROM users WHERE id=?',(uid,)).fetchone()[0]
    for row in db.execute('SELECT family_id,relation_epoch FROM family_members WHERE user_id=? AND active=1',(uid,)).fetchall():
        db.execute('INSERT INTO legacy_relation_epochs(user_id,family_id,membership_version,relation_epoch) VALUES(?,?,?,?)',(uid,row['family_id'],version,row['relation_epoch']))


def add_members(db,family_id,user_ids):
    if not is_v2_schema(db):
        for uid in user_ids:
            db.execute('INSERT INTO family_members(user_id,family_id) VALUES(?,?)',(uid,family_id))
            db.execute('UPDATE users SET membership_version=membership_version+1 WHERE id=?',(uid,))
        return
    for uid in user_ids:
        prior=relation(db,uid,family_id,active=False)
        if prior and prior['active']:
            raise HTTPException(409,detail={'code':'already_in_family'})
        floor=db.execute('SELECT membership_version FROM users WHERE id=?',(uid,)).fetchone()[0]
        epoch=prior['relation_epoch']+1 if prior else max(1,floor+1)
        if epoch>MAX_COUNTER:
            raise HTTPException(409,detail={'code':'version_exhausted'})
        db.execute('INSERT INTO family_members(user_id,family_id,relation_epoch,active,joined_at) VALUES(?,?,?,1,?) ON CONFLICT(user_id,family_id) DO UPDATE SET relation_epoch=excluded.relation_epoch,active=1,joined_at=excluded.joined_at,ended_at=NULL',(uid,family_id,epoch,datetime.now(UTC).isoformat()))
        bump_membership(db,uid)
    family_changed(db,family_id)


def create_explicit_family(db,user_ids,name='演示家庭',*,owner_id=None):
    owner_id=owner_id or user_ids[0]
    if owner_id not in user_ids:
        raise ValueError('Family owner must be a member')
    fid=str(uuid4())
    db.execute('INSERT INTO families(id,name,owner_id) VALUES(?,?,?)',(fid,name,owner_id))
    add_members(db,fid,user_ids)
    return fid


def metadata(db,user_id):
    user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
    families=[]
    for fid in active_ids(db,user_id):
        f=dict(db.execute('SELECT id,name,owner_id,revision FROM families WHERE id=?',(fid,)).fetchone())
        f['relation_epoch']=relation(db,user_id,fid)['relation_epoch']
        f['members']=[dict(r) for r in db.execute('SELECT u.id,u.display_name,u.username,m.relation_epoch FROM users u JOIN family_members m ON m.user_id=u.id WHERE m.family_id=? AND m.active=1 ORDER BY u.id',(fid,))]
        families.append(f)
    state=db.execute('SELECT restore_id,restored_at FROM app_state WHERE singleton=1').fetchone()
    mappings=[dict(r) for r in db.execute('SELECT l.family_id,l.membership_version,l.relation_epoch FROM legacy_relation_epochs l JOIN family_members m ON m.user_id=l.user_id AND m.family_id=l.family_id AND m.relation_epoch=l.relation_epoch AND m.active=1 JOIN families f ON f.id=m.family_id AND f.archived=0 WHERE l.user_id=? ORDER BY l.family_id,l.membership_version',(user_id,))]
    return {'protocol_version':2,**dict(state),'context_version':user['context_version'],'families':families,'default_scope':{'scope':'shared' if user['default_family_id'] else 'private','family_id':user['default_family_id'],'version':user['default_version']},'default_invalidated_family_id':user['default_invalidated_family_id'],'legacy_context_mappings':mappings}


def context(db,user_id):
    if getattr(db,'protocol',0)==2:
        return {'context':metadata(db,user_id)}
    fid=current_id(db,user_id)
    family=None
    if fid:
        family=dict(db.execute('SELECT id,name,owner_id FROM families WHERE id=?',(fid,)).fetchone())
        family['members']=[dict(r) for r in db.execute('SELECT u.id,u.display_name,u.username FROM users u JOIN family_members m ON m.user_id=u.id WHERE m.family_id=? AND m.active=1 ORDER BY u.id',(fid,))]
    user=db.execute('SELECT membership_version,legacy_family_id FROM users WHERE id=?',(user_id,)).fetchone()
    return {'family':family,'membership_version':user['membership_version'],**({'legacy_family_id':user['legacy_family_id']} if user['legacy_family_id'] else {})}


def record_action(db,uid,rid,kind,payload,result_id,fid=None):
    epoch=relation(db,uid,fid)['relation_epoch'] if fid else None
    restore_id=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    db.execute('INSERT INTO family_actions(user_id,request_id,kind,payload,result_id,protocol_version,source_restore_id,relation_epoch) VALUES(?,?,?,?,?,?,?,?)',(uid,str(rid),kind,payload,result_id,getattr(db,'protocol',0) or 2,restore_id,epoch))


def valid_action(db,row,uid,fid):
    restore_id=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    m=relation(db,uid,fid)
    if not m or row['source_restore_id']!=restore_id or row['relation_epoch']!=m['relation_epoch']:
        raise HTTPException(409,detail={'code':'family_changed'})


def create(db,user_id,body):
    db.execute('BEGIN IMMEDIATE')
    old=db.execute('SELECT * FROM family_actions WHERE user_id=? AND request_id=?',(user_id,str(body.request_id))).fetchone()
    if old:
        if old['kind']!='create' or old['payload']!=body.name:
            raise HTTPException(409,detail={'code':'request_id_reused'})
        valid_action(db,old,user_id,old['result_id'])
    else:
        if getattr(db,'protocol',0)!=2 and current_id(db,user_id):
            raise HTTPException(409,detail={'code':'already_in_family'})
        fid=create_explicit_family(db,[user_id],body.name)
        record_action(db,user_id,body.request_id,'create',body.name,fid,fid)
    result=context(db,user_id);db.commit();return result


def invitation(db,row):
    result={k:row[k] for k in ('id','family_id','inviter_id','target_user_id','status','created_at','expires_at','decided_at')}
    result['family_name']=db.execute('SELECT name FROM families WHERE id=?',(row['family_id'],)).fetchone()[0]
    result['inviter_name']=db.execute('SELECT display_name FROM users WHERE id=?',(row['inviter_id'],)).fetchone()[0]
    result['target_username']=db.execute('SELECT username FROM users WHERE id=?',(row['target_user_id'],)).fetchone()[0]
    if result['status']=='pending' and result['expires_at']<=datetime.now(UTC).isoformat():
        result['status']='expired'
    return result


def list_invites(db,user_id):
    result={key:[invitation(db,r) for r in db.execute(f'SELECT * FROM family_invites WHERE {column}=? ORDER BY created_at,id',(user_id,))] for key,column in [('received','target_user_id'),('sent','inviter_id')]}
    if getattr(db,'protocol',0)==2:
        result.update(context(db,user_id))
        result.update({k:result['context'][k] for k in ('restore_id','context_version')})
    return result


def invite(db,user_id,body):
    db.execute('BEGIN IMMEDIATE');fid=require_shared(db,user_id)
    old=db.execute('SELECT * FROM family_actions WHERE user_id=? AND request_id=?',(user_id,str(body.request_id))).fetchone()
    if old:
        if old['kind']!='invite' or old['payload']!=body.username:
            raise HTTPException(409,detail={'code':'request_id_reused'})
        row=db.execute('SELECT * FROM family_invites WHERE id=?',(old['result_id'],)).fetchone()
        if row['family_id']!=fid:
            raise HTTPException(409,detail={'code':'family_changed'})
        valid_action(db,old,user_id,fid)
    else:
        target=db.execute('SELECT id FROM users WHERE username=?',(body.username,)).fetchone()
        if not target:
            raise HTTPException(404,detail={'code':'account_not_found'})
        if target['id']==user_id or relation(db,target['id'],fid) or (getattr(db,'protocol',0)!=2 and active_ids(db,target['id'])):
            raise HTTPException(409,detail={'code':'already_in_family'})
        now=datetime.now(UTC);iid=str(uuid4());epoch=relation(db,user_id,fid)['relation_epoch']
        db.execute("INSERT INTO family_invites(id,family_id,inviter_id,target_user_id,status,created_at,expires_at,inviter_epoch) VALUES(?,?,?,?,'pending',?,?,?)",(iid,fid,user_id,target['id'],now.isoformat(),(now+timedelta(days=7)).isoformat(),epoch))
        record_action(db,user_id,body.request_id,'invite',body.username,iid,fid)
        bump_context(db,[user_id,target['id']])
        row=db.execute('SELECT * FROM family_invites WHERE id=?',(iid,)).fetchone()
    result=invitation(db,row)
    if getattr(db,'protocol',0)==2: result.update(context(db,user_id))
    db.commit();return result


def decide(db,user_id,invite_id,action):
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM family_invites WHERE id=?',(invite_id,)).fetchone()
    if not row or row['inviter_id' if action=='revoke' else 'target_user_id']!=user_id:
        raise HTTPException(404,detail='Invitation not found')
    if action=='revoke': require_shared(db,user_id,row['family_id'])
    active=db.execute('SELECT archived FROM families WHERE id=?',(row['family_id'],)).fetchone()
    inviter=relation(db,row['inviter_id'],row['family_id'])
    if invitation(db,row)['status']!='pending' or active['archived'] or not inviter or inviter['relation_epoch']!=row['inviter_epoch']:
        raise HTTPException(409,detail={'code':'invitation_unavailable'})
    if action=='accept':
        if relation(db,user_id,row['family_id']) or (getattr(db,'protocol',0)!=2 and active_ids(db,user_id)):
            raise HTTPException(409,detail={'code':'already_in_family'})
        add_members(db,row['family_id'],[user_id])
    db.execute('UPDATE family_invites SET status=?,decided_at=? WHERE id=?',({'accept':'accepted','reject':'rejected','revoke':'revoked'}[action],datetime.now(UTC).isoformat(),invite_id))
    bump_context(db,[user_id,row['inviter_id']])
    result=context(db,user_id) if action=='accept' else invitation(db,db.execute('SELECT * FROM family_invites WHERE id=?',(invite_id,)).fetchone())
    if action!='accept' and getattr(db,'protocol',0)==2: result.update(context(db,user_id))
    db.commit();return result


def set_default(db,user_id,body):
    db.execute('BEGIN IMMEDIATE')
    payload=json.dumps(body.model_dump(mode='json'),sort_keys=True,separators=(',',':'))
    old=db.execute('SELECT * FROM family_actions WHERE user_id=? AND request_id=?',(user_id,str(body.request_id))).fetchone()
    restore_id=db.execute('SELECT restore_id FROM app_state WHERE singleton=1').fetchone()[0]
    if old:
        if old['kind']!='default_scope' or old['payload']!=payload or old['source_restore_id']!=restore_id:
            raise HTTPException(409,detail={'code':'request_id_reused'})
    else:
        user=db.execute('SELECT default_version FROM users WHERE id=?',(user_id,)).fetchone()
        if user[0]!=body.base_version:
            raise HTTPException(409,detail={'code':'default_conflict'})
        fid=str(body.family_id) if body.family_id else None
        if fid and not relation(db,user_id,fid):
            raise HTTPException(404,detail='Family not found')
        bump(db,'users','default_version','id=?',(user_id,))
        db.execute('UPDATE users SET default_family_id=?,default_invalidated_family_id=NULL WHERE id=?',(fid,user_id))
        bump_context(db,[user_id]);record_action(db,user_id,body.request_id,'default_scope',payload,fid or 'private')
    result=context(db,user_id);db.commit();return result


def remove_membership(db,user_id,fid):
    member=relation(db,user_id,fid)
    if not member:
        raise HTTPException(404,detail='Member not found')
    bump(db,'family_members','relation_epoch','user_id=? AND family_id=?',(user_id,fid))
    db.execute('UPDATE family_members SET active=0,ended_at=? WHERE user_id=? AND family_id=?',(datetime.now(UTC).isoformat(),user_id,fid))
    bump_membership(db,user_id)
    if db.execute('SELECT 1 FROM users WHERE id=? AND default_family_id=?',(user_id,fid)).fetchone():
        bump(db,'users','default_version','id=?',(user_id,))
        db.execute('UPDATE users SET default_family_id=NULL,default_invalidated_family_id=? WHERE id=?',(fid,user_id))
    targets=[r['target_user_id'] for r in db.execute("SELECT target_user_id FROM family_invites WHERE family_id=? AND inviter_id=? AND status='pending'",(fid,user_id))]
    db.execute("UPDATE family_invites SET status='revoked',decided_at=? WHERE family_id=? AND inviter_id=? AND status='pending'",(datetime.now(UTC).isoformat(),fid,user_id))
    db.execute("UPDATE change_requests SET status=CASE WHEN status='pending' THEN 'stale' ELSE status END,withdrawn=1 WHERE family_id=? AND (requester_id=? OR owner_id=?)",(fid,user_id,user_id))
    db.execute("UPDATE category_changes SET status='stale',decided_at=? WHERE scope='shared' AND namespace=? AND status IN ('preview','pending') AND EXISTS(SELECT 1 FROM json_each(COALESCE(category_changes.relation_context_json,category_changes.context_json),'$.members') WHERE key=?)",(datetime.now(UTC).isoformat(),fid,user_id))
    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE user_id=? AND source_scope='shared' AND family_id=? AND status!='sent'",(user_id,fid))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memo_deletion_requests'").fetchone():
        from server.memos.events import revoke
        revoke(db,user_id,fid)
    family_changed(db,fid,[user_id,*targets])


def archive_family(db,fid):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memo_deletion_requests'").fetchone():
        from server.memos.events import revoke
        for member in db.execute('SELECT user_id FROM family_members WHERE family_id=?',(fid,)).fetchall():revoke(db,member['user_id'],fid)
    db.execute('UPDATE families SET archived=1 WHERE id=?',(fid,))
    db.execute("UPDATE family_invites SET status='revoked',decided_at=? WHERE family_id=? AND status='pending'",(datetime.now(UTC).isoformat(),fid))
    db.execute("UPDATE change_requests SET status=CASE WHEN status='pending' THEN 'stale' ELSE status END,withdrawn=1 WHERE family_id=?",(fid,))
    db.execute("UPDATE category_changes SET status='stale' WHERE scope='shared' AND namespace=? AND status IN ('preview','pending')",(fid,))
    db.execute("UPDATE monthly_settings SET enabled=0 WHERE scope='shared' AND namespace=?",(fid,))
    db.execute('DELETE FROM report_tasks WHERE report_id IN (SELECT id FROM reports WHERE scope=\'shared\' AND owner_id=?)',(fid,))
    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE source_scope='shared' AND family_id=? AND status!='sent'",(fid,))


def require_owner(db,user_id,fid):
    require_shared(db,user_id,fid)
    if db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]!=user_id:
        raise HTTPException(403,detail={'code':'family_owner_required'})


def check_revision(db,fid,body,target=None):
    if getattr(db,'protocol',0)!=2:
        return
    expected=getattr(body,'expected_family_revision',None)
    actual=db.execute('SELECT revision FROM families WHERE id=?',(fid,)).fetchone()[0]
    if expected!=actual:
        raise HTTPException(409,detail={'code':'family_revision_changed'})
    if target is not None and getattr(body,'target_relation_epoch',None)!=target['relation_epoch']:
        raise HTTPException(409,detail={'code':'relation_changed'})


def leave(db,user_id,fid,body=None):
    db.execute('BEGIN IMMEDIATE');require_shared(db,user_id,fid);check_revision(db,fid,body)
    owner=db.execute('SELECT owner_id FROM families WHERE id=?',(fid,)).fetchone()[0]
    others=db.execute('SELECT 1 FROM family_members WHERE family_id=? AND active=1 AND user_id!=?',(fid,user_id)).fetchone()
    if owner==user_id and (others or getattr(db,'protocol',0)==2):
        raise HTTPException(409,detail={'code':'owner_transfer_or_dissolve_required'})
    remove_membership(db,user_id,fid)
    if not db.execute('SELECT 1 FROM family_members WHERE family_id=? AND active=1',(fid,)).fetchone(): archive_family(db,fid)
    result=context(db,user_id);db.commit();return result


def remove(db,user_id,fid,target_id,body=None):
    db.execute('BEGIN IMMEDIATE');require_owner(db,user_id,fid)
    if target_id==user_id: raise HTTPException(409,detail={'code':'use_leave'})
    target=relation(db,target_id,fid)
    if not target: raise HTTPException(404,detail='Member not found')
    check_revision(db,fid,body,target);remove_membership(db,target_id,fid)
    result=context(db,user_id);db.commit();return result


def transfer_owner(db,user_id,fid,target_id,*,leave_after=False,body=None):
    db.execute('BEGIN IMMEDIATE');require_owner(db,user_id,fid)
    if target_id==user_id: raise HTTPException(409,detail={'code':'owner_transfer_target_required'})
    target=relation(db,target_id,fid)
    if not target: raise HTTPException(404,detail='Member not found')
    check_revision(db,fid,body,target)
    db.execute('UPDATE families SET owner_id=? WHERE id=?',(target_id,fid));family_changed(db,fid)
    if leave_after: remove_membership(db,user_id,fid)
    result=context(db,user_id);db.commit();return result


def dissolve(db,user_id,fid,body=None):
    db.execute('BEGIN IMMEDIATE');require_owner(db,user_id,fid);check_revision(db,fid,body)
    affected=[r['user_id'] for r in db.execute('SELECT user_id FROM family_members WHERE family_id=? AND active=1',(fid,))]
    targets=[r['target_user_id'] for r in db.execute("SELECT target_user_id FROM family_invites WHERE family_id=? AND status='pending'",(fid,))]
    for uid in affected: remove_membership(db,uid,fid)
    archive_family(db,fid);bump_context(db,targets)
    result=context(db,user_id);db.commit();return result
