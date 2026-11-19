"""Atomic Markdown task saves keep schema13 identities and per-occurrence checks."""
from uuid import uuid4
from datetime import timedelta
from server.accounting.db import connect
from server.memos import calendar
from server.tests.test_multi_family import home,headers
from server.tests.test_memos import op,saved
from server.tests.test_memo_lifecycle import clock


def test_markdown_save_checks_atomically_and_old_omission_preserves_state(home,monkeypatch):
    path,app,(a,b,c)=home;clock(monkeypatch,calendar.anchor('2090-11-05','08:00'))
    items=[{'id':str(uuid4()),'text':text} for text in ('钥匙','车票')]
    data={'title':'正文清单','body_markdown':'- [x] 钥匙\n- [x] 车票','checklist_items':items,'checklist_checks':[{'item_id':item['id'],'checked':True} for item in items]}
    item=saved(op(a,'memo',str(uuid4()),'create',data))
    assert item['checklist']['states']['whole']['checked_ids']==[entry['id'] for entry in items]
    assert not item['completed'] and not item['archived']
    preserved=saved(op(a,'memo',item['id'],'update',{'title':'只改标题','body_markdown':data['body_markdown']},item['version']))
    assert preserved['checklist']==item['checklist']
    wrong=op(a,'memo',item['id'],'update',{**data,'title':'不能保存','checklist_checks':[{'item_id':str(uuid4()),'checked':True}]},preserved['version'])
    assert wrong.status_code==422
    current=a.get('/api/v2/memos/records/'+item['id'],headers=headers(a)).json()['memo']
    assert current==preserved


def test_markdown_cycle_checked_marks_affect_only_original_occurrence(home,monkeypatch):
    path,app,(a,b,c)=home;at=calendar.anchor('2090-11-05','09:00');clock(monkeypatch,at-timedelta(hours=1))
    key=str(uuid4());rule=str(uuid4());data={'title':'每日清单','body_markdown':'- [x] 钥匙','checklist_items':[{'id':key,'text':'钥匙'}],'checklist_checks':[{'item_id':key,'checked':True}],'reminders':[{'id':rule,'at':at.isoformat()}],'recurrence':{'frequency':'daily','start_date':at.date().isoformat()}}
    item=saved(op(a,'memo',str(uuid4()),'create',data));assert item['checklist']['states'][at.isoformat()]['checked_ids']==[key]
    done=saved(op(a,'memo',item['id'],'complete',{'completed':True,'occurrence_at':at.isoformat()},item['version']))
    # Editing future template text without explicit marker changes never seeds its checks.
    renamed=saved(op(a,'memo',item['id'],'update',{**{k:v for k,v in data.items() if k!='checklist_checks'},'body_markdown':'- [ ] 备用钥匙','checklist_items':[{'id':key,'text':'备用钥匙'}]},done['version']))
    assert renamed['checklist']['states'][at.isoformat()]['items_snapshot'][0]['text']=='钥匙'
    assert set(renamed['checklist']['states'])=={at.isoformat()}
    # Save cannot bypass the normal archived occurrence guard; the full edit rolls back.
    rejected=op(a,'memo',item['id'],'update',{**data,'title':'不应保存','checklist_occurrence_at':at.isoformat()},renamed['version'])
    assert rejected.status_code==409
    assert a.get('/api/v2/memos/records/'+item['id'],headers=headers(a)).json()['memo']['title']=='每日清单'
