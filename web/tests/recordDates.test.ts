import {expect,test} from 'vitest'
import {monthDates,shanghaiDay,shanghaiClock} from '../src/recordDates'
const now=new Date('2026-11-01T00:00:00Z')
test('上海跨日和午夜以24小时HH:mm展示',()=>{
 expect(shanghaiDay('2026-10-31T16:05:00Z')).toBe('2026-11-01')
 expect(shanghaiClock('2026-10-31T16:05:00Z')).toBe('00:05')
 expect(shanghaiClock('2026-11-01T15:59:00Z')).toBe('23:59')
})
test('周一开头月历：月初星期及相邻月补位，完整末周',()=>{
 const cells=monthDates('2026-10',[],now)
 expect(cells).toHaveLength(35)
 expect(cells.slice(0,7).map(c=>c.value)).toEqual(['2026-09-28','2026-09-29','2026-09-30','2026-10-01','2026-10-02','2026-10-03','2026-10-04'])
 expect(cells.at(-1)?.value).toBe('2026-11-01')
 expect(cells.filter(c=>!c.inMonth).every(c=>c.disabled)).toBe(true)
 const monday=monthDates('2026-06',[],now);expect(monday[0]?.value).toBe('2026-06-01')
 const sunday=monthDates('2026-11',[],now);expect(sunday.findIndex(c=>c.value==='2026-11-01')).toBe(6)
})
test('只有上海已到且符合当前记录的日期可点，未来有记录和过去无记录均禁用',()=>{
 const days=monthDates('2026-11',[{occurred_at:'2026-10-31T16:01:00Z'},{occurred_at:'2026-11-02T00:00:00Z'}],now,'2026-11-01')
 expect(days.find(d=>d.value==='2026-11-01')).toMatchObject({day:1,inMonth:true,future:false,hasRecords:true,disabled:false,selected:true})
 expect(days.find(d=>d.value==='2026-11-02')).toMatchObject({future:true,hasRecords:true,disabled:true})
 const past=monthDates('2026-10',[{occurred_at:'2026-10-15T00:00:00Z'},{occurred_at:'2026-09-30T00:00:00Z'}],now,'2026-10-15')
 expect(past.filter(d=>!d.disabled).map(d=>d.value)).toEqual(['2026-10-15'])
 expect(past.find(d=>d.value==='2026-09-30')).toMatchObject({inMonth:false,hasRecords:false,disabled:true})
 expect(past.find(d=>d.value==='2026-10-14')).toMatchObject({future:false,hasRecords:false,disabled:true,selected:false})
})
test('上海跨午夜展开刷新今日后可选；闰年和月底完整，空月份不构建',()=>{
 const records=[{occurred_at:'2026-11-01T16:00:00Z'}]
 expect(monthDates('2026-11',records,new Date('2026-11-01T15:59:00Z')).find(d=>d.value==='2026-11-02')?.disabled).toBe(true)
 expect(monthDates('2026-11',records,new Date('2026-11-01T16:00:00Z')).find(d=>d.value==='2026-11-02')?.disabled).toBe(false)
 expect(monthDates('2024-02',[],now).filter(c=>c.inMonth)).toHaveLength(29)
 expect(monthDates('2026-02',[],now).filter(c=>c.inMonth)).toHaveLength(28)
 expect(monthDates('2024-02',[],now).find(c=>c.value==='2024-02-29')?.day).toBe(29)
 expect(monthDates('',[])).toEqual([]);expect(monthDates('2026-13',[])).toEqual([])
})
