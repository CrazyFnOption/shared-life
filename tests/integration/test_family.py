"""Independent family acceptance over public HTTP and a fresh fictional database.

Run only against a frozen candidate. This file does not import product modules or
inspect its database; the separate migration test uses an archived old release.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import uuid

import httpx
import pytest

from test_http_contract import LiveLedger, ROOT, create_operation, update_operation
from test_ui_refinement import image_path, png, upload


def uid():
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    candidate = Path(os.environ.get("FAMILY_CANDIDATE", ROOT)).resolve()
    instance = LiveLedger(candidate, tmp_path_factory.mktemp("family-http"))
    instance.origin = "http://127.0.0.1:" + os.environ.get("FAMILY_HTTP_PORT", "18779")
    instance.env.update(SHARED_EXPENSE_PORT=instance.origin.rsplit(":", 1)[1],
                        SHARED_EXPENSE_ORIGINS=instance.origin)
    try:
        instance.start()
        yield instance
    finally:
        instance.stop()
        instance.log.close()


def register(service, username):
    client = httpx.Client(base_url=service.origin, trust_env=False,
                         headers={"Origin": service.origin, "X-App-Request": "shared-expense"})
    response = client.post("/api/auth/register", json={"username": username,
        "display_name": username, "password": "fictional-password-2026"})
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["username"] == username and "password" not in user
    client.headers["X-Expected-User"] = user["id"]
    refresh(client)
    return client


def refresh(client):
    # Use a clean request so a stale membership assertion cannot mask the new
    # context after create/accept/leave. The normal client retains its headers.
    with httpx.Client(base_url=client.base_url, trust_env=False, cookies=client.cookies,
                      headers={"X-Expected-User": client.headers["X-Expected-User"]}) as probe:
        response = probe.get("/api/family")
    assert response.status_code == 200, response.text
    context = response.json()
    client.headers["X-Family-Id"] = context["family"]["id"] if context["family"] else "none"
    client.headers["X-Membership-Version"] = str(context["membership_version"])
    return context


def create_family(client, name):
    response = client.post("/api/families", json={"request_id": uid(), "name": name})
    assert response.status_code == 200, response.text
    refresh(client)
    return response.json()["family"]["id"]


def invite(inviter, username):
    response = inviter.post("/api/family-invites", json={"request_id": uid(), "username": username})
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, invitation, action):
    response = client.post(f"/api/family-invites/{invitation['id']}/{action}", json={})
    assert response.status_code == 200, response.text
    refresh(client)
    return response.json()


def expense(client, scope="shared", purpose="fictional-family"):
    op = create_operation(scope, purpose=purpose + "-" + uid())
    response = client.post("/api/operations", json=op)
    assert response.status_code == 200, response.text
    return op, response.json()["expense"]


def visible_ids(client):
    response = client.get("/api/sync")
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["expenses"]}


def denied_object(client, record, photo):
    # Reads are filtered by current membership, independent of forged old IDs.
    assert client.get("/api/expenses/" + record["id"]).status_code == 404
    assert client.get("/api/expenses/" + record["id"] + "/history").status_code == 404
    assert client.get(photo).status_code == 404
    assert client.put(photo, content=png(), headers={"Content-Type": "image/png"}).status_code in (404, 409)


def test_registration_invites_isolation_exit_reentry_and_receipts(service):
    names = ["alpha_" + uid()[:8], "beta_" + uid()[:8], "gamma_" + uid()[:8],
             "delta_" + uid()[:8], "solo_" + uid()[:8]]
    a, b, c, d, solo = [register(service, name) for name in names]
    try:
        for client in (a, b, c, d, solo):
            snapshot = client.get("/api/sync").json()
            assert snapshot["family"] is None and snapshot["expenses"] == []
            assert refresh(client)["membership_version"] == 0
        assert a.post("/api/auth/register", json={"username": names[0],
            "display_name": "duplicate", "password": "fictional-password-2026"}).status_code == 409
        private_op, private = expense(a, "private", "private")
        f1 = create_family(a, "虚构甲家")
        shared_op, shared = expense(a, purpose="shared-one")
        with httpx.Client(base_url=service.origin, trust_env=False, cookies=a.cookies,
                          headers={"Origin": service.origin, "X-App-Request": "shared-expense",
                                   "X-Expected-User": a.headers["X-Expected-User"]}) as legacy:
            absent = legacy.post("/api/operations", json=create_operation())
        assert absent.status_code == 409 and absent.json()["detail"]["code"] == "family_context_required"
        image = image_path(shared)
        assert upload(a, image).status_code == 200
        assert b.get("/api/sync").json()["expenses"] == []
        first = invite(a, names[1])
        assert first["family_id"] == f1
        assert any(row["id"] == first["id"] for row in b.get("/api/family-invites").json()["received"])
        decide(b, first, "accept")
        assert {shared["id"]} <= visible_ids(b)
        assert private["id"] not in visible_ids(b)
        assert b.get(image).content == png()
        b_own_op, b_own = expense(b, purpose="b-paid-before-exit")
        b_own_image = image_path(b_own)
        assert upload(b, b_own_image).status_code == 200
        proposal = {"id": uid(), "expense_id": shared["id"], "base_version": shared["version"],
                    "kind": "update", "data": update_operation(shared, purpose="虚构更正")['data'],
                    "reason": "虚构更正申请"}
        requested = b.post("/api/change-requests", json=proposal)
        assert requested.status_code == 200, requested.text
        assert proposal["id"] in b.get("/api/change-requests").text
        second = invite(a, names[2]); decide(c, second, "accept")
        assert len(refresh(a)["family"]["members"]) == 3
        rejected = invite(a, names[4]); decide(solo, rejected, "reject")
        assert refresh(solo)["family"] is None
        revoked = invite(a, names[4]); decide(a, revoked, "revoke")
        assert solo.post(f"/api/family-invites/{revoked['id']}/accept", json={}).status_code != 200
        f2 = create_family(d, "虚构乙家")
        other_op, other = expense(d, purpose="shared-two")
        other_image = image_path(other)
        assert upload(d, other_image).status_code == 200
        for client, record, photo in [(d, shared, image), (a, other, other_image), (solo, shared, image)]:
            denied_object(client, record, photo)
        assert proposal["id"] not in d.get("/api/change-requests").text
        assert d.post(f"/api/change-requests/{proposal['id']}/decision",
                      json={"decision_id": uid(), "decision": "accept"}).status_code == 404
        assert d.post("/api/change-requests", json={**proposal, "id": uid(), "expense_id": shared["id"]}).status_code == 404
        assert f1 != f2 and shared["id"] not in visible_ids(d) and other["id"] not in visible_ids(a)
        for client, foreign in [(a, other), (d, shared), (solo, shared)]:
            assert foreign["id"] not in client.get("/api/export?format=json&scope=all").text
        old_version = b.headers["X-Membership-Version"]
        leave = b.post(f"/api/families/{f1}/leave", json={})
        assert leave.status_code == 200, leave.text
        refresh(b)
        denied_object(b, shared, image)
        denied_object(b, b_own, b_own_image)
        assert proposal["id"] not in b.get("/api/change-requests").text
        assert shared["id"] not in visible_ids(b)
        assert b.post("/api/operations", json=shared_op).status_code != 200
        assert b.post("/api/operations", json=b_own_op).status_code != 200
        # Even after the same family invites the user again, stale context/receipts stay fenced.
        reentry = invite(a, names[1]); decide(b, reentry, "accept")
        assert b.headers["X-Membership-Version"] != old_version
        stale = b.post("/api/operations", json=create_operation(), headers={
            "X-Family-Id": f1, "X-Membership-Version": old_version})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "family_changed"
        assert shared["id"] in visible_ids(b)
        assert private["id"] not in visible_ids(b)
        assert a.post("/api/operations", json=shared_op).status_code == 200
        assert a.post("/api/operations", json=private_op).status_code == 200
        assert d.post("/api/operations", json=shared_op).status_code != 200
        assert b.post("/api/operations", json=b_own_op).status_code != 200
        assert other_op["expense_id"] not in visible_ids(a)
    finally:
        for client in (a, b, c, d, solo):
            client.close()


def test_concurrent_accept_only_one_family_wins(service):
    names = ["race_" + uid()[:8] for _ in range(3)]
    x, y, target = [register(service, name) for name in names]
    try:
        create_family(x, "并发甲"); create_family(y, "并发乙")
        ix, iy = invite(x, names[2]), invite(y, names[2])
        cookies = dict(target.cookies)
        headers = dict(target.headers)

        def accept(invitation):
            with httpx.Client(base_url=service.origin, trust_env=False, cookies=cookies,
                              headers=headers) as client:
                return client.post(f"/api/family-invites/{invitation['id']}/accept", json={})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, (ix, iy)))
        assert sorted(r.status_code == 200 for r in results) == [False, True]
        chosen = refresh(target)["family"]["id"]
        assert chosen in {ix["family_id"], iy["family_id"]}
    finally:
        for client in (x, y, target):
            client.close()


def test_password_change_revokes_old_sessions(service):
    name = "password_" + uid()[:8]
    a = register(service, name)
    b = httpx.Client(base_url=service.origin, trust_env=False,
                     headers={"Origin": service.origin, "X-App-Request": "shared-expense"})
    try:
        login = b.post("/api/auth/login", json={"username": name, "password": "fictional-password-2026"})
        assert login.status_code == 200
        b.headers["X-Expected-User"] = login.json()["user"]["id"]
        refresh(b)
        changed = a.post("/api/auth/password", json={"old_password": "fictional-password-2026",
                         "new_password": "changed-fictional-2026"})
        assert changed.status_code == 204, changed.text
        assert b.get("/api/auth/me").status_code == 401
        assert b.post("/api/auth/login", json={"username": name,
                      "password": "fictional-password-2026"}).status_code == 401
        assert b.post("/api/auth/login", json={"username": name,
                      "password": "changed-fictional-2026"}).status_code == 200
    finally:
        a.close(); b.close()
