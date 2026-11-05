import type { BlobRestoreAudit } from '../blobRestoreReview'
import type { MemoCalendarCache } from './calendarCache'
import type { Account, AccountContext, Source, User } from '../types'
import { sameSource, sourceKey, sourceValid } from '../sources'
export interface MemoFileManifest {id:string;kind:'photo'|'file';name:string;mime_type:string;size_bytes:number;sha256:string}
export interface MemoCategory {id:string;scope:'private'|'shared';family_id:string|null;creator_id:string;name:string;version:number;deleted?:boolean}
export interface MemoSchedule {start_date:string;end_date:string|null;start_time:string|null;end_time:string|null}
export interface MemoReminder {id:string;mode:'absolute'|'relative';at:string|null;offset_minutes:number;recipient_ids:string[];all_members:boolean}
export interface MemoRecurrence {frequency:'daily'|'weekly'|'monthly'|'yearly';interval:number;calendar:'solar'|'lunar';start_date:string;until:string|null;lunar_month:number|null;lunar_day:number|null;lunar_leap:boolean}
export interface FocusTarget {kind:'memo'|'plan_task';id:string}
export interface MemoFocus {id:string;version:number;targets:FocusTarget[]}
export interface MemoCategoryOrder {id:string;version:number;category_ids:string[]}
export interface MemoDeletionRequest {id:string;memo_id:string;requester_id:string;author_id:string;target_version:number;reason:string;status:'pending'|'accepted'|'rejected'|'stale';version:number;created_at:string;decided_at:string|null}
export interface MemoMessage {id:string;kind:'reminder'|'memo_deletion_request'|'plan_deletion_request'|'expense_change_request'|'expense_category_change'|'family_invite';target_type:'memo'|'deletion_request'|'plan_deletion_request'|'expense_change_request'|'expense_category_change'|'family_invite';target_id:string;record_id:string;title:string;created_at:string;source:Source;ignored?:boolean}
export interface MemoData {category_id:string|null;title:string;body_markdown:string;attachments:MemoFileManifest[];kind?:'memo'|'schedule';schedule?:MemoSchedule|null;reminders?:MemoReminder[];recurrence?:MemoRecurrence|null}
export interface MemoOccurrenceState {start_date:string;archived_at:string|null;completed:boolean;suppressed?:boolean;schedule?:MemoSchedule}
export interface MemoRecord extends MemoData {id:string;scope:'private'|'shared';family_id:string|null;author_id:string;updated_by:string;completed:boolean;version:number;created_at:string;updated_at:string;archived?:boolean;archived_at?:string|null;auto_archive_suppressed?:boolean;archived_occurrences?:MemoOccurrenceState[];deleted?:boolean}
export interface MemoAttachment extends MemoFileManifest {memo_id:string;created_by:string;state:'pending'|'uploaded'|'deleted'}
export interface MemoOperation {op_id:string;entity_type:'category'|'memo'|'focus'|'category_order'|'deletion_request';entity_id:string;kind:string;base_version:number;data:MemoData|{name:string}|{completed:boolean;occurrence_date?:string}|{archived:boolean;occurrence_date?:string}|{targets:FocusTarget[]}|{category_ids:string[]}|{target_category_id:string|null}|{memo_id:string;target_version:number;reason:string}|{accepted:boolean}|Record<string,never>}
export type MemoEntity=MemoCategory|MemoRecord|MemoFocus|MemoCategoryOrder|MemoDeletionRequest
export interface MemoReceipt {op_id:string;entity_type:MemoOperation['entity_type'];entity_id:string;version:number;record:MemoEntity;effects?:{memos?:MemoRecord[];categories?:MemoCategory[];category_order?:MemoCategoryOrder}}
export interface MemoSnapshot {source:Source;categories:MemoCategory[];memos:MemoRecord[];attachments:MemoAttachment[];users:User[];focus?:MemoFocus;category_order?:MemoCategoryOrder;deletion_requests?:MemoDeletionRequest[];messages?:MemoMessage[]}
export interface MemoPending {operation:MemoOperation;source:Source;frozen?:boolean;restore_review?:boolean;local_at?:string;error?:string;error_code?:string;conflict?:MemoEntity}
export interface MemoBlob extends MemoFileManifest {original_source?:Source;restore_binding?:BlobRestoreAudit;memo_id:string;source:Source;blob:Blob;pending:boolean;frozen?:boolean;restore_review?:boolean;error?:string}
export interface MemoPartition {calendar?:Record<string,MemoCalendarCache>;key:string;user_id:string;source:Source;categories:MemoCategory[];local_categories:MemoCategory[];snapshot:MemoRecord[];local:MemoRecord[];attachments:MemoAttachment[];blobs:MemoBlob[];queue:MemoPending[];users:User[];synced_at:string|null;focus?:MemoFocus;local_focus?:MemoFocus;category_order?:MemoCategoryOrder;local_category_order?:MemoCategoryOrder;deletion_requests?:MemoDeletionRequest[];local_deletion_requests?:MemoDeletionRequest[];messages?:MemoMessage[]}
export function newMemoPartition(source:Source):MemoPartition{return{key:sourceKey(source),user_id:source.user_id,source:{...source},categories:[],local_categories:[],snapshot:[],local:[],attachments:[],blobs:[],queue:[],users:[],synced_at:null,calendar:{},deletion_requests:[],local_deletion_requests:[],messages:[]}}
export function applyMemoOperation(p:MemoPartition,op:MemoOperation,userId:string,timestamp=new Date().toISOString()):void{
 if(op.entity_type==='focus'){if(!p.local_focus||p.local_focus.id!==op.entity_id||p.local_focus.version!==op.base_version)throw new Error('focus_conflict');p.local_focus={id:op.entity_id,version:op.base_version+1,targets:(op.data as {targets:FocusTarget[]}).targets.map(t=>({...t}))};return}
 if(op.entity_type==='category_order'){if(!p.local_category_order||p.local_category_order.id!==op.entity_id||p.local_category_order.version!==op.base_version)throw new Error('category_order_conflict');p.local_category_order={id:op.entity_id,version:op.base_version+1,category_ids:[...(op.data as {category_ids:string[]}).category_ids]};return}
 if(op.entity_type==='deletion_request'){
  p.local_deletion_requests??=[]
  if(op.kind==='create'){const d=op.data as {memo_id:string;target_version:number;reason:string},memo=p.local.find(m=>m.id===d.memo_id);if(!memo)throw new Error('memo_not_found');p.local_deletion_requests.push({id:op.entity_id,...d,requester_id:userId,author_id:memo.author_id,status:'pending',version:1,created_at:new Date().toISOString(),decided_at:null})}
  else{const request=p.local_deletion_requests.find(r=>r.id===op.entity_id);if(!request||request.version!==op.base_version)throw new Error('request_conflict');request.version++;request.status=(op.data as {accepted:boolean}).accepted?'accepted':'rejected';request.decided_at=new Date().toISOString();if(request.status==='accepted'){const memo=p.local.find(m=>m.id===request.memo_id);if(memo){memo.deleted=true;memo.version++}}}return
 }
 if(op.entity_type==='category'){
  const before=p.local_categories.find(c=>c.id===op.entity_id)
  if((before?.version??0)!==op.base_version)throw new Error('category_conflict')
  if(op.kind==='create'){if(before)throw new Error('category_exists');p.local_categories.push({id:op.entity_id,scope:p.source.scope,family_id:p.source.family_id,creator_id:userId,name:(op.data as {name:string}).name,version:1})}
  else if(op.kind==='delete'){if(!before)throw new Error('category_not_found');const target=(op.data as {target_category_id:string|null}).target_category_id;for(const memo of p.local.filter(m=>m.category_id===before.id)){memo.category_id=target;memo.version++}p.local_categories=p.local_categories.filter(c=>c.id!==before.id)}
  else{if(!before)throw new Error('category_not_found');before.name=(op.data as {name:string}).name;before.version++}return
 }
 const index=p.local.findIndex(m=>m.id===op.entity_id),before=p.local[index]
 if((before?.version??0)!==op.base_version)throw new Error('memo_conflict')
 let next:MemoRecord
 if(op.kind==='create'){if(before)throw new Error('memo_exists');const now=new Date().toISOString();next={...op.data as MemoData,id:op.entity_id,scope:p.source.scope,family_id:p.source.family_id,author_id:userId,updated_by:userId,completed:false,archived:false,deleted:false,version:1,created_at:now,updated_at:now}}
 else{
  if(!before)throw new Error('memo_not_found')
  let data:Partial<MemoRecord>
  if(op.kind==='complete'||op.kind==='archive'){
   const input=op.data as {completed?:boolean;archived?:boolean;occurrence_date?:string},desired=op.kind==='complete'?!!input.completed:!!input.archived
   if(before.kind==='schedule'&&(before.recurrence||input.occurrence_date&&(before.archived_occurrences??[]).some(e=>e.start_date===input.occurrence_date))){
    const day=input.occurrence_date??before.schedule!.start_date,entries=(before.archived_occurrences??[]).filter(e=>e.start_date!==day),prior=before.archived_occurrences?.find(e=>e.start_date===day)
    const schedule=prior?.schedule??{...before.schedule!,start_date:day,end_date:before.schedule!.end_date?new Date(Date.parse(day)+Date.parse(before.schedule!.end_date)-Date.parse(before.schedule!.start_date)).toISOString().slice(0,10):null}
    entries.push({start_date:day,archived_at:desired?prior?.archived_at??timestamp:null,completed:op.kind==='complete'?desired:desired?prior?.completed??false:false,suppressed:!desired,schedule})
    data={archived_occurrences:entries}
   }else data={archived:desired,archived_at:desired?before.archived_at??timestamp:null,completed:op.kind==='complete'?desired:desired?before.completed:false,auto_archive_suppressed:!desired}
  }else if(op.kind==='delete'||op.kind==='restore')data={deleted:op.kind==='delete'}
  else{data=op.data as MemoData;if(JSON.stringify(data.schedule)!==JSON.stringify(before.schedule)||JSON.stringify(data.recurrence)!==JSON.stringify(before.recurrence))data={...data,auto_archive_suppressed:false,archived_occurrences:(before.archived_occurrences??[]).filter(e=>e.archived_at)}}
  next={...before,...data,updated_by:userId,version:before.version+1,updated_at:timestamp}
 }
 if(index<0)p.local.push(next);else p.local[index]=next
}
export function projectMemos(p:MemoPartition):void{
 const prior=p.local,oldCategories=p.local_categories
 p.local=p.snapshot.map(m=>({...m,attachments:m.attachments.map(a=>({...a}))}));p.local_categories=p.categories.filter(c=>!c.deleted).map(c=>({...c}));p.local_focus=p.focus?{...p.focus,targets:p.focus.targets.map(t=>({...t}))}:undefined;p.local_category_order=p.category_order?{...p.category_order,category_ids:[...p.category_order.category_ids]}:undefined;p.local_deletion_requests=(p.deletion_requests??[]).map(r=>({...r}))
 const blocked=new Set<string>()
 for(const pending of p.queue){const op=pending.operation;if(pending.frozen||pending.restore_review){blocked.add(op.entity_id);continue}if(blocked.has(op.entity_id))continue;try{pending.local_at??=prior.find(m=>m.id===op.entity_id)?.updated_at??new Date().toISOString();applyMemoOperation(p,op,pending.source.user_id,pending.local_at)}catch{const previous=prior.find(m=>m.id===op.entity_id);if(previous)p.local=p.local.filter(m=>m.id!==previous.id).concat(previous);const category=oldCategories.find(c=>c.id===op.entity_id);if(category&&!p.local_categories.some(c=>c.id===category.id))p.local_categories.push(category);blocked.add(op.entity_id)}}
}
export function memoPendingSummary(partitions:MemoPartition[]){let active=0,frozen=0;for(const p of partitions)for(const item of [...p.queue,...p.blobs.filter(b=>b.pending)])item.frozen?frozen++:active++;return{active,frozen}}
export function reconcileMemos(partitions:MemoPartition[],account:Account,_restored:boolean,previous?:AccountContext){
 for(const p of partitions){
  if(sourceValid(account,p.source))continue
  const changedRestore=p.source.restore_id!==account.restore_id
  const before=p.source.scope==='shared'?previous?.families.find(f=>f.id===p.source.family_id):undefined
  const after=p.source.scope==='shared'?account.context?.families.find(f=>f.id===p.source.family_id):undefined
  const currentRelationChanged=previous?.restore_id===account.restore_id&&!!before&&(!after||before.relation_epoch!==after.relation_epoch)
  const relation=p.source.scope==='private'||!!after&&(changedRestore||after.relation_epoch===p.source.relation_epoch)
  for(const item of [...p.queue,...p.blobs.filter(b=>b.pending)]){
   if(!relation||!changedRestore||currentRelationChanged||item.frozen){item.frozen=true;item.error='原家庭访问已失效；本机备忘与附件已冻结，不会投向其他范围。'}
   else{item.restore_review=true;item.error='恢复域已变化，请逐项核对本机备忘，尚未自动重放。'}
  }
  p.snapshot=[];p.categories=[];p.local=[];p.local_categories=[];p.attachments=[];p.users=[];p.blobs=p.blobs.filter(b=>b.pending);p.focus=undefined;p.local_focus=undefined;p.category_order=undefined;p.local_category_order=undefined;p.deletion_requests=[];p.local_deletion_requests=[];p.messages=[];p.calendar={}
 }
}
export function memoProjection(partitions:MemoPartition[],source:Source){return partitions.find(p=>sameSource(p.source,source))??newMemoPartition(source)}
