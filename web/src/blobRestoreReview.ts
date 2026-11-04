import type { Source } from './types'
import type { MemoBlob, MemoFileManifest, MemoSnapshot } from './memos/domain'
import type { PlanBlob } from './planPersistence'
import type { PlanSnapshot } from './plans/types'
import { sameSource } from './sources'
export interface BlobRestoreAudit {from:Source;to:Source;checked_at:string;server_state:'pending'|'uploaded'}
export interface ModuleBlobReview {module:'memo'|'plan';key:string;parent_id:string;original_source:Source;blob:MemoBlob|PlanBlob}
export interface BlobReviewDependency {kind:'memo'|'record'|'task'|'stage';id:string;version:number}
export interface BlobReviewMatch {manifest:MemoFileManifest;state:'pending'|'uploaded';dependencies:BlobReviewDependency[]}
export interface VerifiedBlobReview extends ModuleBlobReview {target_source:Source;match:BlobReviewMatch;verified_blob:Blob;checked_at:string}
export function sameAttachment(a:MemoFileManifest,b:MemoFileManifest):boolean{return a.id===b.id&&a.kind===b.kind&&a.name===b.name&&a.mime_type===b.mime_type&&a.size_bytes===b.size_bytes&&a.sha256===b.sha256}
/** Metadata checks stay specific to the two existing module snapshots. */
export function matchReviewAttachment(review:ModuleBlobReview,target:Source,snapshot:MemoSnapshot|PlanSnapshot):BlobReviewMatch{
 const old=review.original_source
 if(!sameSource(snapshot.source,target)||old.user_id!==target.user_id||old.scope!==target.scope||old.family_id!==target.family_id||old.restore_id===target.restore_id)throw new Error('附件原来源与恢复后的目标不一致；原文件已保留。')
 let manifest:MemoFileManifest|undefined,metadata:(MemoFileManifest&{state?:string;memo_id?:string;parent_id?:string})|undefined,dependencies:BlobReviewDependency[]=[]
 if(review.module==='memo'){
  const data=snapshot as MemoSnapshot,parent=data.memos.find(record=>record.id===review.parent_id)
  if(!parent||parent.deleted||parent.archived||parent.scope!==target.scope||parent.family_id!==target.family_id)throw new Error('原备忘不存在、已删除或已归档；原文件已保留。')
  manifest=parent.attachments.find(file=>file.id===review.blob.id);metadata=data.attachments.find(file=>file.id===review.blob.id)
  if(metadata?.memo_id!==parent.id)throw new Error('附件与原备忘的归属不一致；原文件已保留。')
  dependencies=[{kind:'memo',id:parent.id,version:parent.version}]
 }else{
  const data=snapshot as PlanSnapshot,record=data.records.find(record=>record.id===review.parent_id),task=data.tasks.find(task=>task.id===review.parent_id)
  if(record){
   if(record.deleted||record.archived||record.scope!==target.scope||record.family_id!==target.family_id)throw new Error('原愿望或计划已删除、归档或不可访问；原文件已保留。')
   manifest=record.attachments.find(file=>file.id===review.blob.id);dependencies=[{kind:'record',id:record.id,version:record.version}]
  }else if(task){
   const plan=data.records.find(record=>record.id===task.plan_id&&record.kind==='plan'),stage=data.stages.find(stage=>stage.id===task.stage_id&&stage.plan_id===task.plan_id)
   if(task.deleted||!plan||plan.deleted||plan.archived||plan.scope!==target.scope||plan.family_id!==target.family_id||!stage||stage.deleted)throw new Error('原任务、阶段或计划已删除、归档或不可访问；原文件已保留。')
   manifest=task.attachments.find(file=>file.id===review.blob.id);dependencies=[{kind:'task',id:task.id,version:task.version},{kind:'record',id:plan.id,version:plan.version},{kind:'stage',id:stage.id,version:stage.version}]
  }else throw new Error('原愿望、计划或任务不存在；原文件已保留。')
  metadata=data.attachments.find(file=>file.id===review.blob.id)
  if(metadata?.parent_id!==review.parent_id)throw new Error('附件与原记录的归属不一致；原文件已保留。')
 }
 if(!manifest||!metadata||!sameAttachment(review.blob,manifest)||!sameAttachment(review.blob,metadata))throw new Error('服务端附件编号、字节声明或文件类型已变化；原文件已保留。')
 if(metadata.state!=='pending'&&metadata.state!=='uploaded')throw new Error('服务端附件已移除或状态无法核对；原文件已保留。')
 return{manifest:{id:manifest.id,kind:manifest.kind,name:manifest.name,mime_type:manifest.mime_type,size_bytes:manifest.size_bytes,sha256:manifest.sha256},state:metadata.state,dependencies}
}
