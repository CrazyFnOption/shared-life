"""Multi-clock recurrence checks use only fictional local SQLite and outbox rows."""
from datetime import timedelta
from uuid import uuid4
import pytest

from server.accounting.db import connect
from server.automation.backups import backup, restore
from server.memos import calendar, events, service
from server.tests.test_multi_family import home, headers, create, join
from server.tests.test_memos import op, saved
from server.tests.test_memo_lifecycle import clock, generate, detail


def data(day,frequency='daily',**selected):
    return {'title':'多个提醒','recurrence':{'frequency':frequency,'start_date':day,**selected},
            'reminders':[{'id':str(uuid4()),'at':day+'T'+time+':00+08:00','all_members':True} for time in ('09:00','21:00')]}


def test_month_days_and_clocks_share_slot_completion_and_persist_in_backup(home,monkeypatch,tmp_path):
    path,app,(a,b,c)=home;fid=create(a,'虚构多时家庭');join(a,b,fid)
    clock(monkeypatch,calendar.anchor('2090-02-01','08:00'))
    payload=data('2090-02-01','monthly',month_days=[30,31])
    item=saved(op(a,'memo',str(uuid4()),'create',payload,fid=fid))
    clock(monkeypatch,calendar.anchor('2090-02-28','09:05'))
    generate(path,calendar.anchor('2090-02-28','09:05'))
    current=a.get('/api/v2/memos/records/'+item['id'],headers=headers(a,fid)).json()['memo']
    assert current['current_occurrence_at']=='2090-02-28T09:00:00+08:00'
    # A current-version old date-only command on a new multi-clock record is narrow.
    done=saved(op(b,'memo',item['id'],'complete',{'completed':True,'occurrence_date':'2090-02-28'},1,fid))
    assert not done['completed'] and not done['archived']
    assert done['current_occurrence_at']=='2090-02-28T21:00:00+08:00'
    assert len(done['reminder_occurrences'])==1
    month=a.get('/api/v2/memos/calendar?from_date=2090-02-28&to_date=2090-03-31',headers=headers(a,fid)).json()['occurrences']
    assert [(r['date'],r['start_time']) for r in month]==[('2090-02-28','21:00'),('2090-03-30','09:00'),('2090-03-30','21:00'),('2090-03-31','09:00'),('2090-03-31','21:00')]
    generate(path,calendar.anchor('2090-02-28','21:00'));generate(path,calendar.anchor('2090-02-28','21:00'))
    with connect(path) as db:
        rows=db.execute('SELECT occurrence,status FROM memo_reminder_events ORDER BY occurrence').fetchall()
        assert [(r['occurrence'],r['status']) for r in rows]==[('2090-02-28T09:00:00+08:00','cancelled')]*2+[('2090-02-28T21:00:00+08:00','ready')]*2
        assert db.execute('SELECT count(*) FROM memos').fetchone()[0]==1
    reopened=saved(op(a,'memo',item['id'],'complete',{'completed':False,'occurrence_at':'2090-02-28T01:00:00Z'},2,fid))
    assert reopened['reminder_occurrences'][0]['archived_at'] is None
    assert op(c,'memo',item['id'],'complete',{'completed':True,'occurrence_at':'2090-02-28T09:00:00+08:00'},3).status_code==404
    assert op(a,'memo',item['id'],'delete',{},3,fid).status_code==200
    saved(op(a,'memo',item['id'],'restore',{},4,fid))
    saved_backup=backup(path,tmp_path/'backup');destination=tmp_path/'restored.sqlite3'
    restore(saved_backup,destination,saved_backup/'credentials.json')
    with connect(destination) as db:
        service.validate_database(db)
        row=db.execute('SELECT * FROM memos WHERE id=?',(item['id'],)).fetchone()
        assert row['archive_state_json'] and row['recurrence_json']
        assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1
        assert not db.execute("SELECT 1 FROM memo_reminder_events WHERE status='ready'").fetchone()


