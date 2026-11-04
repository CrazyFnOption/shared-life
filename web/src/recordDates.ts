import type { Expense } from './types'
const dayFormat=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'})
const clockFormat=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'})
export function shanghaiDay(iso:string):string {const parts=dayFormat.formatToParts(new Date(iso));return ['year','month','day'].map(type=>parts.find(p=>p.type===type)!.value).join('-')}
export function shanghaiClock(iso:string):string {return clockFormat.format(new Date(iso))}
export interface CalendarDate { value:string; day:number; inMonth:boolean; future:boolean; hasRecords:boolean; disabled:boolean; selected:boolean }
export function monthDates(month:string,records:Pick<Expense,'occurred_at'>[],now=new Date(),selected=''):CalendarDate[]{
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month))return []
  const [year,number]=month.split('-').map(Number)
  const first=new Date(Date.UTC(year!,number!-1,1)),offset=(first.getUTCDay()+6)%7
  const count=new Date(Date.UTC(year!,number!,0)).getUTCDate(),today=shanghaiDay(now.toISOString())
  const days=new Set(records.map(e=>shanghaiDay(e.occurred_at)))
  return Array.from({length:Math.ceil((offset+count)/7)*7},(_,i)=>{
    const date=new Date(Date.UTC(year!,number!-1,i-offset+1)),value=date.toISOString().slice(0,10)
    const inMonth=value.startsWith(month+'-'),future=value>today,hasRecords=inMonth&&days.has(value)
    return {value,day:date.getUTCDate(),inMonth,future,hasRecords,disabled:!inMonth||future||!hasRecords,selected:inMonth&&value===selected}
  })
}
