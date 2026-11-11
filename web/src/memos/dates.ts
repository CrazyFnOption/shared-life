import type { ScheduleView } from './types'

const alphabetic = new Intl.Collator('zh-CN-u-co-pinyin', { sensitivity: 'base', numeric: false })
export function localDay(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
export function dayLabel(day: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date(`${day}T12:00:00`))
}
export function scheduleOnDay(record: ScheduleView, day: string): boolean {
  if(record.archived)return false
  if((record.recurrence||record.calendar_projection)&&!record.occurrence_key)return record.occurrences?.some(occurrence=>occurrence.date===day)??false
  const end = record.end_date ?? record.start_date
  // A timed event ending at midnight does not occupy the following day.
  return record.start_date <= day && (day < end || day === end && (!record.start_time || !record.end_time || record.end_time !== '00:00' || record.start_date === end))
}
export function schedulesForDay(records:ScheduleView[],day:string):ScheduleView[]{
 return records.filter(record=>!record.archived).flatMap(record=>(record.recurrence||record.calendar_projection)&&!record.occurrence_key?(record.occurrences??[]).filter(o=>o.date===day).map(occurrence=>({...record,start_date:occurrence.start_date,end_date:occurrence.end_date,start_time:occurrence.start_time,end_time:occurrence.end_time,occurrence_date:occurrence.occurrence_date??occurrence.start_date,occurrence_key:`${record.id}:${occurrence.reminder_id??occurrence.start_date}:${occurrence.date}`,occurrences:[occurrence]})):scheduleOnDay(record,day)?[record]:[]).sort(compareSchedules)
}
export function compareSchedules(a: ScheduleView, b: ScheduleView): number {
  if (!a.start_time && !b.start_time) return alphabetic.compare(a.title, b.title)
  if (!a.start_time) return -1
  if (!b.start_time) return 1
  return a.start_time.localeCompare(b.start_time)
}
export function scheduleLabel(record: ScheduleView, day?: string): string {
  if (!record.start_time) return '全天'
  const occurrence=day&&record.recurrence?record.occurrences?.find(o=>o.date===day):undefined
  return day && (occurrence?.start_date??record.start_date) < day ? '持续中' : record.start_time
}
export function monthGrid(month: string): (string | null)[] {
  const [year, index] = month.split('-').map(Number)
  const first = new Date(year!, index! - 1, 1, 12)
  const offset = (first.getDay() + 6) % 7
  const length = new Date(year!, index!, 0, 12).getDate()
  return [...Array<null>(offset).fill(null), ...Array.from({ length }, (_, i) => `${month}-${String(i + 1).padStart(2, '0')}`)]
}
export function adjacentMonth(month: string, delta: number): string {
  const [year, index] = month.split('-').map(Number)
  return localDay(new Date(year!, index! - 1 + delta, 1, 12)).slice(0, 7)
}

export interface SimpleRepeatInput {
 frequency:'daily'|'weekly'|'monthly'|'yearly'
 clock:string
 weekday:number
 monthDay:number
 yearMonth:number
 yearDay:number
 calendar:'solar'|'lunar'
}
/** Internal anchors keep the nominal day (31st/leap day), even in short months. */
export function simpleRepeatAnchor(input:SimpleRepeatInput,now=new Date()):string{
 if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(input.clock))throw new Error('请选择提醒时间。')
 const wall=new Date(now.getTime()+8*3600000),year=wall.getUTCFullYear(),month=wall.getUTCMonth(),day=wall.getUTCDate()
 const at=(y:number,m:number,d:number)=>new Date(Date.UTC(y,m,d,Number(input.clock.slice(0,2))-8,Number(input.clock.slice(3))))
 const format=(value:Date)=>new Date(value.getTime()+8*3600000).toISOString().slice(0,10)
 let candidate=at(year,month,day)
 if(input.frequency==='daily'){if(candidate<=now)candidate=at(year,month,day+1)}
 else if(input.frequency==='weekly'){
  if(!Number.isInteger(input.weekday)||input.weekday<0||input.weekday>6)throw new Error('请选择星期。')
  const delta=(input.weekday-wall.getUTCDay()+7)%7;candidate=at(year,month,day+delta)
  if(candidate<=now)candidate=at(year,month,day+delta+7)
 }else if(input.frequency==='monthly'){
  if(!Number.isInteger(input.monthDay)||input.monthDay<1||input.monthDay>31)throw new Error('每月日期须为1至31号。')
  const last=new Date(Date.UTC(year,month+1,0)).getUTCDate(),next=at(year,month,Math.min(input.monthDay,last))
  // The backend's existing monthly rule uses start_date.day. A nominal 31st
  // must remain a 31st; choosing a clamped 30th would corrupt later months.
  let nominalMonth=month
  if(next<=now)nominalMonth++
  while(new Date(Date.UTC(year,nominalMonth+1,0)).getUTCDate()<input.monthDay)nominalMonth--
  candidate=at(year,nominalMonth,input.monthDay)
 }else if(input.calendar==='solar'){
  if(!Number.isInteger(input.yearMonth)||input.yearMonth<1||input.yearMonth>12||!Number.isInteger(input.yearDay)||input.yearDay<1||input.yearDay>new Date(Date.UTC(2000,input.yearMonth,0)).getUTCDate())throw new Error('请核对纪念日的月份和日期。')
  let nominalYear=year
  const last=new Date(Date.UTC(year,input.yearMonth,0)).getUTCDate()
  if(at(year,input.yearMonth-1,Math.min(input.yearDay,last))<=now)nominalYear++
  while(new Date(Date.UTC(nominalYear,input.yearMonth,0)).getUTCDate()<input.yearDay)nominalYear--
  candidate=at(nominalYear,input.yearMonth-1,input.yearDay)
 }
 return `${format(candidate)}T${input.clock}:00+08:00`
}
