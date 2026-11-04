"""Pre-family report versions must survive a structural schema migration."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
from uuid import uuid4
import pytest

from server.accounting.db import connect, initialize_database, migrate_database
from server.accounting.ledger import digest
from server.automation import reports
from server.accounting import db as database
from server.memos import migration_guard


def test_legacy_report_task_and_notification_versions_migrate_independently(tmp_path):
    path = tmp_path / 'pre-family.sqlite3'
    users = [str(uuid4()), str(uuid4())]
    # These are the complete pre-family schema files, without running new init.
    with sqlite3.connect(path) as db:
        for filename in ('001_initial.sql', '002_categories_images.sql', '003_maintenance.sql', '004_automation.sql'):
            db.executescript((Path('server/migrations') / filename).read_text())
        db.execute("ALTER TABLE expenses ADD COLUMN category TEXT NOT NULL DEFAULT ''")
        db.execute('ALTER TABLE expenses ADD COLUMN voided INTEGER NOT NULL DEFAULT 0')
        db.execute('ALTER TABLE expenses ADD COLUMN rating INTEGER')
        db.execute("ALTER TABLE expenses ADD COLUMN rating_reason TEXT NOT NULL DEFAULT ''")
        for uid, username in zip(users, ('demo-a', 'demo-b')):
            db.execute('INSERT INTO users VALUES(?,?,?,?)', (uid, username, 'unused', username))
        db.execute("INSERT INTO app_state(singleton,restore_id) VALUES(1,'legacy-restore')")

    cases = [
        ('shared', 'ready', True, None),
        ('private', 'ready', True, None),
        ('shared', 'ready', False, None),
        ('private', 'stale', True, True),
        ('shared', 'pending', True, True),
        ('private', 'pending', False, True),
        ('shared', 'running', True, False),
    ]
    originals = []
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        for index, (scope, status, report_fresh, task_fresh) in enumerate(cases, 1):
            month = f'2026-{index:02d}'
            eid, rid = str(uuid4()), str(uuid4())
            expense = {
                'id': eid, 'owner_id': users[0], 'scope': scope, 'amount_cents': 1000,
                'purpose': '迁移前当前支出', 'occurred_at': month + '-10T00:00:00Z',
                'location': '', 'version': 2, 'category': '餐饮', 'voided': False,
                'rating': None, 'rating_reason': '',
            }
            db.execute('''INSERT INTO expenses(id,owner_id,scope,amount_cents,purpose,occurred_at,location,version,category,voided,rating,rating_reason)
                VALUES(:id,:owner_id,:scope,:amount_cents,:purpose,:occurred_at,:location,:version,:category,:voided,:rating,:rating_reason)''', expense)
            active = {'id': str(uuid4()), 'amount_cents': 200, 'occurred_at': month + '-11T00:00:00Z', 'reason': '部分退款', 'voided': False, 'version': 1}
            voided = dict(active, id=str(uuid4()), amount_cents=100, voided=True)
            for refund in (active, voided):
                db.execute('INSERT INTO refunds VALUES(:id,:expense_id,:amount_cents,:occurred_at,:reason,:voided,:version)', dict(refund, expense_id=eid))
            # Exact legacy report snapshot shape: live refunds only, no family_id.
            snapshot = [dict(expense, refunds=[active], net_amount_cents=800)]
            legacy_version = digest(snapshot)
            stale_snapshot = deepcopy(snapshot)
            stale_snapshot[0]['purpose'] = '已经修改的旧用途'
            stale_version = digest(stale_snapshot)
            report_version = legacy_version if report_fresh else stale_version
            content = json.dumps({'items': [{'expense_id': eid, 'analysis': '保留已有分析'}], 'summary': '保留已有总结'}, ensure_ascii=False) if status == 'ready' else None
            db.execute('''INSERT INTO reports(id,scope,owner_id,month,status,data_version,generated_at,provider,model,content_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (rid, scope, '' if scope == 'shared' else users[0], month, status, report_version, '2026-10-01T00:00:00Z', 'old-provider', 'old-model', content))
            old_task = None
            if task_fresh is not None:
                progress = json.dumps({'index': 1, 'items': [{'expense_id': eid, 'analysis': '已完成一批'}]})
                db.execute('''INSERT INTO report_tasks(report_id,target_version,restore_id,next_attempt,claim_token,lease_until,progress_json,failures,rounds,retry_day,config_sig)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (rid, legacy_version if task_fresh else stale_version, 'legacy-restore', 1e30, 'old-claim', 1e30, progress, 1, 2, '2026-10-01', 'old-config'))
                old_task = dict(db.execute('SELECT * FROM report_tasks WHERE report_id=?', (rid,)).fetchone())
            notifications = []
            if status == 'ready':
                for notify_status, uid in zip(('pending', 'sent'), users):
                    nid = str(uuid4())
                    db.execute('''INSERT INTO notification_outbox(id,user_id,dedup_key,report_id,data_version,status,next_attempt)
                        VALUES(?,?,?,?,?,?,?)''', (nid, uid, f'report:{rid}:{report_version}', rid, report_version, notify_status, 1e30))
                    notifications.append(dict(db.execute('SELECT * FROM notification_outbox WHERE id=?', (nid,)).fetchone()))
            originals.append({'id': rid, 'scope': scope, 'status': status, 'report_fresh': report_fresh,
                              'task_fresh': task_fresh, 'legacy_version': legacy_version,
                              'report': dict(db.execute('SELECT * FROM reports WHERE id=?', (rid,)).fetchone()),
                              'task': old_task, 'notifications': notifications})
        db.commit()

    migrate_database(path)
    with connect(path) as db:
        fid = db.execute('SELECT id FROM families').fetchone()[0]
        for case in originals:
            row = dict(db.execute('SELECT * FROM reports WHERE id=?', (case['id'],)).fetchone())
            current = reports.version(reports.snapshot(db, row['scope'], row['owner_id'], row['month']))
            assert current != case['legacy_version']
            expected = dict(case['report'])
            if case['scope'] == 'shared':
                expected['owner_id'] = fid
            if case['report_fresh']:
                expected['data_version'] = current
            assert row == expected  # Preserve status, model content and metadata exactly.
            rendered = reports.read(db, row, detail=True)
            if case['report_fresh'] and case['status'] == 'ready':
                assert rendered['status'] == 'ready'
                assert rendered['content'] == json.loads(case['report']['content_json'])
            else:
                assert rendered['content'] is None
                if not case['report_fresh'] or case['status'] == 'stale':
                    assert rendered['status'] == 'stale'
            task = db.execute('SELECT * FROM report_tasks WHERE report_id=?', (case['id'],)).fetchone()
            if case['task'] is None:
                assert task is None  # No extra generation or model consumption is scheduled.
            else:
                expected_task = dict(case['task'])
                if case['task_fresh']:
                    expected_task['target_version'] = current
                assert dict(task) == expected_task  # Keep paid progress and retry/lease state.
            for old_notification in case['notifications']:
                notification = dict(db.execute('SELECT * FROM notification_outbox WHERE id=?', (old_notification['id'],)).fetchone())
                expected_notification = dict(old_notification)
                if case['report_fresh']:
                    expected_notification['data_version'] = current
                    expected_notification['dedup_key'] = f"report:{case['id']}:{current}"
                assert {key:notification[key] for key in expected_notification} == expected_notification
        before_restart = {table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')] for table in ('reports', 'report_tasks', 'notification_outbox')}
        assert db.execute('SELECT count(*) FROM model_usage').fetchone()[0] == 0
    migrate_database(path)
    with connect(path) as db:
        assert before_restart == {table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')] for table in before_restart}


@pytest.mark.parametrize('damage',[
    "UPDATE reports SET content_json='{}' WHERE status='ready'",
    "UPDATE expenses SET amount_cents=amount_cents+1",
    "UPDATE report_tasks SET progress_json='{}'",
    "UPDATE report_tasks SET lease_until=0",
    "UPDATE reports SET data_version='wrong-report-version'",
    "UPDATE reports SET owner_id=(SELECT id FROM users WHERE username='demo-b') WHERE scope='private' AND status='pending'",
    "UPDATE report_tasks SET target_version='0000000000000000000000000000000000000000000000000000000000000000'",
    "UPDATE notification_outbox SET data_version='wrong-notification-version'",
    "UPDATE notification_outbox SET dedup_key='wrong:'||id",
],ids=['content','money','progress','lease','report-version','report-owner','task-version','notification-version','notification-dedup'])
def test_pre_family_report_mapping_rejects_other_changes_and_rolls_back(tmp_path,monkeypatch,damage):
    original_migrate=database._migrate_memos
    original_capture=migration_guard.capture
    before=[]

    def capture(db):
        snapshot=original_capture(db)
        before.append(snapshot)
        # Even the extra translation proof contains hashes, not report content.
        assert '保留已有分析' not in json.dumps(snapshot,ensure_ascii=False)
        return snapshot

    def corrupt(db):
        original_migrate(db)
        assert db.execute(damage).rowcount>0

    monkeypatch.setattr(migration_guard,'capture',capture)
    monkeypatch.setattr(database,'_migrate_memos',corrupt)
    with pytest.raises(ValueError,match='Migration changed original rows'):
        test_legacy_report_task_and_notification_versions_migrate_independently(tmp_path)
    with connect(tmp_path/'pre-family.sqlite3',read_only=True) as db:
        assert original_capture(db)==before[0]
        assert db.execute('PRAGMA user_version').fetchone()[0]==0


def test_pre_family_wrong_expense_mapping_cannot_disguise_ready_report_as_stale(tmp_path,monkeypatch):
    original_migrate=database._migrate_memos
    original_capture=migration_guard.capture
    before=[]
    old={}

    def capture(db):
        snapshot=original_capture(db)
        before.append(snapshot)
        old['reports']={r['id']:r['data_version'] for r in db.execute("SELECT * FROM reports WHERE scope='shared'")}
        old['tasks']={r['report_id']:r['target_version'] for r in db.execute('SELECT * FROM report_tasks')}
        old['notifications']={r['id']:(r['report_id'],r['data_version'],r['dedup_key']) for r in db.execute('SELECT * FROM notification_outbox')}
        return snapshot

    def corrupt(db):
        original_migrate(db)
        owner=db.execute("SELECT id FROM users WHERE username='demo-a'").fetchone()[0]
        db.execute("INSERT INTO families(id,name,owner_id) VALUES('wrong-family','Wrong migration mapping',?)",(owner,))
        db.execute("INSERT INTO family_members(user_id,family_id,relation_epoch,active) VALUES(?,'wrong-family',1,1)",(owner,))
        assert db.execute("UPDATE expenses SET family_id='wrong-family' WHERE scope='shared'").rowcount>0
        # Keep the old versions, mimicking a faulty mapper that mistakes the
        # now-empty report snapshot for evidence that a ready report was stale.
        for rid,version in old['reports'].items():
            db.execute('UPDATE reports SET data_version=? WHERE id=?',(version,rid))
            if rid in old['tasks']:
                db.execute('UPDATE report_tasks SET target_version=? WHERE report_id=?',(old['tasks'][rid],rid))
        for nid,(rid,version,key) in old['notifications'].items():
            if rid in old['reports']:
                db.execute('UPDATE notification_outbox SET data_version=?,dedup_key=? WHERE id=?',(version,key,nid))

    monkeypatch.setattr(migration_guard,'capture',capture)
    monkeypatch.setattr(database,'_migrate_memos',corrupt)
    with pytest.raises(ValueError,match='Migration changed original rows'):
        test_legacy_report_task_and_notification_versions_migrate_independently(tmp_path)
    with connect(tmp_path/'pre-family.sqlite3',read_only=True) as db:
        assert original_capture(db)==before[0]
        assert db.execute('PRAGMA user_version').fetchone()[0]==0
