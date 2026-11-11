"""Schema10 lifecycle regressions, only isolated fictional home fixture data."""
from datetime import datetime,timedelta
import json
from uuid import uuid4
import pytest
from server.accounting.db import connect
from server.memos import calendar,events,lifecycle,service
from server.tests.test_multi_family import home,headers,create,join
from server.tests.test_memos import op,saved,category,memo


def clock(monkeypatch,at):
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    monkeypatch.setattr(service,'datetime',Frozen)


def schedule(c,*,start='2090-11-05',end=None,start_time=None,end_time=None,repeat=False,reminders=None):
    data={'kind':'schedule','category_id':None,'title':'虚构日程','body_markdown':'# 已保留正文','attachments':[],
          'schedule':{'start_date':start,'end_date':end,'start_time':start_time,'end_time':end_time},'reminders':reminders or []}
    if repeat:data['recurrence']={'start_date':start,'frequency':'daily','interval':1,'calendar':'solar','until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}
    return saved(op(c,'memo',str(uuid4()),'create',data)),data


def generate(path,at):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');events.generate(db,at);db.commit()


def detail(a,item):return a.get('/api/v2/memos/records/'+item['id'],headers=headers(a)).json()['memo']


def test_completion_archives_immediately_persists_and_reopens_without_second_confirmation(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','12:00'))
    m=memo(a,category(a));done=saved(op(a,'memo',m['id'],'complete',{'completed':True},1))
    assert done['completed'] and done['archived'] and done['archived_at']
    assert detail(a,m)==done
    clock(monkeypatch,calendar.anchor('2090-11-06','12:00'))
    removed=saved(op(a,'memo',m['id'],'delete',{},2));restored=saved(op(a,'memo',m['id'],'restore',{},3))
    assert removed['archived_at']==restored['archived_at']==done['archived_at']
    reopened=saved(op(a,'memo',m['id'],'complete',{'completed':False},4))
    assert not reopened['completed'] and not reopened['archived'] and reopened['archived_at'] is None
    assert reopened['body_markdown']==m['body_markdown']


@pytest.mark.parametrize('schedule_data,before,boundary',[
    ({'start_date':'2090-11-05','end_date':None,'start_time':'09:00','end_time':None},'2090-11-05T23:59:59+08:00','2090-11-06T00:00:00+08:00'),
    ({'start_date':'2090-11-05','end_date':'2090-11-06','start_time':None,'end_time':None},'2090-11-06T23:59:59+08:00','2090-11-07T00:00:00+08:00'),
    ({'start_date':'2090-11-05','end_date':'2090-11-06','start_time':'22:00','end_time':'00:00'},'2090-11-05T23:59:59+08:00','2090-11-06T00:00:00+08:00'),
    ({'start_date':'2090-11-05','end_date':'2090-11-07','start_time':'09:00','end_time':'11:30'},'2090-11-07T11:29:59+08:00','2090-11-07T11:30:00+08:00'),
])
def test_legacy_schedule_deadlines_do_not_archive_unfinished_memos(home,monkeypatch,schedule_data,before,boundary):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-01'))
    item,_=schedule(a,start=schedule_data['start_date'],end=schedule_data['end_date'],start_time=schedule_data['start_time'],end_time=schedule_data['end_time'])
    generate(path,datetime.fromisoformat(before));assert not detail(a,item)['archived']
    generate(path,datetime.fromisoformat(boundary));expired=detail(a,item)
    assert not expired['archived'] and not expired['completed']
    timestamp=expired['archived_at'];generate(path,datetime.fromisoformat(boundary)+timedelta(days=1));assert detail(a,item)['archived_at']==timestamp
    restored=saved(op(a,'memo',item['id'],'archive',{'archived':False},expired['version']))
    generate(path,datetime.fromisoformat(boundary)+timedelta(days=2))
    assert not detail(a,item)['archived'] and restored['auto_archive_suppressed']


def test_schedule_manual_completion_and_recurrence_only_archive_one_occurrence(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','10:00'))
    item,_=schedule(a,repeat=True,end='2090-11-07',start_time='09:00',end_time='11:00')
    done=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':'2090-11-05'},1))
    assert not done['archived'] and not done['completed']
    assert done['archived_occurrences'][0]['completed']
    calendar_items=calendar.schedule_occurrences(done,calendar.date(2090,11,5),calendar.date(2090,11,8))
    assert not any(o['start_date']=='2090-11-05' for o in calendar_items)
    assert any(o['start_date']=='2090-11-06' for o in calendar_items)
    generate(path,calendar.anchor('2090-11-08','11:30'));expired=detail(a,item)
    assert {e['start_date'] for e in expired['archived_occurrences'] if e['archived_at']}=={'2090-11-05'}
    restored=saved(op(a,'memo',item['id'],'complete',{'completed':False,'occurrence_date':'2090-11-05'},expired['version']))
    generate(path,calendar.anchor('2090-11-08','12:00'))
    assert next(e for e in detail(a,item)['archived_occurrences'] if e['start_date']=='2090-11-05')['archived_at'] is None
    assert op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':'2080-01-01'},restored['version']).status_code==422


def test_repeating_memo_fixed_cycle_reopens_after_auto_archive_and_keeps_last_cycle_date(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','12:00'))
    cat=category(a);item=saved(op(a,'memo',str(uuid4()),'create',{'category_id':cat['id'],'title':'循环备忘','body_markdown':'','attachments':[],
        'recurrence':{'start_date':'2090-11-05','frequency':'daily','interval':1,'calendar':'solar','until':None,'lunar_month':None,'lunar_day':None,'lunar_leap':False}}))
    saved(op(a,'memo',item['id'],'complete',{'completed':True},1));generate(path,calendar.anchor('2090-11-05','13:00'));assert detail(a,item)['archived']
    generate(path,calendar.anchor('2090-11-06'));reopened=detail(a,item)
    assert not reopened['archived'] and not reopened['completed']
    with connect(path) as db:assert db.execute('SELECT last_cycle FROM memos WHERE id=?',(item['id'],)).fetchone()[0]=='2090-11-06'


def test_old_completed_and_archived_rows_project_without_rewriting_history(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','12:00'))
    item=memo(a,category(a));done=saved(op(a,'memo',item['id'],'complete',{'completed':True},1))
    with connect(path) as db:db.execute("UPDATE memos SET archived=0,archive_state_json='{}' WHERE id=?",(item['id'],))
    assert detail(a,item)['archived'] and detail(a,item)['archived_at']==done['archived_at']
    with connect(path) as db:assert db.execute('SELECT archived,archive_state_json FROM memos WHERE id=?',(item['id'],)).fetchone()[:]==(0,'{}')
    manual=memo(a,category(a,name='第二分类'));archived=saved(op(a,'memo',manual['id'],'archive',{'archived':True},1))
    with connect(path) as db:db.execute("UPDATE memos SET archive_state_json='{}' WHERE id=?",(manual['id'],))
    clock(monkeypatch,calendar.anchor('2090-11-06','12:00'));saved(op(a,'memo',manual['id'],'delete',{},2));saved(op(a,'memo',manual['id'],'restore',{},3))
    assert detail(a,manual)['archived_at']==archived['archived_at']


def test_overlapping_recurrence_completion_only_cancels_its_own_reminder(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','08:00'))
    reminder={'id':str(uuid4()),'mode':'relative','at':None,'offset_minutes':0,'recipient_ids':[],'all_members':False}
    item,_=schedule(a,repeat=True,end='2090-11-07',start_time='09:00',end_time='11:00',reminders=[reminder])
    generate(path,calendar.anchor('2090-11-05','10:00'));generate(path,calendar.anchor('2090-11-06','10:00'))
    clock(monkeypatch,calendar.anchor('2090-11-06','10:30'))
    saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':'2090-11-05'},1))
    with connect(path) as db:
        rows=db.execute('SELECT occurrence,status FROM memo_reminder_events WHERE memo_id=? ORDER BY occurrence',(item['id'],)).fetchall()
        assert [row['status'] for row in rows]==['cancelled','ready']
    assert len(a.get('/api/v2/memos/messages',headers=headers(a)).json()['messages'])==1


def test_future_series_edit_preserves_archived_occurrence_date_and_time(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','10:00'))
    item,data=schedule(a,repeat=True,end='2090-11-07',start_time='09:00',end_time='11:00')
    done=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':'2090-11-05'},1));original=done['archived_occurrences'][0]
    changed={**data,'schedule':{**data['schedule'],'start_date':'2090-11-06','end_date':'2090-11-06'},'recurrence':{**data['recurrence'],'start_date':'2090-11-06'}}
    updated=saved(op(a,'memo',item['id'],'update',changed,2));assert updated['archived_occurrences']==[original]
    restored=saved(op(a,'memo',item['id'],'archive',{'archived':False,'occurrence_date':'2090-11-05'},3))
    assert restored['archived_occurrences'][0]['suppressed']


def test_shared_member_completion_survives_app_restart_and_keeps_attachment_readable(home,monkeypatch):
    import hashlib
    from fastapi.testclient import TestClient
    from server.api.app import create_app
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','10:00'));fid=create(a,'虚构归档家庭');join(a,b,fid)
    content=b'fictional archived original attachment';file={'id':str(uuid4()),'kind':'file','name':'archive.txt','mime_type':'text/plain','size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
    item=memo(a,category(a,fid),fid,[file]);url=f"/api/v2/memos/records/{item['id']}/attachments/{file['id']}"
    assert a.put(url,headers={**headers(a,fid),'Content-Type':'text/plain'},content=content).status_code==200
    done=saved(op(b,'memo',item['id'],'complete',{'completed':True},1,fid))
    assert done['author_id']==item['author_id'] and done['updated_by']!=item['author_id'] and done['archived']
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(a.cookies);restarted.headers.update(a.headers)
        loaded=restarted.get('/api/v2/memos/records/'+item['id'],headers=headers(a,fid)).json()['memo']
        assert loaded==done and restarted.get(url,headers=headers(a,fid)).content==content
    assert c.get('/api/v2/memos/records/'+item['id'],headers=headers(c)).status_code==404
