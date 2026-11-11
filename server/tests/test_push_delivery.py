"""Real WebPush encryption/signing with fictional keys and intercepted HTTP only."""
import base64
from datetime import datetime, timedelta
import json
from uuid import uuid4
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
import pytest
import requests

from server.accounting.db import connect
from server.automation.providers import WebPush, PushError
from server.automation.worker import Worker
from server.memos import calendar
from server.memos.calendar import ZONE
from server.tests.test_multi_family import home
from server.tests.test_memos import op, saved


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


@pytest.fixture
def transport(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    original = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    key_path = tmp_path / 'fictional-vapid.pem'
    key_path.write_bytes(original)
    monkeypatch.setenv('SHARED_EXPENSE_VAPID_PRIVATE_KEY', str(key_path))
    monkeypatch.setenv('SHARED_EXPENSE_VAPID_SUBJECT', 'https://fictional.example:8443')
    subscriber = ec.generate_private_key(ec.SECP256R1())
    subscription = {'endpoint': 'https://web.push.apple.com/fictional', 'keys': {
        'p256dh': b64(subscriber.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
        'auth': b64(b'fictional-auth-16'),
    }}
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 201
        response._content = b''
        return response

    monkeypatch.setattr(requests, 'post', post)
    yield subscription, calls, key
    assert key_path.read_bytes() == original


@pytest.mark.parametrize('endpoint', ['https://web.push.apple.com/fictional', 'https://fcm.googleapis.com/fcm/send/fictional'], ids=['apple', 'android-fcm'])
def test_https_subject_with_port_signs_using_original_key_and_retains_push(transport, endpoint):
    subscription, calls, key = transport
    subscription = {**subscription, 'endpoint': endpoint}
    WebPush().send(subscription, {'title': '同在', 'body': '请打开应用查看', 'url': '/'})
    assert len(calls) == 1
    _, request = calls[0]
    assert str(request['headers']['ttl']) == '86400'
    assert request['headers']['content-encoding'] == 'aes128gcm'
    assert request['timeout'] == 20
    token = request['headers']['Authorization'].split('t=', 1)[1].split(',', 1)[0]
    header, payload, signature = token.split('.')
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    assert claims['sub'] == 'https://fictional.example:8443'
    origin = urlsplit(endpoint)
    assert claims['aud'] == f'{origin.scheme}://{origin.netloc}'
    raw = base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
    signature = encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:]))
    key.public_key().verify(signature, (header + '.' + payload).encode(), ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize('subject', ['https://', 'https://user:secret@fictional.example:8443', 'https://fictional.example:bad'])
def test_invalid_https_contact_does_not_reach_http(transport, monkeypatch, subject):
    subscription, calls, _ = transport
    monkeypatch.setenv('SHARED_EXPENSE_VAPID_SUBJECT', subject)
    with pytest.raises((PushError, ValueError)):
        WebPush().send(subscription, {})
    assert not calls


def test_due_reminder_push_runs_after_clients_close_without_api_polling(home, transport, monkeypatch):
    path, _, clients = home
    subscription, calls, _ = transport
    now = datetime.now(ZONE)
    owner = clients[0]
    rule = {'id': str(uuid4()), 'mode': 'relative', 'at': None, 'offset_minutes': 0, 'recipient_ids': [], 'all_members': False}
    from server.tests.test_memo_lifecycle import clock
    with monkeypatch.context() as creation:
        clock(creation,calendar.anchor(now.date())-timedelta(minutes=1))
        item = saved(op(owner, 'memo', str(uuid4()), 'create', {
            'kind': 'schedule', 'category_id': None, 'title': '虚构后台提醒', 'body_markdown': '', 'attachments': [],
            'schedule': {'start_date': now.date().isoformat(), 'start_time': '00:00', 'end_date': None, 'end_time': None},
            'reminders': [rule],
        }))
    with connect(path) as db:
        db.execute('INSERT INTO push_subscriptions(owner_id,endpoint,keys_json) VALUES(?,?,?)',
                   (item['author_id'], subscription['endpoint'], json.dumps(subscription['keys'])))
    for client in clients:
        client.close()
    worker = Worker(path, push=WebPush(), clock=lambda: now)
    worker.tick()
    worker.tick()
    assert len(calls) == 1
    with connect(path) as db:
        assert db.execute('SELECT status FROM notification_outbox').fetchone()[0] == 'sent'
        assert tuple(db.execute('SELECT status,attempts FROM push_deliveries').fetchone()) == ('sent', 1)
