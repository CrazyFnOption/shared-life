from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from server.accounting.db import create_user, connect
from server.accounting.families import create_explicit_family
from server.api.app import create_app, SESSION_COOKIE


WRITE = {"X-App-Request": "shared-expense"}


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    app = create_app(path)
    alice = create_user(path, "alice", "fictional-a", "甲测试")
    bob = create_user(path, "bob", "fictional-b", "乙测试")
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        fid = create_explicit_family(db,[alice["id"],bob["id"]],"测试家庭")
        db.commit()
    with TestClient(app) as a, TestClient(app) as b:
        for client, name, password, user in [
            (a, "alice", "fictional-a", alice), (b, "bob", "fictional-b", bob)
        ]:
            response = client.post("/api/auth/login", json={"username": name, "password": password}, headers=WRITE)
            assert response.status_code == 200
            assert response.json() == {"user": user}
            client.headers.update(WRITE | {"X-Expected-User": user["id"], "X-Family-Id": fid, "X-Membership-Version": "1"})
        yield path, app, a, b, alice, bob


def operation(scope="shared", **data):
    return {
        "operation_id": str(uuid4()), "kind": "create", "expense_id": str(uuid4()), "base_version": 0,
        "data": {"scope": scope, "amount_cents": 1234, "purpose": "虚构午餐", "occurred_at": "2026-10-30T12:00:00+08:00", "location": "", **data},
    }


def update(created, **data):
    return {
        "operation_id": str(uuid4()), "kind": "update", "expense_id": created["id"], "base_version": created["version"],
        "data": {key: data.get(key, created[key]) for key in ("amount_cents", "purpose", "occurred_at", "location")},
    }


def submit(client, body):
    response = client.post("/api/operations", json=body)
    assert response.status_code == 200, response.text
    return response.json()["expense"]


def test_auth_headers_logout_and_restart(setup):
    path, app, a, _, alice, _ = setup
    assert a.get("/api/auth/me").json() == {"user": alice}
    cookie = a.cookies.get(SESSION_COOKIE)
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.set(SESSION_COOKIE, cookie)
        assert restarted.get("/api/auth/me").json() == {"user": alice}
    response = a.post("/api/auth/logout")
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert a.get("/api/auth/me").status_code == 401
    with TestClient(app) as old_cookie:
        old_cookie.cookies.set(SESSION_COOKIE, cookie)
        assert old_cookie.get("/api/auth/me").status_code == 401


def test_cookie_security_and_login_replaces_current_session(setup):
    _, app, a, _, _, _ = setup
    old_token = a.cookies.get(SESSION_COOKIE)
    response = a.post("/api/auth/login", json={"username": "alice", "password": "fictional-a"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie
    assert a.cookies.get(SESSION_COOKIE) != old_token
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, old_token)
        assert client.get("/api/auth/me").status_code == 401
    for credentials in [{"username": "alice", "password": "wrong"}, {"username": "missing", "password": "wrong"}]:
        assert a.post("/api/auth/login", json=credentials).status_code == 401


def test_default_empty_database_and_secure_cookie_option(tmp_path):
    path = tmp_path / "new.sqlite3"
    app = create_app(path, allowed_origins=["https://ledger.example"], secure_cookies=True)
    with TestClient(app, base_url="https://ledger.example") as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json={"username": "alice", "password": "fictional-a"}, headers=WRITE).status_code == 401
        create_user(path, "alice", "fictional-a", "甲测试")
        response = client.post("/api/auth/login", json={"username": "alice", "password": "fictional-a"}, headers=WRITE | {"Origin": "https://ledger.example"})
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]


