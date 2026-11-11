import { expect, test } from 'vitest'
import { simpleRepeatAnchor, type SimpleRepeatInput } from '../src/memos/dates'
const input:SimpleRepeatInput={frequency:'daily',clock:'09:30',weekday:3,monthDay:12,yearMonth:11,yearDay:12,calendar:'solar'}
const now=new Date('2026-11-11T12:00:00+08:00')
test('每天与每周只从所选时钟/星期计算下一提醒',()=>{
 expect(simpleRepeatAnchor(input,now)).toBe('2026-11-12T09:30:00+08:00')
 expect(simpleRepeatAnchor({...input,clock:'13:15'},now)).toBe('2026-11-11T13:15:00+08:00')
 expect(simpleRepeatAnchor({...input,frequency:'weekly'},now)).toBe('2026-11-18T09:30:00+08:00')
 expect(simpleRepeatAnchor({...input,frequency:'weekly',weekday:5},now)).toBe('2026-11-13T09:30:00+08:00')
})
test('每月保留原几号，短月的31号用有效名义日期交给既有月末规则',()=>{
 expect(simpleRepeatAnchor({...input,frequency:'monthly'},now)).toBe('2026-11-12T09:30:00+08:00')
 expect(simpleRepeatAnchor({...input,frequency:'monthly',monthDay:31},now)).toBe('2026-10-31T09:30:00+08:00')
})
test('年度公农历保持现有日期规则，不向普通周期加入历法设置',()=>{
 expect(simpleRepeatAnchor({...input,frequency:'yearly'},now)).toBe('2026-11-12T09:30:00+08:00')
 expect(simpleRepeatAnchor({...input,frequency:'yearly',calendar:'lunar'},now)).toBe('2026-11-11T09:30:00+08:00')
})
