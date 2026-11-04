"""Independent category/image contract checks through a real HTTP service.

UI_HTTP_CANDIDATE must identify the candidate under acceptance. No product
modules or database rows are imported/read by these tests.
"""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import uuid
import zlib

import pytest

from test_http_contract import LiveLedger, ROOT, create_operation, submit, update_operation


def png(red=40):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes([0, red, 80, 120]))) + chunk(b'IEND', b''))


def service_for(candidate, work, port=None):
    service = LiveLedger(candidate, work)
    service.origin = 'http://127.0.0.1:' + str(port or os.environ.get('UI_HTTP_TEST_PORT', '18776'))
    service.env.update(SHARED_EXPENSE_PORT=service.origin.rsplit(':', 1)[1], SHARED_EXPENSE_ORIGINS=service.origin)
    return service


def seed(service):
    subprocess.run([sys.executable, '-m', 'scripts.seed_demo', '--database', str(service.database)],
                   cwd=service.candidate, env=service.env, check=True, capture_output=True)


@pytest.fixture(scope='module')
def refined_ledger(tmp_path_factory):
    candidate = Path(os.environ.get('UI_HTTP_CANDIDATE', ROOT)).resolve()
    service = service_for(candidate, tmp_path_factory.mktemp('refinement-http'))
    seed(service)
    try:
        service.start()
        yield service
    finally:
        service.stop()
        service.log.close()


def category(client, scope, name):
    result = client.post('/api/categories', json={'scope': scope, 'name': name})
    assert result.status_code == 200, result.text
    return result.json()


def expense(client, scope='shared', cat=''):
    operation = create_operation(scope, purpose='验收-' + str(uuid.uuid4()))
    operation['data']['category'] = cat
    return submit(client, operation)['expense']


def image_path(record, image_id=None):
    return '/api/expenses/' + record['id'] + '/images/' + (image_id or str(uuid.uuid4()))


def upload(client, path, content=None, mime='image/png'):
    return client.put(path, content=png() if content is None else content, headers={'Content-Type': mime})


def test_custom_categories_are_idempotent_and_private_names_do_not_leak(refined_ledger):
    suffix = str(uuid.uuid4())[:8]
    with refined_ledger.client() as a, refined_ledger.client('demo-b') as b:
        shared = category(a, 'shared', '宠物-' + suffix)
        private = category(a, 'private', '私人-' + suffix)
        assert category(b, 'shared', shared['name']) == shared
        own = a.get('/api/sync').json()['categories']
        other = b.get('/api/sync').json()['categories']
        assert own.count(shared) == 1 and own.count(private) == 1
        assert other.count(shared) == 1 and private not in other
        assert private['name'] not in json.dumps(other, ensure_ascii=False)
        # Explicit creation survives a process restart even without any expense.
        refined_ledger.stop()
        refined_ledger.start()
        assert private in a.get('/api/sync').json()['categories']


def test_category_validation_and_record_roundtrip(refined_ledger):
    with refined_ledger.client() as a:
        for name in ['', '   ', '长' * 25]:
            assert a.post('/api/categories', json={'scope': 'shared', 'name': name}).status_code == 422
        record = expense(a, 'private', '  自定义用途  ')
        assert record['category'] == '自定义用途'
        assert {'scope': 'private', 'name': '自定义用途'} in a.get('/api/sync').json()['categories']
        changed = submit(a, update_operation(record, category='另一个类别'))['expense']
        assert a.get('/api/expenses/' + record['id']).json()['category'] == '另一个类别'
        assert changed['amount_cents'] == record['amount_cents']


def test_image_read_permissions_and_owner_only_writes(refined_ledger):
    with refined_ledger.client() as a, refined_ledger.client('demo-b') as b:
        public, private = expense(a), expense(a, 'private')
        public_path, private_path = image_path(public), image_path(private)
        for route in [public_path, private_path]:
            assert upload(a, route).status_code == 200
        for client, route in [(a, public_path), (a, private_path), (b, public_path)]:
            got = client.get(route)
            assert got.status_code == 200 and got.content == png()
            assert got.headers['content-type'].split(';')[0] == 'image/png'
            assert got.headers['cache-control'] == 'no-store'
            assert got.headers['x-content-type-options'] == 'nosniff'
        assert b.get(private_path).status_code == 404
        for route in [public_path, private_path]:
            assert upload(b, route).status_code == 404
            assert b.delete(route).status_code == 404
        own_ids = {x['expense_id'] for x in a.get('/api/sync').json()['images']}
        other_ids = {x['expense_id'] for x in b.get('/api/sync').json()['images']}
        assert {public['id'], private['id']} <= own_ids
        assert public['id'] in other_ids and private['id'] not in other_ids
        assert b.get(image_path(private)).status_code == 404


