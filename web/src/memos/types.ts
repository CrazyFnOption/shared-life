import type { MemoOccurrence } from './calendarCache'
import type { MemoOperation, MemoReminder, MemoRecurrence, MemoSchedule } from './domain'
import type { Source } from '../types'

/** Presentation contracts only. The transport and IDB contracts belong to the host. */
export interface MemoCategoryView {
  id: string
  name: string
  position: number
  version?: number
}
export interface MemoAttachmentView {
  id: string
  kind?: 'photo' | 'file'
  sha256?: string
  file_name: string
  mime_type: string
  size_bytes: number
  /** A host-authorized local/downloaded Blob, never an unauthenticated remote URL. */
  blob?: Blob
  pending?: boolean
  error?: string
}
interface RecordView {
  id: string
  version: number
  title: string
  body: string
  created_at?: string | null
  creator_id: string
  creator_name: string
  attachments: MemoAttachmentView[]
  pending?: boolean
  error?: string
  completed?: boolean
  archived_at?: string | null
  archived_time_unknown?: boolean
  archived?: boolean
  deleted?: boolean
  can_delete?: boolean
  reminders?: MemoReminder[]
  recurrence?: MemoRecurrence | null
  current_occurrence_at?: string | null
  occurrence_at?: string
  occurrence_key?: string
  can_edit?: boolean
  category_id?: string
  focused?: boolean
  focus_position?: number
}
export interface MemoView extends RecordView {
  kind: 'memo'
  origin?: 'plan_task'
  category_id: string
  completed: boolean
  focused: boolean
  focus_position: number
}
export interface ScheduleView extends RecordView {
  kind: 'schedule'
  calendar_projection?: boolean
  occurrence_key?: string
  occurrence_date?: string
  series_schedule?: MemoSchedule
  auto_archive_suppressed?: boolean
  occurrences?: MemoOccurrence[]
  occurrence_months?: string[]
  start_date: string
  end_date: string | null
  start_time: string | null
  end_time: string | null
}
export type MemoRecordView = MemoView | ScheduleView
export interface MemoDraft {
  source?: Source
  id?: string
  base_version: number
  kind: 'memo' | 'schedule'
  title: string
  body: string
  category_id: string
  focused: boolean
  start_date: string
  end_date: string
  start_time: string
  end_time: string
  all_day: boolean
  has_end: boolean
  attachments: MemoAttachmentView[]
  reminders?: MemoReminder[]
  recurrence?: MemoRecurrence | null
}
export interface MemoOperationCommand extends MemoCommandOrigin { operation: MemoOperation }
export interface MemoCommandOrigin { request_id: string; source: Source }
export interface MemoSaveCommand extends MemoCommandOrigin { draft: MemoDraft }
export interface MemoRecordCommand extends MemoCommandOrigin { record_id: string; base_version: number }
export interface MemoCompleteCommand extends MemoRecordCommand { completed: boolean; occurrence_at?: string }
export interface MemoFocusCommand extends MemoRecordCommand { focused: boolean }
export interface MemoCategoryCommand extends MemoCommandOrigin { name: string }
export interface MemoCommandResult extends MemoCommandOrigin {
  status: 'saved' | 'error'
  record_id?: string
  error?: string
}
export interface MemoDraftChange { source: Source; draft: MemoDraft | null }

export function copyDraft(draft: MemoDraft): MemoDraft {
  return { ...draft, source:draft.source?{...draft.source}:undefined, attachments: draft.attachments.map(file => ({ ...file })),reminders:draft.reminders?.map(reminder=>({...reminder,recipient_ids:[...reminder.recipient_ids]})),recurrence:draft.recurrence?{...draft.recurrence,...(draft.recurrence.weekdays?{weekdays:[...draft.recurrence.weekdays]}:{}),...(draft.recurrence.month_days?{month_days:[...draft.recurrence.month_days]}:{})}:null }
}
