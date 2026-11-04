"""Atomic administrator exit, using public HTTP and fictional accounts only."""

from test_family import (service, register, refresh, create_family, invite, decide,
                         expense, visible_ids, denied_object, uid)
from test_ui_refinement import image_path, upload


def test_transfer_and_leave_is_one_request_with_immediate_revocation(service):
    a, b, waiting = [register(service, "exit_" + uid()[:8]) for _ in range(3)]
    try:
        aid, bid = (client.headers["X-Expected-User"] for client in (a, b))
        fid = create_family(a, "虚构原子退出家庭")
        decide(b, invite(a, b.get("/api/auth/me").json()["user"]["username"]), "accept")
        personal_op, personal = expense(a, "private", "owner-private-retained")
        shared_op, shared = expense(a, purpose="owner-paid-shared")
        photo = image_path(shared)
        assert upload(a, photo).status_code == 200
        pending = invite(a, waiting.get("/api/auth/me").json()["user"]["username"])
        a_before, b_before = refresh(a), refresh(b)

        result = a.post(f"/api/families/{fid}/transfer-and-leave", json={"user_id": bid})
        assert result.status_code == 200, result.text
        assert result.json()["family"] is None
        assert result.json()["membership_version"] > a_before["membership_version"]
        # No follow-up leave, owner write, or sync is required to finish exit.
        assert refresh(a)["family"] is None
        remaining = refresh(b)
        assert remaining["family"]["owner_id"] == bid
        assert {member["id"] for member in remaining["family"]["members"]} == {bid}
        assert remaining["membership_version"] == b_before["membership_version"]
        assert aid not in {member["id"] for member in remaining["family"]["members"]}
        denied_object(a, shared, photo)
        assert visible_ids(a) == {personal["id"]}
        assert shared["id"] in visible_ids(b)
        assert b.get(photo).status_code == 200
        assert personal["id"] not in visible_ids(b)
        assert a.post("/api/operations", json=shared_op).status_code != 200
        assert a.post("/api/operations", json=personal_op).status_code == 200
        assert shared["id"] not in a.get("/api/export?format=json&scope=all").text
        assert waiting.post(f"/api/family-invites/{pending['id']}/accept", json={}).status_code != 200
        assert a.post(f"/api/families/{fid}/transfer-and-leave", json={"user_id": bid}).status_code != 200
        assert refresh(b) == remaining
    finally:
        for client in (a, b, waiting):
            client.close()


def test_invalid_successors_leave_owner_and_membership_unchanged(service):
    a, b, departed, foreign = [register(service, "invalid_exit_" + uid()[:8]) for _ in range(4)]
    try:
        aid, bid, departed_id, foreign_id = (client.headers["X-Expected-User"]
                                              for client in (a, b, departed, foreign))
        fid = create_family(a, "虚构拒绝退出家庭")
        for member in (b, departed):
            decide(member, invite(a, member.get("/api/auth/me").json()["user"]["username"]), "accept")
        assert departed.post(f"/api/families/{fid}/leave", json={}).status_code == 200
        refresh(departed)
        foreign_fid = create_family(foreign, "虚构跨家庭目标")
        clients = (a, b, departed, foreign)
        before = [refresh(client) for client in clients]
        cases = [({}, 422), ({"user_id": None}, 422), ({"user_id": ""}, 422),
                 ({"user_id": aid}, 409), ({"user_id": uid()}, 404),
                 ({"user_id": departed_id}, 404), ({"user_id": foreign_id}, 404)]
        for payload, status in cases:
            response = a.post(f"/api/families/{fid}/transfer-and-leave", json=payload)
            assert response.status_code == status, (payload, response.text)
            assert [refresh(client) for client in clients] == before
        for client, target_fid in ((b, fid), (foreign, fid), (a, foreign_fid)):
            response = client.post(f"/api/families/{target_fid}/transfer-and-leave", json={"user_id": bid})
            assert response.status_code in (403, 404), response.text
            assert [refresh(member) for member in clients] == before
    finally:
        for client in (a, b, departed, foreign):
            client.close()


def test_single_owner_can_explicitly_dissolve_and_private_is_retained(service):
    a = register(service, "solo_exit_" + uid()[:8])
    try:
        fid = create_family(a, "虚构单人解散家庭")
        _, personal = expense(a, "private", "solo-private")
        _, shared = expense(a, purpose="solo-shared")
        before = refresh(a)
        result = a.post(f"/api/families/{fid}/dissolve", json={})
        assert result.status_code == 200, result.text
        assert result.json()["family"] is None
        assert result.json()["membership_version"] > before["membership_version"]
        refresh(a)
        assert visible_ids(a) == {personal["id"]}
        assert a.get("/api/expenses/" + shared["id"]).status_code == 404
    finally:
        a.close()
