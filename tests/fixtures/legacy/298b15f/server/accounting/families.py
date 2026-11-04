"""Explicit family membership, transaction-bound context and invitation lifecycle."""
from datetime import datetime, UTC, timedelta
from uuid import uuid4
from fastapi import HTTPException


def current_id(db, user_id):
    row = db.execute('SELECT family_id FROM family_members WHERE user_id=?', (user_id,)).fetchone()
    return row['family_id'] if row else None


def context(db, user_id):
    row = db.execute('SELECT membership_version,legacy_family_id FROM users WHERE id=?', (user_id,)).fetchone()
    fid = current_id(db, user_id)
    family = None
    if fid:
        family = dict(db.execute('SELECT id,name,owner_id FROM families WHERE id=?', (fid,)).fetchone())
        family['members'] = [dict(r) for r in db.execute('SELECT u.id,u.display_name,u.username FROM users u JOIN family_members m ON m.user_id=u.id WHERE m.family_id=? ORDER BY u.id', (fid,))]
    result = {'family': family, 'membership_version': row['membership_version']}
    if row['legacy_family_id']:
        result['legacy_family_id'] = row['legacy_family_id']
    return result


def check_context(db, user_id, *, required=False):
    headers = getattr(db, 'family_headers', None)
    # Internal callers explicitly operate under the same transaction and membership.
    if headers is None:
        return current_id(db, user_id)
    fid, version = headers
    if fid is None or version is None:
        if required:
            raise HTTPException(409, detail={'code': 'family_context_required'})
        if fid is None and version is None:
            return current_id(db, user_id)
        raise HTTPException(409, detail={'code': 'family_changed'})
    actual = current_id(db, user_id)
    actual_version = db.execute('SELECT membership_version FROM users WHERE id=?', (user_id,)).fetchone()[0]
    if fid != (actual or 'none') or version != str(actual_version):
        raise HTTPException(409, detail={'code': 'family_changed'})
    return actual


def require_shared(db, user_id, family_id=None):
    fid = check_context(db, user_id, required=True)
    if not fid or (family_id is not None and family_id != fid):
        raise HTTPException(404, detail='Family not found')
    return fid


def visible(db, expense, user_id):
    if not expense:
        return False
    if expense['scope'] == 'private':
        return expense['owner_id'] == user_id
    return bool(expense['family_id'] and expense['family_id'] == current_id(db, user_id) and (expense['owner_id'] == user_id or not expense['voided']))


def add_members(db, family_id, user_ids):
    for uid in user_ids:
        db.execute('INSERT INTO family_members(user_id,family_id) VALUES(?,?)', (uid, family_id))
        db.execute('UPDATE users SET membership_version=membership_version+1 WHERE id=?', (uid,))


def create_explicit_family(db, user_ids, name='演示家庭', *, owner_id=None):
    owner_id = owner_id or user_ids[0]
    if owner_id not in user_ids:
        raise ValueError('Family owner must be a member')
    fid = str(uuid4())
    db.execute('INSERT INTO families(id,name,owner_id) VALUES(?,?,?)', (fid, name, owner_id))
    add_members(db, fid, user_ids)
    return fid


def create(db, user_id, body):
    db.execute('BEGIN IMMEDIATE')
    prior = db.execute('SELECT * FROM family_actions WHERE user_id=? AND request_id=?', (user_id, str(body.request_id))).fetchone()
    if prior:
        if prior['kind'] != 'create' or prior['payload'] != body.name:
            raise HTTPException(409, detail={'code': 'request_id_reused'})
        if current_id(db, user_id) != prior['result_id']:
            raise HTTPException(409, detail={'code': 'family_changed'})
    else:
        if current_id(db, user_id):
            raise HTTPException(409, detail={'code': 'already_in_family'})
        fid = create_explicit_family(db, [user_id], body.name)
        db.execute('INSERT INTO family_actions VALUES(?,?,?,?,?)', (user_id, str(body.request_id), 'create', body.name, fid))
    result = context(db, user_id)
    db.commit()
    return result


