import type { Source } from '../types'
export interface MemoOccurrence {reminder_id?:string;occurrence_date?:string;occurrence_at?:string;memo_id:string;record_version:number;date:string;start_date:string;end_date:string|null;start_time:string|null;end_time:string|null}
export interface MemoCalendarSnapshot {source:Source;occurrences:MemoOccurrence[];versions:Record<string,number>}
export interface MemoCalendarCache {occurrences:MemoOccurrence[];versions:Record<string,number>}
export function calendarBounds(month:string):{from:string;to:string}{const[year,index]=month.split('-').map(Number);if(!/^\d{4}-\d{2}$/.test(month)||!year||year<1901||year>2099||!index||index<1||index>12)throw new Error('日历支持1901年至2099年。');return{from:month+'-01',to:month+'-'+String(new Date(year,index,0,12).getDate()).padStart(2,'0')}}
/** A late response may fill a month, but cannot replace a newer record's dates. */
export function mergeCalendarCache(before:MemoCalendarCache|undefined,incoming:MemoCalendarSnapshot):MemoCalendarCache{
 const versions={...before?.versions,...incoming.versions}
 for(const[id,version]of Object.entries(before?.versions??{}))versions[id]=Math.max(version,versions[id]??0)
 const occurrences:MemoOccurrence[]=[]
 for(const[id,version]of Object.entries(versions)){
  const source=incoming.versions[id]===version?incoming.occurrences:before?.occurrences??[]
  occurrences.push(...source.filter(o=>o.memo_id===id&&o.record_version===version).map(o=>({...o})))
 }
 return{versions,occurrences}
}
