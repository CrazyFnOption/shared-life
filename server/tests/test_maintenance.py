"""Phase-two amount, atomicity, migration and privacy checks using fictional data."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from server.accounting.db import initialize_database, migrate_database
from server.accounting.ledger import canonical_json, digest
from server.api.app import create_app
from server.tests.test_api import setup, operation, submit, update  # noqa: F401
from server.tests.test_refinement import image_url, upload


def action(expense, kind, **data):
    return {"operation_id": str(uuid4()), "expense_id": expense["id"], "base_version": expense["version"], "kind": kind, "data": data}


def refund(expense, amount=5000, **data):
    return action(expense, "refund_create", **{"refund_id": str(uuid4()), "amount_cents": amount, "occurred_at": "2026-11-02T00:00:00+08:00", "reason": "虚构退款", **data})


def propose(client, expense, kind="update", data=None):
    body = {"id": str(uuid4()), "expense_id": expense["id"], "base_version": expense["version"], "kind": kind,
            "data": update(expense)["data"] if data is None else data, "reason": "请核对虚构记录"}
    response = client.post("/api/change-requests", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def decide(client, request, decision="accept", decision_id=None):
    return client.post(f"/api/change-requests/{request['id']}/decision", json={"decision_id": decision_id or str(uuid4()), "decision": decision})


def test_partial_full_refund_adjustment_and_original_month(setup):
    _, _, a, b, _, _ = setup
    original = submit(a, operation(amount_cents=20000, occurred_at="2026-10-31T23:50:00+08:00"))
    submit(b, operation(amount_cents=10000))
    submit(a, operation("private", amount_cents=76543))
    first_body = refund(original)
    current = submit(a, first_body)
    shared = [e for e in b.get("/api/sync").json()["expenses"] if e["scope"] == "shared"]
    assert sum(e["net_amount_cents"] for e in shared) == 25000
    assert current["amount_cents"] == 20000 and current["net_amount_cents"] == 15000
    assert current["occurred_at"] == "2026-10-31T15:50:00Z"
    assert current["refunds"][0]["occurred_at"] == "2026-11-01T16:00:00Z"
    current = submit(a, refund(current, 15000))
    assert current["net_amount_cents"] == 0 and len(current["refunds"]) == 2
    assert a.post("/api/operations", json=refund(current, 1)).json()["detail"]["code"] == "refund_exceeds_amount"
    assert a.post("/api/operations", json=update(current, amount_cents=19999)).status_code == 422
    assert a.get(f"/api/expenses/{original['id']}").json() == current
    moved = submit(a, update(current, occurred_at="2026-09-15T10:00:00+08:00"))
    assert moved["occurred_at"].startswith("2026-09") and moved["net_amount_cents"] == 0
    voided = submit(a, action(moved, "void"))
    assert voided["voided"] and voided["net_amount_cents"] == 0
    assert len(b.get("/api/sync").json()["expenses"]) == 1


def test_refund_update_void_and_owner_only_history(setup):
    _, _, a, b, _, _ = setup
    original = submit(a, operation(amount_cents=20000))
    current = submit(a, refund(original))
    r = current["refunds"][0]
    current = submit(a, action(current, "refund_update", refund_id=r["id"], amount_cents=6000, occurred_at=r["occurred_at"], reason="更正退款"))
    assert current["refunds"][0]["version"] == 2 and current["net_amount_cents"] == 14000
    current = submit(a, action(current, "refund_void", refund_id=r["id"]))
    assert current["refunds"][0]["version"] == 3 and current["net_amount_cents"] == 20000
    assert b.get(f"/api/expenses/{current['id']}").json()["refunds"] == []
    assert a.post("/api/operations", json=action(current, "refund_void", refund_id=r["id"])).json()["detail"]["code"] == "refund_voided"
    history = a.get(f"/api/expenses/{current['id']}/history").json()["revisions"]
    assert len(history) == 4 and history[-1]["before"]["refunds"][0]["amount_cents"] == 6000
    assert history[-1]["after"] == current
    assert b.get(f"/api/expenses/{current['id']}/history").status_code == 404


def test_refund_id_retry_conflict_and_restart(setup):
    path, _, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    body = refund(expense)
    first = submit(a, body)
    later = submit(a, action(first, "review", rating=4, rating_reason="满意"))
    assert submit(a, body) == first
    collision = refund(later, refund_id=body["data"]["refund_id"])
    assert a.post("/api/operations", json=collision).json()["detail"]["code"] == "refund_exists"
    assert a.post("/api/operations", json=refund(expense)).json()["detail"]["current"] == later
    assert b.post("/api/operations", json=refund(later)).status_code == 404
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(a.cookies)
        restarted.headers.update(a.headers)
        assert submit(restarted, body) == first
        assert restarted.get(f"/api/expenses/{expense['id']}").json() == later


@pytest.mark.parametrize("data", [
    {"rating": 0, "rating_reason": "原因"}, {"rating": 6, "rating_reason": "原因"},
    {"rating": True, "rating_reason": "原因"}, {"rating": "3", "rating_reason": "原因"},
    {"rating": None, "rating_reason": "原因"},
    {"rating_reason": "原因"},
])
def test_rating_pair_validation(setup, data):
    _, _, a, _, _, _ = setup
    assert a.post("/api/operations", json=operation(**data)).status_code == 422
    assert a.get("/api/sync").json()["expenses"] == []


def test_rating_omission_preserves_clear_is_explicit(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation(rating=5, rating_reason="  很满意  "))
    assert expense["rating_reason"] == "很满意"
    expense = submit(a, update(expense, purpose="旧客户端更新"))
    assert expense["rating"] == 5
    assert b.post("/api/operations", json=action(expense, "review", rating=1, rating_reason="越权")).status_code == 404
    expense = submit(a, action(expense, "review", rating=None, rating_reason=""))
    assert expense["rating"] is None and expense["rating_reason"] == ""


def test_request_pending_reject_accept_original_before_and_retry(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    proposal, body = propose(b, expense, "refund_create", refund(expense)["data"])
    assert proposal["before"] == expense and proposal["status"] == "pending"
    assert a.get(f"/api/expenses/{expense['id']}").json() == expense
    assert b.post("/api/change-requests", json=body).json() == proposal
    altered = deepcopy(body); altered["reason"] = "不同申请内容"
    assert b.post("/api/change-requests", json=altered).json()["detail"]["code"] == "request_id_reused"
    assert decide(b, proposal).status_code == 404
    rejection_id = str(uuid4())
    rejected = decide(a, proposal, "reject", rejection_id).json()
    assert rejected["expense"] == expense and rejected["request"]["status"] == "rejected"
    assert decide(a, proposal, "reject", rejection_id).json() == rejected
    assert decide(a, proposal).json()["detail"]["code"] == "request_decided"
    next_request, _ = propose(b, expense, "refund_create", refund(expense)["data"])
    another, _ = propose(b, expense)
    decision_id = str(uuid4())
    accepted = decide(a, next_request, decision_id=decision_id)
    assert accepted.status_code == 200, accepted.text
    accepted = accepted.json()
    assert accepted["expense"]["net_amount_cents"] == 15000 and accepted["request"]["before"] == expense
    assert decide(a, next_request, decision_id=decision_id).json() == accepted
    assert decide(a, next_request).json()["detail"]["code"] == "request_decided"
    assert decide(a, next_request, "reject", decision_id).json()["detail"]["code"] == "request_decided"
    assert decide(a, another).json()["detail"]["code"] == "request_stale"
    assert len(a.get(f"/api/expenses/{expense['id']}/history").json()["revisions"]) == 2


def test_request_validation_is_read_only_and_stale_never_applies(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    invalid = {"id": str(uuid4()), "expense_id": expense["id"], "base_version": 1, "kind": "refund_create", "data": refund(expense, 20001)["data"], "reason": "原因"}
    assert b.post("/api/change-requests", json=invalid).status_code == 422
    assert b.get("/api/change-requests").json() == {"requests": []}
    proposal, body = propose(b, expense)
    current = submit(a, action(expense, "review", rating=4, rating_reason="还不错"))
    assert decide(a, proposal).json()["detail"]["code"] == "request_stale"
    assert decide(a, proposal, "reject").json()["detail"]["code"] == "request_stale"
    old = dict(body, id=str(uuid4()))
    assert b.post("/api/change-requests", json=old).json()["detail"]["current"] == current
    assert a.get(f"/api/expenses/{expense['id']}").json() == current
    for kind, data in [("scope", {"scope": "private"}), ("review", {"rating": 2, "rating_reason": "差"}), ("update", dict(update(current)["data"], rating=3, rating_reason="一般"))]:
        invalid = dict(body, id=str(uuid4()), kind=kind, data=data, base_version=current["version"])
        assert b.post("/api/change-requests", json=invalid).status_code == 422


def test_scope_withdraws_requests_images_and_private_history_permanently(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation())
    url = image_url(expense["id"])
    assert upload(a, url).status_code == 200
    proposal, body = propose(b, expense)
    private = submit(a, action(expense, "scope", scope="private"))
    for path in [f"/api/expenses/{expense['id']}", f"/api/expenses/{expense['id']}/history", url]:
        assert b.get(path).status_code == 404
    assert b.post("/api/change-requests", json=body).status_code == 404
    assert b.get("/api/sync").json()["requests"] == []
    assert b.get("/api/sync").json()["images"] == []
    assert len(a.get("/api/change-requests").json()["requests"]) == 1
    private = submit(a, refund(private, 1000, reason="私人曾用退款原因"))
    private = submit(a, action(private, "refund_void", refund_id=private["refunds"][0]["id"]))
    shared = submit(a, action(private, "scope", scope="shared"))
    visible = b.get("/api/sync").json()
    assert visible["requests"] == [] and len(visible["images"]) == 1
    assert "私人曾用退款原因" not in json.dumps(visible, ensure_ascii=False)
    assert b.post("/api/change-requests", json=body).status_code == 404
    new, _ = propose(b, shared)
    assert new["before"]["refunds"] == []
    assert "私人曾用退款原因" not in json.dumps(b.get("/api/change-requests").json(), ensure_ascii=False)


def test_void_hides_other_side_retains_owner_history_blocks_images_and_mutations(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation())
    url = image_url(expense["id"])
    assert upload(a, url).status_code == 200
    proposal, body = propose(b, expense, "void", {})
    result = decide(a, proposal).json()
    voided = result["expense"]
    assert voided["voided"] and voided["net_amount_cents"] == 0
    assert b.get("/api/sync").json()["expenses"] == []
    assert b.get("/api/change-requests").json()["requests"] == []
    assert b.post("/api/change-requests", json=body).status_code == 404
    assert b.get(url).status_code == 404 and a.get(url).status_code == 200
    assert upload(a, image_url(expense["id"])).json()["detail"]["code"] == "expense_voided"
    for mutation in [update(voided), refund(voided), action(voided, "scope", scope="private"), action(voided, "void")]:
        assert a.post("/api/operations", json=mutation).json()["detail"]["code"] == "expense_voided"
    assert len(a.get(f"/api/expenses/{expense['id']}/history").json()["revisions"]) == 2


def test_concurrent_refunds_and_decisions_do_not_double_apply(setup):
    _, app, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    proposal, _ = propose(b, expense, "refund_create", refund(expense, 15000)["data"])
    decision_id = str(uuid4())
    def approve(_):
        with TestClient(app) as c:
            c.cookies.update(a.cookies); c.headers.update(a.headers)
            return decide(c, proposal, decision_id=decision_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(approve, range(4)))
    assert all(r.status_code == 200 and r.json() == results[0].json() for r in results)
    current = results[0].json()["expense"]
    bodies = [refund(current, 4000), refund(current, 4000)]
    def send(body):
        with TestClient(app) as c:
            c.cookies.update(a.cookies); c.headers.update(a.headers)
            return c.post("/api/operations", json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, bodies))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert a.get(f"/api/expenses/{expense['id']}").json()["net_amount_cents"] == 1000


def test_approval_fault_rolls_back_everything(setup):
    path, app, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    proposal, _ = propose(b, expense, "refund_create", refund(expense)["data"])
    decision_id = str(uuid4())
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON revisions BEGIN SELECT RAISE(ABORT, 'fault'); END")
    with TestClient(app, raise_server_exceptions=False) as c:
        c.cookies.update(a.cookies); c.headers.update(a.headers)
        assert decide(c, proposal, decision_id=decision_id).status_code == 500
    assert a.get(f"/api/expenses/{expense['id']}").json() == expense
    assert a.get("/api/change-requests").json()["requests"][0]["status"] == "pending"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM refunds").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM operations WHERE operation_id = ?", (decision_id,)).fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_revision")
    assert decide(a, proposal, decision_id=decision_id).status_code == 200


def test_new_endpoints_obey_account_origin_and_json(setup):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation())
    proposal, body = propose(b, expense)
    for path in ["/api/change-requests", f"/api/expenses/{expense['id']}/history"]:
        assert a.get(path, headers={"X-Expected-User": "wrong"}).status_code == 409
        assert a.get(path, headers={"Origin": "https://evil.invalid"}).status_code == 403
    decision_url = f"/api/change-requests/{proposal['id']}/decision"
    for path in ["/api/change-requests", decision_url]:
        assert a.post(path, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert a.post(path, json={}, headers={"X-App-Request": "wrong"}).status_code == 403
        assert a.post(path, json={}, headers={"X-Expected-User": "wrong"}).status_code == 409


def test_real_v1_schema_migration_legacy_receipt_and_update(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    expense_id, user_id, operation_id = (str(uuid4()) for _ in range(3))
    data = {"scope": "shared", "amount_cents": 1200, "purpose": "旧数据", "occurred_at": "2026-10-30T04:00:00Z", "location": ""}
    expense = dict(id=expense_id, owner_id=user_id, version=1, **data)
    payload = dict(operation_id=operation_id, kind="create", expense_id=expense_id, base_version=0, data=data)
    schema = Path("server/migrations/001_initial.sql").read_text()
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        db.execute("INSERT INTO users VALUES (?, 'demo-a', 'unused', '旧用户')", (user_id,))
        db.execute("INSERT INTO users VALUES (?, 'demo-b', 'unused', '旧用户乙')", (str(uuid4()),))
        db.execute("INSERT INTO expenses VALUES (:id, :owner_id, :scope, :amount_cents, :purpose, :occurred_at, :location, :version)", expense)
        db.execute("INSERT INTO operations VALUES (?, ?, ?, ?)", (user_id, operation_id, digest(payload), canonical_json({"operation_id": operation_id, "expense": expense})))
        db.execute("INSERT INTO revisions VALUES (?, 1, ?, ?, NULL, ?)", (expense_id, user_id, operation_id, canonical_json(expense)))
    migrate_database(path)
    migrate_database(path)
    from server.accounting.db import connect
    from server.accounting.ledger import submit_operation, history
    from server.api.models import CreateOperation, UpdateOperation
    with connect(path) as db:
        result = submit_operation(db, user_id, CreateOperation.model_validate(payload))["expense"]
        assert result["rating"] is None and result["refunds"] == [] and result["net_amount_cents"] == 1200
        changed = submit_operation(db, user_id, UpdateOperation.model_validate(update(result, purpose="旧队列修改")))["expense"]
        assert changed["version"] == 2
        assert history(db, user_id, expense_id)[0]["after"] == result


@pytest.mark.parametrize("kind", ["update", "refund_update", "refund_void"])
def test_approval_remaining_actions_match_owner_rules(setup, kind):
    _, _, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    expense = submit(a, refund(expense))
    refund_id = expense["refunds"][0]["id"]
    if kind == "update":
        data = update(expense, amount_cents=10000, purpose="核实后的用途")["data"]
    elif kind == "refund_update":
        data = refund(expense, 4000, refund_id=refund_id)["data"]
    else:
        data = {"refund_id": refund_id}
    proposal, _ = propose(b, expense, kind, data)
    response = decide(a, proposal)
    assert response.status_code == 200, response.text
    after = response.json()["expense"]
    assert after["version"] == expense["version"] + 1
    assert after["net_amount_cents"] == {"update": 5000, "refund_update": 16000, "refund_void": 20000}[kind]


def test_concurrent_owner_edit_and_approval_have_one_winner(setup):
    _, app, a, b, _, _ = setup
    expense = submit(a, operation(amount_cents=20000))
    proposal, _ = propose(b, expense, "refund_create", refund(expense, 15000)["data"])
    def send(kind):
        with TestClient(app) as c:
            c.cookies.update(a.cookies); c.headers.update(a.headers)
            return decide(c, proposal) if kind == "approve" else c.post("/api/operations", json=update(expense, amount_cents=10000))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, ["approve", "edit"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = a.get(f"/api/expenses/{expense['id']}").json()
    assert current["version"] == 2
    assert (current["amount_cents"], current["net_amount_cents"]) in [(20000, 5000), (10000, 10000)]
    assert len(a.get(f"/api/expenses/{expense['id']}/history").json()["revisions"]) == 2


@pytest.mark.parametrize("withdrawal", ["scope", "void"])
def test_sync_withdrawal_ids_are_requester_only_and_survive_resharing(setup, withdrawal):
    _, _, a, b, _, _ = setup
    own = submit(a, operation(purpose="撤回前的虚构原始正文"))
    other = submit(b, operation())
    own_request, _ = propose(a, other)
    other_request, _ = propose(b, own)
    assert a.get("/api/sync").json()["withdrawn_request_ids"] == []
    assert b.get("/api/sync").json()["withdrawn_request_ids"] == []

    # Both users have withdrawn requests, but being the owner grants no ID feed.
    submit(b, action(other, "void"))
    withdrawn = submit(a, action(own, withdrawal, **({"scope": "private"} if withdrawal == "scope" else {})))
    assert a.get("/api/sync").json()["withdrawn_request_ids"] == [own_request["id"]]
    snapshot = b.get("/api/sync").json()
    assert snapshot["withdrawn_request_ids"] == [other_request["id"]]
    assert all(r["id"] != other_request["id"] for r in snapshot["requests"])
    assert "撤回前的虚构原始正文" not in json.dumps(snapshot, ensure_ascii=False)

    if withdrawal == "scope":
        # The requester can miss the private interval: the marker remains on reshare.
        shared = submit(a, action(withdrawn, "scope", scope="shared"))
        new_request, _ = propose(b, shared)
        snapshot = b.get("/api/sync").json()
        assert snapshot["withdrawn_request_ids"] == [other_request["id"]]
        assert [r["id"] for r in snapshot["requests"] if r["expense_id"] == own["id"]] == [new_request["id"]]
    denied = b.get("/api/sync", headers={"X-Expected-User": "another-account"})
    assert denied.status_code == 409 and "withdrawn_request_ids" not in denied.json()


def test_default_neutral_and_optional_reasons(setup):
    _, _, a, b, _, _ = setup
    default_body = operation()
    record = submit(a, default_body)
    assert record['rating'] == 3 and record['rating_reason'] == ''
    assert submit(a, default_body) == record  # Same old-shape payload remains replay-safe.
    for rating in range(1, 6):
        record = submit(a, action(record, 'review', rating=rating))
        assert record['rating'] == rating and record['rating_reason'] == ''
    body = {'id': str(uuid4()), 'expense_id': record['id'], 'base_version': record['version'],
            'kind': 'update', 'data': update(record)['data']}
    request = b.post('/api/change-requests', json=body)
    assert request.status_code == 200, request.text
    assert request.json()['reason'] == ''
    assert b.post('/api/change-requests', json=body).json() == request.json()
    accepted = decide(a, request.json())
    assert accepted.status_code == 200, accepted.text
