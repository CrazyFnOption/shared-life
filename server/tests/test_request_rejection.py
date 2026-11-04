"""Rejection reasons: semantic retries, atomic storage, migration and revocation."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.accounting import db as database, ledger
from server.accounting.db import connect, initialize_database, migrate_database
from server.api.app import create_app
from server.tests.legacy_fixture import to_v1, old_backup
from server.automation.backups import backup, restore, sha
from server.tests.test_api import setup, operation, submit  # noqa: F401
from server.tests.test_category_changes import preview, send, trio  # noqa: F401
from server.tests.test_families import group, refresh, leave, invite, join  # noqa: F401
from server.tests.test_maintenance import propose, action
from server.tests.test_refinement import image_url, upload, image_bytes


def make_case(a, b, kind):
    expense = submit(b, operation(category='餐饮'))
    if kind == 'single':
        item, _ = propose(a, expense)
        url = f"/api/change-requests/{item['id']}/decision"
    else:
        p = preview(a)
        item, _ = send(a, p, {'id': str(uuid4()), 'preview_id': p['preview_id'], 'reason': '申请原因'})
        url = f"/api/category-changes/{item['id']}/decision"
    return expense, item, url


@pytest.fixture(params=['single', 'category'])
def case(request, setup):
    path, app, a, b, *_ = setup
    expense, item, url = make_case(a, b, request.param)
    return path, app, a, b, request.param, expense, item, url


def post(b, url, reason='', *, did=None, decision='reject'):
    return b.post(url, json={'decision_id': did or str(uuid4()), 'decision': decision, 'rejection_reason': reason})


def result_item(response, kind):
    return response['request' if kind == 'single' else 'change']


def current_items(client, kind):
    if kind == 'single':
        return client.get('/api/change-requests').json()['requests']
    return client.get('/api/category-changes').json()['changes']


@pytest.mark.parametrize('reason', ['', '  \t\n', '  数额需要再核对 \n', '原' * 500])
def test_rejection_reason_saved_separately_visible_and_retry_frozen(case, reason):
    path, _, a, b, kind, expense, item, url = case
    image = image_url(expense['id'])
    assert upload(b, image).status_code == 200
    did = str(uuid4())
    response = post(b, url, reason, did=did)
    assert response.status_code == 200, response.text
    result = result_item(response.json(), kind)
    assert result['status'] == 'rejected' and result['rejection_reason'] == reason.strip()
    assert result['reason'] == item['reason']
    assert post(b, url, reason.strip(), did=did).json() == response.json()
    assert post(b, url, reason.strip() + '不同', did=did).status_code in {409, 422}
    for client in (a, b):
        assert current_items(client, kind)[0]['rejection_reason'] == reason.strip()
        snapshot = client.get('/api/sync').json()
        assert snapshot['requests' if kind == 'single' else 'category_changes'][0]['rejection_reason'] == reason.strip()
        assert client.get('/api/expenses/' + expense['id']).json() == expense
        assert client.get(image).content == image_bytes()
    table = 'change_requests' if kind == 'single' else 'category_changes'
    with connect(path) as db:
        assert db.execute(f'SELECT rejection_reason FROM {table} WHERE id=?', (item['id'],)).fetchone()[0] == reason.strip()
        assert db.execute('SELECT count(*) FROM revisions WHERE expense_id=?', (expense['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('decision,reason', [('reject', '原' * 501), ('reject', None), ('accept', '不能附拒绝原因')])
def test_invalid_reason_is_422_and_cannot_decide(case, decision, reason):
    _, _, _, b, kind, _, _, url = case
    response = post(b, url, reason, decision=decision)
    assert response.status_code == 422
    assert current_items(b, kind)[0]['status'] == 'pending'
    assert current_items(b, kind)[0]['rejection_reason'] == ''


def test_accept_empty_reason_preserves_application_reason(case):
    _, _, _, b, kind, _, item, url = case
    response = post(b, url, '  ', decision='accept')
    assert response.status_code == 200, response.text
    result = result_item(response.json(), kind)
    assert result['status'] == ('accepted' if kind == 'single' else 'applied')
    assert result['reason'] == item['reason'] and result['rejection_reason'] == ''


def snapshot_tables(db):
    """Capture every old column, including credentials, receipts and image blobs."""
    result = {}
    for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        columns = [r['name'] for r in db.execute(f'PRAGMA table_info({table})') if r['name'] != 'rejection_reason']
        result[table] = (columns, [tuple(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")])
    return result


def remove_new_columns(path):
    to_v1(path)
    with connect(path) as db:
        for table in ('change_requests', 'category_changes'):
            db.execute(f'ALTER TABLE {table} DROP COLUMN rejection_reason')


def test_old_receipt_and_hash_survive_007_and_empty_retries(case):
    path, _, a, b, kind, expense, item, url = case
    assert upload(b, image_url(expense['id'])).status_code == 200
    did = str(uuid4())
    legacy_body = {'decision_id': did, 'decision': 'reject'}
    response = b.post(url, json=legacy_body)
    assert response.status_code == 200
    legacy = deepcopy(response.json())
    result_item(legacy, kind).pop('rejection_reason')
    table = 'operations' if kind == 'single' else 'category_decisions'
    id_column = 'operation_id' if kind == 'single' else 'decision_id'
    uid = b.headers['X-Expected-User']
    hash_body = {'type': 'decision', 'request_id': item['id'], **legacy_body} if kind == 'single' else {'change_id': item['id'], **legacy_body}
    legacy_json = ledger.canonical_json(legacy)
    with connect(path) as db:
        assert db.execute(f'SELECT request_hash FROM {table} WHERE user_id=? AND {id_column}=?', (uid, did)).fetchone()[0] == ledger.digest(hash_body)
        db.execute(f'UPDATE {table} SET request_hash=?,receipt_json=? WHERE user_id=? AND {id_column}=?', (ledger.digest(hash_body), legacy_json, uid, did))
    remove_new_columns(path)
    with connect(path) as db:
        old = snapshot_tables(db)
    migrate_database(path)
    migrate_database(path)
    with connect(path) as db:
        for old_table,(columns,rows) in old.items():
            actual=[tuple(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {old_table} ORDER BY rowid")]
            if old_table=='schema_migrations':actual=[r for r in actual if r[0] not in {'multi-family-v2','memos-v1','plans-v1'}]
            assert actual==rows
        for name in ('change_requests', 'category_changes'):
            assert not db.execute(f"SELECT 1 FROM {name} WHERE rejection_reason!=''").fetchone()
        assert db.execute(f'SELECT receipt_json FROM {table} WHERE user_id=? AND {id_column}=?', (uid, did)).fetchone()[0] == legacy_json
    with TestClient(create_app(path)) as upgraded:
        upgraded.cookies.update(b.cookies)
        upgraded.headers.update(b.headers)
        for body in (legacy_body, dict(legacy_body, rejection_reason=''), dict(legacy_body, rejection_reason=' \n')):
            retry = upgraded.post(url, json=body)
            assert retry.status_code == 200 and retry.json() == legacy
        assert post(upgraded, url, '新增原因', did=did).status_code == 409
    assert current_items(a, kind)[0]['rejection_reason'] == ''


def test_007_failure_rolls_back_both_columns_and_retry_succeeds(setup, monkeypatch):
    path, *_ = setup
    remove_new_columns(path)
    original = database.Connection.execute
    def fail_second(self, sql, parameters=(), /):
        if sql.strip().startswith('ALTER TABLE category_changes ADD COLUMN rejection_reason'):
            raise sqlite3.OperationalError('fictional second-column migration failure')
        return original(self, sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(database.Connection, 'execute', fail_second)
        with pytest.raises(sqlite3.OperationalError):
            migrate_database(path)
    with connect(path) as db:
        for table in ('change_requests', 'category_changes'):
            assert 'rejection_reason' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
    migrate_database(path)
    migrate_database(path)


def test_reason_and_status_roll_back_together_then_fixed_id_can_retry(case):
    path, _, _, b, kind, _, item, url = case
    receipt_table = 'operations' if kind == 'single' else 'category_decisions'
    did = str(uuid4())
    with connect(path) as db:
        db.execute(f"CREATE TRIGGER fail_decision BEFORE INSERT ON {receipt_table} BEGIN SELECT RAISE(ABORT, 'fictional failure'); END")
    assert post(b, url, '固定重试原因', did=did).status_code == 500
    current = current_items(b, kind)[0]
    assert current['status'] == 'pending' and current['rejection_reason'] == ''
    with connect(path) as db:
        if kind == 'category':
            assert db.execute('SELECT status FROM category_approvals WHERE change_id=?', (item['id'],)).fetchone()[0] == 'pending'
        db.execute('DROP TRIGGER fail_decision')
    response = post(b, url, '固定重试原因', did=did)
    assert response.status_code == 200
    assert result_item(response.json(), kind)['rejection_reason'] == '固定重试原因'
    assert post(b, url, '固定重试原因', did=did).json() == response.json()


@pytest.mark.parametrize('kind', ['single', 'category'])
@pytest.mark.parametrize('revoke', ['private', 'departure'])
def test_reason_cannot_leak_through_old_receipt_after_visibility_revoked(trio, kind, revoke):
    _, _, (a, b, _, d, _), fid, _ = trio
    expense, item, url = make_case(a, b, kind)
    did = str(uuid4())
    reason = '只允许原家庭看到的拒绝原因'
    assert post(b, url, reason, did=did).status_code == 200
    assert post(d, url, reason, did=did).status_code == 404
    assert reason not in json.dumps(refresh(d), ensure_ascii=False)
    if revoke == 'private':
        submit(b, action(expense, 'scope', scope='private'))
        assert reason not in json.dumps(refresh(a), ensure_ascii=False)
    else:
        leave(b, fid)
        assert reason not in json.dumps(refresh(a), ensure_ascii=False)
        assert reason not in json.dumps(refresh(b), ensure_ascii=False)
        join(b, invite(a, 'person-1'))
        assert post(b, url, reason, did=did).status_code in {404, 409}
        assert reason not in json.dumps(refresh(a), ensure_ascii=False)
        if kind == 'category':
            assert reason not in json.dumps(refresh(b), ensure_ascii=False)
        else:
            # Existing owner history remains readable on their own record;
            # withdrawal still prevents a decision replay or requester exposure.
            assert current_items(b, kind)[0]['rejection_reason'] == reason


def test_one_rejection_cancels_batch_keeps_other_approval_receipt_original(trio):
    path, _, (a, b, c, *_), _, _ = trio
    originals = [submit(client, operation(category='餐饮')) for client in (a, b, c)]
    change, _ = send(a, preview(a))
    url = f"/api/category-changes/{change['id']}/decision"
    accept_id = str(uuid4())
    first = post(b, url, did=accept_id, decision='accept')
    assert first.status_code == 200 and first.json()['change']['status'] == 'pending'
    rejected = post(c, url, '另一记录者不同意整批更正')
    assert rejected.status_code == 200 and rejected.json()['change']['status'] == 'rejected'
    assert post(b, url, did=accept_id, decision='accept').json() == first.json()
    assert post(b, url).status_code == 409
    for client in (a, b, c):
        assert current_items(client, 'category')[0]['rejection_reason'] == '另一记录者不同意整批更正'
        for expense in originals:
            assert client.get('/api/expenses/' + expense['id']).json() == expense
    with connect(path) as db:
        approvals = dict(db.execute('SELECT owner_id,status FROM category_approvals WHERE change_id=?', (change['id'],)))
        assert approvals == {b.headers['X-Expected-User']: 'accepted', c.headers['X-Expected-User']: 'rejected'}


@pytest.mark.parametrize('legacy', [False, True])
def test_backup_restore_preserves_new_reasons_or_upgrades_old_copy(setup, tmp_path, legacy):
    path, _, a, b, *_ = setup
    cases = [make_case(a, b, kind) for kind in ('single', 'category')]
    reasons = ['单笔拒绝原因', '分类拒绝原因']
    decisions = []
    for (_, _, url), reason in zip(cases, reasons):
        did = str(uuid4())
        assert post(b, url, reason, did=did).status_code == 200
        decisions.append((url, reason, did))
    assert upload(b, image_url(cases[0][0]['id'])).status_code == 200
    with connect(path) as db:
        credentials = [tuple(r) for r in db.execute('SELECT id,password_hash FROM users ORDER BY id')]
    if legacy:
        remove_new_columns(path)
    saved = (old_backup if legacy else backup)(path, tmp_path / 'backup')
    checksum = sha(saved / 'business.sqlite3')
    restored = tmp_path / 'restored.sqlite3'
    result = restore(saved, restored, saved / 'credentials.json')
    assert sha(saved / 'business.sqlite3') == checksum
    with connect(restored) as db:
        for table, (_, item, _), reason in zip(('change_requests', 'category_changes'), cases, reasons):
            assert db.execute(f'SELECT rejection_reason FROM {table} WHERE id=?', (item['id'],)).fetchone()[0] == ('' if legacy else reason)
        assert [tuple(r) for r in db.execute('SELECT id,password_hash FROM users ORDER BY id')] == credentials
        assert db.execute('SELECT data FROM expense_images WHERE deleted=0').fetchone()[0] == image_bytes()
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    with TestClient(create_app(restored)) as client:
        client.cookies.update(b.cookies)
        client.headers.update(b.headers)
        assert post(client, *decisions[0][:2], did=decisions[0][2]).status_code == 401
        assert client.post('/api/auth/login', json={'username': 'bob', 'password': 'fictional-b'}).status_code == 200
        assert post(client, *decisions[0][:2], did=decisions[0][2]).json()['detail']['code'] == 'restore_changed'
        client.headers['X-Restore-Id'] = result['restore_id']
        # Batch context binds to its original restore and must not replay it.
        assert client.get('/api/category-changes').json()['changes'] == []
        assert post(client, *decisions[1][:2], did=decisions[1][2]).status_code == 404
        assert client.get('/api/expenses/' + cases[0][0]['id']).json() == cases[0][0]
