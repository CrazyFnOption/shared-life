import { expect, test } from 'vitest'
import { applyMemoOperation, newMemoPartition, projectMemos } from '../src/memos/domain'
import type { MemoRecord } from '../src/memos/domain'
import { copyDraft } from '../src/memos/types'
import { currentOccurrenceAt, reminderInstances } from '../src/memos/reminderSlots'
import { memoRecordViews, homeFocusedRecords } from '../src/memos/view'
import { reminderCalendarViews } from '../src/memos/reminderCalendar'
import { schedulesForDay } from '../src/memos/dates'
const source={protocol_version:2 as const,user_id:'fake-user',restore_id:'fake-restore',scope:'private' as const,family_id:null,relation_epoch:0}
function record():MemoRecord{return{id:'fake-record',kind:'memo',scope:'private',family_id:null,author_id:source.user_id,updated_by:source.user_id,category_id:null,title:'虚构多时间',body_markdown:'',attachments:[],completed:false,archived:false,version:1,created_at:'2026-11-01T00:00:00Z',updated_at:'2026-11-01T00:00:00Z',recurrence:{frequency:'weekly',interval:1,calendar:'solar',start_date:'2026-11-01',until:null,lunar_month:null,lunar_day:null,lunar_leap:false,weekdays:[1,2]},reminders:['09:00','21:00'].map((clock,index)=>({id:`fake-clock-${index}`,mode:'absolute',at:`2026-11-01T${clock}:00+08:00`,offset_minutes:0,recipient_ids:[source.user_id],all_members:false}))}}
test('offline 09 completion keeps parent and 21, persists one slot, reopens only that slot',()=>{
 const p=newMemoPartition(source);p.snapshot=[record()];projectMemos(p);p.local_focus={id:'focus',version:1,targets:[{kind:'memo',id:'fake-record'}]}
 p.calendar={'2026-11':{versions:{'fake-record':1},occurrences:reminderInstances(record(),Date.parse('2026-11-16T10:00:00+08:00'))}}
 const operation={op_id:'fake-complete',entity_type:'memo' as const,entity_id:'fake-record',base_version:1,kind:'complete',data:{completed:true,occurrence_at:'2026-11-16T01:00:00Z'}};p.queue=[{source,operation}]
 applyMemoOperation(p,operation,source.user_id,'2026-11-16T10:00:00+08:00')
 expect(p.local[0]).toMatchObject({completed:false,archived:false,reminder_occurrences:[{occurrence_at:'2026-11-16T09:00:00+08:00',completed:true}]})
 expect(p.snapshot[0]!.reminder_occurrences).toBeUndefined()
 const views=memoRecordViews(p,Date.parse('2026-11-16T10:00:00+08:00'))
 expect(homeFocusedRecords(views)).toMatchObject([{current_occurrence_at:'2026-11-16T21:00:00+08:00'}])
 expect(views.filter(row=>row.archived)).toHaveLength(1)
 expect(schedulesForDay(reminderCalendarViews(p,views),'2026-11-16').map(row=>row.start_time)).toEqual(['21:00'])
 applyMemoOperation(p,{...operation,base_version:2,data:{completed:false,occurrence_at:'2026-11-16T09:00:00+08:00'}},source.user_id,'2026-11-16T10:01:00+08:00')
 expect(currentOccurrenceAt(p.local[0]!,Date.parse('2026-11-16T10:01:00+08:00'))).toBe('2026-11-16T09:00:00+08:00')
})
test('new planned day supersedes stale current DTO; activation on same day stays authoritative',()=>{
 const item=record();item.current_occurrence_at='2026-11-16T09:00:00+08:00'
 expect(currentOccurrenceAt(item,Date.parse('2026-11-17T08:00:00+08:00'))).toBe('2026-11-17T09:00:00+08:00')
 item.current_occurrence_at='2026-11-17T21:00:00+08:00'
 expect(currentOccurrenceAt(item,Date.parse('2026-11-17T10:00:00+08:00'))).toBe('2026-11-17T21:00:00+08:00')
})
test('monthly 30 and 31 collapse to one February occurrence at each clock',()=>{
 const item=record();item.recurrence={...item.recurrence!,frequency:'monthly',weekdays:undefined,month_days:[30,31]};const rows=reminderInstances(item,Date.parse('2027-02-28T08:00:00+08:00')).filter(row=>row.date==='2027-02-28')
 expect(rows.map(row=>row.start_time)).toEqual(['09:00','21:00'])
})
test('draft and local projection clone optional arrays and recipient/slot objects without adding arrays to legacy rules',()=>{
 const item=record();item.reminder_occurrences=[{occurrence_at:'2026-11-16T09:00:00+08:00',reminder_id:'fake-clock-0',archived_at:null,completed:false}]
 const p=newMemoPartition(source);p.snapshot=[item];projectMemos(p);p.local[0]!.recurrence!.weekdays!.push(3);p.local[0]!.reminders![0]!.recipient_ids.push('other');p.local[0]!.reminder_occurrences![0]!.completed=true
 expect(item.recurrence!.weekdays).toEqual([1,2]);expect(item.reminders![0]!.recipient_ids).toEqual([source.user_id]);expect(item.reminder_occurrences[0]!.completed).toBe(false)
 const draft={base_version:0,kind:'memo' as const,title:'',body:'',category_id:'',focused:false,start_date:'2026-11-01',end_date:'',start_time:'09:00',end_time:'',all_day:true,has_end:false,attachments:[],recurrence:item.recurrence}
 const copied=copyDraft(draft);copied.recurrence!.weekdays!.push(4);expect(item.recurrence!.weekdays).toEqual([1,2]);delete draft.recurrence!.weekdays;expect(copyDraft(draft).recurrence).not.toHaveProperty('weekdays')
 p.local[0]!.reminders=[];p.local_focus={id:'focus',version:1,targets:[{kind:'memo',id:item.id}]};expect(homeFocusedRecords(memoRecordViews(p))).toHaveLength(1)
})

