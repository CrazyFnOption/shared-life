"""Independent contract tests against a frozen service over real HTTP.

Run from the repository root; HTTP_CANDIDATE selects an immutable archive.
No test imports server internals or reads application database tables.
"""

import copy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]


class LiveLedger:
    def __init__(self, candidate, work):
        self.candidate = candidate
        self.database = work / "fictional.sqlite3"
        self.origin = "http://127.0.0.1:" + os.environ.get("HTTP_TEST_PORT", "18765")
        self.env = dict(os.environ, SHARED_EXPENSE_DB=str(self.database),
                        SHARED_EXPENSE_PORT=self.origin.rsplit(":", 1)[1],
                        SHARED_EXPENSE_ORIGINS=self.origin, PYTHONDONTWRITEBYTECODE="1",
                        SHARED_EXPENSE_ALLOW_MODEL_CALLS="0", SHARED_EXPENSE_MODEL_CALL_LIMIT="0",
                        SHARED_EXPENSE_ALLOW_PUSH="0", SHARED_EXPENSE_BACKUP_DIR="")
        self.log = (work / "service.log").open("w+")
        self.process = None

    def start(self):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", int(self.env["SHARED_EXPENSE_PORT"]))) == 0:
                raise RuntimeError("The independent test port is already occupied")
        self.process = subprocess.Popen([sys.executable, "-m", "server.main"],
                                        cwd=self.candidate, env=self.env,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 10
        with httpx.Client(base_url=self.origin, timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    if client.get("/api/health").status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                time.sleep(.05)
        self.log.seek(0)
        raise RuntimeError("Test service did not start: " + self.log.read())

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    @contextmanager
    def client(self, username="demo-a", *, legacy=False):
        with httpx.Client(base_url=self.origin, trust_env=False,
                          headers={"X-App-Request": "shared-expense", "Origin": self.origin}) as client:
            response = client.post("/api/auth/login", json={"username": username, "password": "demo-only-2026"})
            assert response.status_code == 200, response.text
            user = response.json()["user"]
            client.headers["X-Expected-User"] = user["id"]
            if not legacy:
                family = client.get("/api/family")
                assert family.status_code == 200, family.text
                context = family.json()
                client.headers["X-Family-Id"] = context["family"]["id"] if context["family"] else "none"
                client.headers["X-Membership-Version"] = str(context["membership_version"])
            yield client


@pytest.fixture(scope="session")
def ledger(tmp_path_factory):
    candidate = Path(os.environ.get("HTTP_CANDIDATE", ROOT)).resolve()
    work = tmp_path_factory.mktemp("independent-http")
    service = LiveLedger(candidate, work)
    subprocess.run([sys.executable, "-m", "scripts.seed_demo", "--database", str(service.database)],
                   cwd=candidate, env=service.env, check=True, capture_output=True, text=True)
    try:
        service.start()
        yield service
    finally:
        service.stop()
        service.log.close()


def create_operation(scope="shared", amount=20000, purpose="虚构共同餐费"):
    return {"operation_id": str(uuid.uuid4()), "kind": "create", "expense_id": str(uuid.uuid4()),
            "base_version": 0, "data": {"scope": scope, "amount_cents": amount, "purpose": purpose,
            "occurred_at": "2026-09-30T23:30:00+08:00", "location": "虚构餐厅"}}


def update_operation(expense, **changes):
    data = {key: expense[key] for key in ("amount_cents", "purpose", "occurred_at", "location")}
    data.update(changes)
    return {"operation_id": str(uuid.uuid4()), "kind": "update", "expense_id": expense["id"],
            "base_version": expense["version"], "data": data}


def submit(client, operation):
    response = client.post("/api/operations", json=operation)
    assert response.status_code == 200, response.text
    return response.json()


def test_two_accounts_only_share_shared_records_and_owner_can_edit(ledger):
    with ledger.client() as a, ledger.client("demo-b") as b:
        shared = submit(a, create_operation())["expense"]
        private = submit(a, create_operation("private", purpose="虚构私人用品"))["expense"]
        changed = submit(a, update_operation(shared, amount_cents=12345, purpose="本人更正"))["expense"]
        assert changed["version"] == 2
        assert changed["amount_cents"] == 12345
        assert b.get("/api/expenses/" + shared["id"]).json() == changed
        assert b.get("/api/expenses/" + private["id"]).status_code == 404
        visible = {row["id"] for row in b.get("/api/sync").json()["expenses"]}
        assert shared["id"] in visible and private["id"] not in visible
        own = {row["id"] for row in a.get("/api/sync").json()["expenses"]}
        assert {shared["id"], private["id"]} <= own
        for expense in (changed, private):
            denied = b.post("/api/operations", json=update_operation(expense, purpose="越权更正"))
            assert denied.status_code == 404
            assert "current" not in denied.text
            assert a.get("/api/expenses/" + expense["id"]).json() == expense


@pytest.mark.parametrize("amount", [1, 999999999])
def test_integer_cents_and_timezone_are_preserved(ledger, amount):
    with ledger.client() as client:
        operation = create_operation(amount=amount)
        expense = submit(client, operation)["expense"]
        assert expense["amount_cents"] == amount
        assert datetime.fromisoformat(expense["occurred_at"]) == datetime.fromisoformat("2026-09-30T15:30:00+00:00")
        assert client.get("/api/expenses/" + expense["id"]).json() == expense


@pytest.mark.parametrize("amount", [0, -1, 1.5, "1", True, 1000000000])
def test_invalid_amount_is_rejected_without_creating_record(ledger, amount):
    with ledger.client() as client:
        operation = create_operation(amount=amount)
        response = client.post("/api/operations", json=operation)
        assert response.status_code == 422, response.text
        assert client.get("/api/expenses/" + operation["expense_id"]).status_code == 404


@pytest.mark.parametrize("field,value", [("owner_id", "forged-payer"), ("extra", "unknown"),
                                        ("occurred_at", "2026-10-01T01:00:00"), ("purpose", "")])
def test_invalid_content_and_payer_forgery_are_rejected(ledger, field, value):
    with ledger.client() as client:
        operation = create_operation()
        operation["data"][field] = value
        assert client.post("/api/operations", json=operation).status_code == 422
        assert client.get("/api/expenses/" + operation["expense_id"]).status_code == 404


def test_account_assertion_blocks_late_reads_writes_and_logout_after_cookie_switch(ledger):
    with ledger.client() as client:
        original_user = client.headers["X-Expected-User"]
        private = submit(client, create_operation("private"))["expense"]
        switched = client.post("/api/auth/login", json={"username": "demo-b", "password": "demo-only-2026"})
        assert switched.status_code == 200
        new_user = switched.json()["user"]["id"]
        assert new_user != original_user
        late_operation = create_operation("private", purpose="甲旧标签的离线内容")
        requests = [("GET", "/api/sync", None), ("GET", "/api/expenses/" + private["id"], None),
                    ("POST", "/api/operations", late_operation), ("POST", "/api/auth/logout", None)]
        for method, path, body in requests:
            response = client.request(method, path, json=body)
            assert response.status_code == 409
            assert response.json() == {"detail": {"code": "account_changed"}}
        # Late logout must leave the switched user's active session intact.
        assert client.get("/api/auth/me").json()["user"]["id"] == new_user
        client.headers["X-Expected-User"] = new_user
        assert client.get("/api/expenses/" + late_operation["expense_id"]).status_code == 404
        assert private["id"] not in {row["id"] for row in client.get("/api/sync").json()["expenses"]}


def test_missing_account_assertion_does_not_return_business_data(ledger):
    with ledger.client() as client:
        del client.headers["X-Expected-User"]
        response = client.get("/api/sync")
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "account_changed"}}


