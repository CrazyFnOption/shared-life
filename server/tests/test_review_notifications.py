"""Current-scope notification regression checks; fictional DB, no external Push/model."""
from datetime import datetime,UTC
from urllib.parse import urlsplit,parse_qs
from uuid import uuid4
import json
from server.accounting.db import connect
from server.automation import reports,notifications
from server.automation.worker import Worker
from server.tests.test_multi_family import home,headers,create,join,submit
from server.tests.test_api import operation


def uid(client):return client.get('/api/auth/me').json()['user']['id']
def inbox(client,fid=None,ignored=False):
    r=client.get('/api/v2/memos/messages',headers=headers(client,fid),params={'include_ignored':ignored})
    assert r.status_code==200,r.text
    return r.json()['messages']
def resolve(client,notification,fid=None):return client.get('/api/v2/memos/notifications/'+notification,headers=headers(client,fid))
def ready_report(path,client,fid=None,month='2026-10'):
    with connect(path) as db:
        user=uid(client);rid=str(uuid4());scope='shared' if fid else 'private';owner=fid or user
        version=reports.version(reports.snapshot(db,scope,owner,month))
        db.execute('INSERT INTO reports(id,scope,owner_id,month,status,data_version,generated_at,content_json) VALUES(?,?,?,?,?,?,?,?)',(rid,scope,owner,month,'ready',version,datetime.now(UTC).isoformat(),json.dumps({'summary':'fictional','items':[]})))
        reports.enqueue_notification(db,user,f'report:{rid}:{version}',datetime.now(UTC).timestamp(),rid,version)
        notification=db.execute('SELECT id FROM notification_outbox WHERE report_id=? AND user_id=?',(rid,user)).fetchone()[0]
    return rid,notification


def test_report_bell_is_current_scope_and_view_ignore_restore_leave_outbox_alone(home):
    path,app,(a,b,c)=home;fid=create(a,'fictional');join(a,b,fid);rid,nid=ready_report(path,a,fid)
    message=next(m for m in inbox(a,fid) if m['kind']=='monthly_report')
    assert message['target_id']==rid and not message['ignored']
    assert not inbox(a) and not inbox(b,fid)
    assert resolve(a,nid,fid).json()['message']['id']==message['id']
    assert len(inbox(a,fid))==1 # viewing is not an acknowledgement
    with connect(path) as db:before=tuple(db.execute('SELECT * FROM notification_outbox WHERE id=?',(nid,)).fetchone())
    assert a.post('/api/v2/memos/messages/'+message['id']+'/ignore',headers=headers(a,fid),json={}).status_code==200
    assert inbox(a,fid)==[] and inbox(a,fid,True)[0]['ignored']
    assert a.post('/api/v2/memos/messages/'+message['id']+'/restore',headers=headers(a,fid),json={}).status_code==200
    assert not inbox(a,fid)[0]['ignored']
    with connect(path) as db:assert tuple(db.execute('SELECT * FROM notification_outbox WHERE id=?',(nid,)).fetchone())==before
    assert resolve(a,nid).status_code==404
    assert resolve(b,nid,fid).status_code==404
    assert resolve(c,nid).status_code==404


def test_old_report_link_reads_current_state_without_reviving_old_version_or_old_epoch(home):
    path,app,(a,b,c)=home;fid=create(a,'fictional');rid,nid=ready_report(path,a,fid)
    with connect(path) as db:db.execute("UPDATE reports SET data_version='changed',status='stale',content_json=NULL WHERE id=?",(rid,))
    assert inbox(a,fid)==[]
    response=resolve(a,nid,fid);assert response.status_code==200
    assert '当前状态' in response.json()['notice'] and response.json()['message']['target_id']==rid
    with connect(path) as db:db.execute("UPDATE reports SET status='ready',content_json='{}' WHERE id=?",(rid,))
    assert '已更新' in resolve(a,nid,fid).json()['notice']
    with connect(path) as db:db.execute('UPDATE family_members SET relation_epoch=relation_epoch+1 WHERE user_id=? AND family_id=?',(uid(a),fid))
    assert resolve(a,nid,fid).status_code==404


def test_weekly_account_link_requires_explicit_scope_choice_and_lists_only_own_unrated(home):
    path,app,(a,b,c)=home;first=create(a,'first');second=create(a,'second');join(a,b,second)
    own=operation('shared');own['data']['rating']=None;own['data']['rating_reason']='';first_item=submit(a,first,own)
    other=operation('shared');other['data']['rating']=None;other['data']['rating_reason']='';second_item=submit(b,second,other)
    with connect(path) as db:
        # Legacy unrated rows: new creates intentionally default to 3 in the existing product.
        db.execute('UPDATE expenses SET rating=NULL WHERE id IN (?,?)',(first_item['id'],second_item['id']))
        reports.enqueue_notification(db,uid(a),'weekly:2026-11-09',datetime.now(UTC).timestamp())
        row=db.execute('SELECT * FROM notification_outbox WHERE user_id=?',(uid(a),)).fetchone();nid=row['id'];payload=notifications.payload(db,row)
    assert parse_qs(urlsplit(payload['url']).query)['kind']==['weekly_review']
    assert resolve(a,nid).json()['choose_scope'] is True
    assert resolve(a,nid,second).json()['choose_scope'] is True
    assert not inbox(a) and not inbox(a,second)
    message=next(m for m in inbox(a,first) if m['kind']=='weekly_review')
    assert resolve(a,nid,first).json()['message']['id']==message['id']
    assert a.post('/api/v2/memos/messages/'+message['id']+'/ignore',headers=headers(a,first),json={}).status_code==200
    assert inbox(a,first)==[] and inbox(a,first,True)[0]['ignored']
    with connect(path) as db:
        assert db.execute('SELECT status FROM notification_outbox WHERE id=?',(nid,)).fetchone()[0]=='pending'
        db.execute('UPDATE expenses SET rating=3 WHERE owner_id=?',(uid(a),))
    assert '全部处理' in resolve(a,nid).json()['notice']
    assert resolve(b,nid,second).status_code==404
    with connect(path) as db:
        current=dict(db.execute('SELECT * FROM notification_outbox WHERE id=?',(nid,)).fetchone())
        assert notifications.payload(db,current) is None
        db.execute('INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,?,?)',(uid(a),'https://fictional.example/push','{}'))
    calls=[]
    class NoNetworkPush:
        def send(self,subscription,payload):calls.append(payload)
    Worker(path,push=NoNetworkPush())._send_one(current,datetime.now(UTC).timestamp())
    assert calls==[]
    with connect(path) as db:assert db.execute('SELECT status FROM notification_outbox WHERE id=?',(nid,)).fetchone()[0]=='cancelled'


def test_worker_uses_fixed_private_payload_and_target_link_without_external_delivery(home):
    path,app,(a,b,c)=home;rid,nid=ready_report(path,a)
    sent=[]
    class LocalPush:
        def send(self,subscription,payload):sent.append(payload)
    with connect(path) as db:
        db.execute('INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,?,?)',(uid(a),'https://fictional.example/push','{}'))
        row=dict(db.execute('SELECT * FROM notification_outbox WHERE id=?',(nid,)).fetchone())
    Worker(path,push=LocalPush())._send_one(row,datetime.now(UTC).timestamp())
    assert len(sent)==1 and sent[0]['kind']=='monthly_report'
    assert sent[0]['body']=='月度复盘已生成，请打开查看'
    params=parse_qs(urlsplit(sent[0]['url']).query)
    assert params['notification']==[nid] and params['scope']==['private']
    assert 'fictional' not in sent[0]['body'] and 'report_id' not in params