test('old date-completed 09 stays completed after adding 21 offline, while old history remains',()=>{
 const p=newMemoPartition(source),item=record();item.kind='schedule';item.schedule={start_date:'2026-11-16',end_date:null,start_time:'09:00',end_time:null};item.recurrence={...item.recurrence!,start_date:'2026-11-16'};item.reminders=[{...item.reminders![0]!,at:'2026-11-16T09:00:00+08:00'}];item.archived_occurrences=[{start_date:'2026-11-16',archived_at:'2026-11-16T09:01:00+08:00',completed:true}];p.local=[item]
 const {id,version,created_at,updated_at,scope,family_id,author_id,updated_by,completed,archived,archived_occurrences,...data}=item
 const operation={op_id:'fake-add',entity_type:'memo' as const,entity_id:id,kind:'update',base_version:version,data:{...data,reminders:[...item.reminders,{...item.reminders[0]!,id:'new-21',at:'2026-11-16T21:00:00+08:00'}]}}
 applyMemoOperation(p,operation,source.user_id,'2026-11-16T10:00:00+08:00')
 expect(p.local[0]!.archived_occurrences).toEqual(item.archived_occurrences)
 expect(p.local[0]!.reminder_occurrences).toMatchObject([{occurrence_at:'2026-11-16T09:00:00+08:00',completed:true}])
 expect(currentOccurrenceAt(p.local[0]!,Date.parse('2026-11-16T10:00:00+08:00'))).toBe('2026-11-16T21:00:00+08:00')
})
test('old date-only multi-clock operation uses planned day when a relative reminder appears the previous day',()=>{
 const p=newMemoPartition(source),item=record();item.kind='schedule';item.schedule={start_date:'2026-11-16',end_date:null,start_time:'00:30',end_time:null};item.recurrence={...item.recurrence!,start_date:'2026-11-16'};item.reminders=[{...item.reminders![0]!,mode:'relative',at:null,offset_minutes:60},{...item.reminders![1]!,mode:'relative',at:null,offset_minutes:0}];p.local=[item]
 applyMemoOperation(p,{op_id:'old-date-only',entity_type:'memo',entity_id:item.id,base_version:1,kind:'complete',data:{completed:true,occurrence_date:'2026-11-16'}},source.user_id,'2026-11-16T10:00:00+08:00')
 expect(p.local[0]!.reminder_occurrences).toMatchObject([{occurrence_at:'2026-11-15T23:30:00+08:00',completed:true}]);expect(p.local[0]!.completed).toBe(false)
})
