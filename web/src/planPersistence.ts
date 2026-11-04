import type { BlobRestoreAudit } from './blobRestoreReview'
import type { Account, AccountContext, Source } from './types'
import type { PlanAttachment, PlanOperation, PlanSnapshot, PlanReceipt } from './plans/types'
import { emptyPlanSnapshot } from './plans/types'
import { applyPlanReceipt, projectOperation } from './plans/domain'
import { sameSource, sourceKey, sourceValid } from './sources'
export interface PlanPending {operation:PlanOperation;source:Source;frozen?:boolean;restore_review?:boolean;error?:string;error_code?:string;conflict?:unknown}
export interface PlanBlob extends PlanAttachment {original_source?:Source;restore_binding?:BlobRestoreAudit;parent_id:string;source:Source;blob:Blob;pending:boolean;frozen?:boolean;restore_review?:boolean;error?:string}
export interface PlanPartition {key:string;user_id:string;source:Source;snapshot:PlanSnapshot;local:PlanSnapshot;queue:PlanPending[];blobs:PlanBlob[];synced_at:string|null}
export function newPlanPartition(source:Source):PlanPartition{return{key:sourceKey(source),user_id:source.user_id,source:{...source},snapshot:emptyPlanSnapshot(source),local:emptyPlanSnapshot(source),queue:[],blobs:[],synced_at:null}}
export function planPendingSummary(partitions:PlanPartition[]){let active=0,frozen=0;for(const p of partitions)for(const item of [...p.queue,...p.blobs.filter(b=>b.pending)])item.frozen?frozen++:active++;return{active,frozen}}
export function projectPlans(partition:PlanPartition){const prior=partition.local;let next=partition.snapshot;const blocked=new Set<string>();for(const pending of partition.queue){const op=pending.operation;if(pending.frozen||pending.restore_review){blocked.add(op.entity_id);continue}if(blocked.has(op.entity_id))continue;try{next=projectOperation(next,op)}catch{const record=[...prior.records,...prior.stages,...prior.tasks,...prior.deletion_requests].find(r=>r.id===op.entity_id);if(record)next=applyPlanReceipt(next,{op_id:op.op_id,entity_type:op.entity_type,entity_id:op.entity_id,version:record.version,record});blocked.add(op.entity_id)}}partition.local=next}
export function reconcilePlans(partitions:PlanPartition[],account:Account,_restored:boolean,previous?:AccountContext){
 for(const p of partitions){
  if(sourceValid(account,p.source))continue
  const changedRestore=p.source.restore_id!==account.restore_id
  const before=p.source.scope==='shared'?previous?.families.find(f=>f.id===p.source.family_id):undefined
  const after=p.source.scope==='shared'?account.context?.families.find(f=>f.id===p.source.family_id):undefined
  const currentRelationChanged=previous?.restore_id===account.restore_id&&!!before&&(!after||before.relation_epoch!==after.relation_epoch)
  const relation=p.source.scope==='private'||!!after&&(changedRestore||after.relation_epoch===p.source.relation_epoch)
  for(const item of [...p.queue,...p.blobs.filter(b=>b.pending)]){
   if(!relation||!changedRestore||currentRelationChanged||item.frozen){item.frozen=true;item.error='原家庭访问已失效；本机计划与附件已冻结。'}
   else{item.restore_review=true;item.error='恢复域已变化，请逐项核对本机计划，尚未自动重放。'}
  }
  p.snapshot=emptyPlanSnapshot(p.source);p.local=emptyPlanSnapshot(p.source);p.blobs=p.blobs.filter(b=>b.pending)
 }
}
export function planDisplaySnapshot(partition:PlanPartition|undefined,source:Source):PlanSnapshot{if(!partition||!sameSource(partition.source,source))return emptyPlanSnapshot(source);const file=(a:PlanAttachment)=>{const meta=partition.local.attachments.find(m=>m.id===a.id),blob=partition.blobs.find(b=>b.id===a.id&&!b.frozen&&!b.restore_review);return{...a,...meta,blob:blob?.blob,state:blob?.pending?'pending' as const:meta?.state??a.state}};return{...partition.local,records:partition.local.records.map(r=>({...r,attachments:r.attachments.map(file)})),tasks:partition.local.tasks.map(t=>({...t,attachments:t.attachments.map(file)})),attachments:partition.local.attachments.map(file)}}
