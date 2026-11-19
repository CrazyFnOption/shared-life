"""Local fictional checklist and original-occurrence reminder actions."""
from datetime import timedelta
from uuid import uuid4
import json
import shutil
import pytest
from server.accounting import db as database
from server.accounting.db import connect,initialize_database,migrate_database,preflight
from server.automation.backups import backup,restore,validate_database
from server.memos import calendar,events,notifications
from server.tests.test_multi_family import home,headers,create,join
from server.tests.test_memos import op,saved
from server.tests.test_memo_lifecycle import clock,generate


def test_checklist_shared_version_freeze_and_new_slot_without_cancelling_reminder(home,monkeypatch,tmp_path):
    path,app,(a,b,c)=home;fid=create(a,'虚构家庭');join(a,b,fid)
    at=calendar.anchor('2090-11-05','09:00');clock(monkeypatch,at-timedelta(hours=1))
    items=[{'id':str(uuid4()),'text':text} for text in ('带钥匙','准备车票')]
    data={'title':'出门准备','checklist_items':items,'reminders':[{'id':str(uuid4()),'at':at.isoformat(),'all_members':True}],'recurrence':{'frequency':'daily','start_date':'2090-11-05'}}
    item=saved(op(a,'memo',str(uuid4()),'create',data,fid=fid));clock(monkeypatch,at+timedelta(minutes=1));generate(path,at+timedelta(minutes=1))
    for index,entry in enumerate(items):item=saved(op(a,'memo',item['id'],'check_item',{'item_id':entry['id'],'checked':True,'occurrence_at':at.isoformat()},item['version'],fid))
    assert not item['completed'] and not item['archived']
    with connect(path) as db:
        assert db.execute("SELECT count(*) FROM memo_reminder_events WHERE status='ready'").fetchone()[0]==2
        assert all(notifications.valid(db,row) for row in db.execute('SELECT * FROM notification_outbox'))
    # Another connection/member sees persisted checks; conflicting input cannot replace them.
    assert b.get('/api/v2/memos/records/'+item['id'],headers=headers(b,fid)).json()['memo']['checklist']==item['checklist']
    assert op(b,'memo',item['id'],'check_item',{'item_id':items[0]['id'],'checked':False,'occurrence_at':at.isoformat()},1,fid).status_code==409
    item=saved(op(b,'memo',item['id'],'check_item',{'item_id':items[0]['id'],'checked':False,'occurrence_at':at.isoformat()},item['version'],fid))
    item=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_at':at.isoformat()},item['version'],fid))
    original=item['checklist']['states'][at.isoformat()]
    assert original['items_snapshot']==items and original['checked_ids']==[items[1]['id']]
    item=saved(op(a,'memo',item['id'],'update',{**data,'checklist_items':[{'id':items[0]['id'],'text':'新名字'}]},item['version'],fid))
    assert item['checklist']['states'][at.isoformat()]==original
    next_at=at+timedelta(days=1);clock(monkeypatch,next_at+timedelta(minutes=1))
    item=saved(op(b,'memo',item['id'],'check_item',{'item_id':items[0]['id'],'checked':True,'occurrence_at':next_at.isoformat()},item['version'],fid))
    assert item['checklist']['states'][next_at.isoformat()]['checked_ids']==[items[0]['id']]
    # Old clients omit the new field and must preserve template and every state.
    item=saved(op(a,'memo',item['id'],'update',{key:value for key,value in data.items() if key!='checklist_items'},item['version'],fid))
    assert item['checklist']['items'][0]['text']=='新名字'
    assert item['checklist']['states'][at.isoformat()]==original
    assert op(c,'memo',item['id'],'check_item',{'item_id':items[0]['id'],'checked':True,'occurrence_at':next_at.isoformat()},item['version']).status_code==404
    bundle=backup(path,tmp_path/'backup');restored=tmp_path/'restored.sqlite3';restore(bundle,restored,bundle/'credentials.json')
    with connect(restored) as db:
        validate_database(db)
        assert json.loads(db.execute('SELECT checklist_json FROM memos WHERE id=?',(item['id'],)).fetchone()[0])==item['checklist']
        assert db.execute('SELECT automation_paused FROM app_state').fetchone()[0]==1


