import 'fake-indexeddb/auto'
import { expect, test } from 'vitest'
import { ApiError } from '../src/api'
import { applyOperation } from '../src/operations'
import { AccountChanged, FamilyChanged, RecordChanged, RestoreChanged } from '../src/store'
import { LedgerStore,SyncEngine } from './demoFixture'
import type { Expense, FamilyContext, Operation, Snapshot } from '../src/types'
const user={id:'a',display_name:'甲'}
const family={id:'family',name:'虚构家庭',owner_id:'a',members:[user]}
const context:FamilyContext={family,membership_version:1}
const data={amount_cents:10000,purpose:'应保留的离线虚构消费',occurred_at:'2026-11-01T00:00:00Z',location:'虚构地点',category:'旧分类',rating:3,rating_reason:''}
const baseSnapshot=(expenses:Expense[]=[],categories=['交通']):Snapshot=>({user,users:[user],expenses,categories:categories.flatMap(name=>[{scope:'private' as const,name},{scope:'shared' as const,name}]),images:[],synced_at:'now',restore_id:'restore-a',...context})
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
async function setup(shared=false){
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',baseSnapshot())
 const expense=await store.save('a',data,shared?'shared':'private',undefined,undefined,undefined,{add:[{id:'photo',blob:new Blob(['irreplaceable fictional image'])}],remove:[]},'restore-a',context)
 return {store,expense}
}
async function markDeleted(store:LedgerStore){const p=(await store.read('a')).queue[0]!;await store.markFailed('a',p.operation.operation_id,'此分类已删除',undefined,'restore-a','category_deleted',context)}
async function draft(store:LedgerStore){return(await store.read('a')).local[0]!}
test('category_deleted保留结构化码；分类修正保留整条create/update/refund/review链与Blob并准确同步一次',async()=>{
 const {store}=await setup();let current=await draft(store)
 current=await store.save('a',{...data,amount_cents:12000,purpose:'第二步用途'},'private',current.id,current.version,current,undefined,'restore-a',context)
 current=await store.maintain('a',current,{kind:'refund_create',data:{refund_id:'refund',amount_cents:100,occurred_at:data.occurred_at,reason:'虚构退款'}},'restore-a',context)
 await store.maintain('a',current,{kind:'review',data:{rating:4,rating_reason:'保留原因'}},'restore-a',context)
 const original=await store.read('a'),accepted=new Map<string,Expense>();let remote:Expense|undefined,deleted=true,uploads=0
 const engine=new SyncEngine(store,{
  me:async()=>({user}),snapshot:async()=>({...baseSnapshot(remote?[remote]:[]),images:uploads?[{id:'photo',expense_id:remote!.id,mime_type:'text/plain',size_bytes:28}]:[]}),
  send:async(_id,operation)=>{
   if(deleted&&(operation.kind==='create'||operation.kind==='update')&&operation.data.category==='旧分类')throw new ApiError(409,'category_deleted')
   if(accepted.has(operation.operation_id))return{operation_id:operation.operation_id,expense:accepted.get(operation.operation_id)!}
   remote={...applyOperation(remote,operation,'a'),family_id:null};accepted.set(operation.operation_id,remote)
   return{operation_id:operation.operation_id,expense:remote}
  },uploadImage:async(_u,e,id)=>{uploads++;return{id,expense_id:e,mime_type:'text/plain',size_bytes:28}},deleteImage:async()=>{}
 })
 await engine.run('a');let account=await store.read('a')
 expect(account.queue[0]).toMatchObject({error_code:'category_deleted',error:expect.stringContaining('此分类已删除')});expect(accepted.size).toBe(0)
 await store.retryDeletedCategory('a',account.queue[0]!.operation.operation_id,account.local[0]!,'','restore-a',context)
 account=await store.read('a');expect(account.queue.map(p=>p.operation.kind)).toEqual(['create','update','refund_create','review'])
 expect(account.queue.map(p=>p.operation.base_version)).toEqual([0,1,2,3])
 expect(account.queue.slice(0,2).every((p,i)=>p.operation.operation_id!==original.queue[i]!.operation.operation_id)).toBe(true)
 expect(account.queue.slice(2).map(p=>p.operation.operation_id)).toEqual(original.queue.slice(2).map(p=>p.operation.operation_id))
 expect(account.local[0]).toMatchObject({id:original.local[0]!.id,category:'',amount_cents:12000,purpose:'第二步用途',occurred_at:data.occurred_at,location:data.location,rating:4,rating_reason:'保留原因',net_amount_cents:11900,version:4})
 expect(await account.images[0]!.blob!.text()).toBe('irreplaceable fictional image')
 deleted=false;await engine.run('a');await engine.run('a');account=await store.read('a')
 expect(account.queue).toEqual([]);expect(account.snapshot).toHaveLength(1);expect(account.snapshot[0]).toMatchObject({id:original.local[0]!.id,amount_cents:12000,net_amount_cents:11900,version:4,category:''})
 expect(accepted.size).toBe(4);expect(uploads).toBe(1);expect(await account.images[0]!.blob!.text()).toBe('irreplaceable fictional image')
})
test('仅服务端已明确恢复同名目录后才允许原载荷原operation_id重试',async()=>{
 const {store}=await setup();await markDeleted(store);const before=await store.read('a'),op=before.queue[0]!.operation
 await expect(store.retryDeletedCategory('a',op.operation_id,before.local[0]!,undefined,'restore-a',context)).rejects.toThrow('已删除或未同步')
 expect(await store.read('a')).toEqual(before)
 await store.snapshot('a',baseSnapshot([],['交通','旧分类']),'restore-a');await store.retryDeletedCategory('a',op.operation_id,await draft(store),undefined,'restore-a',context)
 const after=await store.read('a');expect(after.queue[0]!.operation).toEqual(op);expect(after.queue[0]!.error).toBeUndefined();expect(after.queue[0]!.error_code).toBeUndefined()
 expect(await after.images[0]!.blob!.text()).toBe('irreplaceable fictional image')
})
test('后续主动选择的不同分类和操作ID保留，只有已删同名payload换ID',async()=>{
 const {store}=await setup();const first=await draft(store)
 await store.save('a',{...data,purpose:'随后主动选购物',category:'交通'},'private',first.id,first.version,first,undefined,'restore-a',context)
 await markDeleted(store);const before=await store.read('a')
 await store.retryDeletedCategory('a',before.queue[0]!.operation.operation_id,before.local[0]!,'','restore-a',context)
 const after=await store.read('a');expect(after.queue[1]!.operation).toEqual(before.queue[1]!.operation);expect(after.local[0]!.category).toBe('交通')
 expect(after.queue[0]!.operation.operation_id).not.toBe(before.queue[0]!.operation.operation_id)
})
test('已存在记录的category_deleted update仅修正分类，不绕过原base_version',async()=>{
 const {store}=await setup();const created=(await store.read('a')).queue[0]!.operation
 const remote={...applyOperation(undefined,created,'a'),family_id:null,category:'交通'}
 await store.acknowledge('a',{operation_id:created.operation_id,expense:remote},'restore-a')
 await store.save('a',{...data,amount_cents:12000,purpose:'更新保留'},'private',remote.id,remote.version,remote,undefined,'restore-a',context)
 await markDeleted(store);let before=await store.read('a');const old=before.queue[0]!.operation
 await store.retryDeletedCategory('a',old.operation_id,before.local[0]!,'交通','restore-a',context)
 let after=await store.read('a');expect(after.queue[0]!.operation).toMatchObject({expense_id:remote.id,kind:'update',base_version:1,data:{category:'交通',amount_cents:12000,purpose:'更新保留'}})
 expect(after.queue[0]!.operation.operation_id).not.toBe(old.operation_id)
 await store.markFailed('a',after.queue[0]!.operation.operation_id,'冲突',{...remote,version:2},'restore-a','version_conflict',context);before=await store.read('a')
 await expect(store.retryDeletedCategory('a',before.queue[0]!.operation.operation_id,before.local[0]!,'','restore-a',context)).rejects.toThrow('不能直接重试')
 expect(await store.read('a')).toEqual(before)
})
test('旧generic/no error_code create只按原payload原ID重试，服务端再判为category_deleted后进入明确修正',async()=>{
 const {store}=await setup();let e=await draft(store);await store.save('a',{...data,amount_cents:12000,purpose:'旧版第二步'},'private',e.id,e.version,e,undefined,'restore-a',context)
 const op=(await store.read('a')).queue[0]!.operation;await store.markFailed('a',op.operation_id,'此笔同步未完成，请处理本地变更后重试',undefined,'restore-a')
 const before=await store.read('a');await store.retryLegacyCreate('a',op.operation_id,before.local[0]!,'restore-a',context)
 const retried=await store.read('a');expect(retried.queue.map(p=>p.operation)).toEqual(before.queue.map(p=>p.operation));expect(retried.queue[0]!.error).toBeUndefined()
 let sendCalls=0;const engine=new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>baseSnapshot(),send:async(_id,sent)=>{sendCalls++;expect(sent).toEqual(op);throw new ApiError(409,'category_deleted')}})
 await engine.run('a');const coded=await store.read('a');expect(coded.queue[0]!.error_code).toBe('category_deleted');expect(sendCalls).toBe(1)
 await expect(store.retryLegacyCreate('a',op.operation_id,coded.local[0]!,'restore-a',context)).rejects.toThrow('仅旧版')
 await store.retryDeletedCategory('a',op.operation_id,coded.local[0]!,'交通','restore-a',context)
 expect((await store.read('a')).local[0]).toMatchObject({category:'交通',purpose:'旧版第二步',amount_cents:12000,version:2});expect(await(await store.read('a')).images[0]!.blob!.text()).toBe('irreplaceable fictional image')
})
test.each(['other_error','restore_review','frozen','owner','changed','missing_category','version'] as const)('不放宽%s门禁，失败事务保留全部队列和Blob',async reason=>{
 const {store}=await setup();await markDeleted(store)
 await store.mutate('a',account=>{
  if(reason==='other_error')account.queue[0]!.error_code='invalid_request'
  if(reason==='restore_review')account.queue[0]!.restore_review=true
  if(reason==='frozen')account.queue[0]!.frozen=true
  if(reason==='owner')account.local[0]!.owner_id='b'
  if(reason==='version')account.snapshot=[{...account.local[0]!,version:9}]
 })
 const before=await store.read('a'),expected=reason==='changed'?{...before.local[0]!,purpose:'旧输入'}:before.local[0]!
 await expect(store.retryDeletedCategory('a',before.queue[0]!.operation.operation_id,expected,reason==='missing_category'?'不存在':'交通','restore-a',context)).rejects.toThrow()
 expect(await store.read('a')).toEqual(before)
})
test('恢复、家庭/membership及账号变化拒绝旧分类修复；迟到错误不覆盖冻结状态',async()=>{
 const {store}=await setup(true);await markDeleted(store);const before=await store.read('a'),op=before.queue[0]!.operation
 await expect(store.retryDeletedCategory('a',op.operation_id,before.local[0]!,'交通','old-restore',context)).rejects.toBeInstanceOf(RestoreChanged)
 await expect(store.retryDeletedCategory('a',op.operation_id,before.local[0]!,'交通','restore-a',{...context,membership_version:0})).rejects.toBeInstanceOf(FamilyChanged)
 await store.transition('a',{family:null,membership_version:2},'restore-a');const frozen=await store.read('a')
 await store.markFailed('a',op.operation_id,'旧请求迟到',undefined,'restore-a','category_deleted',context)
 expect(await store.read('a')).toEqual(frozen)
 await expect(store.retryDeletedCategory('a',op.operation_id,before.local[0]!,'交通','restore-a',context)).rejects.toBeInstanceOf(FamilyChanged)
 await store.activate({id:'b',display_name:'乙'},'bob')
 await expect(store.retryDeletedCategory('a',op.operation_id,before.local[0]!,'交通','restore-a',context)).rejects.toBeInstanceOf(AccountChanged)
})
test('payload修正换ID后，旧失败/回执迟到均不能污染新待办',async()=>{
 const {store}=await setup();await markDeleted(store);const before=await store.read('a'),old=before.queue[0]!.operation
 await store.retryDeletedCategory('a',old.operation_id,before.local[0]!,'交通','restore-a',context);const fixed=await store.read('a')
 await store.markFailed('a',old.operation_id,'旧失败',undefined,'restore-a','category_deleted',context)
 expect(await store.read('a')).toEqual(fixed)
 await expect(store.acknowledge('a',{operation_id:old.operation_id,expense:{...applyOperation(undefined,old,'a'),family_id:null}},'restore-a')).rejects.toThrow('回执')
 expect(await store.read('a')).toEqual(fixed)
})
test.each(['structured','restore','frozen','conflict'] as const)('旧create原载荷出口不绕过%s保护',async reason=>{
 const {store}=await setup();const first=(await store.read('a')).queue[0]!.operation;await store.markFailed('a',first.operation_id,'旧失败',undefined,'restore-a')
 await store.mutate('a',account=>{const p=account.queue[0]!;if(reason==='structured')p.error_code='invalid_request';if(reason==='restore')p.restore_review=true;if(reason==='frozen')p.frozen=true;if(reason==='conflict')p.conflict=account.local[0]})
 const before=await store.read('a');await expect(store.retryLegacyCreate('a',first.operation_id,before.local[0]!,'restore-a',context)).rejects.toThrow('仅旧版');expect(await store.read('a')).toEqual(before)
})