def test_visibility_ownership_and_integer_totals(setup):
    _, _, a, b, alice, bob = setup
    common_a = submit(a, operation(amount_cents=20000))
    private_a = submit(a, operation("private", amount_cents=76543))
    common_b = submit(b, operation(amount_cents=10000))
    private_b = submit(b, operation("private"))
    for client, own_private, hidden in [(a, private_a, private_b), (b, private_b, private_a)]:
        snapshot = client.get("/api/sync").json()
        assert {e["id"] for e in snapshot["expenses"]} == {common_a["id"], common_b["id"], own_private["id"]}
        assert sum(e["amount_cents"] for e in snapshot["expenses"] if e["scope"] == "shared") == 30000
        assert {u["id"] for u in snapshot["users"]} == {alice["id"], bob["id"]}
        assert client.get(f"/api/expenses/{hidden['id']}").status_code == 404
        assert client.get(f"/api/expenses/{uuid4()}").status_code == 404
    assert b.post("/api/operations", json=update(common_a)).status_code == 404
    assert b.post("/api/operations", json=update(private_a)).status_code == 404
    assert common_a["owner_id"] == alice["id"]
    assert common_a["occurred_at"] == "2026-10-30T04:00:00Z"


def test_account_assertion_prevents_cross_tab_reads_and_writes(setup):
    _, _, a, _, alice, bob = setup
    created = submit(a, operation())
    a.post("/api/auth/login", json={"username": "bob", "password": "fictional-b"})
    for method, url, body in [
        ("GET", "/api/sync", None), ("GET", f"/api/expenses/{created['id']}", None),
        ("POST", "/api/operations", operation()), ("POST", "/api/auth/logout", None),
    ]:
        response = a.request(method, url, json=body)
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "account_changed"}}
    assert a.get("/api/auth/me").json()["user"] == bob
    a.headers["X-Expected-User"] = bob["id"]
    assert len(a.get("/api/sync").json()["expenses"]) == 1
    del a.headers["X-Expected-User"]
    assert a.get("/api/sync").status_code == 409


