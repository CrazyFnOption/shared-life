"""Integration review regressions and simulated C WebPush, no real transport."""
from datetime import datetime,timedelta
import json
from uuid import uuid4
from urllib.parse import urlparse,parse_qs
import pytest
from server.accounting.db import connect
from server.automation.worker import Worker
from server.automation.providers import PushError
from server.automation.backups import backup,restore,validate_database
from server.memos import calendar,events
from server.tests.test_multi_family import home,headers,create,join,management
from server.tests.test_memos import op,saved,category


def relative_record(a,day):
    rule={'id':str(uuid4()),'mode':'relative','at':None,'offset_minutes':0,'recipient_ids':[],'all_members':False}
    data={'kind':'schedule','category_id':None,'title':'私有日程正文不出站','body_markdown':'秘密正文','attachments':[],'schedule':{'start_date':day.isoformat(),'start_time':'00:00','end_date':None,'end_time':None},'reminders':[rule]}
    return saved(op(a,'memo',str(uuid4()),'create',data)),data


def test_i02_relative_move_cancels_prior_occurrence_but_title_and_end_edits_preserve(home):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();item,data=relative_record(a,today)
    messages=a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages'];old=messages[0]['id']
    edited={**data,'title':'仅改标题','schedule':{**data['schedule'],'end_date':today.isoformat(),'end_time':'01:00'}}
    saved(op(a,'memo',item['id'],'update',edited,1))
    assert a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages'][0]['id']==old
    moved={**edited,'schedule':{**edited['schedule'],'start_date':(today+timedelta(days=1)).isoformat(),'end_date':(today+timedelta(days=1)).isoformat()}}
    saved(op(a,'memo',item['id'],'update',moved,2))
    assert not a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages']
    with connect(path) as db:
        assert db.execute('SELECT status FROM memo_reminder_events WHERE id=?',(old,)).fetchone()[0]=='cancelled'
        assert db.execute('SELECT status FROM notification_outbox WHERE dedup_key=?',('memo:'+old,)).fetchone()[0]=='cancelled'


