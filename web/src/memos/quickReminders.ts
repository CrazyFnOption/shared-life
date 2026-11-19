import { copyDraft } from './types'
import type { MemoDraft, MemoQuickReminder } from './types'

const milliseconds = { minute: 60000, hour: 3600000, day: 86400000, week: 604800000 }
const units = { minute: '分钟', hour: '小时', day: '天', week: '周' }

export function quickReminderLabel(choice: MemoQuickReminder): string { return `${choice.amount} ${units[choice.unit]}` }
export function quickReminderAt(choice: MemoQuickReminder, now: number): string {
  const at = now + choice.amount * milliseconds[choice.unit]
  if (!Number.isSafeInteger(choice.amount) || choice.amount <= 0 || !Number.isFinite(new Date(at).getTime())) throw new Error('请输入有效的正整数时长。')
  return new Date(at).toISOString()
}

/** Run before the existing save chain, never during offline replay or sync. */
export function freezeQuickReminder(draft: MemoDraft, now: number): MemoDraft {
  const saved = copyDraft(draft)
  if (saved.quick_reminder && saved.reminders?.[0]) {
    saved.reminders[0] = { ...saved.reminders[0], mode: 'absolute', offset_minutes: 0, at: quickReminderAt(saved.quick_reminder, now) }
  }
  delete saved.quick_reminder
  return saved
}
