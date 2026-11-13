import type { MemoRecord, MemoRecurrence, MemoReminderOccurrence } from './domain'
import type { MemoOccurrence } from './calendarCache'

const DAY=86400000
export function occurrenceAt(value:string):string{
 const instant=Date.parse(value)
 return Number.isFinite(instant)?new Date(instant+8*3600000).toISOString().slice(0,19)+'+08:00':''
}
export function occurrenceLabel(value:string):string{
 return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))
}
function dayNumber(day:string){return Date.parse(day+'T00:00:00Z')/DAY}
function plannedOn(rule:MemoRecurrence,day:string):boolean{
 if(day<rule.start_date||rule.until&&day>rule.until)return false
 const origin=new Date(rule.start_date+'T00:00:00Z'),date=new Date(day+'T00:00:00Z'),difference=dayNumber(day)-dayNumber(rule.start_date)
 if(rule.frequency==='daily')return difference%rule.interval===0
 if(rule.frequency==='weekly'){
  if(!rule.weekdays?.length)return difference%(7*rule.interval)===0
  const originMonday=dayNumber(rule.start_date)-((origin.getUTCDay()+6)%7)
  return rule.weekdays.includes(date.getUTCDay()||7)&&Math.floor((dayNumber(day)-originMonday)/7)%rule.interval===0
 }
 const last=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate()
 if(rule.frequency==='monthly')return ((date.getUTCFullYear()-origin.getUTCFullYear())*12+date.getUTCMonth()-origin.getUTCMonth())%rule.interval===0&&(rule.month_days??[origin.getUTCDate()]).some(wanted=>date.getUTCDate()===Math.min(wanted,last))
 return (date.getUTCFullYear()-origin.getUTCFullYear())%rule.interval===0&&date.getUTCMonth()===origin.getUTCMonth()&&date.getUTCDate()===Math.min(origin.getUTCDate(),last)
}
/** Solar offline projection shares the stored rule; lunar dates use server cache. */
export function reminderInstances(record:MemoRecord,now=Date.now(),cached:MemoOccurrence[]=[],effectiveFrom?:string):MemoOccurrence[]{
 const rule=record.recurrence
 if(rule?.calendar==='lunar')return cached.map(row=>({...row,occurrence_at:row.occurrence_at??occurrenceAt(`${row.date}T${row.start_time??'00:00'}:00+08:00`)}))
 const today=dayNumber(new Date(now+8*3600000).toISOString().slice(0,10))
 const span=rule?({daily:1,weekly:7,monthly:32,yearly:366}[rule.frequency]*(rule.interval+1)):0
 const earliest=rule?Math.max(dayNumber('1901-01-01'),today-span):dayNumber('1901-01-01')
 const latest=rule?Math.min(dayNumber('2099-12-31'),today+span):dayNumber('2099-12-31')
 const rows:MemoOccurrence[]=[]
 const bound=effectiveFrom?Date.parse(effectiveFrom):Date.parse(record.created_at)
 for(const reminder of record.reminders??[]){
  const initial=reminder.mode==='absolute'?Date.parse(reminder.at??''):record.schedule?Date.parse(`${record.schedule.start_date}T${record.schedule.start_time??'00:00'}:00+08:00`)-reminder.offset_minutes*60000:NaN
  if(!Number.isFinite(initial))continue
  const wall=new Date(initial+8*3600000).toISOString(),firstDay=dayNumber(wall.slice(0,10)),origin=rule?dayNumber(rule.start_date):reminder.mode==='relative'?dayNumber(record.schedule!.start_date):firstDay,shift=firstDay-origin
  const start=rule?earliest:firstDay,end=rule?latest:firstDay
  for(let actual=start;actual<=end;actual++){
   const planned=new Date((actual-shift)*DAY).toISOString().slice(0,10)
   if(rule&&!plannedOn(rule,planned)||record.kind==='schedule'&&(record.reminders?.length??0)<=1&&record.archived_occurrences?.some(entry=>entry.start_date===planned&&entry.archived_at))continue
   const instant=initial+(actual-firstDay)*DAY
   if(rule&&instant<bound)continue
   const at=occurrenceAt(new Date(instant).toISOString()),day=at.slice(0,10)
   if(record.completed&&rule&&day<=occurrenceAt(record.completed_at??record.updated_at).slice(0,10))continue
   rows.push({memo_id:record.id,record_version:record.version,date:day,start_date:day,end_date:null,start_time:at.slice(11,16),end_time:null,reminder_id:reminder.id,occurrence_date:planned,occurrence_at:at})
  }
 }
 return rows.sort((a,b)=>a.occurrence_at!.localeCompare(b.occurrence_at!)||(a.reminder_id??'').localeCompare(b.reminder_id??''))
}
export function currentOccurrenceAt(record:MemoRecord,now=Date.now(),cached:MemoOccurrence[]=[],effectiveFrom?:string):string|null{
 if(record.deleted||record.archived&&!record.completed||record.completed&&!record.recurrence)return null
 const finished=new Set((record.reminder_occurrences??[]).filter(row=>row.archived_at).map(row=>occurrenceAt(row.occurrence_at)))
 const confirmed=record.current_occurrence_at&&!finished.has(occurrenceAt(record.current_occurrence_at))?occurrenceAt(record.current_occurrence_at):null
 const today=new Date(now+8*3600000).toISOString().slice(0,10),latest=new Map<string,MemoOccurrence>(),future=new Map<string,MemoOccurrence>()
 const rows=reminderInstances(record,now,cached,effectiveFrom)
 // Completed lunar slots remain relevant even after the calendar hides them.
 if(record.recurrence?.calendar==='lunar')for(const slot of record.reminder_occurrences??[]){const at=occurrenceAt(slot.occurrence_at);rows.push({memo_id:record.id,record_version:record.version,date:at.slice(0,10),start_date:at.slice(0,10),end_date:null,start_time:at.slice(11,16),end_time:null,reminder_id:slot.reminder_id,occurrence_at:at})}
 rows.sort((a,b)=>(a.occurrence_at??'').localeCompare(b.occurrence_at??''))
 for(const row of rows){const id=row.reminder_id??'';if(row.date<=today)latest.set(id,row);else if(!future.has(id))future.set(id,row)}
 const candidates:MemoOccurrence[]=[]
 for(const[id,row]of latest){const candidate=finished.has(occurrenceAt(row.occurrence_at!))?future.get(id):row;if(candidate&&!finished.has(occurrenceAt(candidate.occurrence_at!)))candidates.push(candidate)}
 for(const[id,row]of future)if(!latest.has(id)&&!finished.has(occurrenceAt(row.occurrence_at!)))candidates.push(row)
 const due=candidates.filter(row=>Date.parse(row.occurrence_at!)<=now)
 const resolved=(due.length?due:candidates).sort((a,b)=>a.occurrence_at!.localeCompare(b.occurrence_at!))[0]?.occurrence_at??null
 // Keep the server's activation boundary, while allowing offline cycle rollover.
 return confirmed&&(!resolved||confirmed.slice(0,10)>=resolved.slice(0,10))?confirmed:resolved
}

/** Keep old completed instants when an old single-clock schedule gains clocks. */
export function legacyReminderHistory(record:MemoRecord):MemoReminderOccurrence[]{
 if(record.kind!=='schedule'||!record.recurrence||record.reminders?.length!==1)return[]
 const reminder=record.reminders[0]!,history:MemoReminderOccurrence[]=[]
 for(const entry of record.archived_occurrences??[]){
  if(!entry.archived_at)continue
  let instant:number
  if(reminder.mode==='relative'){const schedule=entry.schedule??record.schedule!;const day=entry.schedule?.start_date??entry.start_date;instant=Date.parse(`${day}T${schedule.start_time??'00:00'}:00+08:00`)-reminder.offset_minutes*60000}
  else instant=Date.parse(reminder.at!)+(dayNumber(entry.start_date)-dayNumber(record.recurrence.start_date))*DAY
  history.push({occurrence_at:occurrenceAt(new Date(instant).toISOString()),reminder_id:reminder.id,archived_at:entry.archived_at,completed:entry.completed})
 }
 return history
}
