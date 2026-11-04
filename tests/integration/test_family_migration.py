"""Upgrade a real 351ffe7 demo database, then restart and replay its old receipt.

The old database is made by the archived release itself, not by current schema
helpers. The archive and every database contain fictional data only.
"""

import os
from pathlib import Path
import subprocess
import sys
import hashlib
import sqlite3
from contextlib import contextmanager
import json

import httpx
from server.tests.legacy_fixture import copy_legacy

from test_http_contract import LiveLedger, ROOT, create_operation
from test_ui_refinement import image_path, png, upload


@contextmanager
def old_client(instance):
    client = httpx.Client(base_url=instance.origin, trust_env=False,
                         headers={"Origin": instance.origin, "X-App-Request": "shared-expense"})
    login = client.post("/api/auth/login", json={"username": "demo-a", "password": "demo-only-2026"})
    assert login.status_code == 200, login.text
    client.headers["X-Expected-User"] = login.json()["user"]["id"]
    try:
        yield client
    finally:
        client.close()


def prepare_old_reports(old, database, env, output):
    """The archived release itself creates ready and stale simulated reports."""
    code = r'''
import json,sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from server.api.app import create_app
from server.automation.worker import Worker
db_path=Path(sys.argv[1]); output=Path(sys.argv[2])
class Provider:
    name='old-fixture'; model='simulated'
    def call(self,payload):
        if payload['kind']=='items':
            return {'items':[{'expense_id':e['id'],'analysis':'OLD-ITEM'} for e in payload['expenses']]}
        return {'summary':'OLD-READY-CONTENT'}
clock=[datetime.fromisoformat('2026-10-01T12:00:00+08:00')]
worker=Worker(db_path,primary=Provider(),clock=lambda:clock[0],call_limit=100)
app=create_app(db_path,worker=worker)
with TestClient(app) as client:
    client.headers['X-App-Request']='shared-expense'
    login=client.post('/api/auth/login',json={'username':'demo-a','password':'demo-only-2026'})
    assert login.status_code==200,login.text
    client.headers['X-Expected-User']=login.json()['user']['id']
    def create(month):
        body={'operation_id':str(uuid4()),'expense_id':str(uuid4()),'base_version':0,'kind':'create',
              'data':{'scope':'shared','amount_cents':12345,'purpose':'old-report-'+month,
                      'occurred_at':month+'-15T12:00:00+08:00','location':'','category':''}}
        response=client.post('/api/operations',json=body)
        assert response.status_code==200,response.text
        return response.json()['expense']
    september=create('2026-09');create('2026-10')
    setting=client.put('/api/settings',json={'monthly':{'enabled':True,'time':'20:00'}})
    assert setting.status_code==200,setting.text
    clock[0]=datetime.fromisoformat('2026-10-04T20:00:01+08:00')
    for _ in range(8):worker.tick()
    clock[0]=datetime.fromisoformat('2026-11-01T20:00:01+08:00')
    for _ in range(12):worker.tick()
    before={r['month']:r for r in client.get('/api/reports?scope=shared').json()['reports']}
    assert set(before)=={'2026-09','2026-10'} and all(r['status']=='ready' for r in before.values()),before
    change={'operation_id':str(uuid4()),'expense_id':september['id'],'base_version':september['version'],
            'kind':'update','data':{'amount_cents':12346,'purpose':september['purpose'],
                                   'occurred_at':september['occurred_at'],'location':'','category':''}}
    changed=client.post('/api/operations',json=change)
    assert changed.status_code==200,changed.text
    after={r['month']:r for r in client.get('/api/reports?scope=shared').json()['reports']}
    assert after['2026-09']['status']=='stale' and after['2026-10']['status']=='ready',after
    detail=client.get('/api/reports/'+after['2026-10']['id']).json()
    assert detail['content']['summary']=='OLD-READY-CONTENT'
    output.write_text(json.dumps({'ready_id':after['2026-10']['id'],'stale_id':after['2026-09']['id'],
                                  'ready_content':detail['content']},ensure_ascii=False))
'''
    result = subprocess.run([sys.executable, "-c", code, str(database), str(output)], cwd=old,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output.read_text())


