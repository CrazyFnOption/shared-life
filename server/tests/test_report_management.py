"""Month generation/deletion over the real handlers with fictional providers only."""
from datetime import datetime, UTC
from uuid import uuid4
import pytest
from server.accounting.db import connect
from server.automation import reports
from server.automation.backups import validate_database
from server.automation.providers import ProviderResult
from server.tests.test_automation import setup, seed
from server.tests.test_api import operation, submit, update


def source(c, scope='shared'):
    ctx=c.get('/api/context',headers={'X-App-Protocol':'2'}).json()
    family=ctx['families'][0] if scope=='shared' else None
    return {'X-App-Protocol':'2','X-Restore-Id':ctx['restore_id'],'X-Scope':scope,'X-Family-Id':family['id'] if family else 'none','X-Relation-Epoch':str(family['relation_epoch'] if family else 0)}


def generate(c,h,month='2026-09'):
    response=c.post('/api/v2/reports',headers=h,json={'scope':h['X-Scope'],'month':month})
    assert response.status_code==200,response.text
    return response.json()


def test_generate_save_reopen_delete_and_explicit_regenerate(setup):
    path,c,w,m,clock,a,b,p=setup
    expense=seed(c);h=source(c)
    row=generate(c,h);identity=row['id'];assert row['status']=='pending'
    assert generate(c,h)['id']==identity
    with connect(path) as db:assert db.execute('SELECT count(*) FROM report_tasks').fetchone()[0]==1
    for _ in range(2):w._model_step(clock[0])
    assert len(m.calls)==2
    assert generate(c,h)['status']=='ready';assert len(m.calls)==2
    detail=c.get('/api/v2/reports/'+identity,headers=h).json();assert detail['content']['summary']
    with connect(path) as db:
        report_notifications=db.execute('SELECT id FROM notification_outbox WHERE report_id=?',(identity,)).fetchall();assert report_notifications
        db.execute("INSERT INTO push_deliveries(outbox_id,endpoint,status) VALUES(?,'https://example.test/fictional','sent')",(report_notifications[0]['id'],));db.commit()
        preserved={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ('expenses','revisions','refunds','expense_images','ai_costs','model_usage')}
    assert c.delete('/api/v2/reports/'+identity,headers=h).status_code==204
    assert c.get('/api/v2/reports/'+identity,headers=h).status_code==404
    assert c.get('/api/v2/reports?scope=shared',headers=h).json()['reports']==[]
    assert c.post('/api/v2/reports/'+identity+'/retry',headers=h,json={}).status_code==404
    # Existing automatic settings, subsequent ledger changes and refreshes must not revive deletion.
    assert c.put('/api/settings?scope=shared',json={'monthly':{'enabled':True,'time':'20:00'}}).status_code==200
    submit(c,update(expense,purpose='仍是原始支出'))
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE');reports.schedule(db,datetime(2026,10,4,13,tzinfo=UTC));db.commit()
        assert db.execute("SELECT status FROM reports WHERE id=?",(identity,)).fetchone()[0]=='deleted'
        assert not db.execute('SELECT 1 FROM report_tasks WHERE report_id=?',(identity,)).fetchone()
        assert not db.execute('SELECT 1 FROM notification_outbox WHERE report_id=?',(identity,)).fetchone()
        assert not db.execute('SELECT 1 FROM push_deliveries').fetchone()
        for table in ('refunds','expense_images','ai_costs','model_usage'):
            assert [tuple(r) for r in db.execute('SELECT * FROM '+table)]==preserved[table]
        validate_database(db)
    replacement=generate(c,h);assert replacement['id']!=identity
    assert generate(c,h)['id']==replacement['id'];assert len(m.calls)==2


def test_current_month_future_empty_and_source_permissions(setup):
    path,c,w,m,clock,a,b,p=setup;clock[0]=datetime(2026,11,13,12,tzinfo=UTC)
    seed(c);submit(c,operation(occurred_at='2026-11-12T12:00:00+08:00'))
    submit(c,operation(occurred_at='2026-11-30T12:00:00+08:00',purpose='本月未来记录'))
    shared=source(c);private=source(c,'private')
    current=generate(c,shared,'2026-11');assert current['month']=='2026-11' and current['summary']['total_cents']==1234
    assert c.post('/api/v2/reports',headers=shared,json={'scope':'shared','month':'2026-12'}).status_code==422
    assert c.post('/api/v2/reports',headers=shared,json={'scope':'shared','month':'2026-08'}).status_code==409
    assert c.post('/api/v2/reports',headers=private,json={'scope':'shared','month':'2026-11'}).status_code==409
    assert c.delete('/api/v2/reports/'+current['id'],headers=private).status_code==404
    assert c.delete('/api/v2/reports/'+current['id'],headers={**shared,'X-Restore-Id':str(uuid4())}).status_code==409
    assert c.delete('/api/v2/reports/'+current['id'],headers={**shared,'X-Relation-Epoch':'99'}).status_code==409
    private_expense=seed(c,'private');own=generate(c,private)
    original=c.headers['X-Expected-User']
    c.post('/api/auth/login',headers={'X-App-Request':'shared-expense'},json={'username':'bob','password':'fictional-b'})
    c.headers['X-Expected-User']=b['id'];other=source(c,'private')
    assert c.delete('/api/v2/reports/'+own['id'],headers=other).status_code==404
    # Current family members may manage shared reports, as for the existing retry action.
    assert c.delete('/api/v2/reports/'+current['id'],headers=source(c)).status_code==204
    assert not m.calls


