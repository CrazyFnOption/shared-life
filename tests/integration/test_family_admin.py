"""Independent administrator acceptance against a live, fictional HTTP service.

The service fixture starts the frozen candidate on the test agent's port. These
tests use public endpoints only; they do not import product code or edit its DB.
"""

from test_family import (service, register, refresh, create_family, invite, decide,
                         expense, visible_ids, denied_object, uid)
from test_ui_refinement import image_path, upload


def member_ids(context):
    return {member["id"] for member in context["family"]["members"]}


def assert_denied(response):
    assert response.status_code in (403, 404, 409), response.text


def test_owner_transfer_then_remove_and_private_retention(service):
    a, b, c, other = [register(service, "admin_" + uid()[:8]) for _ in range(4)]
    try:
        aid, bid, cid = (client.headers["X-Expected-User"] for client in (a, b, c))
        fid = create_family(a, "虚构管理员家庭")
        assert refresh(a)["family"]["owner_id"] == aid
        private_op, private = expense(b, "private", "member-private")
        shared_op, shared = expense(a, purpose="owner-shared")
        photo = image_path(shared)
        assert upload(a, photo).status_code == 200
        first = invite(a, b.get("/api/auth/me").json()["user"]["username"])
        decide(b, first, "accept")
        second = invite(b, c.get("/api/auth/me").json()["user"]["username"])
        decide(c, second, "accept")
        assert member_ids(refresh(a)) == {aid, bid, cid}
        assert {first["id"], second["id"]} <= {
            row["id"] for row in a.get("/api/family-invites").json()["sent"]
        } | {row["id"] for row in b.get("/api/family-invites").json()["sent"]}
        assert any(row["status"] == "accepted" for row in b.get("/api/family-invites").json()["received"])

        # A current member cannot acquire administrator authority through a
        # forged path, target ID, or a valid family context header.
        for response in (b.post(f"/api/families/{fid}/members/{cid}/remove", json={}),
                         b.post(f"/api/families/{fid}/owner", json={"user_id": bid}),
                         b.post(f"/api/families/{fid}/dissolve", json={})):
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "family_owner_required"
        other_fid = create_family(other, "另一家庭")
        assert_denied(b.post(f"/api/families/{other_fid}/owner", json={"user_id": bid}))
        self_remove = a.post(f"/api/families/{fid}/members/{aid}/remove", json={})
        assert self_remove.status_code == 409 and self_remove.json()["detail"]["code"] == "use_leave"
        self_transfer = a.post(f"/api/families/{fid}/owner", json={"user_id": aid})
        assert self_transfer.status_code == 409 and self_transfer.json()["detail"]["code"] == "owner_transfer_target_required"
        assert a.post(f"/api/families/{fid}/owner", json={"user_id": other.headers['X-Expected-User']}).status_code == 404
        assert member_ids(refresh(a)) == {aid, bid, cid}
        blocked_leave = a.post(f"/api/families/{fid}/leave", json={})
        assert blocked_leave.status_code == 409, blocked_leave.text
        assert blocked_leave.json()["detail"]["code"] == "owner_transfer_or_dissolve_required"
        assert refresh(a)["family"]["owner_id"] == aid

        versions_before = {client.headers["X-Expected-User"]: refresh(client)["membership_version"]
                           for client in (a, b, c)}
        transfer = a.post(f"/api/families/{fid}/owner", json={"user_id": cid})
        assert transfer.status_code == 200, transfer.text
        assert transfer.json()["family"]["owner_id"] == cid
        assert refresh(c)["family"]["owner_id"] == cid
        assert {client.headers["X-Expected-User"]: refresh(client)["membership_version"]
                for client in (a, b, c)} == versions_before
        refresh(a)
        assert_denied(a.post(f"/api/families/{fid}/members/{bid}/remove", json={}))
        assert_denied(a.post(f"/api/families/{fid}/dissolve", json={}))
        removed = c.post(f"/api/families/{fid}/members/{bid}/remove", json={})
        assert removed.status_code == 200, removed.text
        assert removed.json()["family"]["owner_id"] == cid
        assert bid not in member_ids(refresh(c))
        assert refresh(b)["family"] is None
        assert int(b.headers["X-Membership-Version"]) > versions_before[bid]
        denied_object(b, shared, photo)
        assert shared["id"] not in visible_ids(b)
        assert private["id"] in visible_ids(b)
        assert b.post("/api/operations", json=shared_op).status_code != 200
        assert b.post("/api/operations", json=private_op).status_code == 200
        assert_denied(b.post(f"/api/families/{fid}/members/{aid}/remove", json={}))
    finally:
        for client in (a, b, c, other):
            client.close()


def test_dissolve_revokes_every_members_shared_access_but_keeps_private(service):
    clients = [register(service, "dissolve_" + uid()[:8]) for _ in range(3)]
    a, b, c = clients
    try:
        fid = create_family(a, "虚构解散家庭")
        invitations = [invite(a, client.get("/api/auth/me").json()["user"]["username"])
                       for client in (b, c)]
        for client, invitation in zip((b, c), invitations):
            decide(client, invitation, "accept")
        personal = [expense(client, "private", "retained-private")[1] for client in clients]
        shared_op, shared = expense(b, purpose="dissolved-shared")
        photo = image_path(shared)
        assert upload(b, photo).status_code == 200
        for client in clients:
            assert shared["id"] in visible_ids(client)
        versions_before = [refresh(client)["membership_version"] for client in clients]
        assert_denied(c.post(f"/api/families/{fid}/dissolve", json={}))
        assert a.post(f"/api/families/{fid}/leave", json={}).status_code == 409
        dissolved = a.post(f"/api/families/{fid}/dissolve", json={})
        assert dissolved.status_code == 200, dissolved.text
        assert dissolved.json()["family"] is None
        for client, own, old_version in zip(clients, personal, versions_before):
            assert refresh(client)["family"] is None
            assert int(client.headers["X-Membership-Version"]) > old_version
            assert visible_ids(client) == {own["id"]}
            denied_object(client, shared, photo)
            exported = client.get("/api/export?format=json&scope=all")
            assert exported.status_code == 200 and shared["id"] not in exported.text
            assert client.post("/api/operations", json=shared_op).status_code != 200
        for client, invitation in zip((b, c), invitations):
            assert client.post(f"/api/family-invites/{invitation['id']}/accept", json={}).status_code != 200
    finally:
        for client in clients:
            client.close()