def test_image_replay_limits_and_deletion_do_not_mutate_expense(refined_ledger):
    with refined_ledger.client() as a:
        record = expense(a)
        paths = [image_path(record) for _ in range(4)]
        first = upload(a, paths[0])
        assert first.status_code == 200
        assert upload(a, paths[0]).json() == first.json()
        reused = upload(a, paths[0], png(90))
        assert reused.status_code == 409 and reused.json()['detail']['code'] == 'image_id_reused'
        assert upload(a, paths[1]).status_code == 200
        assert upload(a, paths[2]).status_code == 200
        overflow = upload(a, paths[3])
        assert overflow.status_code == 409 and overflow.json()['detail']['code'] == 'image_limit'
        assert a.delete(paths[0]).status_code == 204
        assert a.delete(paths[0]).status_code == 204
        assert a.get(paths[0]).status_code == 404
        late = upload(a, paths[0])
        assert late.status_code == 409 and late.json()['detail']['code'] == 'image_deleted'
        assert upload(a, paths[3]).status_code == 200
        assert a.get('/api/expenses/' + record['id']).json() == record
        images = [x for x in a.get('/api/sync').json()['images'] if x['expense_id'] == record['id']]
        assert len(images) == 3
        assert all(set(x) == {'id', 'expense_id', 'mime_type', 'size_bytes'} for x in images)
        assert {x['size_bytes'] for x in images} == {len(png())}


@pytest.mark.parametrize('content,mime', [(b'not an image', 'image/png'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 'image/svg+xml'),
    (b'x' * (2 * 1024 * 1024 + 1), 'image/png')])
def test_invalid_image_is_rejected_without_metadata(refined_ledger, content, mime):
    with refined_ledger.client() as a:
        record = expense(a)
        route = image_path(record)
        rejected = upload(a, route, content, mime)
        assert 400 <= rejected.status_code < 500, rejected.text
        assert a.get(route).status_code == 404
        assert not any(x['expense_id'] == record['id'] for x in a.get('/api/sync').json()['images'])


def test_image_write_guards_and_old_account_assertion(refined_ledger):
    with refined_ledger.client() as a:
        record = expense(a, 'private')
        route = image_path(record)
        a.headers.pop('X-App-Request')
        assert upload(a, route).status_code == 403
        assert a.delete(route).status_code == 403
        a.headers['X-App-Request'] = 'shared-expense'
        a.headers['Origin'] = 'https://untrusted.invalid'
        assert upload(a, route).status_code == 403
        a.headers['Origin'] = refined_ledger.origin
        a.post('/api/auth/login', json={'username': 'demo-b', 'password': 'demo-only-2026'}).raise_for_status()
        for response in [upload(a, route), a.get(route), a.delete(route),
                         a.post('/api/categories', json={'scope': 'private', 'name': '旧账号分类'})]:
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'account_changed'


def test_old_database_and_lost_receipt_survive_upgrade(tmp_path):
    old_candidate = Path(os.environ.get('UI_OLD_CANDIDATE', ROOT / '.local/candidates/334eb13')).resolve()
    assert (old_candidate / 'server/main.py').is_file(), 'Provide frozen v1 through UI_OLD_CANDIDATE'
    new_candidate = Path(os.environ.get('UI_HTTP_CANDIDATE', ROOT)).resolve()
    service = service_for(old_candidate, tmp_path, 18779)
    seed(service)
    try:
        service.start()
        with service.client(legacy=True) as a:
            operation = create_operation('private', purpose='升级前已提交，回执丢失')
            before = submit(a, operation)
            service.stop()
            service.candidate = new_candidate
            service.start()
            context = a.get('/api/family').json()
            a.headers['X-Family-Id'] = context['family']['id']
            a.headers['X-Membership-Version'] = str(context['membership_version'])
            # Retry the exact old request; the old receipt content remains usable.
            replay = a.post('/api/operations', json=operation)
            assert replay.status_code == 200, replay.text
            assert replay.json()['operation_id'] == before['operation_id']
            assert replay.json()['expense']['id'] == before['expense']['id']
            rows = a.get('/api/sync').json()['expenses']
            assert len(rows) == 1 and rows[0]['amount_cents'] == 20000
            assert rows[0]['category'] == '' and rows[0]['version'] == 1
            # New fields work after migration without replacing prior data.
            current = submit(a, update_operation(rows[0], category='升级后分类'))['expense']
            route = image_path(current)
            assert upload(a, route).status_code == 200
            service.stop()
            service.start()
            assert a.get(route).content == png()
            assert a.get('/api/expenses/' + current['id']).json()['category'] == '升级后分类'
    finally:
        service.stop()
        service.log.close()