def test_snooze_only_original_recipients_original_slot_idempotence_and_completion(home,monkeypatch,tmp_path):
    path,app,(a,b,c)=home;fid=create(a,'虚构推后');join(a,b,fid)
    at=calendar.anchor('2090-11-05','09:00');clock(monkeypatch,at-timedelta(hours=1))
    data={'title':'家庭提醒','reminders':[{'id':str(uuid4()),'at':at.isoformat(),'all_members':True}],'recurrence':{'frequency':'daily','start_date':'2090-11-05'}}
    item=saved(op(a,'memo',str(uuid4()),'create',data,fid=fid));clock(monkeypatch,at+timedelta(minutes=1));generate(path,at+timedelta(minutes=1))
    message=a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages'][0]
    assert message['completion_scope']=='occurrence' and message['occurrence_at']==at.isoformat()
    with connect(path) as db:old_outbox=db.execute("SELECT id FROM notification_outbox WHERE user_id=?",(message['source']['user_id'],)).fetchone()[0]
    # Joining after original delivery must never expand the snoozed recipients.
    join(a,c,fid)
    until=at+timedelta(days=1,hours=2);op_id=str(uuid4());payload={'message_id':message['id'],'occurrence_at':at.isoformat(),'until':until.isoformat()}
    first=op(a,'memo',item['id'],'snooze',payload,1,fid,op_id);item=saved(first)
    assert op(a,'memo',item['id'],'snooze',payload,1,fid,op_id).json()==first.json()
    assert op(b,'memo',item['id'],'complete',{'completed':True,'occurrence_at':at.isoformat()},1,fid).status_code==409
    assert not a.get('/api/v2/memos/messages',headers=headers(a,fid)).json()['messages']
    resolved=a.get('/api/v2/memos/notifications/'+old_outbox,headers=headers(a,fid)).json();assert '推后至' in resolved['notice']
    generate(path,at+timedelta(minutes=2))
    with connect(path) as db:
        rows=db.execute('SELECT * FROM memo_reminder_events').fetchall();assert len(rows)==2
        assert all(row['delivery_revision']==1 and row['occurrence']==at.isoformat() for row in rows)
        current=db.execute("SELECT * FROM notification_outbox WHERE status='pending'").fetchall();assert len(current)==2
        assert all(notifications.valid(db,row) and row['next_attempt']==until.timestamp() for row in current)
        assert db.execute("SELECT count(*) FROM notification_outbox WHERE status='cancelled'").fetchone()[0]==2
    # A later cycle is generated normally even while this original slot is delayed.
    next_at=at+timedelta(days=1);clock(monkeypatch,next_at+timedelta(minutes=1));generate(path,next_at+timedelta(minutes=1))
    assert len(c.get('/api/v2/memos/messages',headers=headers(c,fid)).json()['messages'])==1
    bundle=backup(path,tmp_path/'snooze-backup');destination=tmp_path/'snooze-restored.sqlite3';restore(bundle,destination,bundle/'credentials.json')
    with connect(destination) as db:
        validate_database(db);assert db.execute('SELECT count(*) FROM memo_reminder_events WHERE delivery_revision=1').fetchone()[0]==2
    clock(monkeypatch,until+timedelta(minutes=1))
    delayed=[m for m in b.get('/api/v2/memos/messages',headers=headers(b,fid)).json()['messages'] if m['occurrence_at']==at.isoformat()][0]
    assert delayed['id'].endswith(':1')
    assert op(b,'memo',item['id'],'snooze',{'message_id':delayed['id'],'occurrence_at':at.isoformat(),'until':at.isoformat()},item['version'],fid).status_code==422
    done=saved(op(b,'memo',item['id'],'complete',{'completed':True,'message_id':delayed['id'],'occurrence_at':at.isoformat()},item['version'],fid))
    assert done['reminder_occurrences'][0]['occurrence_at']==at.isoformat()
    assert op(a,'memo',item['id'],'snooze',payload,done['version'],fid).status_code==409
    with connect(path) as db:
        assert not db.execute("SELECT 1 FROM notification_outbox o JOIN memo_reminder_events e ON o.dedup_key='memo:'||e.id||':1' WHERE e.occurrence=? AND o.status='pending'",(at.isoformat(),)).fetchone()


def test_schema12_to13_preserves_original_columns_and_rolls_back(tmp_path,monkeypatch):
    source=tmp_path/'schema12.sqlite3'
    with connect(source) as db:
        db.execute('BEGIN');database._initialize_legacy(db);database._migrate_multi_family(db);database._migrate_memos(db);database._migrate_memo_archive(db);database._migrate_ai(db);database._migrate_plan_bodies(db);db.commit()
    from server.memos.migration_guard import capture,verify
    with connect(source) as db:before=capture(db)
    failed=tmp_path/'failed.sqlite3';shutil.copy2(source,failed);actual=database._migrate_memo_actions
    def fail(db):actual(db);raise RuntimeError('fictional interruption')
    monkeypatch.setattr(database,'_migrate_memo_actions',fail)
    with pytest.raises(RuntimeError,match='fictional interruption'):migrate_database(failed)
    with connect(failed) as db:verify(db,before);assert db.execute('PRAGMA user_version').fetchone()[0]==12
    monkeypatch.setattr(database,'_migrate_memo_actions',actual)
    assert migrate_database(source) and not migrate_database(source)
    with connect(source) as db:verify(db,before);validate_database(db)
    assert preflight(source)=='current'
