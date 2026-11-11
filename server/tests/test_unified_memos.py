"""Unified memo presentation keeps the existing records and source boundaries."""
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient

from server.accounting.db import connect
from server.api.app import create_app
from server.memos import calendar, service
from server.tests.test_multi_family import home, headers, create, join
from server.tests.test_memos import op, saved, category, snapshot


def test_uncategorized_memo_persists_reminder_attachment_and_creator_rights(home):
    path,app,(a,b,c)=home;fid=create(a,'统一备忘家庭');join(a,b,fid)
    binary=b'fictional unified memo attachment';attachment_id=str(uuid4())
    attachment={'id':attachment_id,'kind':'file','name':'notes.txt','mime_type':'text/plain',
                'size_bytes':len(binary),'sha256':hashlib.sha256(binary).hexdigest()}
    reminder={'id':str(uuid4()),'at':'2090-11-05T09:30:00+08:00'}
    data={'title':'统一备忘','body_markdown':'原文保留','attachments':[attachment],'reminders':[reminder]}
    item=saved(op(a,'memo',str(uuid4()),'create',data,fid=fid))
    assert item['kind']=='memo' and item['category_id'] is None
    url=f"/api/v2/memos/records/{item['id']}/attachments/{attachment_id}"
    assert a.put(url,headers={**headers(a,fid),'Content-Type':'text/plain'},content=binary).status_code==200
    changed=saved(op(b,'memo',item['id'],'update',{**data,'title':'成员修改'},1,fid))
    assert changed['author_id']==item['author_id'] and changed['attachments']==[attachment]
    assert op(b,'memo',item['id'],'delete',{},2,fid).status_code==403
    assert c.get('/api/v2/memos/records/'+item['id'],headers=headers(c)).status_code==404
    with TestClient(create_app(path)) as reopened:
        reopened.cookies.update(a.cookies);reopened.headers.update(a.headers)
        record=reopened.get('/api/v2/memos/records/'+item['id'],headers=headers(a,fid)).json()['memo']
        assert record==changed
        assert reopened.get(url,headers=headers(a,fid)).content==binary
    with connect(path) as db:service.validate_database(db)


