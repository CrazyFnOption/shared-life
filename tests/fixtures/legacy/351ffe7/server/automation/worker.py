"""One persistent bounded model step per tick. Network I/O is outside transactions."""
from datetime import datetime, UTC, timedelta
import json
import os
import threading
from uuid import uuid4
from pathlib import Path
from server.accounting.db import connect
from server.accounting.ledger import canonical_json, digest
from server.automation import reports
from server.automation.providers import configured_providers, push_configured, WebPush, ProviderError, PushError

MAX_INPUT_BYTES = 24000

def groups(items, key):
    result=[]; group=[]
    for item in items:
        if len(canonical_json({key:[item]}).encode())>MAX_INPUT_BYTES:
            raise ProviderError('invalid')
        if group and (len(group)>=50 or len(canonical_json({key:group+[item]}).encode())>MAX_INPUT_BYTES):
            result.append(group); group=[]
        group.append(item)
    if group:
        result.append(group)
    return result

def model_expense(expense):
    """Only bounded consumption-level input; refund details stay in the report UI."""
    result = {key: expense[key] for key in (
        'id', 'owner_id', 'purpose', 'occurred_at', 'category', 'rating', 'rating_reason',
        'amount_cents', 'net_amount_cents',
    )}
    result['refund_total_cents'] = expense['amount_cents'] - expense['net_amount_cents']
    return result


def validate(payload, output):
    if not isinstance(output,dict):
        raise ProviderError('invalid')
    if payload['kind']=='items':
        if set(output)!={'items'} or not isinstance(output['items'],list):
            raise ProviderError('invalid')
        wanted=[e['id'] for e in payload['expenses']]
        got=[]
        for item in output['items']:
            if not isinstance(item,dict) or set(item)!={'expense_id','analysis'} or not isinstance(item['analysis'],str) or not 1<=len(item['analysis'])<=800:
                raise ProviderError('invalid')
            got.append(item['expense_id'])
        if len(got)!=len(set(got)) or set(got)!=set(wanted):
            raise ProviderError('invalid')
        return output
    if set(output)!={'summary'} or not isinstance(output['summary'],str) or not 1<=len(output['summary'])<=1500:
        raise ProviderError('invalid')
    return output