def test_weekday_sets_and_effective_start_do_not_backfill_or_change_old_signatures(home,monkeypatch):
    path,app,(a,b,c)=home;start=calendar.date(2090,11,6)
    clock(monkeypatch,calendar.anchor(start,'10:00'))
    payload=data(start.isoformat(),'weekly',weekdays=[start.isoweekday(),(start+timedelta(days=1)).isoweekday()])
    item=saved(op(a,'memo',str(uuid4()),'create',payload))
    assert item['current_occurrence_at']==start.isoformat()+'T21:00:00+08:00'
    generate(path,calendar.anchor(start,'10:00'))
    with connect(path) as db:assert not db.execute('SELECT 1 FROM memo_reminder_events').fetchone()
    month=a.get('/api/v2/memos/calendar',headers=headers(a),params={'from_date':start.isoformat(),'to_date':(start+timedelta(days=2)).isoformat()}).json()['occurrences']
    assert [(r['date'],r['start_time']) for r in month]==[(start.isoformat(),'21:00'),((start+timedelta(days=1)).isoformat(),'09:00'),((start+timedelta(days=1)).isoformat(),'21:00')]
    old=data(start.isoformat());old['reminders']=old['reminders'][:1]
    legacy=saved(op(a,'memo',str(uuid4()),'create',old));signature=events.signature(legacy['reminders'][0],legacy['recurrence'])
    renamed=saved(op(a,'memo',legacy['id'],'update',{**old,'title':'只改标题'},1))
    assert 'weekdays' not in renamed['recurrence'] and 'month_days' not in renamed['recurrence']
    assert events.signature(renamed['reminders'][0],renamed['recurrence'])==signature


@pytest.mark.parametrize('relative',[False,True])
def test_adding_clock_preserves_legacy_archive_and_planned_date_target(home,monkeypatch,relative):
    path,app,(a,b,c)=home;day='2090-11-05'
    original_at='2090-11-04T23:00:00+08:00' if relative else day+'T09:00:00+08:00'
    created_at=calendar.anchor('2090-11-04','08:00') if relative else calendar.anchor(day,'08:00')
    clock(monkeypatch,created_at)
    reminder={'id':str(uuid4()),'mode':'relative','offset_minutes':60} if relative else {'id':str(uuid4()),'at':original_at}
    payload={'kind':'schedule','title':'旧已完成保真','schedule':{'start_date':day,'start_time':'00:00' if relative else '09:00'},
             'recurrence':{'frequency':'weekly','start_date':day},'reminders':[reminder]}
    item=saved(op(a,'memo',str(uuid4()),'create',payload))
    clock(monkeypatch,calendar.local_time(original_at)+timedelta(minutes=1))
    archived=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':day},1))
    old_history=archived['archived_occurrences']
    clock(monkeypatch,calendar.local_time(original_at)+timedelta(minutes=2))
    changed=saved(op(a,'memo',item['id'],'update',{**payload,'reminders':[reminder,{'id':str(uuid4()),'at':day+'T21:00:00+08:00'}]},2))
    assert changed['archived_occurrences']==old_history
    assert changed['reminder_occurrences'][0]['occurrence_at']==original_at
    assert changed['reminder_occurrences'][0]['completed']
    rows=a.get('/api/v2/memos/calendar?from_date=2090-11-04&to_date=2090-11-05',headers=headers(a)).json()['occurrences']
    assert [row['occurrence_at'] for row in rows]==[day+'T21:00:00+08:00']
    with connect(path) as db:
        from server.memos.lifecycle import event_is_archived
        row=db.execute('SELECT * FROM memos WHERE id=?',(item['id'],)).fetchone()
        assert event_is_archived(row,{'occurrence':original_at},changed['reminders'][0])
        assert not event_is_archived(row,{'occurrence':day+'T21:00:00+08:00'},changed['reminders'][1])
    # The old key is the planned date, including a relative clock on the prior day.
    reopened=saved(op(a,'memo',item['id'],'complete',{'completed':False,'occurrence_date':day},3))
    assert reopened['reminder_occurrences'][0]['archived_at'] is None
    again=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_date':day},4))
    assert again['reminder_occurrences'][0]['archived_at']
    assert again['current_occurrence_at']==day+'T21:00:00+08:00'
