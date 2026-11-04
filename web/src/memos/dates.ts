import type { ScheduleView } from './types'

const alphabetic = new Intl.Collator('zh-CN-u-co-pinyin', { sensitivity: 'base', numeric: false })
export function localDay(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
export function dayLabel(day: string): string {
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date(`${day}T12:00:00`))
}
export function scheduleOnDay(record: ScheduleView, day: string): boolean {
  if(record.recurrence)return record.occurrences?.some(occurrence=>occurrence.date===day)??false
  const end = record.end_date ?? record.start_date
  // A timed event ending at midnight does not occupy the following day.
  return record.start_date <= day && (day < end || day === end && (!record.start_time || !record.end_time || record.end_time !== '00:00' || record.start_date === end))
}
export function schedulesForDay(records:ScheduleView[],day:string):ScheduleView[]{
 return records.filter(record=>!record.archived).flatMap(record=>record.recurrence?(record.occurrences??[]).filter(o=>o.date===day).map(occurrence=>({...record,start_date:occurrence.start_date,end_date:occurrence.end_date,start_time:occurrence.start_time,end_time:occurrence.end_time,occurrence_key:`${record.id}:${occurrence.start_date}:${occurrence.date}`,occurrences:[occurrence]})):scheduleOnDay(record,day)?[record]:[]).sort(compareSchedules)
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
