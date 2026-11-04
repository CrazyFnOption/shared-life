import 'fake-indexeddb/auto'
import { IDBObjectStore } from 'fake-indexeddb'
import { expect, test, vi } from 'vitest'
import { LedgerStore } from '../src/store'
import { sourceFor, sameSource } from '../src/sources'
import { blobHash } from '../src/memos/files'
import { matchReviewAttachment } from '../src/blobRestoreReview'
import { emptyPlanSnapshot } from '../src/plans/types'
import type { AccountContext, Source } from '../src/types'
import type { MemoRecord, MemoSnapshot } from '../src/memos/domain'
import type { ModuleBlobReview, VerifiedBlobReview } from '../src/blobRestoreReview'
import type { PlanRecord, PlanTask, PlanSnapshot } from '../src/plans/types'
const user={id:'review-user',display_name:'虚构成员'},lock=async<T>(_name:string,fn:()=>Promise<T>)=>fn()
function context(restore='R1',version=1,epoch=1):AccountContext{return{protocol_version:2,restore_id:restore,context_version:version,families:[{id:'family',name:'原家庭',owner_id:user.id,revision:1,relation_epoch:epoch,members:[user]},{id:'other-family',name:'其他家庭',owner_id:user.id,revision:1,relation_epoch:1,members:[user]}],default_scope:{scope:'private',family_id:null,version:1},default_invalidated_family_id:null,legacy_context_mappings:[]}}
async function setup(module:'memo'|'plan'){
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'review');await store.reconcile(user.id,context())
 const source=sourceFor(await store.read(user.id),{scope:'shared',family_id:'family'}),blob=new Blob(['original attachment'],{type:'text/plain'}),file={id:'attachment',kind:'file' as const,name:'original.txt',mime_type:'text/plain',size_bytes:blob.size,sha256:await blobHash(blob)},parentId=module==='memo'?'memo-parent':'task-parent'
 let snapshot:MemoSnapshot|PlanSnapshot
 if(module==='memo'){
  const record:MemoRecord={id:parentId,scope:'shared',family_id:'family',author_id:user.id,updated_by:user.id,category_id:'category',kind:'memo',title:'原备忘',body_markdown:'原正文',attachments:[file],completed:false,archived:false,deleted:false,version:1,created_at:'now',updated_at:'now'}
  await store.memoSave(user.id,source,{op_id:'parent-op',entity_type:'memo',entity_id:parentId,kind:'create',base_version:0,data:{category_id:'category',title:record.title,body_markdown:record.body_markdown,attachments:[file]}},[{...file,memo_id:parentId,source,blob,pending:true}]);await store.memoAcknowledge(user.id,{op_id:'parent-op',entity_type:'memo',entity_id:parentId,version:1,record},source)
  snapshot={source,categories:[],memos:[record],attachments:[{...file,memo_id:parentId,created_by:user.id,state:'pending'}],users:[user]};await store.memoSnapshot(user.id,snapshot,source)
 }else{
  const plan:PlanRecord={id:'plan-parent',kind:'plan',scope:'shared',family_id:'family',author_id:user.id,updated_by:user.id,title:'原计划',body_markdown:'',wish_kind:'',target_date:null,source_wish_id:null,realized:false,archived:false,deleted:false,version:1,created_at:'now',updated_at:'now',attachments:[]}
  const base=emptyPlanSnapshot(source);base.records=[plan];base.stages=[{id:'stage-parent',plan_id:plan.id,title:'原阶段',position:0,version:1,deleted:false}];await store.planSnapshot(user.id,base,source)
  const task:PlanTask={id:parentId,plan_id:plan.id,stage_id:'stage-parent',author_id:user.id,updated_by:user.id,title:'原任务',body_markdown:'',assignee_ids:[],due_date:null,completed:false,version:1,position:0,deleted:false,attachments:[{...file,parent_id:parentId,state:'pending'}]}
  await store.planSave(user.id,source,{op_id:'parent-op',entity_type:'task',entity_id:parentId,kind:'create',base_version:0,data:{plan_id:plan.id,stage_id:'stage-parent',title:task.title,body_markdown:'',assignee_ids:[],due_date:null,position:0,attachments:[file]}},[{...file,parent_id:parentId,source,blob,pending:true}]);await store.planAcknowledge(user.id,{op_id:'parent-op',entity_type:'task',entity_id:parentId,version:1,record:task},source)
  snapshot={...base,tasks:[task],attachments:task.attachments};await store.planSnapshot(user.id,snapshot,source)
 }
 await store.reconcile(user.id,context('R2',1,3));await store.reconcile(user.id,context('R2',2,3))
 const target=sourceFor(await store.read(user.id),{scope:'shared',family_id:'family'}),review=await store.moduleBlobForReview(user.id,module+':blob:'+file.id,source),fresh={...snapshot,source:target}
 if(module==='memo')await store.memoSnapshot(user.id,fresh as MemoSnapshot,target);else await store.planSnapshot(user.id,fresh as PlanSnapshot,target)
 const verified:VerifiedBlobReview={...review,target_source:target,match:matchReviewAttachment(review,target,fresh),verified_blob:blob,checked_at:'2026-11-04T00:00:00.000Z'}
 return{store,source,target,review,verified,snapshot:fresh,file,blob,parentId}
}
for(const module of ['memo','plan'] as const){
 test(`${module}：连续刷新保旧域review，确认重绑定保原ID/字节和from/to审计`,async()=>{
  const{store,source,target,verified,file,blob}=await setup(module)
  expect(await store.commitModuleBlobReview(user.id,verified)).toBe('pending')
  const part=module==='memo'?await store.memoRead(user.id,target):await store.planRead(user.id,target),saved=part.blobs.find(b=>b.id===file.id)!
  expect(part.queue).toEqual([]);expect(await saved.blob.text()).toBe(await blob.text());expect(saved.sha256).toBe(file.sha256);expect(saved.restore_review).toBeUndefined();expect(sameSource(saved.original_source,source)).toBe(true);expect(sameSource(saved.restore_binding?.from,source)).toBe(true);expect(sameSource(saved.restore_binding?.to,target)).toBe(true);expect(saved.pending).toBe(true)
 })
 test(`${module}：跨分区保存失败原字节/来源/review仍在，恢复写入后可继续`,async()=>{
  const{store,source,verified,file,blob}=await setup(module),put=IDBObjectStore.prototype.put
  const spy=vi.spyOn(IDBObjectStore.prototype,'put').mockImplementation(function(this:IDBObjectStore,value:any,key?:IDBValidKey){if(this.name===(module==='memo'?'memo_partitions':'plans_partitions')&&value.source.restore_id==='R2'&&value.blobs.some((b:any)=>b.id===file.id))throw new DOMException('quota','QuotaExceededError');return put.call(this,value,key)})
  await expect(store.commitModuleBlobReview(user.id,verified)).rejects.toThrow('quota');spy.mockRestore()
  const old=await store.moduleBlobForReview(user.id,module+':blob:'+file.id,source);expect(old.blob.restore_review).toBe(true);expect(old.blob.restore_binding).toBeUndefined();expect(await old.blob.blob.text()).toBe(await blob.text());expect(sameSource(old.blob.source,source)).toBe(true)
  expect(await store.commitModuleBlobReview(user.id,verified)).toBe('pending')
 })
 test(`${module}：同新域内其他家变化不影响review，原家epoch改变则冻结且不解冻`,async()=>{
  const{store,source,file}=await setup(module),unrelated=context('R2',3,3);unrelated.families[1]!.relation_epoch=7;await store.reconcile(user.id,unrelated);expect((await store.moduleBlobForReview(user.id,module+':blob:'+file.id,source)).blob.frozen).toBeUndefined()
  await store.reconcile(user.id,context('R2',4,5));await expect(store.moduleBlobForReview(user.id,module+':blob:'+file.id,source)).rejects.toThrow('冻结');await store.reconcile(user.id,context('R3',1,9));await expect(store.moduleBlobForReview(user.id,module+':blob:'+file.id,source)).rejects.toThrow('冻结')
 })
 test(`${module}：目标hash/父对象不符不绑定，目标同ID不同声明也拒绝覆盖`,async()=>{
  const{store,target,review,verified,snapshot,file,source}=await setup(module),wrong=structuredClone(snapshot);wrong.attachments[0]!.sha256='b'.repeat(64);expect(()=>matchReviewAttachment(review,target,wrong)).toThrow('声明')
  expect(()=>matchReviewAttachment({...review,parent_id:'different-parent'},target,snapshot)).toThrow()
  const part=module==='memo'?await store.memoRead(user.id,target):await store.planRead(user.id,target)
  await store.transaction('readwrite',async(_a,_s,l)=>{part.blobs.push({...review.blob,source:target,restore_review:false,pending:false,sha256:'c'.repeat(64)} as never);l.transaction.objectStore(module==='memo'?'memo_partitions':'plans_partitions').put(part,[user.id,part.key])})
  await expect(store.commitModuleBlobReview(user.id,verified)).rejects.toThrow('不同');expect((await store.moduleBlobForReview(user.id,module+':blob:'+file.id,source)).blob.sha256).toBe(file.sha256)
 })
}
test('D task：已归档计划、已删阶段和任何父链待同步操作均阻止孤立附件续传',async()=>{
 const{store,target,review,verified,snapshot,source,file}=await setup('plan'),archived=structuredClone(snapshot) as PlanSnapshot;archived.records[0]!.archived=true;expect(()=>matchReviewAttachment(review,target,archived)).toThrow('归档')
 const deleted=structuredClone(snapshot) as PlanSnapshot;deleted.stages[0]!.deleted=true;expect(()=>matchReviewAttachment(review,target,deleted)).toThrow('阶段')
 await store.planSave(user.id,target,{op_id:'stage-edit',entity_type:'stage',entity_id:'stage-parent',kind:'update',base_version:1,data:{title:'阶段本机修改',position:0}})
 await expect(store.commitModuleBlobReview(user.id,verified)).rejects.toThrow('待同步');expect((await store.moduleBlobForReview(user.id,'plan:blob:'+file.id,source)).blob.restore_review).toBe(true)
})