def test_origin_write_marker_json_and_no_store(setup):
    _, _, a, _, _, _ = setup
    assert a.get("/api/sync", headers={"Origin": "https://evil.example"}).status_code == 403
    assert a.get("/api/sync", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
    for url in ["/api/health", "/api/auth/me", "/api/sync"]:
        assert a.get(url).headers["cache-control"] == "no-store"
    assert a.post("/api/operations", json=operation(), headers={"X-App-Request": "wrong"}).status_code == 403
    response = a.post("/api/operations", content="{}", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in a.get("/api/health").headers


@pytest.mark.parametrize("invalid", [0, -1, 1000000000, 1.25, 100.0, "100", True, None])
def test_reject_non_integer_or_out_of_range_money(setup, invalid):
    _, _, a, _, _, _ = setup
    response = a.post("/api/operations", json=operation(amount_cents=invalid))
    assert response.status_code == 422
    assert a.get("/api/sync").json()["expenses"] == []


@pytest.mark.parametrize("data", [
    {"purpose": ""}, {"purpose": "   "}, {"purpose": "a" * 501}, {"location": "a" * 201},
    {"occurred_at": "2026-10-30T12:00:00"}, {"occurred_at": 12345678}, {"scope": "other"}, {"owner_id": "forged"},
])
def test_reject_invalid_fields(setup, data):
    _, _, a, _, _, _ = setup
    assert a.post("/api/operations", json=operation(**data)).status_code == 422


def test_create_replay_duplicate_and_semantic_digest(setup):
    _, _, a, _, _, _ = setup
    body = operation()
    first = a.post("/api/operations", json=body)
    equivalent = deepcopy(body)
    equivalent["data"]["occurred_at"] = "2026-10-30T04:00:00Z"
    assert a.post("/api/operations", json=equivalent).json() == first.json()
    changed = deepcopy(body)
    changed["data"]["amount_cents"] += 1
    response = a.post("/api/operations", json=changed)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "operation_id_reused"
    collision = dict(body, operation_id=str(uuid4()))
    response = a.post("/api/operations", json=collision)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "expense_exists"
    submit(a, operation())
    assert len(a.get("/api/sync").json()["expenses"]) == 2


def test_update_chain_conflict_receipt_replay_and_restart(setup):
    path, _, a, _, _, _ = setup
    created = submit(a, operation())
    first_edit = update(created, amount_cents=30000)
    second = submit(a, first_edit)
    third = submit(a, update(second, purpose="第二次更正"))
    assert third["version"] == 3
    response = a.post("/api/operations", json=update(created, amount_cents=40000))
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "version_conflict", "current": third}
    submit(a, operation())
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(a.cookies)
        restarted.headers.update(a.headers)
        assert submit(restarted, first_edit) == second
        assert restarted.get(f"/api/expenses/{created['id']}").json() == third
    bad_edit = update(third)
    bad_edit["data"]["scope"] = "private"
    assert a.post("/api/operations", json=bad_edit).status_code == 422


def test_concurrent_retries_commit_once(setup):
    _, app, a, _, _, _ = setup
    body = operation()
    def send(_):
        with TestClient(app) as client:
            client.cookies.update(a.cookies)
            client.headers.update(a.headers)
            return client.post("/api/operations", json=body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(send, range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert len(a.get("/api/sync").json()["expenses"]) == 1


def test_transaction_failure_rolls_back_receipt_expense_and_revision(setup):
    path, app, a, _, _, _ = setup
    # Fault injection lives only in this temporary test database, never in product hooks.
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON revisions BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    body = operation()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.update(a.cookies)
        client.headers.update(a.headers)
        response = client.post("/api/operations", json=body)
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
    assert a.get("/api/sync").json()["expenses"] == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_revision")
    assert submit(a, body)["version"] == 1


def test_same_operation_id_is_scoped_to_user(setup):
    _, _, a, b, _, _ = setup
    body_a, body_b = operation("private"), operation("private")
    body_b["operation_id"] = body_a["operation_id"]
    expense_a, expense_b = submit(a, body_a), submit(b, body_b)
    assert expense_a["id"] != expense_b["id"]
    assert a.get("/api/sync").json()["expenses"] == [expense_a]
    assert b.get("/api/sync").json()["expenses"] == [expense_b]


def test_expired_session_and_request_shape(setup):
    path, _, a, _, _, _ = setup
    for change in [{"owner_id": "forged"}, {"base_version": True}, {"base_version": 1}, {"expense_id": "bad"}]:
        body = dict(operation(), **change)
        assert a.post("/api/operations", json=body).status_code == 422
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert a.post("/api/operations", json=operation()).status_code == 401
    assert a.get("/api/sync").status_code == 401


def test_password_change_rejects_login_verified_before_change(setup, monkeypatch):
    """Pause login after Argon2 verification, change password, then resume it."""
    from threading import Event
    from server.accounting.db import password_hasher

    path, app, current, _, alice, _ = setup
    verified, resume = Event(), Event()
    hasher_class = type(password_hasher)
    original_verify = hasher_class.verify

    def pause_first_verification(self, encoded, password, *args, **kwargs):
        result = original_verify(self, encoded, password, *args, **kwargs)
        if password == 'fictional-a' and not verified.is_set():
            verified.set()
            assert resume.wait(timeout=10), 'Password change did not release paused login'
        return result

    monkeypatch.setattr(hasher_class, 'verify', pause_first_verification)
    with TestClient(app) as stale_login, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            stale_login.post, '/api/auth/login',
            json={'username': 'alice', 'password': 'fictional-a'}, headers=WRITE,
        )
        try:
            assert verified.wait(timeout=10), 'Login did not reach password verification'
            changed = current.post('/api/auth/password', json={
                'old_password': 'fictional-a', 'new_password': 'new-fictional-password',
            })
            assert changed.status_code == 204, changed.text
        finally:
            resume.set()
        response = pending.result(timeout=10)
        assert response.status_code == 401, response.text
        assert 'set-cookie' not in response.headers
        assert stale_login.get('/api/auth/me').status_code == 401
        assert current.get('/api/auth/me').json() == {'user': alice}
        with connect(path) as db:
            assert db.execute('SELECT count(*) FROM sessions WHERE user_id=?', (alice['id'],)).fetchone()[0] == 1
        fresh = stale_login.post('/api/auth/login', json={
            'username': 'alice', 'password': 'new-fictional-password',
        }, headers=WRITE)
        assert fresh.status_code == 200
        assert stale_login.get('/api/auth/me').json() == {'user': alice}