def invitation(db, row):
    result = dict(row)
    result['family_name'] = db.execute('SELECT name FROM families WHERE id=?', (row['family_id'],)).fetchone()[0]
    result['inviter_name'] = db.execute('SELECT display_name FROM users WHERE id=?', (row['inviter_id'],)).fetchone()[0]
    result['target_username'] = db.execute('SELECT username FROM users WHERE id=?', (row['target_user_id'],)).fetchone()[0]
    if result['status'] == 'pending' and result['expires_at'] <= datetime.now(UTC).isoformat():
        result['status'] = 'expired'
    return result


def list_invites(db, user_id):
    return {key: [invitation(db, r) for r in db.execute(f'SELECT * FROM family_invites WHERE {column}=? ORDER BY created_at,id', (user_id,))] for key, column in [('received','target_user_id'), ('sent','inviter_id')]}


def invite(db, user_id, body):
    db.execute('BEGIN IMMEDIATE')
    fid = require_shared(db, user_id)
    old = db.execute('SELECT * FROM family_actions WHERE user_id=? AND request_id=?', (user_id, str(body.request_id))).fetchone()
    if old:
        if old['kind'] != 'invite' or old['payload'] != body.username:
            raise HTTPException(409, detail={'code': 'request_id_reused'})
        row = db.execute('SELECT * FROM family_invites WHERE id=?', (old['result_id'],)).fetchone()
        if row['family_id'] != fid:
            raise HTTPException(409, detail={'code': 'family_changed'})
    else:
        target = db.execute('SELECT id FROM users WHERE username=?', (body.username,)).fetchone()
        if not target:
            raise HTTPException(404, detail={'code': 'account_not_found'})
        if current_id(db, target['id']):
            raise HTTPException(409, detail={'code': 'already_in_family'})
        now = datetime.now(UTC)
        iid = str(uuid4())
        db.execute('INSERT INTO family_invites VALUES(?,?,?,?,?,?,?,?)', (iid, fid, user_id, target['id'], 'pending', now.isoformat(), (now+timedelta(days=7)).isoformat(), None))
        db.execute('INSERT INTO family_actions VALUES(?,?,?,?,?)', (user_id, str(body.request_id), 'invite', body.username, iid))
        row = db.execute('SELECT * FROM family_invites WHERE id=?', (iid,)).fetchone()
    result = invitation(db, row)
    db.commit()
    return result


def decide(db, user_id, invite_id, action):
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM family_invites WHERE id=?', (invite_id,)).fetchone()
    if not row or row['inviter_id' if action == 'revoke' else 'target_user_id'] != user_id:
        raise HTTPException(404, detail='Invitation not found')
    if invitation(db, row)['status'] != 'pending':
        raise HTTPException(409, detail={'code': 'invitation_unavailable'})
    active = db.execute('SELECT archived FROM families WHERE id=?', (row['family_id'],)).fetchone()
    if active['archived'] or current_id(db, row['inviter_id']) != row['family_id']:
        raise HTTPException(409, detail={'code': 'invitation_unavailable'})
    if action == 'accept':
        if current_id(db, user_id):
            raise HTTPException(409, detail={'code': 'already_in_family'})
        add_members(db, row['family_id'], [user_id])
    db.execute('UPDATE family_invites SET status=?,decided_at=? WHERE id=?', ({'accept':'accepted','reject':'rejected','revoke':'revoked'}[action], datetime.now(UTC).isoformat(), invite_id))
    result = context(db, user_id) if action == 'accept' else invitation(db, db.execute('SELECT * FROM family_invites WHERE id=?', (invite_id,)).fetchone())
    db.commit()
    return result


