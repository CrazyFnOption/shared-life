import { expect, test } from 'vitest'
import { copyDraft } from '../src/memos/types'
import type { MemoDraft } from '../src/memos/types'
import { freezeQuickReminder, quickReminderAt } from '../src/memos/quickReminders'

function draft():MemoDraft { return { kind:'memo',base_version:0,title:'虚构快捷提醒',body:'',category_id:'',focused:false,start_date:'2026-11-16',end_date:'2026-11-16',start_time:'09:00',end_time:'09:30',all_day:true,has_end:false,attachments:[],quick_reminder:{amount:1,unit:'minute'},reminders:[{id:'fictional-reminder',mode:'absolute',at:null,offset_minutes:0,recipient_ids:['fictional-user'],all_members:false}],recurrence:null } }

test('会话草稿复制保留时长；保存从提交时起计满一分钟并保留秒，后续重开/重试不顺延',()=>{
 const original=draft(),restored=copyDraft(original)
 restored.quick_reminder!.amount=10
 expect(original.quick_reminder!.amount).toBe(1)
 const click=Date.parse('2026-11-16T23:59:47.321+08:00')
 const saved=freezeQuickReminder(copyDraft(original),click+30000)
 expect(saved.reminders![0]!.at).toBe('2026-11-16T16:01:17.321Z')
 expect(Date.parse(saved.reminders![0]!.at!)-(click+30000)).toBe(60000)
 expect(saved.quick_reminder).toBeUndefined()
 expect(original.quick_reminder).toEqual({amount:1,unit:'minute'})
 expect(freezeQuickReminder(saved,click+86400000).reminders).toEqual(saved.reminders)
 expect(saved.reminders![0]!.recipient_ids).toEqual(['fictional-user'])
})
test('小时/天/周均为保存后的时长，无效自定义不能生成提醒',()=>{
 const now=Date.parse('2026-11-16T00:00:00Z')
 expect(quickReminderAt({amount:2,unit:'hour'},now)).toBe('2026-11-16T02:00:00.000Z')
 expect(quickReminderAt({amount:3,unit:'day'},now)).toBe('2026-11-19T00:00:00.000Z')
 expect(quickReminderAt({amount:2,unit:'week'},now)).toBe('2026-11-30T00:00:00.000Z')
 for(const amount of [0,-1,1.5,Infinity,Number.MAX_SAFE_INTEGER])expect(()=>quickReminderAt({amount,unit:'minute'},now)).toThrow()
})
