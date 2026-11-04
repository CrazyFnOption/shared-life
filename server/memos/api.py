"""Routes share B authentication, response provenance and transaction-bound source."""
from urllib.parse import quote
from uuid import UUID
from datetime import date,datetime,UTC
from server.memos import calendar,events
from fastapi import Request, Response
from server.memos import models, service


def register_routes(app,Database,ExpectedUser):
    @app.get('/api/v2/memos/sync')
    def sync(db:Database,user:ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        result=service.snapshot(db,user['id']);db.commit();return result

    @app.post('/api/v2/memos/operations')
    def operation(body:models.Operation,db:Database,user:ExpectedUser):
        return service.submit(db,user['id'],body)

    @app.get('/api/v2/memos/operations/{op_id}')
    def receipt(op_id:UUID,db:Database,user:ExpectedUser):
        import json
        db.execute('BEGIN')
        old=service.receipt_lookup(db,user['id'],str(op_id))
        result={'source':service.source(db,user['id']),'receipt':json.loads(old['receipt_json'])}
        db.commit();return result

    @app.get('/api/v2/memos/records/{memo_id}')
    def record(memo_id:UUID,db:Database,user:ExpectedUser):
        db.execute('BEGIN')
        result=service.detail(db,user['id'],str(memo_id));db.commit();return result

    @app.put('/api/v2/memos/records/{memo_id}/attachments/{attachment_id}')
    async def upload(memo_id:UUID,attachment_id:UUID,request:Request,db:Database,user:ExpectedUser):
        row=service.require_attachment(db,user['id'],str(memo_id),str(attachment_id))
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>min(service.MAX_FILE_BYTES,row['size_bytes']):
                service.fail('attachment_too_large',413)
            data.extend(chunk)
        mime=request.headers.get('content-type','').split(';',1)[0].strip().lower()
        return service.upload(db,user['id'],str(memo_id),str(attachment_id),bytes(data),mime)

    @app.get('/api/v2/memos/records/{memo_id}/attachments/{attachment_id}')
    def download(memo_id:UUID,attachment_id:UUID,db:Database,user:ExpectedUser):
        db.execute('BEGIN')
        row=service.require_attachment(db,user['id'],str(memo_id),str(attachment_id))
        if row['state']!='uploaded':service.fail('attachment_not_uploaded')
        response=Response(row['data'],media_type=row['mime_type'],headers={'X-Content-Type-Options':'nosniff','Content-Disposition':('inline' if row['kind']=='photo' else 'attachment')+"; filename*=UTF-8''"+quote(row['name'],safe='')})
        db.commit();return response

    @app.get('/api/v2/memos/messages')
    def messages(db:Database,user:ExpectedUser,include_ignored:bool=False):
        db.execute('BEGIN IMMEDIATE')
        src=service.source(db,user['id']);events.generate(db,datetime.now(UTC),scope=src['scope'],namespace=service.namespace(src))
        result={'source':src,'messages':events.messages(db,user['id'],include_ignored=include_ignored)};db.commit();return result

    @app.post('/api/v2/memos/messages/{event_id}/ignore')
    def ignore(event_id:str,body:models.Empty,db:Database,user:ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        result=events.ignore(db,user['id'],event_id);db.commit();return result

    @app.get('/api/v2/memos/calendar')
    def occurrences(from_date:date,to_date:date,db:Database,user:ExpectedUser):
        if not 1901<=from_date.year<=2099 or not 1901<=to_date.year<=2099 or to_date<from_date or (to_date-from_date).days>365:service.fail('invalid_calendar_range',422)
        db.execute('BEGIN');src=service.source(db,user['id']);ns=service.namespace(src);items=[];versions={}
        for row in db.execute("SELECT * FROM memos WHERE scope=? AND namespace=? AND kind='schedule' AND deleted=0 AND archived=0",(src['scope'],ns)):
            versions[row['id']]=row['version']
            items.extend(calendar.schedule_occurrences(service.memo_dto(db,row),from_date,to_date))
        items.sort(key=lambda r:(r['date'],r['start_time'] is not None,r['start_time'] or '',r['memo_id']))
        db.commit();return {'source':src,'occurrences':items,'versions':versions}

    @app.post('/api/v2/memos/messages/{event_id}/restore')
    def restore_message(event_id:str,body:models.Empty,db:Database,user:ExpectedUser):
        db.execute('BEGIN IMMEDIATE')
        result=events.restore_message(db,user['id'],event_id);db.commit();return result

    @app.get('/api/v2/memos/notifications/{notification_id}')
    def notification(notification_id:UUID,db:Database,user:ExpectedUser):
        from .notifications import resolve
        db.execute('BEGIN')
        result=resolve(db,user['id'],str(notification_id));db.commit();return result
