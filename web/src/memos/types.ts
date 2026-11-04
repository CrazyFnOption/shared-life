import type { MemoOccurrence } from './calendarCache'
import type { MemoOperation, MemoReminder, MemoRecurrence } from './domain'
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
  creator_id: string
  creator_name: string
  attachments: MemoAttachmentView[]
  pending?: boolean
  error?: string
  archived?: boolean
  deleted?: boolean
  can_delete?: boolean
  reminders?: MemoReminder[]
  recurrence?: MemoRecurrence | null
  can_edit?: boolean
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
  occurrence_key?: string
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
export interface MemoCompleteCommand extends MemoRecordCommand { completed: boolean }
export interface MemoFocusCommand extends MemoRecordCommand { focused: boolean }
export interface MemoCategoryCommand extends MemoCommandOrigin { name: string }
export interface MemoCommandResult extends MemoCommandOrigin {
  status: 'saved' | 'error'
  record_id?: string
  error?: string
}
export interface MemoDraftChange { source: Source; draft: MemoDraft | null }

export function copyDraft(draft: MemoDraft): MemoDraft {
  return { ...draft, source:draft.source?{...draft.source}:undefined, attachments: draft.attachments.map(file => ({ ...file })),reminders:draft.reminders?.map(reminder=>({...reminder,recipient_ids:[...reminder.recipient_ids]})),recurrence:draft.recurrence?{...draft.recurrence}:null }
}