def test_351ffe7_demo_upgrade_repeated_start_and_receipt(tmp_path):
    old = tmp_path / "old-351ffe7"
    old.mkdir()
    copy_legacy("351ffe7", old)
    candidate = Path(os.environ.get("FAMILY_CANDIDATE", ROOT)).resolve()
    instance = LiveLedger(old, tmp_path)
    instance.origin = "http://127.0.0.1:" + os.environ.get("FAMILY_HTTP_PORT", "18779")
    instance.env.update(SHARED_EXPENSE_PORT=instance.origin.rsplit(":", 1)[1],
                        SHARED_EXPENSE_ORIGINS=instance.origin)
    subprocess.run([sys.executable, "-m", "scripts.seed_demo", "--database", str(instance.database)],
                   cwd=old, env=instance.env, check=True, capture_output=True, text=True)
    try:
        instance.start()
        with old_client(instance) as client:
            operation = create_operation(purpose="351ffe7-legacy-receipt")
            accepted = client.post("/api/operations", json=operation)
            assert accepted.status_code == 200, accepted.text
            old_receipt = accepted.json()
            old_id = client.headers["X-Expected-User"]
        with sqlite3.connect(instance.database) as db:
            original_receipt, original_hash = db.execute(
                "SELECT receipt_json,request_hash FROM operations WHERE operation_id=?",
                (operation["operation_id"],)).fetchone()
        instance.stop()
        old_reports = prepare_old_reports(old, instance.database, instance.env, tmp_path / "old-reports.json")
        with sqlite3.connect(instance.database) as db:
            # Freeze the fictional scheduler so this migration/restart test
            # measures persisted state, not a legitimate later "unconfigured"
            # transition caused by the intentionally disabled real providers.
            db.execute('UPDATE app_state SET automation_paused=1 WHERE singleton=1')
            old_task_ids = {row[0] for row in db.execute("SELECT report_id FROM report_tasks")}
            old_ready_body = db.execute("SELECT content_json FROM reports WHERE id=?",
                                        (old_reports["ready_id"],)).fetchone()[0]
        instance.candidate = candidate
        migrated = subprocess.run([sys.executable, "-m", "scripts.migrate_memos", "--database",
                                   str(instance.database)], cwd=candidate, env=instance.env, capture_output=True, text=True)
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        with sqlite3.connect(instance.database) as db:
            assert {row[0] for row in db.execute("SELECT report_id FROM report_tasks")} == old_task_ids
            assert db.execute("SELECT content_json FROM reports WHERE id=?", (old_reports["ready_id"],)).fetchone()[0] == old_ready_body
        instance.start()
        with httpx.Client(base_url=instance.origin, trust_env=False,
                          headers={"Origin": instance.origin, "X-App-Request": "shared-expense"}) as client:
            login = client.post("/api/auth/login", json={"username": "demo-a", "password": "demo-only-2026"})
            assert login.status_code == 200, login.text
            client.headers["X-Expected-User"] = old_id
            family = client.get("/api/family").json()
            assert family["family"] is not None
            assert family["legacy_family_id"] == family["family"]["id"]
            assert {member["username"] for member in family["family"]["members"]} == {"demo-a", "demo-b"}
            client.headers["X-Family-Id"] = family["family"]["id"]
            client.headers["X-Membership-Version"] = str(family["membership_version"])
            first = client.get("/api/sync").json()
            assert operation["expense_id"] in {e["id"] for e in first["expenses"]}
            reports = {r["id"]:r for r in client.get("/api/reports?scope=shared").json()["reports"]}
            assert reports[old_reports["ready_id"]]["status"] == "ready"
            assert reports[old_reports["stale_id"]]["status"] == "stale"
            assert client.get("/api/reports/" + old_reports["ready_id"]).json()["content"] == old_reports["ready_content"]
            replay = client.post("/api/operations", json=operation)
            assert replay.status_code == 200, replay.text
            assert replay.json()["expense"]["id"] == old_receipt["expense"]["id"]
            assert replay.json()["expense"]["amount_cents"] == old_receipt["expense"]["amount_cents"]
            instance.stop()
            instance.start()
            second_family = client.get("/api/family").json()
            second = client.get("/api/sync").json()
            assert second_family == family
            assert {e["id"] for e in second["expenses"]} == {e["id"] for e in first["expenses"]}
            replay = client.post("/api/operations", json=operation)
            assert replay.status_code == 200, replay.text
            assert replay.json()["expense"]["id"] == old_receipt["expense"]["id"]
            assert replay.json()["expense"]["amount_cents"] == old_receipt["expense"]["amount_cents"]
        with sqlite3.connect(instance.database) as db:
            assert db.execute("SELECT receipt_json,request_hash FROM operations WHERE operation_id=?",
                              (operation["operation_id"],)).fetchone() == (original_receipt, original_hash)
    finally:
        instance.stop()
        instance.log.close()


