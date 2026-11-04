"""Backend contract checks using fictional accounts and temporary databases."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from server.tests.legacy_fixture import to_v1
from server.accounting.db import initialize_database, migrate_database
from server.accounting.categories import DEFAULT_CATEGORIES
from server.accounting.ledger import canonical_json
from server.api.app import create_app
from server.api.models import CreateOperation
from server.tests.test_api import setup, operation, submit, update  # noqa: F401


def image_bytes(color="red", fmt="PNG", size=(4, 3)):
    output = BytesIO()
    Image.new("RGB", size, color=color).save(output, format=fmt)
    return output.getvalue()


def image_url(expense_id, image_id=None):
    return f"/api/expenses/{expense_id}/images/{image_id or uuid4()}"


def upload(client, url, data=None, mime="image/png"):
    return client.put(url, content=image_bytes() if data is None else data, headers={"Content-Type": mime})


def test_custom_categories_are_trimmed_persistent_scoped_and_idempotent(setup):
    path, _, a, b, _, _ = setup
    for scope, name in [("shared", " 宠物 "), ("private", "私人健康")]:
        response = a.post("/api/categories", json={"scope": scope, "name": name})
        assert response.status_code == 200
        assert response.json() == {"scope": scope, "name": name.strip()}
        assert a.post("/api/categories", json=response.json()).json() == response.json()
    assert {tuple(c.values()) for c in b.get('/api/sync').json()['categories']} == {('private',name) for name in DEFAULT_CATEGORIES} | {('shared',name) for name in (*DEFAULT_CATEGORIES,'宠物')}
    assert len(a.get("/api/sync").json()["categories"]) == 2 * len(DEFAULT_CATEGORIES) + 2
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(a.cookies)
        restarted.headers.update(a.headers)
        assert len(restarted.get("/api/sync").json()["categories"]) == 2 * len(DEFAULT_CATEGORIES) + 2
    for name in ["", "  ", "长" * 25, 12, None]:
        assert a.post("/api/categories", json={"scope": "shared", "name": name}).status_code == 422
    assert a.post("/api/categories", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert a.post("/api/categories", json={"scope": "shared", "name": "其他"}, headers={"X-App-Request": "bad"}).status_code == 403
    assert a.post("/api/categories", json={"scope": "shared", "name": "其他"}, headers={"X-Expected-User": "bad"}).status_code == 409


def test_expense_category_edit_and_conflict(setup):
    _, _, a, b, _, _ = setup
    created = submit(a, operation(category=" 餐饮 "))
    assert created["category"] == "餐饮"
    assert {"scope": "shared", "name": "餐饮"} in b.get("/api/sync").json()["categories"]
    edit = update(created)
    edit["data"]["category"] = "自定义"
    changed = submit(a, edit)
    assert changed["category"] == "自定义"
    assert a.post("/api/operations", json=update(created)).json()["detail"]["current"]["category"] == "自定义"
    submit(a, operation("private", category="秘密分类"))
    assert "秘密分类" not in json.dumps(b.get("/api/sync").json(), ensure_ascii=False)


def test_old_database_and_original_receipt_can_upgrade_and_replay(setup):
    path, _, a, _, alice, _ = setup
    body = operation()
    created = submit(a, body)
    # Recreate the actual v1 database shape and old semantic receipt/hash in this test DB.
    old_payload = CreateOperation.model_validate(body).model_dump(mode="json")
    old_payload["data"].pop("category")
    old_payload["data"].pop("rating")
    old_payload["data"].pop("rating_reason")
    legacy = {"operation_id": body["operation_id"], "expense": {k: v for k, v in created.items() if k != "category"}}
    to_v1(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE expense_images")
        db.execute("DROP TABLE categories")
        db.execute("ALTER TABLE expenses DROP COLUMN category")
        db.execute("UPDATE operations SET request_hash = ?, receipt_json = ? WHERE user_id = ? AND operation_id = ?",
                   (sha256(canonical_json(old_payload).encode()).hexdigest(), canonical_json(legacy), alice["id"], body["operation_id"]))
    migrate_database(path)
    with TestClient(create_app(path)) as upgraded:
        upgraded.cookies.update(a.cookies)
        upgraded.headers.update(a.headers)
        for payload in [body, dict(body, data=dict(body["data"], category=""))]:
            replay = upgraded.post("/api/operations", json=payload)
            assert replay.status_code == 200
            assert replay.json()["expense"] == created
        changed = deepcopy(body)
        changed["data"]["category"] = "餐饮"
        assert upgraded.post("/api/operations", json=changed).status_code == 409
        assert upgraded.get("/api/sync").json()["expenses"] == [created]
    migrate_database(path)  # A second startup must preserve the migrated data.


def test_image_bytes_visibility_and_identity(setup):
    _, _, a, b, _, _ = setup
    shared, private = submit(a, operation()), submit(a, operation("private"))
    shared_url, private_url = image_url(shared["id"]), image_url(private["id"])
    data = image_bytes()
    meta = upload(a, shared_url, data).json()
    assert meta["expense_id"] == shared["id"] and meta["size_bytes"] == len(data)
    assert upload(a, private_url).status_code == 200
    for client in [a, b]:
        response = client.get(shared_url)
        assert response.status_code == 200 and response.content == data
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert b.get(private_url).status_code == 404
    assert upload(b, shared_url).status_code == 404
    assert b.delete(shared_url).status_code == 404
    assert b.get("/api/sync").json()["images"] == [meta]
    assert a.get(shared_url, headers={"X-Expected-User": "wrong"}).status_code == 409
    assert a.put(shared_url, content=data, headers={"X-App-Request": "wrong", "Content-Type": "image/png"}).status_code == 403
    assert a.delete(shared_url, headers={"Origin": "https://evil.example"}).status_code == 403
    assert a.get(f"/api/expenses/{shared['id']}").json()["version"] == 1


def test_image_retry_limit_delete_tombstone_and_restart(setup):
    path, _, a, _, _, _ = setup
    expense = submit(a, operation())
    urls = [image_url(expense["id"]) for _ in range(4)]
    first = upload(a, urls[0])
    assert first.status_code == 200
    for _ in range(2):
        assert upload(a, urls[0]).json() == first.json()
    assert upload(a, urls[0], image_bytes("blue")).json()["detail"]["code"] == "image_id_reused"
    for url in urls[1:3]:
        assert upload(a, url).status_code == 200
    assert upload(a, urls[3]).json()["detail"]["code"] == "image_limit"
    assert a.delete(urls[0]).status_code == 204
    assert a.delete(urls[0]).status_code == 204
    assert a.get(urls[0]).status_code == 404
    assert upload(a, urls[0]).json()["detail"]["code"] == "image_deleted"
    assert upload(a, urls[3]).status_code == 200
    never_uploaded = image_url(expense["id"])
    assert a.delete(never_uploaded).status_code == 204
    assert upload(a, never_uploaded).json()["detail"]["code"] == "image_deleted"
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(a.cookies)
        restarted.headers.update(a.headers)
        assert len(restarted.get("/api/sync").json()["images"]) == 3
        assert restarted.get(urls[1]).content == image_bytes()
        assert upload(restarted, urls[0]).json()["detail"]["code"] == "image_deleted"


@pytest.mark.parametrize("data,mime,status", [
    (b"not an image", "image/png", 422), (b"<svg></svg>", "image/svg+xml", 415),
    (image_bytes(), "image/jpeg", 422), (b"a" * (2 * 1024 * 1024 + 1), "image/jpeg", 413),
    (image_bytes(fmt="JPEG")[:-10], "image/jpeg", 422),
])
def test_invalid_images_are_not_saved(setup, data, mime, status):
    _, _, a, _, _, _ = setup
    expense = submit(a, operation())
    assert upload(a, image_url(expense["id"]), data, mime).status_code == status
    assert a.get("/api/sync").json()["images"] == []


def test_image_pixel_limit_and_supported_formats(setup):
    _, _, a, _, _, _ = setup
    expense = submit(a, operation())
    too_many = image_bytes(size=(5000, 4001))
    assert len(too_many) < 2 * 1024 * 1024
    assert upload(a, image_url(expense["id"]), too_many).status_code == 422
    for fmt, mime in [("JPEG", "image/jpeg"), ("WEBP", "image/webp")]:
        assert upload(a, image_url(expense["id"]), image_bytes(fmt=fmt), mime).status_code == 200


def test_concurrent_image_uploads_respect_limit(setup):
    _, app, a, _, _, _ = setup
    expense = submit(a, operation())
    def send(_):
        with TestClient(app) as client:
            client.cookies.update(a.cookies)
            client.headers.update(a.headers)
            return upload(client, image_url(expense["id"])).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(send, range(4)))
    assert sorted(statuses) == [200, 200, 200, 409]
    assert len(a.get("/api/sync").json()["images"]) == 3