@pytest.mark.parametrize('known_usage',[True,False])
def test_delete_during_metered_callback_cannot_resurrect_and_keeps_cost(setup,known_usage):
    path,c,w,m,clock,a,b,p=setup;h=source(c);seed(c);row=generate(c,h)
    m.metered=True
    original_call=m.call
    def call(payload):
        output=original_call(payload)
        assert c.delete('/api/v2/reports/'+row['id'],headers=h).status_code==204
        replacement=generate(c,h)
        assert replacement['id']!=row['id']
        return ProviderResult(output,usage={'input_tokens':100,'output_tokens':20} if known_usage else None)
    m.call=call
    w._model_step(clock[0])
    with connect(path) as db:
        assert not db.execute('SELECT 1 FROM reports WHERE id=?',(row['id'],)).fetchone()
        cost=db.execute('SELECT * FROM ai_costs').fetchone();assert cost['status']==('settled' if known_usage else 'unknown')
        assert cost['reserved_micro_usd']>0
        assert db.execute('SELECT sum(calls) FROM model_usage').fetchone()[0]==1
        assert not db.execute('SELECT 1 FROM notification_outbox WHERE report_id=?',(row['id'],)).fetchone()
        pending=db.execute('SELECT * FROM reports').fetchone();assert pending['status']=='pending' and not pending['content_json']
        assert db.execute('SELECT claim_token FROM report_tasks').fetchone()[0] is None
        validate_database(db)


def test_ledger_update_keeps_old_analysis_until_explicit_update(setup):
    path,c,w,m,clock,a,b,p=setup;expense=seed(c);h=source(c);row=generate(c,h)
    for _ in range(2):w._model_step(clock[0])
    detail=c.get('/api/v2/reports/'+row['id'],headers=h).json();old=detail['content']
    assert detail['content_is_current'] and len(m.calls)==2
    submit(c,update(expense,amount_cents=4567,purpose='更新后的虚构用途'))
    detail=c.get('/api/v2/reports/'+row['id'],headers=h).json()
    assert detail['status']=='stale' and not detail['content_is_current']
    assert detail['content']==old and detail['summary']['total_cents']==4567
    assert generate(c,h)['status']=='stale'
    for _ in range(2):w._model_step(clock[0])
    assert len(m.calls)==2
    with connect(path) as db:assert not db.execute('SELECT 1 FROM report_tasks').fetchone()
    assert c.post('/api/v2/reports/'+row['id']+'/retry',headers=h,json={}).status_code==200
    # Repeated user clicks reuse the active task and its claim/progress.
    assert c.post('/api/v2/reports/'+row['id']+'/retry',headers=h,json={}).status_code==200
    for _ in range(2):w._model_step(clock[0])
    current=c.get('/api/v2/reports/'+row['id'],headers=h).json()
    assert current['status']=='ready' and current['content_is_current'] and len(m.calls)==4
    assert m.calls[2]['expenses'][0]['amount_cents']==4567


def test_shared_void_revokes_old_analysis_for_other_member_without_requeue(setup):
    path,c,w,m,clock,a,b,p=setup
    expense=seed(c);owner_source=source(c);report=generate(c,owner_source)
    for _ in range(2):w._model_step(clock[0])
    assert c.get('/api/v2/reports/'+report['id'],headers=owner_source).json()['content']
    submit(c,{'operation_id':str(uuid4()),'kind':'void','expense_id':expense['id'],'base_version':expense['version'],'data':{}})
    assert c.post('/api/auth/login',json={'username':'bob','password':'fictional-b'}).status_code==200
    c.headers['X-Expected-User']=b['id'];other_source=source(c)
    assert c.get('/api/v2/expenses/'+expense['id'],headers=other_source).status_code==404
    detail=c.get('/api/v2/reports/'+report['id'],headers=other_source).json()
    assert detail['status']=='stale' and detail['expenses']==[]
    assert detail['content'] is None  # Old shared prose may describe the revoked item.
    with connect(path) as db:assert not db.execute('SELECT 1 FROM report_tasks').fetchone()
    assert len(m.calls)==2