def test_351ffe7_backup_restores_into_new_family_schema(tmp_path):
    old = tmp_path / "old-351ffe7"
    old.mkdir()
    copy_legacy("351ffe7", old)
    candidate = Path(os.environ.get("FAMILY_CANDIDATE", ROOT)).resolve()
    instance = LiveLedger(old, tmp_path)
    instance.origin = "http://127.0.0.1:" + os.environ.get("FAMILY_HTTP_PORT", "18779")
    instance.env.update(SHARED_EXPENSE_PORT=instance.origin.rsplit(":", 1)[1],
                        SHARED_EXPENSE_ORIGINS=instance.origin)
    subprocess.run([sys.executable, "-m", "scripts.seed_demo", "--database", str(instance.database)],
                   cwd=old, env=instance.env, check=True, capture_output=True, text=True)
    backup = tmp_path / "backup"
    destination = tmp_path / "restored.sqlite3"
    try:
        instance.start()
        with old_client(instance) as client:
            record = create_operation(amount=12345, purpose="351ffe7-restored-photo")
            created = client.post("/api/operations", json=record)
            assert created.status_code == 200, created.text
            image = image_path(created.json()["expense"])
            assert upload(client, image).status_code == 200
            stale_cookie = dict(client.cookies)
            before = client.get("/api/sync").json()
        result = subprocess.run([sys.executable, "-m", "scripts.backup", "--database", str(instance.database),
                                 "--destination", str(backup)], cwd=old, env=instance.env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        original_hashes = {name: hashlib.sha256((backup / name).read_bytes()).hexdigest()
                           for name in ("manifest.json", "business.sqlite3", "credentials.json")}
        instance.stop()
        restored = subprocess.run([sys.executable, "-m", "scripts.restore", "--backup", str(backup),
                                   "--destination", str(destination), "--credentials", str(backup / "credentials.json")],
                                  cwd=candidate, env=instance.env, capture_output=True, text=True)
        assert restored.returncode == 0, restored.stdout + restored.stderr
        assert {name: hashlib.sha256((backup / name).read_bytes()).hexdigest() for name in original_hashes} == original_hashes
        instance.candidate = candidate
        instance.database = destination
        instance.env["SHARED_EXPENSE_DB"] = str(destination)
        instance.start()
        with httpx.Client(base_url=instance.origin, trust_env=False, cookies=stale_cookie) as stale:
            assert stale.get("/api/auth/me").status_code == 401
        with httpx.Client(base_url=instance.origin, trust_env=False,
                          headers={"Origin": instance.origin, "X-App-Request": "shared-expense"}) as client:
            login = client.post("/api/auth/login", json={"username": "demo-a", "password": "demo-only-2026"})
            assert login.status_code == 200, login.text
            client.headers["X-Expected-User"] = login.json()["user"]["id"]
            family = client.get("/api/family").json()
            assert family["family"] is not None and family["legacy_family_id"] == family["family"]["id"]
            client.headers["X-Family-Id"] = family["family"]["id"]
            client.headers["X-Membership-Version"] = str(family["membership_version"])
            snapshot = client.get("/api/sync").json()
            assert snapshot["restore_id"] != before["restore_id"]
            assert snapshot["restored_at"]
            assert record["expense_id"] in {item["id"] for item in snapshot["expenses"]}
            assert client.get("/api/expenses/" + record["expense_id"]).json()["amount_cents"] == 12345
            assert client.get(image).content == png()
            assert client.get("/api/settings").json()["automation_paused"] is True
    finally:
        instance.stop()
        instance.log.close()
