"""Device endpoint binding checks only; fictional subscriptions, no external Push."""
import hashlib
from uuid import uuid4
from server.accounting.db import connect
from server.tests.test_multi_family import home

ENDPOINT='https://fcm.googleapis.com/fcm/send/fictional-private-token'

def check(client,endpoint=ENDPOINT,**kwargs):
    return client.post('/api/v2/push-subscriptions/check',json={'endpoint':endpoint},**kwargs)


def test_local_endpoint_registration_active_expired_and_account_isolation(home):
    path,app,(a,b,c)=home
    initial=check(a);assert initial.status_code==200,initial.text
    expected={'registered':False,'active':False,'provider':'FCM','endpoint_fingerprint':hashlib.sha256(ENDPOINT.encode()).hexdigest()[:12]}
    assert initial.json()==expected
    assert a.post('/api/v2/push-subscriptions',json={'endpoint':ENDPOINT,'keys':{'p256dh':'fictional','auth':'secret'}}).status_code==200
    with connect(path) as db:before=[tuple(r) for r in db.execute('SELECT * FROM push_subscriptions ORDER BY owner_id,endpoint')]
    assert check(a).json()=={**expected,'registered':True,'active':True}
    assert check(b).json()==expected  # Another account cannot discover A's binding.
    with connect(path) as db:
        assert before==[tuple(r) for r in db.execute('SELECT * FROM push_subscriptions ORDER BY owner_id,endpoint')]
        db.execute('UPDATE push_subscriptions SET active=0 WHERE endpoint=?',(ENDPOINT,));db.commit()
    expired=check(a);assert expired.json()=={**expected,'registered':True,'active':False}
    for response in (initial,expired):
        assert ENDPOINT not in response.text and 'keys' not in response.json() and 'owner_id' not in response.json()


def test_check_requires_current_account_restore_json_and_redacts_invalid_input(home):
    path,app,(a,b,c)=home
    bid=b.get('/api/auth/me').json()['user']['id']
    assert check(a,headers={'X-Expected-User':bid}).status_code==409
    assert check(a,headers={'X-Restore-Id':str(uuid4())}).status_code==409
    assert check(a,headers={'X-App-Request':'not-allowed'}).status_code==403
    bad=a.post('/api/v2/push-subscriptions/check',json={'endpoint':ENDPOINT,'owner_id':bid,'keys':{'auth':'private-test-value'}})
    assert bad.status_code==422 and ENDPOINT not in bad.text and 'private-test-value' not in bad.text
    assert check(a,'https://evil.invalid/private-test-value').json()=={'detail':{'code':'invalid_push_check_request'}}
