import { expect, test } from 'vitest'
import { applyMemoOperation, newMemoPartition } from '../src/memos/domain'
import type { MemoRecord } from '../src/memos/domain'
import { reminderCalendarViews } from '../src/memos/reminderCalendar'
import { memoRecordViews } from '../src/memos/view'
import { schedulesForDay } from '../src/memos/dates'
const source={protocol_version:2 as const,user_id:'fictional-user',restore_id:'fictional-restore',scope:'private' as const,family_id:null,relation_epoch:0}
function record():MemoRecord{return{id:'fictional-memo',kind:'memo',scope:'private',family_id:null,author_id:source.user_id,updated_by:source.user_id,category_id:null,title:'虚构备忘',body_markdown:'',attachments:[],completed:false,version:1,created_at:'2026-11-11T00:00:00Z',updated_at:'2026-11-11T00:00:00Z'}}
const reminder={id:'fictional-reminder',mode:'absolute' as const,at:'2026-11-11T07:30:00Z',offset_minutes:0,recipient_ids:[source.user_id],all_members:false}
test('标题备忘不出日历；明确提醒以提醒日时显示，旧日程本身不再产生日期',()=>{
 const p=newMemoPartition(source);p.local=[record(),{...record(),id:'legacy',kind:'schedule',schedule:{start_date:'2026-11-11',end_date:null,start_time:'09:00',end_time:null}}]
 expect(reminderCalendarViews(p,memoRecordViews(p))).toEqual([])
 p.local[0]!.reminders=[reminder]
 const rows=reminderCalendarViews(p,memoRecordViews(p))
 expect(schedulesForDay(rows,'2026-11-11')).toMatchObject([{id:'fictional-memo',start_time:'15:30',occurrence_date:'2026-11-11'}])
 expect(schedulesForDay(rows,'2026-11-12')).toEqual([])
})
test('旧跨日相对提醒按换算时刻投影，不按跨日原区间展开',()=>{
 const p=newMemoPartition(source);p.local=[{...record(),kind:'schedule',schedule:{start_date:'2026-11-12',end_date:'2026-11-14',start_time:'00:30',end_time:'11:00'},reminders:[{...reminder,mode:'relative',at:null,offset_minutes:60}]}]
 const rows=reminderCalendarViews(p,memoRecordViews(p))
 expect(schedulesForDay(rows,'2026-11-11')).toMatchObject([{start_time:'23:30',occurrence_date:'2026-11-12'}])
 expect(schedulesForDay(rows,'2026-11-12')).toEqual([])
})
test('循环备忘当天提前完成，本期不再出现，缓存中的未来周期仍显示',()=>{
 const p=newMemoPartition(source),item=record();item.reminders=[reminder];item.recurrence={frequency:'daily',interval:1,calendar:'solar',start_date:'2026-11-11',until:null,lunar_month:null,lunar_day:null,lunar_leap:false};item.completed=true;item.archived=true;item.completed_at='2026-11-11T00:00:00Z';p.local=[item]
 p.calendar={'2026-11':{versions:{[item.id]:1},occurrences:['2026-11-11','2026-11-12'].map(day=>({memo_id:item.id,record_version:1,date:day,start_date:day,end_date:null,start_time:'15:30',end_time:null,reminder_id:reminder.id,occurrence_date:day}))}}
 const rows=reminderCalendarViews(p,memoRecordViews(p))
 expect(schedulesForDay(rows,'2026-11-11')).toEqual([])
 expect(schedulesForDay(rows,'2026-11-12')).toHaveLength(1)
 p.local[0]!.version=2
 expect(schedulesForDay(reminderCalendarViews(p,memoRecordViews(p)),'2026-11-12')).toEqual([])
})

test('旧离线单次完成按计划日匹配，提前到前一天的提醒退出日历，下周期保留',()=>{
 const p=newMemoPartition(source),item:MemoRecord={...record(),kind:'schedule',schedule:{start_date:'2026-11-12',end_date:null,start_time:'00:30',end_time:null},reminders:[{...reminder,mode:'relative',at:null,offset_minutes:60}],recurrence:{frequency:'weekly',interval:1,calendar:'solar',start_date:'2026-11-12',until:null,lunar_month:null,lunar_day:null,lunar_leap:false}};p.local=[item];p.snapshot=[{...item}];p.calendar={'2026-11':{versions:{[item.id]:1},occurrences:[['2026-11-11','2026-11-12'],['2026-11-18','2026-11-19']].map(([shown,planned])=>({memo_id:item.id,record_version:1,date:shown!,start_date:shown!,end_date:null,start_time:'23:30',end_time:null,occurrence_date:planned!,reminder_id:reminder.id}))}}
 const operation={op_id:'fictional-old-op',entity_type:'memo' as const,entity_id:item.id,kind:'complete',base_version:1,data:{completed:true,occurrence_date:'2026-11-12'}};p.queue=[{source,operation}];applyMemoOperation(p,operation,source.user_id)
 const records=memoRecordViews(p),rows=reminderCalendarViews(p,records)
 expect((records[0] as any).occurrences).toHaveLength(1)
 expect(schedulesForDay(rows,'2026-11-11')).toEqual([])
 expect(schedulesForDay(rows,'2026-11-18')).toHaveLength(1)
})
