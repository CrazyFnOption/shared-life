"""Independent worker contract checks using public API + injected transports.

No private table manipulation; time advances explicitly, calls never leave the
process. PHASE3_CANDIDATE must be a frozen archive when reporting acceptance.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
import importlib
import json
import os
from pathlib import Path
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from test_http_contract import ROOT, create_operation, submit, update_operation
from test_phase2 import action


class Clock:
    def __init__(self, value="2026-10-01T12:00:00+08:00"):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value

    def advance(self, **changes):
        self.value += timedelta(**changes)


class Provider:
    name = "independent-primary"
    model = "fictional-only"

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def call(self, payload):
        self.calls.append(json.loads(json.dumps(payload)))
        if self.failure:
            raise self.failure
        if payload["kind"] == "items":
            return {"items": [{"expense_id": row["id"], "analysis": "虚构分析，无评价时按一般理解"}
                              for row in payload["expenses"]]}
        return {"summary": "仅基于已验证逐笔结果的虚构复盘"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    candidate = Path(os.environ.get("PHASE3_CANDIDATE", ROOT)).resolve()
    sys.path.insert(0, str(candidate))
    dbmod = importlib.import_module("server.accounting.db")
    appmod = importlib.import_module("server.api.app")
    workmod = importlib.import_module("server.automation.worker")
    assert Path(appmod.__file__).is_relative_to(candidate), "Run this file separately to avoid importing another candidate"
    monkeypatch.setenv("SHARED_EXPENSE_ALLOW_MODEL_CALLS", "0")
    monkeypatch.setenv("SHARED_EXPENSE_ALLOW_PUSH", "0")
    monkeypatch.delenv("SHARED_EXPENSE_BACKUP_DIR", raising=False)
    path = tmp_path / "automation.sqlite3"
    clock, primary = Clock(), Provider()
    worker = workmod.Worker(path, primary=primary, clock=clock, call_limit=500)
    app = appmod.create_app(path, worker=worker)
    alice = dbmod.create_user(path, "alice", "fictional-a", "甲测试")
    bob = dbmod.create_user(path, "bob", "fictional-b", "乙测试")
    # New users start solo. This historical two-person fixture explicitly joins them.
    families = importlib.import_module("server.accounting.families")
    with dbmod.connect(path) as db:
        families.create_explicit_family(db, [alice["id"], bob["id"]])
    with ExitStack() as stack:
        a, b = stack.enter_context(TestClient(app)), stack.enter_context(TestClient(app))
        for client, name, password, user in [(a, "alice", "fictional-a", alice), (b, "bob", "fictional-b", bob)]:
            client.headers.update({"X-App-Request": "shared-expense"})
            response = client.post("/api/auth/login", json={"username": name, "password": password})
            assert response.status_code == 200, response.text
            client.headers["X-Expected-User"] = user["id"]
            family = client.get("/api/family").json()
            client.headers["X-Family-Id"] = family["family"]["id"]
            client.headers["X-Membership-Version"] = str(family["membership_version"])
        yield {"path": path, "candidate": candidate, "clock": clock, "worker": worker, "primary": primary,
               "a": a, "b": b, "Worker": workmod.Worker, "app": app}
    sys.path.remove(str(candidate))


def enable(env):
    response = env["a"].put("/api/settings", json={"monthly": {"enabled": True, "time": "20:00"}})
    assert response.status_code == 200, response.text
    # These historical report cases exercise both shared and each user's private
    # monthly jobs. The new scoped settings require explicit opt-in per namespace.
    for client in (env["a"], env["b"]):
        response = client.put("/api/settings?scope=private", json={"monthly": {"enabled": True, "time": "20:00"}})
        assert response.status_code == 200, response.text


def add(client, month="2026-09", scope="shared", purpose="虚构月报记录"):
    op = create_operation(scope, 12345, purpose)
    op["data"]["occurred_at"] = month + "-15T12:00:00+08:00"
    return submit(client, op)["expense"]


def reports(client, scope="shared"):
    response = client.get("/api/reports", params={"scope": scope})
    assert response.status_code == 200, response.text
    return response.json()["reports"]


def detail(client, report):
    response = client.get("/api/reports/" + report["id"])
    assert response.status_code == 200, response.text
    return response.json()


def drive(env, count=40, seconds=120):
    for _ in range(count):
        env["worker"].tick()
        env["clock"].advance(seconds=seconds)


def due(env):
    env["clock"].value = datetime.fromisoformat("2026-10-04T20:00:01+08:00")


def test_two_families_reports_settings_and_notifications_are_separate(env):
    with TestClient(env["app"]) as other:
        other.headers["X-App-Request"] = "shared-expense"
        name = "report-family-c"
        registration = other.post("/api/auth/register", json={"username": name,
            "display_name": "丙测试", "password": "fictional-password-2026"})
        assert registration.status_code == 200, registration.text
        other.headers["X-Expected-User"] = registration.json()["user"]["id"]
        solo = other.get("/api/family").json()
        assert solo["family"] is None
        made = other.post("/api/families", json={"request_id": "00000000-0000-4000-8000-000000000001", "name": "乙家庭"})
        assert made.status_code == 200, made.text
        context = made.json()
        other.headers["X-Family-Id"] = context["family"]["id"]
        other.headers["X-Membership-Version"] = str(context["membership_version"])
        own = add(env["a"], purpose="family-a-visible-only")
        foreign = add(other, purpose="family-c-visible-only")
        settings = other.get("/api/settings?scope=shared").json()
        assert settings["monthly"]["enabled"] is False
        enable(env)
        assert other.get("/api/settings?scope=shared").json()["monthly"]["enabled"] is False
        assert other.get("/api/settings?scope=private").json()["monthly"]["enabled"] is False
        enabled = other.put("/api/settings?scope=shared", json={"monthly": {"enabled": True, "time": "21:00"}})
        assert enabled.status_code == 200, enabled.text
        due(env); drive(env, count=8, seconds=0)
        first = reports(env["a"])[0]
        assert reports(env["b"])[0]["id"] == first["id"]
        assert reports(other) == []
        assert other.get("/api/reports/" + first["id"]).status_code == 404
        assert foreign["id"] not in {row["id"] for row in detail(env["a"], first)["expenses"]}
        env["clock"].value = datetime.fromisoformat("2026-10-04T21:00:01+08:00")
        drive(env)
        second = reports(other)[0]
        assert second["id"] != first["id"]
        assert env["a"].get("/api/reports/" + second["id"]).status_code == 404
        assert own["id"] not in {row["id"] for row in detail(other, second)["expenses"]}
        assert "family-c-visible-only" not in env["a"].get("/api/export?format=json&scope=all").text
        assert "family-a-visible-only" not in other.get("/api/export?format=json&scope=all").text
        assert env["a"].get("/api/settings").json()["notification_pending"] >= 1
        assert other.get("/api/settings").json()["notification_pending"] >= 1


def test_scheduler_respects_first_sunday_no_current_month_and_catches_up_after_restart(env):
    add(env["a"], "2026-08", purpose="不得追溯启用前月份")
    for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
        add(env["a"], month)
    enable(env)
    env["clock"].value = datetime.fromisoformat("2026-10-04T19:59:59+08:00")
    env["worker"].tick()
    assert reports(env["a"]) == [] and env["primary"].calls == []
    due(env)
    drive(env)
    assert {r["month"] for r in reports(env["a"])} == {"2026-09"}
    assert reports(env["b"], "private") == []
    env["clock"].value = datetime.fromisoformat("2026-12-07T21:00:00+08:00")
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], clock=env["clock"], call_limit=500)
    drive(env)
    result = reports(env["a"])
    assert {r["month"] for r in result} == {"2026-09", "2026-10", "2026-11"}
    assert len(result) == 3 and all(r["status"] == "ready" for r in result)
    assert env["a"].get("/api/reports?scope=invalid").status_code == 422


def test_report_inputs_are_scope_isolated_minimal_and_null_rating_is_preserved(env):
    shared = add(env["a"], purpose="SHARED-visible")
    own = add(env["a"], scope="private", purpose="ALICE-private-only")
    foreign = add(env["b"], scope="private", purpose="BOB-private-only")
    enable(env)
    due(env)
    drive(env)
    shared_report = reports(env["a"])[0]
    own_report = reports(env["a"], "private")[0]
    foreign_report = reports(env["b"], "private")[0]
    for client, allowed, blocked in [(env["a"], own_report, foreign_report), (env["b"], foreign_report, own_report)]:
        assert detail(client, allowed)["content"]
        assert client.get("/api/reports/" + blocked["id"]).status_code == 404
    shown = detail(env["b"], shared_report)
    assert shown["expenses"][0]["rating"] is None and shown["summary"]["unrated_count"] == 1
    item_calls = [p for p in env["primary"].calls if p["kind"] == "items"]
    sets = [{e["id"] for e in p["expenses"]} for p in item_calls]
    assert {shared["id"]} in sets and {own["id"]} in sets and {foreign["id"]} in sets
    assert all(len(ids) == 1 for ids in sets)
    serialized = json.dumps(item_calls)
    for forbidden in ("password_hash", "image_bytes", "change_requests", "revisions", "sessions"):
        assert forbidden not in serialized


def test_shared_scope_revocation_immediately_removes_old_content_and_never_resurrects_it(env):
    record = add(env["a"], purpose="withdrawn-private-marker")
    enable(env)
    due(env)
    drive(env)
    report = reports(env["b"])[0]
    old = detail(env["b"], report)
    assert old["content"] and old["expenses"]
    record = submit(env["a"], action(record, "scope", scope="private"))["expense"]
    hidden = detail(env["b"], report)
    assert hidden["content"] is None and not hidden.get("expenses")
    assert "withdrawn-private-marker" not in json.dumps(hidden)
    submit(env["a"], action(record, "scope", scope="shared"))
    returned = detail(env["b"], report)
    assert returned["content"] is None, "scope toggling must not revive revoked prose"
    drive(env)
    assert detail(env["b"], report)["status"] == "ready"


def test_generation_race_discards_late_result_and_rebuilds_latest_snapshot(env):
    record = add(env["a"], purpose="before-generation")
    enable(env)
    due(env)
    entered, release = threading.Event(), threading.Event()
    original = env["primary"].call
    def blocked(payload):
        if payload["kind"] == "items" and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(payload)
    env["primary"].call = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(env["worker"].tick)
        assert entered.wait(10)
        edited = submit(env["a"], update_operation(record, purpose="after-generation", amount_cents=22222))["expense"]
        release.set()
        running.result(timeout=10)
    report = reports(env["a"])[0]
    assert detail(env["a"], report)["content"] is None
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], clock=env["clock"], call_limit=500)
    drive(env)
    latest = detail(env["a"], report)
    assert latest["status"] == "ready" and latest["summary"]["total_cents"] == edited["net_amount_cents"]
    assert latest["expenses"][0]["purpose"] == "after-generation"


def test_budget_survives_worker_restart_and_no_calls_when_exhausted(env):
    add(env["a"])
    enable(env)
    due(env)
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], clock=env["clock"], call_limit=1)
    drive(env, 10)
    assert len(env["primary"].calls) == 1
    assert reports(env["a"])[0]["status"] != "ready"
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], clock=env["clock"], call_limit=1)
    drive(env, 10)
    assert len(env["primary"].calls) == 1


@pytest.mark.parametrize("malformation", ["missing", "duplicate", "extra", "amount"])
def test_untrusted_model_output_never_publishes_partial_duplicate_extra_or_amount_results(env, malformation):
    add(env["a"])
    enable(env)
    due(env)
    original = env["primary"].call
    def invalid(payload):
        value = original(payload)
        if payload["kind"] == "items":
            if malformation == "missing": value["items"] = []
            elif malformation == "duplicate": value["items"] *= 2
            elif malformation == "extra": value["items"].append({"expense_id": "foreign-id", "analysis": "不得发表"})
            else: value["items"][0]["net_amount_cents"] = 1
        return value
    env["primary"].call = invalid
    drive(env, 10)
    shown = detail(env["a"], reports(env["a"])[0])
    assert shown["status"] != "ready" and shown["content"] is None
    assert len(env["primary"].calls) <= 3, "invalid results must not spin without bounds"


def test_batches_cover_all_expenses_and_final_summary_uses_every_validated_item(env):
    records = [add(env["a"], purpose="批次虚构记录-" + str(i)) for i in range(51)]
    enable(env)
    due(env)
    drive(env, 15)
    shown = detail(env["a"], reports(env["a"])[0])
    assert shown["status"] == "ready"
    expected = {r["id"] for r in records}
    assert {item["expense_id"] for item in shown["content"]["items"]} == expected
    items = [call for call in env["primary"].calls if call["kind"] == "items"]
    assert len(items) >= 2 and all(len(call["expenses"]) <= 50 for call in items)
    actual = [row["id"] for call in items for row in call["expenses"]]
    assert len(actual) == 51 and set(actual) == expected
    summary = [call for call in env["primary"].calls if call["kind"] == "summary"]
    covered = [item["expense_id"] for call in summary for item in call["analyses"] if isinstance(item, dict)]
    assert set(covered) == expected and len(covered) == 51
    assert summary and summary[-1]["analyses"], "hierarchical summaries must retain all group results"


def test_primary_retry_is_bounded_then_backup_and_daily_failed_rounds_are_bounded(env):
    from server.automation.providers import ProviderError
    add(env["a"])
    enable(env)
    due(env)
    primary = Provider(ProviderError("transient"))
    backup = Provider(ProviderError("transient"))
    backup.name = "independent-backup"
    env["worker"] = env["Worker"](env["path"], primary=primary, backup=backup, clock=env["clock"], call_limit=500)
    drive(env, count=30, seconds=360)
    assert len(primary.calls) == 6 and len(backup.calls) == 3
    shown = detail(env["a"], reports(env["a"])[0])
    assert shown["status"] == "failed" and shown["content"] is None
    assert "transient" not in shown["error"]
    env["worker"] = env["Worker"](env["path"], primary=primary, backup=backup, clock=env["clock"], call_limit=500)
    drive(env, count=10, seconds=360)
    assert len(primary.calls) == 6 and len(backup.calls) == 3
    env["clock"].advance(days=1)
    drive(env, count=10)
    assert len(primary.calls) == 6 and len(backup.calls) == 3, "exhausted rounds require explicit retry/config change even on another day"


def test_explicit_primary_unavailable_uses_backup_once_per_step_and_records_actual_provider(env):
    from server.automation.providers import ProviderError
    add(env["a"])
    enable(env)
    due(env)
    primary = Provider(ProviderError("unavailable"))
    backup = Provider()
    backup.name = "independent-backup"
    env["worker"] = env["Worker"](env["path"], primary=primary, backup=backup, clock=env["clock"], call_limit=500)
    drive(env, count=10)
    shown = detail(env["a"], reports(env["a"])[0])
    assert shown["status"] == "ready" and shown["provider"] == backup.name
    assert shown["model"] == backup.model
    assert len(primary.calls) == 2 and len(backup.calls) == 2  # items + summary


class Push:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send(self, subscription, payload):
        self.calls.append((subscription, payload))
        if self.error:
            raise self.error


def subscribe(client, endpoint="https://fcm.googleapis.com/independent-fictional"):
    response = client.post("/api/push-subscriptions", json={"endpoint": endpoint,
        "keys": {"p256dh": "fictional-public-key", "auth": "fictional-auth"}})
    assert response.status_code in (200, 201, 204), response.text


def test_weekly_push_is_personal_deduplicated_and_contains_no_ledger_details(env):
    push = Push()
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], push=push, clock=env["clock"], call_limit=500)
    subscribe(env["a"])
    subscribe(env["a"])
    add(env["a"], scope="private", purpose="DO-NOT-PUSH-private-marker")
    response = env["a"].put("/api/settings", json={"weekly": {"enabled": True, "weekday": 6, "time": "19:00"}})
    assert response.status_code == 200
    due(env)
    drive(env, count=5)
    assert len(push.calls) == 1
    subscription, payload = push.calls[0]
    assert subscription["endpoint"].endswith("independent-fictional")
    assert payload == {"title": "一起记", "body": "你有待办，请打开一起记", "url": "/"}
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], push=push, clock=env["clock"], call_limit=500)
    drive(env, count=5)
    assert len(push.calls) == 1
    env["clock"].advance(days=7)
    drive(env, count=5)
    assert len(push.calls) == 2


def test_expired_push_subscription_stops_and_uncertain_transport_never_automatically_repeats(env):
    from server.automation.providers import PushError
    push = Push(PushError(410))
    add(env["a"], scope="private")
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], push=push, clock=env["clock"], call_limit=500)
    subscribe(env["a"])
    response = env["a"].put("/api/settings", json={"weekly": {"enabled": True, "weekday": 6, "time": "19:00"}})
    assert response.status_code == 200
    due(env)
    drive(env, count=10)
    assert len(push.calls) == 1
    env["clock"].advance(days=7)
    drive(env, count=5)
    assert len(push.calls) == 1
    subscribe(env["a"], "https://fcm.googleapis.com/independent-uncertain")
    push.error = PushError()
    drive(env, count=10)
    assert len(push.calls) == 2
    env["worker"] = env["Worker"](env["path"], primary=env["primary"], push=push, clock=env["clock"], call_limit=500)
    drive(env, count=10)
    assert len(push.calls) == 2


def test_competing_worker_and_expired_claim_cannot_publish_old_result(env):
    add(env["a"])
    enable(env)
    due(env)
    entered, release = threading.Event(), threading.Event()
    primary = env["primary"]
    original = primary.call
    def delayed(payload):
        is_first = not entered.is_set()
        if is_first:
            entered.set()
            assert release.wait(10)
        output = original(payload)
        if is_first:
            output["items"][0]["analysis"] = "OLD-CLAIM-MUST-NOT-PUBLISH"
        return output
    primary.call = delayed
    second = env["Worker"](env["path"], primary=primary, clock=env["clock"], call_limit=500)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(env["worker"].tick)
        assert entered.wait(10)
        second.tick()
        assert primary.calls == [], "a second worker must respect the first active claim"
        env["clock"].advance(seconds=121)
        second.tick()  # Simulate restart after a lease expired, with a charged call retained.
        release.set()
        first.result(timeout=10)
    env["worker"] = second
    drive(env, 5)
    result = detail(env["a"], reports(env["a"])[0])
    assert result["status"] == "ready"
    assert "OLD-CLAIM-MUST-NOT-PUBLISH" not in json.dumps(result)


def test_one_large_valid_refund_record_cannot_crash_tick_or_starve_another_report(env):
    from test_phase2 import refund
    record = add(env["a"], purpose="合法大退款输入")
    for index in range(16):
        record = submit(env["a"], refund(record, 100, reason=("虚构退款原因" * 100)[:500]))["expense"]
    add(env["a"], scope="private", purpose="必须继续处理的另一份报告")
    enable(env)
    due(env)
    drive(env, count=12)
    private = reports(env["a"], "private")[0]
    assert detail(env["a"], private)["status"] == "ready"
    heavy = detail(env["a"], reports(env["a"])[0])
    assert heavy["status"] == "ready"
    assert heavy["summary"]["total_cents"] == 10745
    assert all(len(json.dumps(call, ensure_ascii=False).encode()) <= 24000 for call in env["primary"].calls)


def test_restored_paused_worker_must_not_backup_until_explicit_admin_resume(env, monkeypatch, tmp_path):
    import subprocess
    backup = tmp_path / "source-backup"
    destination = tmp_path / "paused.sqlite3"
    automatic = tmp_path / "automatic-backups"
    automatic.mkdir()
    for module, arguments in [
        ("scripts.backup", ["--database", env["path"], "--destination", backup]),
        ("scripts.restore", ["--backup", backup, "--destination", destination]),
    ]:
        subprocess.run([sys.executable, "-m", module, *map(str, arguments)], cwd=env["candidate"], check=True, capture_output=True)
    monkeypatch.setenv("SHARED_EXPENSE_BACKUP_DIR", str(automatic))
    restored = env["Worker"](destination, primary=env["primary"], clock=env["clock"], call_limit=500)
    restored.tick()
    assert list(automatic.iterdir()) == [], "paused restore must not copy data to an inherited backup destination"
    subprocess.run([sys.executable, "-m", "scripts.automation", "--database", str(destination), "--resume"], cwd=env["candidate"], check=True, capture_output=True)
    restored.tick()
    created = list(automatic.iterdir())
    assert len(created) == 1 and (created[0] / "business.sqlite3").is_file()


def test_unconfigured_primary_does_not_silently_use_backup(env):
    add(env["a"])
    enable(env)
    due(env)
    backup = Provider()
    env["worker"] = env["Worker"](env["path"], backup=backup, clock=env["clock"], call_limit=500)
    drive(env, count=10)
    assert reports(env["a"])[0]["status"] == "unconfigured"
    assert backup.calls == []


def test_cross_month_edit_invalidates_both_existing_reports_and_rebuilds_program_totals(env):
    september = add(env["a"], "2026-09", purpose="从九月移动到十月")
    add(env["a"], "2026-10", purpose="十月保留记录")
    enable(env)
    env["clock"].value = datetime.fromisoformat("2026-11-01T20:00:01+08:00")
    drive(env, 10)
    existing = {row["month"]: row for row in reports(env["a"])}
    assert set(existing) == {"2026-09", "2026-10"}
    assert all(detail(env["a"], row)["status"] == "ready" for row in existing.values())
    submit(env["a"], update_operation(september, occurred_at="2026-10-15T12:00:00+08:00"))
    assert all(detail(env["a"], row)["content"] is None for row in existing.values())
    drive(env, 10)
    old, new = detail(env["a"], existing["2026-09"]), detail(env["a"], existing["2026-10"])
    assert old["summary"]["total_cents"] == 0 and not old["content"]
    assert new["status"] == "ready" and new["summary"]["total_cents"] == 24690
    assert len(new["expenses"]) == 2
