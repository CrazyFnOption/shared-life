"""Creator decisions bound to the exact record, restore and member relationships."""
from datetime import datetime,UTC
from server.accounting import families
from server.memos import models


def invalidate(db,memo_id,exclude_id=None):
    db.execute("UPDATE memo_deletion_requests SET status='stale',version=version+1,decided_at=? WHERE memo_id=? AND status='pending' AND id!=?",(datetime.now(UTC).isoformat(),memo_id,exclude_id or ''))


def dto(row):
    return {key:row[key] for key in ('id','memo_id','requester_id','author_id','target_version','reason','status','version','created_at','decided_at')}


def require(db,user_id,identity):
    from .service import require_record,fail
    row=db.execute('SELECT * FROM memo_deletion_requests WHERE id=?',(identity,)).fetchone()
    if not row:fail('memo_not_found',404)
    require_record(db,user_id,row['memo_id'],include_deleted=True)
    return row


def apply(db,user_id,operation):
    from .service import source,parse,require_record,fail,memo_dto
    src=source(db,user_id);identity=str(operation.entity_id);now=datetime.now(UTC).isoformat();effects={}
    if operation.kind=='create':
        data=parse(models.DeletionData,operation.data)
        memo=require_record(db,user_id,data['memo_id'])
        if src['scope']!='shared' or memo['author_id']==user_id:fail('creator_can_delete_directly',422)
        if memo['version']!=data['target_version']:fail('memo_conflict',current={**memo_dto(db,memo),'entity_type':'memo'},current_version=memo['version'])
        author=families.relation(db,memo['author_id'],src['family_id'])
        if not author:fail('author_unavailable')
        if operation.base_version!=0:fail('invalid_base_version',422)
        if db.execute('SELECT 1 FROM memo_deletion_requests WHERE id=?',(identity,)).fetchone():fail('memo_id_reused')
        if db.execute("SELECT 1 FROM memo_deletion_requests WHERE memo_id=? AND requester_id=? AND status='pending'",(memo['id'],user_id)).fetchone():fail('deletion_request_pending')
        db.execute("INSERT INTO memo_deletion_requests(id,memo_id,requester_id,author_id,target_version,reason,status,source_restore_id,requester_epoch,author_epoch,version,created_at) VALUES(?,?,?,?,?,?,'pending',?,?,?,1,?)",(identity,memo['id'],user_id,memo['author_id'],data['target_version'],data['reason'],src['restore_id'],src['relation_epoch'],author['relation_epoch'],now))
    elif operation.kind=='decide':
        data=parse(models.DecideData,operation.data);row=require(db,user_id,identity);memo=require_record(db,user_id,row['memo_id'])
        if user_id!=row['author_id']:fail('creator_required',403)
        if row['version']!=operation.base_version:fail('memo_conflict',current={**dto(row),'entity_type':'deletion_request'},current_version=row['version'])
        requester=families.relation(db,row['requester_id'],src['family_id']);author=families.relation(db,row['author_id'],src['family_id'])
        if row['status']!='pending' or row['target_version']!=memo['version'] or row['source_restore_id']!=src['restore_id'] or not requester or not author or requester['relation_epoch']!=row['requester_epoch'] or author['relation_epoch']!=row['author_epoch']:fail('deletion_request_stale')
        if data['accepted']:
            db.execute('UPDATE memos SET deleted=1,version=version+1,updated_by=?,updated_at=? WHERE id=?',(user_id,now,memo['id']))
            invalidate(db,memo['id'],identity)
            effects={'memos':[memo_dto(db,db.execute('SELECT * FROM memos WHERE id=?',(memo['id'],)).fetchone())]}
        db.execute('UPDATE memo_deletion_requests SET status=?,version=version+1,decided_at=? WHERE id=?',('accepted' if data['accepted'] else 'rejected',now,identity))
    else:fail('unsupported_memo_operation',422)
    return dto(db.execute('SELECT * FROM memo_deletion_requests WHERE id=?',(identity,)).fetchone()),effects
