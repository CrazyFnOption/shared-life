import type { PlanOperation, PlanRecord, PlanTask, PlanStage, PlanDeletionRequest, PlanReceipt, PlanSnapshot, PlanAttachment } from './types'
export function planProgress(snapshot:PlanSnapshot,planId:string){const tasks=snapshot.tasks.filter(t=>t.plan_id===planId&&!t.deleted);const done=tasks.filter(t=>t.completed).length;return{done,total:tasks.length,percent:tasks.length?Math.round(done/tasks.length*100):0}}
export function taskReference(snapshot:PlanSnapshot,id:string):PlanTask|undefined {const task=snapshot.tasks.find(t=>t.id===id&&!t.deleted);return task&&snapshot.records.some(p=>p.id===task.plan_id&&!p.deleted&&!p.archived)&&snapshot.stages.some(s=>s.id===task.stage_id&&!s.deleted)?task:undefined}
export function taskCompleteOperation(task:PlanTask,completed:boolean,opId=crypto.randomUUID()):PlanOperation{return{op_id:opId,entity_type:'task',entity_id:task.id,kind:'complete',base_version:task.version,data:{completed}}}
function copy(snapshot:PlanSnapshot):PlanSnapshot {return{...snapshot,source:{...snapshot.source},records:snapshot.records.map(r=>({...r,attachments:r.attachments.map(a=>({...a}))})),stages:snapshot.stages.map(s=>({...s})),tasks:snapshot.tasks.map(t=>({...t,assignee_ids:[...t.assignee_ids],attachments:t.attachments.map(a=>({...a}))})),attachments:snapshot.attachments.map(a=>({...a})),deletion_requests:snapshot.deletion_requests.map(r=>({...r})),users:snapshot.users.map(u=>({...u}))}}
function upsert<T extends {id:string}>(items:T[],value:T){const index=items.findIndex(item=>item.id===value.id);if(index<0)items.push(value);else items[index]=value}
export function applyPlanReceipt(snapshot:PlanSnapshot,receipt:PlanReceipt):PlanSnapshot {
  const next=copy(snapshot)
  if(receipt.entity_type==='stage')upsert(next.stages,receipt.record as PlanStage)
  else if(receipt.entity_type==='task')upsert(next.tasks,receipt.record as PlanTask)
  else if(receipt.entity_type==='deletion_request'){
    const request=receipt.record as PlanDeletionRequest;upsert(next.deletion_requests,request)
    if(request.status==='accepted'){
      const target=[...next.records,...next.tasks].find(item=>item.id===request.target_id)
      if(target){target.deleted=true;if(target.version===request.target_version)target.version++}
    }
  }
  else upsert(next.records,receipt.record as PlanRecord)
  next.attachments=[...next.records,...next.tasks].flatMap(record=>record.attachments)
  return next
}
/** Pure local projection. The host must commit this together with queue + Blobs. */
export function projectOperation(snapshot:PlanSnapshot,operation:PlanOperation):PlanSnapshot {
  const next=copy(snapshot),{entity_type:entity,entity_id:id,kind,data,base_version:base}=operation
  const items=entity==='stage'?next.stages:entity==='task'?next.tasks:entity==='deletion_request'?next.deletion_requests:next.records
  const current=items.find(item=>item.id===id)
  const timestamp=new Date().toISOString(),uid=next.source.user_id
  if(kind==='create'||kind==='from_wish'){
    if(current||base!==0)throw new Error('记录身份已存在')
    if(entity==='wish'||entity==='plan'){
      if(kind==='from_wish'&&next.records.some(p=>p.source_wish_id===data.source_wish_id&&!p.deleted))throw new Error('这个愿望已经有计划')
      const record:PlanRecord={id,kind:entity,scope:next.source.scope,family_id:next.source.family_id,author_id:uid,updated_by:uid,title:String(data.title),body_markdown:String(data.body_markdown??''),wish_kind:String(data.wish_kind??''),target_date:data.target_date as string|null??null,source_wish_id:data.source_wish_id as string|null??null,realized:false,archived:false,deleted:false,version:1,created_at:timestamp,updated_at:timestamp,attachments:(data.attachments as PlanAttachment[]??[]).map(a=>({...a,parent_id:id,state:'pending'}))};next.records.push(record)
    }else if(entity==='stage')next.stages.push({id,plan_id:String(data.plan_id),title:String(data.title),position:Number(data.position),version:1,deleted:false})
    else if(entity==='task')next.tasks.push({id,plan_id:String(data.plan_id),stage_id:String(data.stage_id),author_id:uid,updated_by:uid,title:String(data.title),body_markdown:String(data.body_markdown??''),assignee_ids:[...data.assignee_ids as string[]],due_date:data.due_date as string|null??null,completed:false,version:1,position:Number(data.position),deleted:false,attachments:(data.attachments as PlanAttachment[]??[]).map(a=>({...a,parent_id:id,state:'pending'}))})
    else {
      const target=[...next.records,...next.tasks].find(r=>r.id===data.target_id)
      if(!target)throw new Error('申请目标已不可用')
      next.deletion_requests.push({id,target_type:data.target_type as 'wish'|'plan'|'task',target_id:String(data.target_id),requester_id:uid,author_id:target.author_id,target_version:Number(data.target_version),reason:String(data.reason??''),status:'pending',version:1})
    }
  }else{
    if(!current||current.version!==base)throw new Error('记录已变化，请保留输入并刷新')
    if(kind==='update')Object.assign(current,data)
    else if(kind==='complete')Object.assign(current,{completed:data.completed})
    else if(kind==='realized')Object.assign(current,{realized:data.realized})
    else if(kind==='archive')Object.assign(current,{archived:data.archived})
    else if(kind==='delete')Object.assign(current,{deleted:true})
    else if(kind==='decide'){
      const request=current as PlanDeletionRequest;request.status=data.accepted?'accepted':'rejected'
      if(data.accepted){const target=[...next.records,...next.tasks].find(r=>r.id===request.target_id);if(target){target.deleted=true;target.version++}}
    }else throw new Error('不支持的操作')
    current.version++
    if('updated_by' in current)current.updated_by=uid
    if('updated_at' in current)current.updated_at=timestamp
    for(const request of next.deletion_requests)if(request.target_id===id&&request.status==='pending'){request.status='stale';request.version++}
  }
  next.attachments=[...next.records,...next.tasks].flatMap(record=>record.attachments)
  return next
}
export const applyReceipt=applyPlanReceipt