def test_create_and_update_replay_return_original_receipt_and_same_amount_can_be_kept_twice(ledger):
    with ledger.client() as client:
        operation = create_operation()
        created = submit(client, operation)
        assert submit(client, operation) == created
        edited_operation = update_operation(created["expense"], amount_cents=101)
        edited = submit(client, edited_operation)
        assert submit(client, edited_operation) == edited
        # An old successful create remains replayable after the record changes.
        assert submit(client, operation) == created
        assert client.get("/api/expenses/" + operation["expense_id"]).json() == edited["expense"]
        reused = copy.deepcopy(operation)
        reused["data"]["amount_cents"] = 2
        response = client.post("/api/operations", json=reused)
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "operation_id_reused"}}
        duplicate = copy.deepcopy(operation)
        duplicate["operation_id"] = str(uuid.uuid4())
        response = client.post("/api/operations", json=duplicate)
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "expense_exists"}}
        second = submit(client, create_operation())["expense"]
        assert second["id"] != created["expense"]["id"]
        visible = {row["id"] for row in client.get("/api/sync").json()["expenses"]}
        assert {second["id"], created["expense"]["id"]} <= visible


def test_semantically_identical_json_replays_without_key_order_dependency(ledger):
    with ledger.client() as client:
        operation = create_operation()
        receipt = submit(client, operation)
        reordered = dict(reversed(list(operation.items())))
        reordered["data"] = dict(reversed(list(operation["data"].items())))
        assert submit(client, reordered) == receipt


