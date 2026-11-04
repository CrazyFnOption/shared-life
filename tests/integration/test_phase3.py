"""Phase-3 public HTTP/CLI acceptance with fictional, isolated data only.

PHASE3_CANDIDATE selects the frozen source/build. Network providers and push
are explicitly disabled. SQLite inspection is limited to the backup artifact,
where the contract expressly promises credential separation.
"""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from test_http_contract import ROOT, create_operation, submit
from test_phase2 import action, expense, refund, request
from test_ui_refinement import image_path, png, seed, service_for, upload


@pytest.fixture(scope="module")
def phase3(tmp_path_factory):
    candidate = Path(os.environ.get("PHASE3_CANDIDATE", ROOT)).resolve()
    service = service_for(candidate, tmp_path_factory.mktemp("phase3-http"), 18778)
    service.env.update(SHARED_EXPENSE_ALLOW_MODEL_CALLS="0", SHARED_EXPENSE_ALLOW_PUSH="0",
                       SHARED_EXPENSE_MODEL_CALL_LIMIT="0")
    seed(service)
    try:
        service.start()
        yield service
    finally:
        service.stop()
        service.log.close()


def settings(client, **body):
    response = client.put("/api/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_defaults_shared_monthly_personal_weekly_and_validation(phase3):
    with phase3.client() as a, phase3.client("demo-b") as b:
        initial = a.get("/api/settings")
        assert initial.status_code == 200, initial.text
        value = initial.json()
        assert value["monthly"] == {"enabled": False, "time": "20:00", "updated_by": None}
        assert value["weekly"] == {"enabled": False, "weekday": 6, "time": "19:00"}
        assert value["automation_paused"] is False and value["push_available"] is False
        assert not value["provider_configured"]
        changed = settings(a, monthly={"enabled": True, "time": "21:07"},
                           weekly={"enabled": True, "weekday": 2, "time": "18:03"})
        assert changed["monthly"]["updated_by"] == a.headers["X-Expected-User"]
        other = b.get("/api/settings").json()
        assert other["monthly"] == changed["monthly"]
        assert other["weekly"] == initial.json()["weekly"]
        for body in [{"monthly": {"enabled": True, "time": "24:00"}},
                     {"weekly": {"enabled": True, "weekday": 7, "time": "09:00"}},
                     {"automation_paused": False}, {"provider_configured": True}]:
            assert a.put("/api/settings", json=body).status_code == 422
        settings(a, monthly={"enabled": False, "time": "20:00"},
                 weekly={"enabled": False, "weekday": 6, "time": "19:00"})


def test_new_write_apis_require_origin_identity_and_json(phase3):
    with phase3.client() as a:
        routes = [("PUT", "/api/settings", {"monthly": {"enabled": False, "time": "20:00"}}),
                  ("POST", "/api/push-subscriptions", {"endpoint": "https://fcm.googleapis.com/fictional-independent-push",
                    "keys": {"p256dh": "fictional-public", "auth": "fictional-auth"}})]
        for method, route, body in routes:
            assert a.request(method, route, json=body, headers={"Origin": "https://evil.invalid"}).status_code == 403
            headers = dict(a.headers)
            del headers["x-expected-user"]
            import httpx
            with httpx.Client(base_url=phase3.origin, cookies=a.cookies, headers=headers, trust_env=False) as late:
                assert late.request(method, route, json=body).status_code == 409
            assert a.request(method, route, content=json.dumps(body),
                             headers={"Content-Type": "text/plain"}).status_code in (403, 415, 422)


def test_export_scope_current_content_net_amounts_and_no_foreign_private_data(phase3):
    with phase3.client() as a, phase3.client("demo-b") as b:
        shared = expense(a, amount=20000, purpose="export-shared")
        shared = submit(a, refund(shared, 5000))["expense"]
        own = expense(a, amount=999, scope="private", purpose="export-own-private")
        foreign = expense(b, amount=777, scope="private", purpose="export-foreign-private")
        void = expense(a, amount=300, purpose="export-void")
        submit(a, action(void, "void"))
        for scope, included, excluded in [
            ("shared", {shared["id"]}, {own["id"], foreign["id"], void["id"]}),
            ("private", {own["id"]}, {shared["id"], foreign["id"], void["id"]}),
            ("all", {own["id"], shared["id"]}, {foreign["id"], void["id"]}),
        ]:
            response = a.get("/api/export", params={"format": "json", "scope": scope})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            value = response.json()
            assert value["schema_version"] and value["exported_at"] and value["timezone"] == "Asia/Shanghai"
            records = {row["id"]: row for row in value["expenses"]}
            assert included <= records.keys() and not excluded & records.keys()
            assert "export-foreign-private" not in response.text
            assert not any(key in value for key in ("revisions", "change_requests", "sessions", "password_hash"))
            if shared["id"] in records:
                assert records[shared["id"]]["amount_cents"] == 20000
                assert records[shared["id"]]["net_amount_cents"] == 15000
        assert a.get("/api/export?format=json&scope=other").status_code == 422
        assert a.get("/api/export?format=xml&scope=all").status_code == 422
        # An owner override must not widen visibility, whether rejected or ignored.
        override = a.get("/api/export", params={"format": "json", "scope": "private", "owner_id": b.headers["X-Expected-User"]})
        assert override.status_code in (200, 422)
        assert "export-foreign-private" not in override.text


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t=", "\r="])
def test_csv_formula_escaping_in_every_user_text_column(phase3, prefix):
    with phase3.client() as a:
        dangerous = prefix + "SUM(1,2)"
        record = expense(a, scope="private", purpose=dangerous, location=dangerous,
                         category=dangerous, rating=4, rating_reason=dangerous)
        submit(a, refund(record, 100, reason=dangerous))
        response = a.get("/api/export?format=csv&scope=private")
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert response.headers["cache-control"] == "no-store"
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        matching = [row for row in rows[1:] if record["id"] in row]
        assert len(matching) == 1
        for cell in matching[0]:
            if "SUM(1,2)" in cell:
                assert cell.startswith("'"), repr(cell)


def cli(service, module, *arguments, check=True):
    result = subprocess.run([sys.executable, "-m", module, *map(str, arguments)],
                            cwd=service.candidate, env=service.env, capture_output=True, text=True)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_online_backup_is_consistent_credentials_are_separate_and_restore_is_guarded(phase3, tmp_path):
    with phase3.client() as a, phase3.client("demo-b") as b:
        original = expense(a, amount=20000, purpose="backup-relations")
        original = submit(a, refund(original, 5000))["expense"]
        image = image_path(original)
        assert upload(a, image).status_code == 200
        pending, _ = request(b, original)
        subscribed = a.post("/api/push-subscriptions", json={"endpoint": "https://fcm.googleapis.com/backup-fictional-subscription", "keys": {"p256dh": "fictional-public", "auth": "fictional-backup-private-auth"}})
        assert subscribed.status_code == 200, subscribed.text
        before = a.get("/api/sync").json()
        backup = tmp_path / "backup"
        cli(phase3, "scripts.backup", "--database", phase3.database, "--destination", backup)
        assert stat.S_IMODE(backup.stat().st_mode) == 0o700
        assert stat.S_IMODE((backup / "credentials.json").stat().st_mode) == 0o600
        assert (backup / "business.sqlite3").is_file() and (backup / "manifest.json").is_file()
        assert b"fictional-backup-private-auth" not in (backup / "business.sqlite3").read_bytes()
        session_hash = hashlib.sha256(a.cookies.get("shared_expense_session").encode()).hexdigest().encode()
        assert session_hash not in (backup / "business.sqlite3").read_bytes()
        manifest_text = (backup / "manifest.json").read_text()
        assert hashlib.sha256((backup / "business.sqlite3").read_bytes()).hexdigest() in manifest_text
        with sqlite3.connect(backup / "business.sqlite3") as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert all(not row[0] for row in db.execute("SELECT password_hash FROM users"))
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if "subscription" in table:
                    assert db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] == 0
        # Backup must leave the source session/password/ledger usable.
        assert a.get("/api/sync").json()["expenses"] == before["expenses"]
        external_credentials = tmp_path / "external-credentials.json"
        (backup / "credentials.json").rename(external_credentials)
        for row in json.loads(external_credentials.read_text())["users"]:
            assert row["password_hash"].encode() not in (backup / "business.sqlite3").read_bytes()
        destination = tmp_path / "restored.sqlite3"
        cli(phase3, "scripts.restore", "--backup", backup, "--destination", destination,
            "--credentials", external_credentials)
        assert cli(phase3, "scripts.restore", "--backup", backup, "--destination", destination,
                   "--credentials", external_credentials, check=False).returncode != 0
        restored = service_for(phase3.candidate, tmp_path, 18779)
        restored.database = destination
        restored.env.update(SHARED_EXPENSE_DB=str(destination), SHARED_EXPENSE_ALLOW_MODEL_CALLS="0",
                            SHARED_EXPENSE_ALLOW_PUSH="0", SHARED_EXPENSE_MODEL_CALL_LIMIT="0")
        try:
            restored.start()
            import httpx
            with httpx.Client(base_url=restored.origin, cookies=a.cookies, trust_env=False) as stale:
                assert stale.get("/api/auth/me").status_code == 401
            with restored.client() as new:
                snapshot = new.get("/api/sync").json()
                assert snapshot["restore_id"] != before["restore_id"] and snapshot["restored_at"]
                assert snapshot["expenses"] == before["expenses"]
                assert new.get(image).content == png()
                assert any(row["id"] == pending["id"] for row in new.get("/api/change-requests").json()["requests"])
                assert new.get("/api/settings").json()["automation_paused"] is True
                for header in (None, before["restore_id"]):
                    headers = {} if header is None else {"X-Restore-Id": header}
                    response = new.post("/api/operations", json=create_operation(), headers=headers)
                    assert response.status_code == 409, response.text
                    assert response.json()["detail"]["code"] == "restore_changed"
                    for method, route, body in [
                        ("PUT", "/api/settings", {"monthly": {"enabled": True, "time": "23:59"}}),
                        ("POST", "/api/categories", {"scope": "shared", "name": "must-not-write"}),
                        ("DELETE", image, None),
                        ("POST", "/api/reports/nonexistent/retry", {}),
                        ("POST", "/api/push-subscriptions", {"endpoint": "https://fcm.googleapis.com/no-write", "keys": {"p256dh": "fictional", "auth": "fictional"}}),
                    ]:
                        rejected_write = new.request(method, route, json=body, headers=headers)
                        assert rejected_write.status_code == 409, (route, rejected_write.text)
                        assert rejected_write.json()["detail"]["code"] == "restore_changed"
                    assert new.get("/api/sync").json()["expenses"] == snapshot["expenses"]
                    assert new.get(image).content == png()
                new.headers["X-Restore-Id"] = snapshot["restore_id"]
                settings(new, monthly={"enabled": False, "time": "20:00"})
                assert new.get("/api/settings").json()["automation_paused"] is True
                duplicate_create = create_operation(amount=1)
                duplicate_create["expense_id"] = original["id"]
                assert new.post("/api/operations", json=duplicate_create).status_code == 409
                duplicate_refund = refund(original, 5000, refund_id=original["refunds"][0]["id"])
                assert new.post("/api/operations", json=duplicate_refund).status_code in (409, 422)
                current_expense = new.get("/api/expenses/" + original["id"]).json()
                assert current_expense["net_amount_cents"] == 15000 and len(current_expense["refunds"]) == 1
                assert submit(new, create_operation())["expense"]
            cli(phase3, "scripts.automation", "--database", destination, "--resume")
        finally:
            restored.stop()
        disabled = tmp_path / "disabled.sqlite3"
        result = cli(phase3, "scripts.restore", "--backup", backup, "--destination", disabled)
        assert "Login disabled" in result.stdout
        restored.database = disabled
        restored.env["SHARED_EXPENSE_DB"] = str(disabled)
        try:
            restored.start()
            import httpx
            with httpx.Client(base_url=restored.origin, headers={"X-App-Request": "shared-expense"}, trust_env=False) as login:
                assert login.post("/api/auth/login", json={"username": "demo-a", "password": "demo-only-2026"}).status_code == 401
        finally:
            restored.stop()
            restored.log.close()
        # A corrupted backup must be rejected before creating a usable database.
        with (backup / "business.sqlite3").open("ab") as file:
            file.write(b"tampered")
        rejected = cli(phase3, "scripts.restore", "--backup", backup, "--destination", tmp_path / "corrupt.sqlite3", check=False)
        assert rejected.returncode != 0


def test_each_account_last_sync_time_only_advances_for_that_account(phase3):
    with phase3.client() as a, phase3.client("demo-b") as b:
        first = a.get("/api/sync").json()
        before = {row["id"]: row.get("last_synced_at") for row in first["users"]}
        own_id, other_id = a.headers["X-Expected-User"], b.headers["X-Expected-User"]
        assert before[own_id]
        second = {row["id"]: row.get("last_synced_at") for row in a.get("/api/sync").json()["users"]}
        assert second[own_id] >= before[own_id]
        assert second[other_id] == before[other_id], "reading as A cannot claim B's device synchronized"
        b.get("/api/sync").raise_for_status()
        after = {row["id"]: row.get("last_synced_at") for row in a.get("/api/sync").json()["users"]}
        assert after[other_id] and (not before[other_id] or after[other_id] > before[other_id])