def test_i03_completed_cycle_is_persistently_resolved_across_fixed_reopen(home,monkeypatch):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();yesterday=today-timedelta(days=1)
    class Yesterday(datetime):
        @classmethod
        def now(cls,tz=None):return calendar.anchor(yesterday,'23:00').astimezone(tz) if tz else calendar.anchor(yesterday,'23:00').replace(tzinfo=None)
    recurrence={'frequency':'daily','interval':1,'calendar':'solar','start_date':yesterday.isoformat(),'until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}
    rule={'id':str(uuid4()),'mode':'absolute','at':calendar.anchor(yesterday).isoformat(),'offset_minutes':0,'recipient_ids':[],'all_members':False}
    item=saved(op(a,'memo',str(uuid4()),'create',{'category_id':category(a)['id'],'title':'循环记录','reminders':[rule],'recurrence':recurrence}))
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');events.generate(db,calendar.anchor(yesterday,'12:00'));db.commit()
        old=db.execute('SELECT id FROM memo_reminder_events WHERE memo_id=?',(item['id'],)).fetchone()[0]
    ignored=a.post('/api/v2/memos/messages/'+old+'/ignore',headers=headers(a),json={});assert ignored.status_code==200
    from server.memos import service
    monkeypatch.setattr(service,'datetime',Yesterday)
    saved(op(a,'memo',item['id'],'complete',{'completed':True},1))
    monkeypatch.undo()
    after=a.get('/api/v2/memos/messages?include_ignored=true',headers=headers(a)).json()['messages']
    assert old not in {m['id'] for m in after}
    assert len([m for m in after if m['record_id']==item['id']])==1
    assert a.post('/api/v2/memos/messages/'+old+'/restore',headers=headers(a),json={}).status_code==404
    with connect(path) as db:
        assert db.execute('SELECT status FROM memo_reminder_events WHERE id=?',(old,)).fetchone()[0]=='cancelled'
        assert db.execute('SELECT count(*) FROM memo_message_ignores WHERE event_id=?',(old,)).fetchone()[0]==1
        assert db.execute('SELECT completed FROM memos WHERE id=?',(item['id'],)).fetchone()[0]==0


def test_i03_older_program_completed_state_settles_before_next_cycle_reopen(home):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();yesterday=today-timedelta(days=1)
    recurrence={'frequency':'daily','interval':1,'calendar':'solar','start_date':yesterday.isoformat(),'until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}
    rule={'id':str(uuid4()),'mode':'absolute','at':calendar.anchor(yesterday).isoformat(),'offset_minutes':0,'recipient_ids':[],'all_members':False}
    item=saved(op(a,'memo',str(uuid4()),'create',{'category_id':category(a)['id'],'title':'兼容完成事实','reminders':[rule],'recurrence':recurrence}))
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');events.generate(db,calendar.anchor(yesterday,'12:00'));db.commit()
        db.execute('UPDATE memos SET completed=1,completed_at=? WHERE id=?',(calendar.anchor(yesterday,'23:00').isoformat(),item['id']))
    result=a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages']
    assert len([r for r in result if r['record_id']==item['id']])==1
    with connect(path) as db:
        assert [r[0] for r in db.execute('SELECT status FROM memo_reminder_events WHERE memo_id=? ORDER BY occurrence',(item['id'],))]==['cancelled','ready']


def test_calendar_versions_cover_empty_range_and_occurrences_source(home):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();item,data=relative_record(a,today)
    month=a.get('/api/v2/memos/calendar',headers=headers(a),params={'from_date':today.isoformat(),'to_date':today.isoformat()}).json()
    assert month['versions']=={item['id']:1} and month['occurrences'][0]['record_version']==1
    future=(today+timedelta(days=1)).isoformat()
    empty=a.get('/api/v2/memos/calendar',headers=headers(a),params={'from_date':future,'to_date':future}).json()
    assert empty['versions']=={item['id']:1} and not empty['occurrences']
    stranger=b.get('/api/v2/memos/calendar',headers=headers(b),params={'from_date':today.isoformat(),'to_date':today.isoformat()}).json()
    assert not stranger['versions'] and not stranger['occurrences']


def test_i04_c_and_d_conflicts_return_only_authorized_current_dto(home):
    path,app,(a,b,c)=home;fid=create(a,'家');join(a,b,fid)
    memo=saved(op(a,'memo',str(uuid4()),'create',{'category_id':category(a,fid)['id'],'title':'初稿'},fid=fid))
    changed=saved(op(a,'memo',memo['id'],'update',{'category_id':memo['category_id'],'title':'服务器稿','body_markdown':'服务端正文'},1,fid))
    conflict=op(b,'memo',memo['id'],'update',{'category_id':memo['category_id'],'title':'本机稿'},1,fid)
    assert conflict.status_code==409
    assert conflict.json()['detail']['current']['entity_type']=='memo'
    assert conflict.json()['detail']['current']['body_markdown']=='服务端正文'
    def plan(body,client=a,scope=fid):return client.post('/api/v2/plans/operations',headers=headers(client,scope),json=body)
    identity=str(uuid4());body={'op_id':str(uuid4()),'entity_type':'wish','entity_id':identity,'kind':'create','base_version':0,'data':{'title':'初稿','wish_kind':'未来旅行'}}
    saved(plan(body));updated={**body,'op_id':str(uuid4()),'kind':'update','base_version':1,'data':{'title':'服务器愿望','body_markdown':'服务器详情','wish_kind':'未来旅行'}};saved(plan(updated))
    attempted={**updated,'op_id':str(uuid4()),'data':{**updated['data'],'title':'未同步本机稿'}}
    rejected=plan(attempted,b);assert rejected.status_code==409
    current=rejected.json()['detail']['current'];assert current['entity_type']=='wish' and current['id']==identity and current['version']==2 and current['body_markdown']=='服务器详情'
    assert plan(attempted,c,None).status_code==404
    assert saved(plan({**attempted,'op_id':str(uuid4()),'base_version':current['version']},b))['title']=='未同步本机稿'


class FakePush:
    def __init__(self,status=None,hook=None):self.sent=[];self.status=status;self.hook=hook
    def send(self,subscription,payload):
        self.sent.append((subscription,payload))
        if self.hook:self.hook()
        if self.status is not None:raise PushError(self.status)


def subscribe(path,user_id):
    with connect(path) as db:db.execute('INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,?,?)',(user_id,'https://fictional-push.invalid/subscription',json.dumps({'p256dh':'fictional','auth':'fictional'})))


def test_memo_outbox_simulated_sender_privacy_deep_link_and_restore(home,tmp_path):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();item,data=relative_record(a,today)
    a.get('/api/v2/memos/messages',headers=headers(a))
    subscribe(path,item['author_id']);push=FakePush();worker=Worker(path,push=push)
    now=datetime.now(calendar.ZONE);worker._notifications(now);worker._notifications(now)
    assert len(push.sent)==1
    payload=push.sent[0][1];assert payload['title']=='同在' and data['title'] not in json.dumps(payload) and data['body_markdown'] not in json.dumps(payload)
    args=parse_qs(urlparse(payload['url']).query);outbox_id=args['notification'][0]
    result=a.get('/api/v2/memos/notifications/'+outbox_id,headers=headers(a));assert result.status_code==200,result.text
    assert result.json()['message']['record_id']==item['id'] and result.json()['source']['user_id']==item['author_id']
    assert b.get('/api/v2/memos/notifications/'+outbox_id,headers=headers(b)).status_code==404
    saved_backup=backup(path,tmp_path/'backup');restored=tmp_path/'restored.sqlite3';restore(saved_backup,restored,saved_backup/'credentials.json')
    restored_sender=FakePush();Worker(restored,push=restored_sender)._notifications(now)
    assert not restored_sender.sent
    with connect(restored) as db:validate_database(db)


def test_memo_outbox_bounded_retry_and_change_before_send(home):
    path,app,(a,b,c)=home;today=datetime.now(calendar.ZONE).date();item,data=relative_record(a,today)
    a.get('/api/v2/memos/messages',headers=headers(a));subscribe(path,item['author_id'])
    push=FakePush(503);worker=Worker(path,push=push);now=datetime.now(calendar.ZONE)
    for n in range(5):worker._notifications(now+timedelta(minutes=6*n))
    assert len(push.sent)==3
    with connect(path) as db:assert db.execute("SELECT status FROM notification_outbox WHERE dedup_key LIKE 'memo:%'").fetchone()[0]=='failed'
    second,data2=relative_record(a,today);a.get('/api/v2/memos/messages',headers=headers(a))
    tomorrow=(today+timedelta(days=1)).isoformat();saved(op(a,'memo',second['id'],'update',{**data2,'schedule':{**data2['schedule'],'start_date':tomorrow}},1))
    fresh=FakePush();Worker(path,push=fresh)._notifications(now+timedelta(hours=1));assert not fresh.sent


def test_memo_outbox_rechecks_revocation_after_simulated_external_send(home):
    path,app,(a,b,c)=home;fid=create(a,'家');join(a,b,fid);today=datetime.now(calendar.ZONE).date()
    data={'kind':'schedule','category_id':None,'title':'共同提醒','schedule':{'start_date':today.isoformat(),'start_time':'00:00','end_date':None,'end_time':None},'reminders':[{'id':str(uuid4()),'mode':'relative','at':None,'offset_minutes':0,'recipient_ids':[b.get('/api/auth/me').json()['user']['id']],'all_members':False}]}
    item=saved(op(a,'memo',str(uuid4()),'create',data,fid=fid));b.get('/api/v2/memos/messages',headers=headers(b,fid));bid=b.get('/api/auth/me').json()['user']['id'];subscribe(path,bid)
    def leave():
        response=b.post('/api/v2/families/'+fid+'/leave',headers=headers(b,fid),json=management(b,fid));assert response.status_code==200,response.text
    push=FakePush(hook=leave);Worker(path,push=push)._notifications(datetime.now(calendar.ZONE));assert len(push.sent)==1
    with connect(path) as db:
        assert db.execute("SELECT status FROM notification_outbox WHERE dedup_key LIKE 'memo:%'").fetchone()[0]=='cancelled'
        assert db.execute('SELECT status FROM push_deliveries').fetchone()[0]=='sent'
