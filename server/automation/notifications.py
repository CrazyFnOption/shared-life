"""Report/review links and current-scope inbox projections over the existing outbox."""
from datetime import datetime,UTC
from urllib.parse import urlencode
from server.accounting import families
from . import reports


def same_source(row,src):
    return row['source_status']=='verified' and row['source_restore_id']==src['restore_id'] and row['source_scope']==src['scope'] and row['family_id']==src['family_id'] and (row['relation_epoch'] or 0)==src['relation_epoch']


def unrated(db,user_id,src):
    return db.execute("SELECT 1 FROM expenses WHERE owner_id=? AND scope=? AND family_id IS ? AND voided=0 AND rating IS NULL LIMIT 1",(user_id,src['scope'],src['family_id'])).fetchone() is not None


def report_message(report,src):
    return {'id':f'report:{report["id"]}:{report["data_version"]}','kind':'monthly_report','target_type':'monthly_report','target_id':report['id'],'record_id':report['id'],'title':f'{report["month"]} 月度复盘','created_at':report['generated_at'] or '', 'source':src}


def weekly_message(row,src):
    namespace=src['family_id'] or src['user_id']
    return {'id':f'{row["dedup_key"]}:{src["scope"]}:{namespace}','kind':'weekly_review','target_type':'weekly_review','target_id':row['id'],'record_id':row['id'],'title':'本人待补评价','created_at':datetime.fromtimestamp(row['next_attempt'],UTC).isoformat(),'source':src}


def weekly_row(db,user_id,restore_id):
    return db.execute("SELECT * FROM notification_outbox WHERE user_id=? AND source_scope='account' AND source_status='verified' AND source_restore_id=? AND dedup_key LIKE 'weekly:%' ORDER BY dedup_key DESC LIMIT 1",(user_id,restore_id)).fetchone()


def messages(db,user_id,src):
    result=[]
    for row in db.execute('SELECT * FROM notification_outbox WHERE user_id=? AND report_id IS NOT NULL',(user_id,)):
        if not same_source(row,src):continue
        report=db.execute('SELECT * FROM reports WHERE id=?',(row['report_id'],)).fetchone()
        owner=src['family_id'] if src['scope']=='shared' else user_id
        if report and report['scope']==src['scope'] and report['owner_id']==owner and report['status']=='ready' and report['data_version']==row['data_version']:
            result.append(report_message(report,src))
    row=weekly_row(db,user_id,src['restore_id'])
    if row and unrated(db,user_id,src):result.append(weekly_message(row,src))
    return result


def payload(db,row):
    if row['dedup_key'].startswith('memo:'):
        from server.memos.notifications import payload as memo_payload
        return {**memo_payload(row),'kind':'memo_reminder'}
    kind='monthly_report' if row['report_id'] else 'weekly_review'
    src={'scope':row['source_scope'],'family_id':row['family_id'],'relation_epoch':row['relation_epoch'] or 0}
    if kind=='weekly_review':
        # A weekly reminder is account-wide; the link is a source hint, never permission to switch it.
        src=None
        for item in db.execute("SELECT DISTINCT scope,family_id FROM expenses WHERE owner_id=? AND voided=0 AND rating IS NULL ORDER BY scope,family_id",(row['user_id'],)):
            if item['scope']=='private':src={'scope':'private','family_id':None,'relation_epoch':0};break
            member=families.relation(db,row['user_id'],item['family_id'])
            if member and db.execute('SELECT 1 FROM families WHERE id=? AND archived=0',(item['family_id'],)).fetchone():
                src={'scope':'shared','family_id':item['family_id'],'relation_epoch':member['relation_epoch']};break
        if src is None:return None
    params={'notification':row['id'],'kind':kind,'scope':src['scope'],'family_id':src['family_id'] or 'none','user_id':row['user_id'],'restore_id':row['source_restore_id'],'relation_epoch':src['relation_epoch']}
    return {'title':'同在','kind':kind,'body':'月度复盘已生成，请打开查看' if kind=='monthly_report' else '你有待补评价，请打开查看','url':'/?'+urlencode(params)}


def resolve(db,user_id,row,src):
    from server.memos.service import fail
    if row['source_status']!='verified' or row['source_restore_id']!=src['restore_id']:fail('notification_unavailable',404)
    if row['report_id']:
        if not same_source(row,src):fail('notification_unavailable',404)
        report=reports.authorize(db,row['report_id'],user_id)
        notice='报告已更新，现展示当前版本。' if report['data_version']!=row['data_version'] else ''
        if report['status']!='ready':notice='报告正在更新或暂未完成，现展示当前状态。'
        return {'source':src,'message':report_message(report,src),'notice':notice}
    if row['source_scope']=='account' and row['dedup_key'].startswith('weekly:'):
        if unrated(db,user_id,src):return {'source':src,'message':weekly_message(row,src)}
        other=bool(reports.has_unrated_expenses(db,user_id))
        return {'source':src,'message':None,'choose_scope':other,'notice':'其他范围还有待补评价，请选择要查看的范围。' if other else '待补评价已全部处理，当前没有待处理事项。'}
    fail('notification_unavailable',404)
