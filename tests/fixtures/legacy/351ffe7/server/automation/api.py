"""Small account-bound settings, reports and exports API helpers."""
import csv
import io
import json
import os
from datetime import datetime, UTC
from typing import Literal
from urllib.parse import urlparse
from pydantic import Field, StrictBool, StrictInt, field_validator
from fastapi import HTTPException
from server.api.models import RequestModel
from server.accounting import ledger, images
from server.accounting.categories import visible_categories
from server.automation import reports

class Monthly(RequestModel):
    enabled: StrictBool
    time: str = Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
class Weekly(Monthly):
    weekday: StrictInt = Field(ge=0,le=6)
class Settings(RequestModel):
    monthly: Monthly | None = None
    weekly: Weekly | None = None
class Keys(RequestModel):
    p256dh: str = Field(min_length=1,max_length=256,pattern=r'^[A-Za-z0-9_=-]+$')
    auth: str = Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_=-]+$')
class Endpoint(RequestModel):
    endpoint: str = Field(min_length=1,max_length=2048)
    @field_validator('endpoint')
    @classmethod
    def secure_endpoint(cls,value):
        parsed=urlparse(value)
        # Only known browser push endpoints may receive server requests, preventing SSRF.
        host=parsed.hostname or ''
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not any(host==d or host.endswith('.'+d) for d in ('fcm.googleapis.com','push.services.mozilla.com','notify.windows.com','web.push.apple.com')):
            raise ValueError('Unsupported browser push endpoint')
        return value
class Subscription(Endpoint):
    keys: Keys
class Empty(RequestModel):
    pass

def get_settings(db,user_id,worker):
    state=reports.state(db)
    weekly=db.execute('SELECT enabled,weekday,time FROM weekly_settings WHERE user_id=?',(user_id,)).fetchone()
    counts={r['active']:r['n'] for r in db.execute('SELECT active,count(*) n FROM push_subscriptions WHERE owner_id=? GROUP BY active',(user_id,))}
    return {'monthly':{'enabled':bool(state['monthly_enabled']),'time':state['monthly_time'],'updated_by':state['updated_by']},
            'weekly':dict(weekly,enabled=bool(weekly['enabled'])) if weekly else {'enabled':False,'weekday':6,'time':'19:00'},
            'automation_paused':bool(state['automation_paused']),'provider_configured':worker.primary is not None,
            'push_available':worker.push is not None,'vapid_public_key':os.getenv('SHARED_EXPENSE_VAPID_PUBLIC_KEY') if worker.push else None,
            'push_subscription_state':'active' if counts.get(1) else ('expired' if counts.get(0) else 'none'),
            'notification_pending':db.execute("SELECT count(*) FROM notification_outbox WHERE user_id=? AND status IN ('pending','failed','uncertain')",(user_id,)).fetchone()[0],
            'notification_uncertain':db.execute("SELECT count(*) FROM notification_outbox WHERE user_id=? AND status='uncertain'",(user_id,)).fetchone()[0]}

def set_settings(db,user_id,body,worker):
    db.execute('BEGIN IMMEDIATE')
    if body.monthly is not None:
        old=reports.state(db)
        start=old['monthly_start'] or (reports.previous_month(worker.clock()) if body.monthly.enabled else None)
        db.execute('UPDATE app_state SET monthly_enabled=?,monthly_time=?,monthly_start=?,updated_by=? WHERE singleton=1',(body.monthly.enabled,body.monthly.time,start,user_id))
    if body.weekly is not None:
        db.execute('INSERT INTO weekly_settings(user_id,enabled,weekday,time) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,weekday=excluded.weekday,time=excluded.time',(user_id,body.weekly.enabled,body.weekly.weekday,body.weekly.time))
    result=get_settings(db,user_id,worker);db.commit();return result

def export(db,user_id,scope,format):
    db.execute('BEGIN')
    expenses=[]
    for row in db.execute("SELECT id FROM expenses WHERE voided=0 AND (scope='shared' OR owner_id=?) ORDER BY occurred_at,id",(user_id,)):
        e=ledger.shared_expense(ledger.read_expense(db,row['id']))
        if scope=='all' or e['scope']==scope:
            expenses.append(e)
    categories=[c for c in visible_categories(db,user_id) if scope=='all' or c['scope']==scope]
    image_list=[i for i in images.visible_images(db,user_id) if i['expense_id'] in {e['id'] for e in expenses}]
    db.commit()
    if format=='json':
        return json.dumps({'schema_version':1,'exported_at':datetime.now(UTC).isoformat(),'timezone':'Asia/Shanghai','expenses':expenses,'categories':categories,'images':image_list},ensure_ascii=False).encode()
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    fields=['id','owner_id','scope','amount_cents','refund_cents','net_amount_cents','occurred_at','purpose','location','category','rating','rating_reason']
    writer.writerow(fields)
    def safe(value):
        if isinstance(value,str) and value.lstrip(' \t\r\n').startswith(('=','+','-','@')):
            return "'"+value
        return value
    for e in expenses:
        row=dict(e,refund_cents=e['amount_cents']-e['net_amount_cents'])
        writer.writerow([safe(row.get(k,'')) for k in fields])
    return ('\ufeff'+stream.getvalue()).encode('utf-8')
