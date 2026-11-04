"""Mounted using the application's existing source and session dependencies."""
from typing import Annotated
from urllib.parse import quote
from uuid import UUID
from fastapi import Request, Response
from . import service
from .models import Operation

def register_routes(app,Database,ExpectedUser):
    @app.get('/api/v2/plans/sync')
    def sync(db:Database,user:ExpectedUser):return service.sync(db,user['id'])

    @app.post('/api/v2/plans/operations')
    def operate(body:Operation,db:Database,user:ExpectedUser):return service.apply(db,user['id'],body)

    @app.get('/api/v2/plans/operations/{op_id}')
    def receipt(op_id:UUID,db:Database,user:ExpectedUser):
        db.execute('BEGIN')
        try:
            origin=service.source(db,user['id']);old=service.previous(db,user['id'],str(op_id))
            if old is None:service.fail('plan_not_found',404)
            db.commit();return {'source':origin,'receipt':old}
        except BaseException:db.rollback();raise

    @app.get('/api/v2/plans/records/{record_id}')
    def record(record_id:UUID,db:Database,user:ExpectedUser):
        db.execute('BEGIN')
        try:
            origin=service.source(db,user['id']);identity=str(record_id)
            row=db.execute('SELECT kind FROM plan_records WHERE id=?',(identity,)).fetchone()
            entity=row['kind'] if row else 'task';result=service.dto(db,entity,service.require(db,user['id'],entity,identity))
            db.commit();return {'source':origin,'record':result}
        except BaseException:db.rollback();raise

    @app.put('/api/v2/plans/records/{record_id}/attachments/{attachment_id}')
    async def upload(record_id:UUID,attachment_id:UUID,request:Request,db:Database,user:ExpectedUser):
        # Authorize before reading the body; recheck under the final write lock.
        service.attachment_parent(db,user['id'],str(record_id))
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>20*1024*1024:service.fail('attachment_too_large',413)
        return service.put_attachment(db,user['id'],str(record_id),str(attachment_id),bytes(data),request.headers.get('content-type','').split(';')[0].strip().lower())

    @app.get('/api/v2/plans/records/{record_id}/attachments/{attachment_id}')
    def download(record_id:UUID,attachment_id:UUID,db:Database,user:ExpectedUser):
        row=service.get_attachment(db,user['id'],str(record_id),str(attachment_id))
        disposition='inline' if row['kind']=='photo' else 'attachment'
        return Response(content=row['data'],media_type=row['mime_type'],headers={'Content-Disposition':f"{disposition}; filename*=UTF-8''{quote(row['name'],safe='')}",'X-Content-Type-Options':'nosniff'})