def send_without_receiving_receipt(ledger, client, operation, expected_version):
    """Real network fault: send the request; never read its HTTP response.

    Observe commit using a separate authorized GET before dropping the socket,
    so this specifically covers a committed operation with an unreceived reply.
    """
    body = json.dumps(operation).encode()
    headers = {"Host": ledger.origin.removeprefix("http://"), "Content-Type": "application/json",
               "Content-Length": str(len(body)), "Cookie": "; ".join(f"{k}={v}" for k, v in client.cookies.items()),
               "X-App-Request": "shared-expense", "X-Expected-User": client.headers["X-Expected-User"],
               "X-Family-Id": client.headers["X-Family-Id"],
               "X-Membership-Version": client.headers["X-Membership-Version"],
               "Origin": ledger.origin, "Connection": "close"}
    request = "POST /api/operations HTTP/1.1\r\n" + "\r\n".join(f"{k}: {v}" for k, v in headers.items()) + "\r\n\r\n"
    port = int(ledger.origin.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(request.encode() + body)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/expenses/" + operation["expense_id"])
            if response.status_code == 200 and response.json()["version"] == expected_version:
                return response.json()
            time.sleep(.02)
    raise AssertionError("Unreceived operation was not observed committed through HTTP")


def test_commit_with_unreceived_create_and_update_receipts_can_be_retried_after_restart(ledger):
    with ledger.client() as client:
        operation = create_operation("private", amount=7, purpose="丢回执创建")
        created = send_without_receiving_receipt(ledger, client, operation, 1)
        ledger.stop()
        ledger.start()
        assert submit(client, operation)["expense"] == created
        update = update_operation(created, amount_cents=8, purpose="丢回执修改")
        updated = send_without_receiving_receipt(ledger, client, update, 2)
        ledger.stop()
        ledger.start()
        assert submit(client, update)["expense"] == updated
        assert client.get("/api/expenses/" + created["id"]).json() == updated
        rows = [row for row in client.get("/api/sync").json()["expenses"] if row["id"] == created["id"]]
        assert rows == [updated]


def test_stale_edit_keeps_current_value_and_does_not_block_another_record(ledger):
    with ledger.client() as first_device, ledger.client() as second_device:
        original = submit(first_device, create_operation())["expense"]
        current = submit(second_device, update_operation(original, purpose="设备二先提交", amount_cents=30000))["expense"]
        stale = update_operation(original, purpose="设备一保留草稿", amount_cents=40000)
        conflict = first_device.post("/api/operations", json=stale)
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": {"code": "version_conflict", "current": current}}
        unrelated = submit(first_device, create_operation(purpose="不相关记录仍提交"))["expense"]
        assert first_device.get("/api/expenses/" + original["id"]).json() == current
        assert second_device.get("/api/expenses/" + unrelated["id"]).json() == unrelated


def test_unauthenticated_wrong_password_and_revoked_session_are_denied(ledger):
    with httpx.Client(base_url=ledger.origin, trust_env=False) as anonymous:
        assert anonymous.get("/api/auth/me").status_code == 401
        assert anonymous.get("/api/sync", headers={"X-Expected-User": str(uuid.uuid4())}).status_code == 401
        denied = anonymous.post("/api/auth/login", headers={"X-App-Request": "shared-expense"},
                                json={"username": "demo-a", "password": "incorrect-fictional-password"})
        assert denied.status_code == 401
    with ledger.client() as client:
        old_cookies = dict(client.cookies)
        user = client.headers["X-Expected-User"]
        assert client.post("/api/auth/logout").status_code == 204
        with httpx.Client(base_url=ledger.origin, cookies=old_cookies, trust_env=False) as replay:
            assert replay.get("/api/auth/me").status_code == 401
            assert replay.get("/api/sync", headers={"X-Expected-User": user}).status_code == 401


def test_cookie_and_origin_write_guards_and_no_store_headers(ledger):
    with ledger.client() as client:
        response = client.get("/api/sync")
        assert response.headers["cache-control"] == "no-store"
        operation = create_operation()
        client.headers.pop("X-App-Request")
        denied = client.post("/api/operations", json=operation)
        assert denied.status_code == 403
        client.headers["X-App-Request"] = "shared-expense"
        denied = client.post("/api/operations", headers={"Origin": "https://foreign.invalid"}, json=operation)
        assert denied.status_code == 403
        assert "access-control-allow-origin" not in denied.headers
        assert client.get("/api/expenses/" + operation["expense_id"]).status_code == 404
        login = client.post("/api/auth/login", json={"username": "demo-a", "password": "demo-only-2026"})
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert "secure" not in cookie  # this isolated origin is explicitly HTTP


def test_concurrent_duplicate_and_stale_updates_apply_only_once(ledger):
    with ledger.client() as client:
        operation = create_operation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/api/operations", json=operation), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].json() == responses[1].json()
        expense = responses[0].json()["expense"]
        edits = [update_operation(expense, amount_cents=11), update_operation(expense, amount_cents=12)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda edit: client.post("/api/operations", json=edit), edits))
        assert sorted(result.status_code for result in results) == [200, 409]
        winner = next(result.json()["expense"] for result in results if result.status_code == 200)
        assert client.get("/api/expenses/" + expense["id"]).json() == winner
        assert winner["version"] == 2
