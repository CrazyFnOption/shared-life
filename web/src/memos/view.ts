import { currentOccurrenceAt, occurrenceAt } from './reminderSlots'
import type { MemoPartition, MemoOccurrenceState, MemoSchedule } from './domain'
import type { MemoRecordView, ScheduleView } from './types'

/** All schedule clocks use the product's Asia/Shanghai calendar. */
export function scheduleDeadline(schedule:MemoSchedule,occurrence=schedule.start_date):number{
 const shift=Date.parse(occurrence+'T00:00:00+08:00')-Date.parse(schedule.start_date+'T00:00:00+08:00')
 const end=Date.parse((schedule.end_date??schedule.start_date)+'T'+(schedule.end_date&&schedule.end_time?schedule.end_time+':00':'00:00:00')+'+08:00')+shift
 return end+(schedule.end_date&&schedule.end_time?0:86400000)
}
export function compareArchived(a:MemoRecordView,b:MemoRecordView):number{
 return (b.archived_at?Date.parse(b.archived_at):0)-(a.archived_at?Date.parse(a.archived_at):0)||a.id.localeCompare(b.id)||(a.kind==='schedule'?a.occurrence_date??'':'').localeCompare(b.kind==='schedule'?b.occurrence_date??'':'')
}
export function archivePage<T>(records:T[],page:number,size=20):T[]{return records.slice((Math.max(1,page)-1)*size,Math.max(1,page)*size)}
export function occurrenceView(record:ScheduleView,day:string):ScheduleView{
 const span=Date.parse(record.end_date??record.start_date)-Date.parse(record.start_date)
 return {...record,start_date:day,end_date:record.end_date?new Date(Date.parse(day)+span).toISOString().slice(0,10):null,occurrence_date:day,occurrence_key:`${record.id}:${day}`}
}
export function memoRecordViews(partition:MemoPartition|undefined,now=Date.now()):MemoRecordView[]{
 if(!partition)return[]
 return partition.local.filter(record=>!record.deleted).flatMap((record):MemoRecordView[]=>{
  const focus=partition.local_focus?.targets??[],position=focus.findIndex(t=>t.kind==='memo'&&t.id===record.id)
  const completed=record.completed,legacy=record.kind!=='schedule'&&completed
  const archived=!!record.archived||legacy
  const archivedAt=record.archived_at??null
  const currentAt=currentOccurrenceAt(record,now,Object.values(partition.calendar??{}).flatMap(cache=>cache.occurrences.filter(row=>row.memo_id===record.id)))
  const common={current_occurrence_at:currentAt,category_id:record.category_id??'',focused:position>=0,focus_position:position,id:record.id,version:record.version,title:record.title,created_at:record.created_at,body:record.body_markdown,creator_id:record.author_id,creator_name:partition.users.find(u=>u.id===record.author_id)?.display_name??'原记录人',completed,archived,archived_at:archivedAt,archived_time_unknown:archived&&!archivedAt,can_edit:!archived,can_delete:record.author_id===partition.source.user_id,reminders:record.reminders??[],recurrence:record.recurrence??null,attachments:record.attachments.map(file=>{const local=partition.blobs.find(b=>b.id===file.id&&!b.frozen&&!b.restore_review),meta=partition.attachments.find(a=>a.id===file.id);return{id:file.id,kind:file.kind,sha256:file.sha256,file_name:file.name,mime_type:file.mime_type,size_bytes:file.size_bytes,blob:local?.blob,pending:local?.pending??meta?.state==='pending',error:local?.error}}),pending:partition.queue.some(q=>q.operation.entity_id===record.id),error:partition.queue.find(q=>q.operation.entity_id===record.id&&q.error)?.error}
  const slotHistory:MemoRecordView[]=(record.reminder_occurrences??[]).filter(slot=>slot.archived_at).map(slot=>({...common,kind:'memo' as const,category_id:record.category_id??'',focused:false,focus_position:-1,occurrence_key:`${record.id}:${occurrenceAt(slot.occurrence_at)}`,occurrence_at:occurrenceAt(slot.occurrence_at),current_occurrence_at:occurrenceAt(slot.occurrence_at),archived:true,completed:slot.completed,archived_at:slot.archived_at,archived_time_unknown:false,can_edit:false}))
  if(record.kind==='schedule'&&record.schedule){
   const pending=partition.queue.filter(q=>q.operation.entity_type==='memo'&&q.operation.entity_id===record.id),snapshot=partition.snapshot.find(m=>m.id===record.id)
   const calendarVersion=pending.length&&pending.every(q=>['complete','archive'].includes(q.operation.kind))?snapshot?.version??record.version:record.version
   const months=Object.entries(partition.calendar??{}).filter(([,cache])=>cache.versions[record.id]===calendarVersion)
   const entries=new Map<string,MemoOccurrenceState>((record.archived_occurrences??[]).map(e=>[e.start_date,{...e}]))
   // Offline expiration is a display projection. The existing worker persists
   // authoritative transitions on the next sync; no offline timer writes data.
   const occurrences=months.flatMap(([,cache])=>cache.occurrences.filter(o=>o.memo_id===record.id&&o.record_version===calendarVersion))
   // A restored occurrence can outlive the series rule that originally
   // produced it. Keep its saved interval reachable when the new calendar
   // cannot represent that same occurrence; matching cached dates stay owned
   // by the series so ordinary restores do not create a duplicate row.
   const restored=[...entries.values()].filter(entry=>!entry.archived_at&&entry.suppressed&&entry.schedule&&!occurrences.some(item=>item.start_date===entry.start_date&&item.end_date===entry.schedule!.end_date&&item.start_time===entry.schedule!.start_time&&item.end_time===entry.schedule!.end_time))
   const restoredDays=new Set(restored.map(entry=>entry.start_date))
   const base:ScheduleView={...common,kind:'schedule',...record.schedule,series_schedule:record.schedule,auto_archive_suppressed:record.auto_archive_suppressed,occurrences:occurrences.filter(o=>((record.reminders?.length??0)>1||!entries.get(o.occurrence_date??o.start_date)?.archived_at)&&!restoredDays.has(o.occurrence_date??o.start_date)),occurrence_months:months.map(([month])=>month)}
   const history=[...entries.values()].filter(e=>e.archived_at&&(record.reminders?.length??0)<=1).map(entry=>({...occurrenceView(base,entry.start_date),...(entry.schedule??{}),archived:true,archived_at:entry.archived_at,completed:entry.completed,can_edit:false}))
   const first=base.occurrences?.filter(o=>scheduleDeadline(record.schedule!,o.start_date)>now).sort((a,b)=>a.start_date.localeCompare(b.start_date))[0]
   const current=record.recurrence&&first?{...occurrenceView(base,first.occurrence_date??first.start_date),occurrence_key:undefined}:base
   const reopened=restored.map(entry=>({...occurrenceView(base,entry.start_date),...entry.schedule!,archived:false,archived_at:null,archived_time_unknown:false,completed:false,auto_archive_suppressed:true,occurrences:[],can_edit:false}))
   return[current,...history,...reopened,...slotHistory]
  }
  return[{...common,kind:'memo',category_id:record.category_id??'',focused:position>=0,focus_position:position},...slotHistory]
 })
}

/** Creation time belongs to the original record, independent of focus or edits. */
export function homeFocusedRecords(records:MemoRecordView[]):MemoRecordView[]{
 return records.filter(record=>record.focused&&!record.completed&&!record.archived).sort((a,b)=>(Date.parse(b.created_at??'')||0)-(Date.parse(a.created_at??'')||0)||a.id.localeCompare(b.id))
}
