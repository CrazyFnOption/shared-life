"""Upgrade and restore real 7262ef2 databases with creator ownership intact."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
from server.tests.legacy_fixture import copy_legacy

from test_family import register, create_family, invite, decide, refresh, uid
from test_http_contract import LiveLedger, ROOT


def check_owners(database, expected):
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(families)")}
        assert "owner_id" in columns
        rows = dict(db.execute("SELECT id,owner_id FROM families"))
        assert rows == expected
        assert all(owner in {row[0] for row in db.execute(
            "SELECT user_id FROM family_members WHERE family_id=?", (fid,))}
                   for fid, owner in rows.items())


def login(service, username, password):
    client = httpx.Client(base_url=service.origin, trust_env=False,
                         headers={"Origin": service.origin, "X-App-Request": "shared-expense"})
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-Expected-User"] = response.json()["user"]["id"]
    refresh(client)
    return client


def test_726_creator_mapping_repeat_start_and_restore(tmp_path):
    old = tmp_path / "old-7262ef2"
    old.mkdir()
    copy_legacy("7262ef2", old)
    candidate = Path(os.environ.get("FAMILY_CANDIDATE", ROOT)).resolve()
    service = LiveLedger(old, tmp_path)
    service.origin = "http://127.0.0.1:" + os.environ.get("FAMILY_HTTP_PORT", "18779")
    service.env.update(SHARED_EXPENSE_PORT=service.origin.rsplit(":", 1)[1],
                       SHARED_EXPENSE_ORIGINS=service.origin)
    subprocess.run([sys.executable, "-m", "scripts.seed_demo", "--database", str(service.database)],
                   cwd=old, env=service.env, check=True, capture_output=True, text=True)
    try:
        service.start()
        demo = login(service, "demo-a", "demo-only-2026")
        creator_name = "creator_" + uid()[:8]
        member_name = "member_" + uid()[:8]
        departed_name = "departed_" + uid()[:8]
        early_name = "early_" + uid()[:8]
        late_name = "late_" + uid()[:8]
        creator = register(service, creator_name)
        member = register(service, member_name)
        departed = register(service, departed_name)
        early = register(service, early_name)
        late = register(service, late_name)
        try:
            demo_id = demo.headers["X-Expected-User"]
            demo_fid = refresh(demo)["family"]["id"]
            creator_id = creator.headers["X-Expected-User"]
            own_fid = create_family(creator, "旧版自建家庭")
            invited = invite(creator, member.get("/api/auth/me").json()["user"]["username"])
            decide(member, invited, "accept")
            departed_fid = create_family(departed, "创建者已离组家庭")
            for newcomer, name in ((early, early_name), (late, late_name)):
                decide(newcomer, invite(departed, name), "accept")
            early_id = early.headers["X-Expected-User"]
            assert departed.post(f"/api/families/{departed_fid}/leave", json={}).status_code == 200
            expected = {demo_fid: demo_id, own_fid: creator_id, departed_fid: early_id}
            old_cookie = dict(creator.cookies)
        finally:
            for client in (demo, creator, member, departed, early, late):
                client.close()
        backup = tmp_path / "old-backup"
        subprocess.run([sys.executable, "-m", "scripts.backup", "--database", str(service.database),
                        "--destination", str(backup)], cwd=old, env=service.env,
                       check=True, capture_output=True, text=True)
        service.stop()

        # Upgrade the real old file in place, then restart. A repeated migration
        # cannot assign a different creator or add duplicate memberships.
        service.candidate = candidate
        subprocess.run([sys.executable, "-m", "scripts.migrate_memos", "--database", str(service.database)],
                       cwd=candidate, env=service.env, check=True, capture_output=True, text=True)
        service.start()
        check_owners(service.database, expected)
        clients = [login(service, name, password) for name, password in (
            ("demo-a", "demo-only-2026"), (creator_name, "fictional-password-2026"),
            (member_name, "fictional-password-2026"), (early_name, "fictional-password-2026"),
            (late_name, "fictional-password-2026"))]
        try:
            demo_new, creator_new, member_new, early_new, late_new = clients
            assert refresh(demo_new)["family"]["owner_id"] == demo_id
            assert refresh(creator_new)["family"]["owner_id"] == creator_id
            assert refresh(member_new)["family"]["owner_id"] == creator_id
            assert refresh(early_new)["family"]["owner_id"] == early_id
            assert refresh(late_new)["family"]["owner_id"] == early_id
        finally:
            for client in clients:
                client.close()
        service.stop()
        service.start()
        check_owners(service.database, expected)

        restored = tmp_path / "restored.sqlite3"
        result = subprocess.run([sys.executable, "-m", "scripts.restore", "--backup", str(backup),
                                 "--destination", str(restored), "--credentials", str(backup / "credentials.json")],
                                cwd=candidate, env=service.env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        service.stop()
        service.database = restored
        service.env["SHARED_EXPENSE_DB"] = str(restored)
        service.start()
        check_owners(restored, expected)
        with httpx.Client(base_url=service.origin, trust_env=False, cookies=old_cookie) as stale:
            assert stale.get("/api/auth/me").status_code == 401
        demo_after = login(service, "demo-a", "demo-only-2026")
        early_after = login(service, early_name, "fictional-password-2026")
        try:
            assert refresh(demo_after)["family"]["owner_id"] == demo_id
            assert refresh(early_after)["family"]["owner_id"] == early_id
        finally:
            demo_after.close(); early_after.close()
    finally:
        service.stop()
        service.log.close()