class Worker:
    def __init__(self, db_path, *, primary=None, backup=None, push=None, clock=None, call_limit=None):
        self.db_path=db_path
        production_primary,production_backup=configured_providers()
        self.primary=primary if primary is not None else production_primary
        self.backup=backup if backup is not None else production_backup
        self.push=push if push is not None else (WebPush() if push_configured() else None)
        self.clock=clock or (lambda:datetime.now(UTC))
        self.call_limit=max(0,int(os.getenv('SHARED_EXPENSE_MODEL_CALL_LIMIT','0'))) if call_limit is None else max(0,call_limit)
        self.stop_event=threading.Event()
        self.config_sig=digest([(p.name,p.model,digest(getattr(p,'key',''))) if p else None for p in (self.primary,self.backup)]+[self.call_limit])

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # Keep the lifecycle alive without writing private exception payloads to logs.
                import logging
                logging.getLogger(__name__).error('Automation tick failed; durable tasks retained')
            self.stop_event.wait(30)

    def tick(self):
        if self.stop_event.is_set():
            return
        now=self.clock(); stamp=now.timestamp()
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if reports.state(db)['automation_paused']:
                db.commit(); return
            reports.schedule(db,now)
            # A dead worker's claimed call was already charged. Recover without unbounded retries.
            db.execute("UPDATE report_tasks SET claim_token=NULL,lease_until=NULL WHERE lease_until<?",(stamp,))
            db.execute("UPDATE report_tasks SET next_attempt=?,failures=0,rounds=0,progress_json='{}' WHERE config_sig IS NOT NULL AND config_sig!=?",(stamp,self.config_sig))
            db.execute('UPDATE report_tasks SET config_sig=?',(self.config_sig,))
            db.commit()
        self._backup(now)
        self._model_step(now)
        self._notifications(now)

    def _model_step(self,now):
        if self.stop_event.is_set():
            return
        stamp=now.timestamp(); day=now.astimezone(reports.ZONE).date().isoformat()
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            task=db.execute('SELECT * FROM report_tasks WHERE next_attempt<=? AND claim_token IS NULL ORDER BY next_attempt,report_id LIMIT 1',(stamp,)).fetchone()
            if not task:
                db.commit(); return
            task=dict(task); row=dict(db.execute('SELECT * FROM reports WHERE id=?',(task['report_id'],)).fetchone())
            if not self.primary:
                db.execute("UPDATE reports SET status='unconfigured',error='请由管理员配置主用模型并启用调用' WHERE id=?",(row['id'],))
                db.execute('UPDATE report_tasks SET next_attempt=? WHERE report_id=?',(stamp+300,row['id']))
                db.commit(); return
            expenses=reports.snapshot(db,row['scope'],row['owner_id'],row['month'])
            current=reports.version(expenses)
            if current!=task['target_version'] or task['restore_id']!=reports.state(db)['restore_id']:
                reports.queue(db,row['id'],current,when=stamp+60)
                db.execute("UPDATE reports SET status='stale',content_json=NULL,data_version=? WHERE id=?",(current,row['id']))
                db.commit(); return
            if not expenses:
                db.execute("UPDATE reports SET status='stale',content_json=NULL WHERE id=?",(row['id'],))
                db.execute('DELETE FROM report_tasks WHERE report_id=?',(row['id'],)); db.commit(); return
            try:
                progress=json.loads(task['progress_json'])
                batches=groups([model_expense(expense) for expense in expenses],'expenses')
                index=progress.get('index',0)
                if index<len(batches):
                    payload={'kind':'items','expenses':batches[index]}
                else:
                    analyses=progress.get('summaries',progress['items'])
                    chunks=groups(analyses,'analyses')
                    summary_index=progress.get('summary_index',0)
                    payload={'kind':'summary','analyses':chunks[summary_index]}
            except (ProviderError, ValueError, TypeError, KeyError, IndexError, AttributeError):
                # Deterministic preparation failures cannot be fixed by another paid call.
                # Park only this task; later tasks and this tick's notifications still run.
                db.execute("UPDATE reports SET status='failed',error='报告输入处理失败，任务已保留，请管理员检查后重试' WHERE id=?",(row['id'],))
                db.execute('UPDATE report_tasks SET next_attempt=?,claim_token=NULL,lease_until=NULL WHERE report_id=?',(1e30,row['id']))
                db.commit();return
            if task['failures']>=3:
                self._failure(db,task,day,stamp,'transient'); db.commit(); return
            provider=self.primary if task['failures']<2 else self.backup
            if provider is None:
                self._failure(db,task,day,stamp,'unavailable'); db.commit(); return
            db.execute('INSERT OR IGNORE INTO model_usage(day,calls) VALUES(?,0)',(day,))
            if db.execute('SELECT calls FROM model_usage WHERE day=?',(day,)).fetchone()['calls']>=self.call_limit:
                tomorrow=(now.astimezone(reports.ZONE)+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
                db.execute("UPDATE reports SET status='pending',error='已达到每日模型调用上限，保留待办' WHERE id=?",(row['id'],))
                db.execute('UPDATE report_tasks SET next_attempt=? WHERE report_id=?',(tomorrow.timestamp(),row['id']))
                db.commit(); return
            token=str(uuid4())
            db.execute('UPDATE model_usage SET calls=calls+1 WHERE day=?',(day,))
            db.execute('UPDATE report_tasks SET claim_token=?,lease_until=?,failures=failures+1 WHERE report_id=?',(token,stamp+120,row['id']))
            db.execute("UPDATE reports SET status='running',error=NULL WHERE id=?",(row['id'],))
            db.commit()
        if self.stop_event.is_set():
            return
        error=None
        try:
            output=validate(payload,provider.call(payload))
        except ProviderError as exc:
            error=exc.kind
        except Exception:
            error='transient'
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            live=db.execute('SELECT * FROM report_tasks WHERE report_id=? AND claim_token=?',(row['id'],token)).fetchone()
            if not live or reports.state(db)['restore_id']!=task['restore_id'] or reports.version(reports.snapshot(db,row['scope'],row['owner_id'],row['month']))!=current:
                db.commit(); return
            live=dict(live)
            if error:
                self._failure(db,live,day,stamp,error); db.commit(); return
            progress.setdefault('providers',[])
            label={'provider':provider.name,'model':provider.model}
            if label not in progress['providers']:
                progress['providers'].append(label)
            finished=False
            if payload['kind']=='items':
                progress.setdefault('items',[]).extend(output['items']);progress['index']=index+1
            else:
                analyses=progress.get('summaries',progress['items']); chunks=groups(analyses,'analyses')
                if len(chunks)==1:
                    content={'items':progress['items'],'summary':output['summary']};finished=True
                else:
                    progress.setdefault('next_summaries',[]).append(output['summary'])
                    progress['summary_index']=progress.get('summary_index',0)+1
                    if progress['summary_index']==len(chunks):
                        progress['summaries']=progress.pop('next_summaries');progress['summary_index']=0
            if finished:
                db.execute("UPDATE reports SET status='ready',content_json=?,generated_at=?,provider=?,model=?,error=NULL WHERE id=?",(canonical_json(content),now.isoformat(),','.join(p['provider'] for p in progress['providers']),','.join(p['model'] for p in progress['providers']),row['id']))
                db.execute('DELETE FROM report_tasks WHERE report_id=?',(row['id'],))
                recipients=[r['id'] for r in db.execute('SELECT id FROM users')] if row['scope']=='shared' else [row['owner_id']]
                for user in recipients:
                    reports.enqueue_notification(db,user,f"report:{row['id']}:{current}",stamp,row['id'],current)
            else:
                db.execute('UPDATE report_tasks SET progress_json=?,claim_token=NULL,lease_until=NULL,failures=0,next_attempt=? WHERE report_id=?',(canonical_json(progress),stamp,row['id']))
            db.commit()

    def _failure(self,db,task,day,stamp,kind):
        failures=task['failures']
        if kind in ('unavailable','invalid') and failures<2:
            failures=2
        rounds=task['rounds'] if task['retry_day']==day else 0
        terminal=failures>=3 or (failures>=2 and self.backup is None)
        if terminal:
            rounds+=1; failures=0
            next_attempt=stamp+3600 if rounds<3 else 1e30
        else:
            next_attempt=stamp+(30 if failures<2 else 0)
        db.execute("UPDATE reports SET status=?,error='模型处理失败，任务已保留，可配置后重试' WHERE id=?",('failed' if terminal else 'pending',task['report_id']))
        db.execute('UPDATE report_tasks SET failures=?,rounds=?,retry_day=?,next_attempt=?,claim_token=NULL,lease_until=NULL WHERE report_id=?',(failures,rounds,day,next_attempt,task['report_id']))

    def _notifications(self,now):
        if not self.push or self.stop_event.is_set():
            return
        stamp=now.timestamp()
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            # A crashed send has unknown delivery. Never automatically retry it.
            db.execute("UPDATE push_deliveries SET status='uncertain' WHERE status='sending' AND outbox_id IN (SELECT id FROM notification_outbox WHERE next_attempt<?)",(stamp-120,))
            rows=list(db.execute("SELECT * FROM notification_outbox WHERE status='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 10",(stamp,)))
            db.commit()
        for row in rows:
            if self.stop_event.is_set():
                return
            self._send_one(dict(row),stamp)

    def _send_one(self,row,stamp):
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            live=db.execute('SELECT * FROM notification_outbox WHERE id=?',(row['id'],)).fetchone()
            if live['status']!='pending':
                db.commit();return
            if row['report_id']:
                report=db.execute('SELECT * FROM reports WHERE id=?',(row['report_id'],)).fetchone()
                if report['status']!='ready' or report['data_version']!=row['data_version']:
                    db.execute("UPDATE notification_outbox SET status='cancelled' WHERE id=?",(row['id'],));db.commit();return
            subs=list(db.execute('SELECT * FROM push_subscriptions WHERE owner_id=? AND active=1',(row['user_id'],)))
            for sub in subs:
                db.execute('INSERT OR IGNORE INTO push_deliveries(outbox_id,endpoint) VALUES(?,?)',(row['id'],sub['endpoint']))
            db.commit()
        for sub in subs:
            if self.stop_event.is_set():
                return
            with connect(self.db_path) as db:
                db.execute('BEGIN IMMEDIATE')
                claimed=db.execute("UPDATE push_deliveries SET status='sending',attempts=attempts+1 WHERE outbox_id=? AND endpoint=? AND status='pending' AND attempts<3 AND EXISTS(SELECT 1 FROM notification_outbox WHERE id=? AND status='pending')",(row['id'],sub['endpoint'],row['id'])).rowcount
                if claimed:
                    db.execute('UPDATE notification_outbox SET next_attempt=? WHERE id=?',(stamp,row['id']))
                db.commit()
            if not claimed:
                continue
            if self.stop_event.is_set():
                return
            status='sent'; http_status=None
            try:
                self.push.send({'endpoint':sub['endpoint'],'keys':json.loads(sub['keys_json'])},{'title':'一起记','body':'你有待办，请打开一起记','url':'/'})
            except PushError as exc:
                http_status=exc.status
                status='expired' if http_status in (404,410) else ('pending' if http_status is not None else 'uncertain')
            except Exception:
                status='uncertain'
            with connect(self.db_path) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE push_deliveries SET status=? WHERE outbox_id=? AND endpoint=?',(status,row['id'],sub['endpoint']))
                if status=='expired':
                    db.execute('UPDATE push_subscriptions SET active=0 WHERE owner_id=? AND endpoint=?',(row['user_id'],sub['endpoint']))
                db.commit()
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            delivery=list(db.execute('SELECT * FROM push_deliveries WHERE outbox_id=?',(row['id'],)))
            if delivery and all(r['status']=='sent' for r in delivery):
                status='sent'
            elif delivery and any(r['status']=='uncertain' for r in delivery):
                status='uncertain'
            elif delivery and all(r['status'] in ('expired','sent') or r['attempts']>=3 for r in delivery):
                status='failed'
            else:
                status='pending'
            db.execute("UPDATE notification_outbox SET status=?,next_attempt=? WHERE id=? AND status='pending'",(status,stamp+300,row['id']))
            db.commit()

    def _backup(self,now):
        directory=os.getenv('SHARED_EXPENSE_BACKUP_DIR')
        if not directory:
            return
        day=now.astimezone(reports.ZONE).date().isoformat()
        destination=Path(directory)/(day+'-'+str(uuid4()))
        with connect(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            if reports.state(db)['automation_paused']:
                db.commit();return
            claimed=db.execute("INSERT OR IGNORE INTO backup_runs(day,status,destination) VALUES(?,'started',?)",(day,str(destination))).rowcount
            db.commit()
        if not claimed:
            return
        from server.automation.backups import backup
        try:
            backup(self.db_path,destination);status='complete'
        except Exception:
            status='failed'
        with connect(self.db_path) as db:
            db.execute('UPDATE backup_runs SET status=? WHERE day=?',(status,day))
