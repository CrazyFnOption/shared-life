import type { Source } from '../types'
export interface PlanAttachment { id:string; kind:'photo'|'file'; name:string; mime_type:string; size_bytes:number; sha256:string; parent_id?:string; state?:'pending'|'uploaded'|'deleted'; blob?:Blob }
export interface PlanRecord { id:string; kind:'wish'|'plan'; scope:'private'|'shared'; family_id:string|null; author_id:string; updated_by:string; title:string; body_markdown:string; wish_kind:string; target_date:string|null; source_wish_id:string|null; realized:boolean; archived:boolean; deleted:boolean; version:number; created_at:string; updated_at:string; attachments:PlanAttachment[] }
export interface PlanStage { id:string; plan_id:string; title:string; position:number; version:number; deleted:boolean }
export interface PlanTask { created_at?:string|null; id:string; plan_id:string; stage_id:string; author_id:string; updated_by:string; title:string; body_markdown:string; assignee_ids:string[]; due_date:string|null; completed:boolean; version:number; position:number; deleted:boolean; attachments:PlanAttachment[] }
export interface PlanDeletionRequest { id:string; target_type:'wish'|'plan'|'task'; target_id:string; requester_id:string; author_id:string; target_version:number; reason:string; status:'pending'|'accepted'|'rejected'|'stale'; version:number }
export interface PlanUser { id:string; display_name:string; assignable?:boolean }
export interface PlanSnapshot { source:Source; records:PlanRecord[]; stages:PlanStage[]; tasks:PlanTask[]; attachments:PlanAttachment[]; deletion_requests:PlanDeletionRequest[]; users:PlanUser[] }
export interface PlanOperation { op_id:string; entity_type:'wish'|'plan'|'stage'|'task'|'deletion_request'; entity_id:string; kind:string; base_version:number; data:Record<string,unknown> }
export interface PlanReceipt { op_id:string; entity_type:PlanOperation['entity_type']; entity_id:string; version:number; record:PlanRecord|PlanStage|PlanTask|PlanDeletionRequest }
export interface PlanCommand { request_id:string; source:Source; operation:PlanOperation; blobs?:{id:string; parent_id:string; blob:Blob}[] }
export interface PlanCommandResult { request_id:string; source:Source; status:'saved'|'error'; record_id?:string; error?:string }
export interface PlanAttachmentCommand { source:Source; parent_id:string; attachment:PlanAttachment }
export interface PlanFocusCommand { source:Source; request_id:string; task_id:string; focused:boolean }
export interface PlanEditorDraft {
  source:Source; id:string; entity:'wish'|'plan'|'stage'|'task'; base:number; title:string;
  body_markdown:string; wish_kind:string; target_date:string; plan_id:string; stage_id:string;
  assignee_ids:string[]; due_date:string; position:number; source_wish_id:string; attachments:PlanAttachment[]
}
export function copyPlanDraft(draft:PlanEditorDraft):PlanEditorDraft{return{...draft,source:{...draft.source},assignee_ids:[...draft.assignee_ids],attachments:draft.attachments.map(file=>({...file,blob:file.blob}))}}
export interface PlanDraftChange { source:Source; dirty:boolean; draft:PlanEditorDraft|null }
export function emptyPlanSnapshot(source:Source):PlanSnapshot{return{source:{...source},records:[],stages:[],tasks:[],attachments:[],deletion_requests:[],users:[]}}