def test_calendar_uses_reminder_clock_for_all_kinds_and_limits_new_reminders(home):
    path,app,(a,b,c)=home
    reminder={'id':str(uuid4()),'at':'2090-11-05T01:30:00Z'}
    data={'title':'提醒即日程','reminders':[reminder]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    assert op(a,'memo',str(uuid4()),'create',{**data,'reminders':[reminder,{**reminder,'id':str(uuid4())}]}).status_code==422
    saved(op(a,'memo',str(uuid4()),'create',{'kind':'schedule','title':'无提醒旧日程','schedule':{'start_date':'2090-11-05'}}))
    result=a.get('/api/v2/memos/calendar?from_date=2090-11-05&to_date=2090-11-05',headers=headers(a)).json()
    assert [(o['memo_id'],o['start_time']) for o in result['occurrences']]==[(item['id'],'09:30')]
    assert result['occurrences'][0]['reminder_id']==reminder['id']


def test_legacy_schedule_stays_open_and_completed_repeat_reopens_at_reminder_clock(home,monkeypatch):
    from server.tests.test_memo_lifecycle import clock, generate, detail
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','10:00'))
    data={'kind':'schedule','title':'统一循环','schedule':{'start_date':'2090-11-05','start_time':'09:00','end_time':'09:30'},
          'recurrence':{'start_date':'2090-11-05','frequency':'weekly'},
          'reminders':[{'id':str(uuid4()),'at':'2090-11-05T09:30:00+08:00'}]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    generate(path,calendar.anchor('2090-11-05','10:00'))
    assert not detail(a,item)['archived']
    done=saved(op(a,'memo',item['id'],'complete',{'completed':True},1))
    assert done['archived'] and done['completed']
    # Completing this cycle keeps the next fixed reminder on the calendar.
    upcoming=a.get('/api/v2/memos/calendar?from_date=2090-11-12&to_date=2090-11-12',headers=headers(a)).json()['occurrences']
    assert [(o['memo_id'],o['start_time']) for o in upcoming]==[(item['id'],'09:30')]
    generate(path,calendar.anchor('2090-11-12','09:29'))
    assert detail(a,item)['archived']
    generate(path,calendar.anchor('2090-11-12','09:30'))
    reopened=detail(a,item)
    assert not reopened['archived'] and not reopened['completed']
    with connect(path) as db:assert db.execute('SELECT count(*) FROM memos WHERE id=?',(item['id'],)).fetchone()[0]==1


def test_early_completion_consumes_today_cycle_until_next_weekly_reminder(home,monkeypatch):
    from server.tests.test_memo_lifecycle import clock, generate, detail
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','08:00'))
    data={'title':'提前完成本期','recurrence':{'start_date':'2090-11-05','frequency':'weekly'},
          'reminders':[{'id':str(uuid4()),'at':'2090-11-05T09:30:00+08:00'}]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    saved(op(a,'memo',item['id'],'complete',{'completed':True},1))
    generate(path,calendar.anchor('2090-11-05','09:30'))
    assert detail(a,item)['completed']
    upcoming=a.get('/api/v2/memos/calendar?from_date=2090-11-05&to_date=2090-11-12',headers=headers(a)).json()['occurrences']
    assert [(o['date'],o['start_time']) for o in upcoming]==[('2090-11-12','09:30')]
    generate(path,calendar.anchor('2090-11-12','09:29'));assert detail(a,item)['completed']
    generate(path,calendar.anchor('2090-11-12','09:30'));assert not detail(a,item)['completed']


def test_monthly_31_nominal_anchor_skips_past_creation_and_title_edits_keep_due_time(home,monkeypatch):
    from server.tests.test_memo_lifecycle import clock, generate
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-01-31','10:00'))
    data={'title':'每月31日','recurrence':{'frequency':'monthly','start_date':'2090-01-31'},
          'reminders':[{'id':str(uuid4()),'at':'2090-01-31T09:00:00+08:00'}]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    generate(path,calendar.anchor('2090-01-31','10:00'))
    with connect(path) as db:assert not db.execute('SELECT 1 FROM memo_reminder_events').fetchone()
    upcoming=a.get('/api/v2/memos/calendar?from_date=2090-01-31&to_date=2090-03-31',headers=headers(a)).json()['occurrences']
    assert [(o['date'],o['start_time']) for o in upcoming]==[('2090-02-28','09:00'),('2090-03-31','09:00')]
    clock(monkeypatch,calendar.anchor('2090-02-28','10:00'))
    saved(op(a,'memo',item['id'],'update',{**data,'title':'仅改标题'},1))
    generate(path,calendar.anchor('2090-02-28','10:00'))
    with connect(path) as db:
        due=db.execute('SELECT occurrence FROM memo_reminder_events').fetchall()
        assert [r[0] for r in due]==['2090-02-28T09:00:00+08:00']


def test_legacy_schedule_can_join_category_and_focus_without_changing_identity(home):
    path,app,(a,b,c)=home
    data={'kind':'schedule','title':'旧日程保留','schedule':{'start_date':'2090-11-05','start_time':'00:00'},
          'reminders':[{'id':str(uuid4()),'mode':'relative','offset_minutes':30}]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    cat=category(a);changed=saved(op(a,'memo',item['id'],'update',{**data,'category_id':cat['id']},1))
    assert changed['kind']==item['kind'] and changed['schedule']==item['schedule']
    assert changed['author_id']==item['author_id'] and changed['reminders']==item['reminders']
    occurrence=a.get('/api/v2/memos/calendar?from_date=2090-11-04&to_date=2090-11-04',headers=headers(a)).json()['occurrences'][0]
    assert occurrence['start_time']=='23:30' and occurrence['occurrence_date']=='2090-11-05'
    focus=snapshot(a)['focus']
    saved(op(a,'focus',focus['id'],'update',{'targets':[{'kind':'memo','id':item['id']}]},focus['version']))
    assert snapshot(a)['focused_records'][0]['record']['id']==item['id']
    foreign=category(b)
    assert op(a,'memo',item['id'],'update',{**data,'category_id':foreign['id']},2).status_code==404
    with connect(path) as db:service.validate_database(db)