def remove_membership(db, user_id, fid):
    """Revoke one member inside the caller's write transaction."""
    db.execute('DELETE FROM family_members WHERE user_id=?', (user_id,))
    db.execute('UPDATE users SET membership_version=membership_version+1 WHERE id=?', (user_id,))
    db.execute("UPDATE family_invites SET status='revoked',decided_at=? WHERE family_id=? AND inviter_id=? AND status='pending'", (datetime.now(UTC).isoformat(),fid,user_id))
    db.execute("UPDATE change_requests SET status=CASE WHEN status='pending' THEN 'stale' ELSE status END,withdrawn=1 WHERE family_id=? AND (requester_id=? OR owner_id=?)", (fid,user_id,user_id))
    db.execute("""UPDATE category_changes SET status='stale',decided_at=?
        WHERE scope='shared' AND namespace=? AND status IN ('preview','pending')
        AND EXISTS (SELECT 1 FROM json_each(category_changes.context_json,'$.members') WHERE key=?)""",(datetime.now(UTC).isoformat(),fid,user_id))
    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE user_id=? AND (report_id IS NULL OR report_id IN (SELECT id FROM reports WHERE scope='shared' AND owner_id=?)) AND status!='sent'", (user_id,fid))


def archive_family(db, fid):
    db.execute('UPDATE families SET archived=1 WHERE id=?', (fid,))
    db.execute("UPDATE family_invites SET status='revoked',decided_at=? WHERE family_id=? AND status='pending'", (datetime.now(UTC).isoformat(),fid))
    db.execute("UPDATE change_requests SET status=CASE WHEN status='pending' THEN 'stale' ELSE status END,withdrawn=1 WHERE family_id=?", (fid,))
    db.execute("UPDATE monthly_settings SET enabled=0 WHERE scope='shared' AND namespace=?", (fid,))
    db.execute("DELETE FROM report_tasks WHERE report_id IN (SELECT id FROM reports WHERE scope='shared' AND owner_id=?)", (fid,))
    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE report_id IN (SELECT id FROM reports WHERE scope='shared' AND owner_id=?) AND status!='sent'", (fid,))


def require_owner(db, user_id, fid):
    require_shared(db, user_id, fid)
    if db.execute('SELECT owner_id FROM families WHERE id=?', (fid,)).fetchone()['owner_id'] != user_id:
        raise HTTPException(403, detail={'code': 'family_owner_required'})


def leave(db, user_id, fid):
    db.execute('BEGIN IMMEDIATE')
    require_shared(db, user_id, fid)
    owner = db.execute('SELECT owner_id FROM families WHERE id=?', (fid,)).fetchone()['owner_id']
    if owner == user_id and db.execute('SELECT 1 FROM family_members WHERE family_id=? AND user_id!=?', (fid,user_id)).fetchone():
        raise HTTPException(409, detail={'code': 'owner_transfer_or_dissolve_required'})
    remove_membership(db, user_id, fid)
    if not db.execute('SELECT 1 FROM family_members WHERE family_id=?', (fid,)).fetchone():
        archive_family(db, fid)
    result = context(db, user_id)
    db.commit()
    return result


def remove(db, user_id, fid, target_id):
    db.execute('BEGIN IMMEDIATE')
    require_owner(db, user_id, fid)
    if target_id == user_id:
        raise HTTPException(409, detail={'code': 'use_leave'})
    if current_id(db, target_id) != fid:
        raise HTTPException(404, detail='Member not found')
    remove_membership(db, target_id, fid)
    result = context(db, user_id)
    db.commit()
    return result


def transfer_owner(db, user_id, fid, target_id, *, leave_after=False):
    db.execute('BEGIN IMMEDIATE')
    require_owner(db, user_id, fid)
    if target_id == user_id:
        raise HTTPException(409, detail={'code': 'owner_transfer_target_required'})
    if current_id(db, target_id) != fid:
        raise HTTPException(404, detail='Member not found')
    db.execute('UPDATE families SET owner_id=? WHERE id=?', (target_id,fid))
    if leave_after:
        remove_membership(db, user_id, fid)
    result = context(db, user_id)
    db.commit()
    return result


def dissolve(db, user_id, fid):
    db.execute('BEGIN IMMEDIATE')
    require_owner(db, user_id, fid)
    for member in db.execute('SELECT user_id FROM family_members WHERE family_id=?', (fid,)).fetchall():
        remove_membership(db, member['user_id'], fid)
    archive_family(db, fid)
    result = context(db, user_id)
    db.commit()
    return result
