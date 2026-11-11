import type { MemoPartition } from './domain'
import type { MemoOccurrence } from './calendarCache'
import type { MemoRecordView, ScheduleView } from './types'

/** Calendar rows are reminders; storage kind remains an implementation detail. */
export function reminderCalendarViews(partition:MemoPartition|undefined,records:MemoRecordView[]):ScheduleView[]{
 if(!partition)return[]
 return records.filter(record=>(!record.archived||record.completed&&record.recurrence)&&!(record.kind==='schedule'&&record.occurrence_key)).flatMap(record=>{
  const raw=partition.local.find(item=>item.id===record.id)
  if(!raw?.reminders?.length)return[]
  const pending=partition.queue.filter(q=>q.operation.entity_type==='memo'&&q.operation.entity_id===record.id)
  const version=pending.length&&pending.every(q=>['complete','archive'].includes(q.operation.kind))?partition.snapshot.find(item=>item.id===record.id)?.version??record.version:record.version
  const months=Object.entries(partition.calendar??{}).filter(([,cache])=>cache.versions[record.id]===version)
  let occurrences=months.flatMap(([,cache])=>cache.occurrences.filter(item=>item.memo_id===record.id&&item.record_version===version))
  if(record.completed)occurrences=occurrences.filter(item=>item.date>new Date(Date.parse(raw.completed_at??raw.updated_at)+8*3600000).toISOString().slice(0,10))
  // Nonrepeating reminders also work before the first online calendar fetch.
  if(!record.recurrence){occurrences=raw.reminders.flatMap(reminder=>{
   const instant=reminder.mode==='absolute'?Date.parse(reminder.at??''):raw.schedule?Date.parse(`${raw.schedule.start_date}T${raw.schedule.start_time??'00:00'}:00+08:00`)-reminder.offset_minutes*60000:NaN
   if(!Number.isFinite(instant))return[]
   const local=new Date(instant+8*3600000).toISOString(),day=local.slice(0,10)
   return[{memo_id:record.id,record_version:record.version,date:day,start_date:day,end_date:null,start_time:local.slice(11,16),end_time:null,reminder_id:reminder.id,occurrence_date:raw.schedule?.start_date??day} satisfies MemoOccurrence]
  })}
  occurrences=occurrences.filter(item=>!raw.archived_occurrences?.some(entry=>entry.start_date===(item.occurrence_date??item.start_date)&&entry.archived_at))
  const first=occurrences[0]
  return[{...record,archived:false,completed:false,kind:'schedule' as const,start_date:first?.start_date??'',end_date:null,start_time:first?.start_time??null,end_time:null,occurrences,occurrence_months:months.map(([month])=>month),calendar_projection:true}]
 })
}
