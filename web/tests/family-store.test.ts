import 'fake-indexeddb/auto'
import { expect, test } from 'vitest'
import { FamilyChanged, RestoreChanged, frozenCount } from '../src/store'
import { FamilyLedgerStore as LedgerStore, SyncEngine } from './demoFixture'
import type { FamilyContext, Snapshot } from '../src/types'

const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
const user={id:'a',username:'alice',display_name:'甲'}
const family={id:'family-1',name:'原家庭',owner_id:'a',members:[user]}
const context:FamilyContext={family,membership_version:1}
const data={amount_cents:1000,purpose:'虚构早餐',occurred_at:'2026-10-30T00:00:00Z',location:''}
const snapshot=(ctx:FamilyContext,expenses:Snapshot['expenses']=[]):Snapshot=>({user,users:[user],expenses,synced_at:'now',...ctx})

test('同成员版本的管理员移交和成员列表变化只更新家庭元数据，保留共同草稿和Blob',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice')
 await store.snapshot('a',{...snapshot(context),restore_id:'domain-1'})
 const expense=await store.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'photo',blob:new Blob(['draft'])}],remove:[]},'domain-1',context)
 const member={id:'b',username:'bob',display_name:'乙'}
 const transferred={family:{...family,owner_id:'b',members:[user,member]},membership_version:1}
 await store.transition('a',transferred,'domain-1')
 let account=await store.read('a')
 expect(account.family).toEqual({...transferred.family,revision:1,relation_epoch:1})
 expect(account.local.some(item=>item.id===expense.id)).toBe(true)
 expect(account.queue[0]?.frozen).toBeUndefined()
 expect(await account.images[0]?.blob?.text()).toBe('draft')
 await store.snapshot('a',{...snapshot({...transferred,family:{...transferred.family,members:[user]}}),restore_id:'domain-1'},'domain-1')
 account=await store.read('a')
 expect(account.family?.members).toEqual([user])
 expect(account.queue[0]?.frozen).toBeUndefined()
 await expect(store.transition('a',context,'old-domain')).rejects.toBeInstanceOf(RestoreChanged)
})

test('solo只允许私人写入；家庭加入后可写共同账',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice')
 await store.snapshot('a',snapshot({family:null,membership_version:0}))
 await expect(store.save('a',data,'shared')).rejects.toThrow('家庭访问已变化')
 await store.save('a',data,'private')
 await store.transition('a',context)
 await store.save('a',data,'shared')
 expect((await store.read('a')).queue.map(item=>item.family_id)).toEqual([null,'family-1'])
})

test('同家退出再加入版本变化冻结旧共同操作和Blob；私人待办继续保留',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',snapshot(context))
 const shared=await store.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'photo',blob:new Blob(['unique image'])}],remove:[]},undefined,context)
 await store.save('a',{...data,purpose:'私人'},'private')
 await store.transition('a',{family:null,membership_version:2})
 await store.transition('a',{family,membership_version:3})
 const account=await store.read('a')
 expect(account.local.map(e=>e.scope)).toEqual(['private'])
 expect(account.queue[0]?.frozen).toBe(true)
 expect(account.images[0]?.blob && await account.images[0].blob.text()).toBe('unique image')
 expect(frozenCount(account)).toBe(2)
 expect(account.queue[1]?.frozen).toBeUndefined()
 expect(account.queue[0]?.operation.expense_id).toBe(shared.id)
 await expect(store.save('a',data,'shared',undefined,undefined,undefined,undefined,undefined,context)).rejects.toBeInstanceOf(FamilyChanged)
 await expect(store.addCategory('a','shared','旧分类',context)).rejects.toBeInstanceOf(FamilyChanged)
 await store.discardFrozen('a')
 expect((await store.read('a')).queue).toHaveLength(1)
})

async function seedV1(name:string,account:any){await new Promise<void>((resolve,reject)=>{const open=indexedDB.open(name,1);open.onupgradeneeded=()=>{open.result.createObjectStore('accounts');open.result.createObjectStore('settings')};open.onsuccess=()=>{const db=open.result,tx=db.transaction(['accounts','settings'],'readwrite');tx.objectStore('accounts').put(account,'a');tx.objectStore('settings').put('a','active');tx.oncomplete=()=>{db.close();resolve()};tx.onabort=()=>reject(tx.error)}})}
const oldAccount=(queue:any[],local:any[])=>({user,username:'alice',restore_id:'demo-domain',...context,snapshot:[],local,queue,users:[user],synced_at:null,categories:[],categoryQueue:[],images:[]})
test('旧队列仅凭服务端精确legacy映射绑定；无来源或无映射则冻结',async()=>{
 for(const eligibility of ['exact','missing-source','missing-mapping']){
  const operation={operation_id:'op',expense_id:'old',base_version:0,kind:'create',data:{...data,scope:'shared'}},name=crypto.randomUUID()
  await seedV1(name,oldAccount([{operation,...(eligibility==='missing-source'?{}:{family_id:family.id,membership_version:1})}],[{...data,id:'old',owner_id:'a',scope:'shared',family_id:family.id,version:1}]))
  const store=new LedgerStore(name,lock),remote=await store.legacyContext('a',context)
  remote.legacy_context_mappings=eligibility==='missing-mapping'?[]:[{family_id:family.id,membership_version:1,relation_epoch:1}]
  await store.reconcile('a',remote)
  expect((await store.read('a')).queue[0]?.frozen===true).toBe(eligibility!=='exact')
  expect(JSON.stringify((await store.read('a')).queue[0]!.operation)).toBe(JSON.stringify(operation))
 }
})

test('旧家庭分类请求在途时切家，迟到回执不能加入新家庭；同名新分类仍可同步',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',snapshot(context))
 await store.addCategory('a','shared','宠物',context)
 const old=(await store.read('a')).categoryQueue[0]!
 let finish!:(value:{scope:'shared';name:string})=>void,started!:()=>void
 const entered=new Promise<void>(resolve=>{started=resolve})
 const delayed=new Promise<{scope:'shared';name:string}>(resolve=>{finish=resolve})
 const newer={family:{id:'family-2',name:'新家庭',members:[user]},membership_version:3}
 const engine=new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>snapshot((await store.read('a')).membership_version===3?newer:context),send:async()=>{throw new Error('无账务操作')},addCategory:async()=>{started();return delayed}})
 const running=engine.run('a');await entered
 await store.transition('a',newer);finish({scope:'shared',name:'宠物'});await running
 expect((await store.read('a')).categoryQueue[0]?.frozen).toBe(true)
 expect((await store.read('a')).categories.some(c=>c.name==='宠物')).toBe(false)
 await store.addCategory('a','shared','宠物',newer)
 const fresh=(await store.read('a')).categoryQueue.find(c=>!c.frozen)!
 await store.categoryResult('a',old)
 expect((await store.read('a')).categoryQueue.find(c=>!c.frozen)).toEqual(fresh)
 await store.categoryResult('a',fresh)
 expect((await store.read('a')).categories).toContainEqual({scope:'shared',name:'宠物'})
})

test('迟到的旧离组响应不能回退新家庭版本或冻结新待办',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',snapshot(context))
 const newer={family:{id:'family-2',name:'新家庭',members:[user]},membership_version:3}
 await store.transition('a',newer);await store.save('a',data,'shared',undefined,undefined,undefined,undefined,undefined,newer)
 await store.transition('a',{family:null,membership_version:2})
 const account=await store.read('a')
 expect(account.family?.id).toBe('family-2');expect(account.membership_version).toBe(3)
 expect(account.queue[0]?.frozen).toBeUndefined()
})

test('旧IDB私人创建后转共同再转私人，整条操作链按曾转共同冻结且绝不送入新家庭',async()=>{
 const id='legacy-chain',queue=[{operation:{operation_id:'create',expense_id:id,base_version:0,kind:'create',data:{...data,scope:'private'}}},{operation:{operation_id:'scope-shared',expense_id:id,base_version:1,kind:'scope',data:{scope:'shared'}}},{operation:{operation_id:'scope-private',expense_id:id,base_version:2,kind:'scope',data:{scope:'private'}}}],name=crypto.randomUUID()
 await seedV1(name,oldAccount(queue,[{...data,id,owner_id:'a',scope:'private',family_id:null,version:3}]))
 const store=new LedgerStore(name,lock),before=queue.map(p=>JSON.stringify(p.operation)),newer={family:{id:'family-2',name:'新家庭',members:[user]},membership_version:3}
 let sends=0
 await new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>({...snapshot(newer),legacy_family_id:'family-1'}),send:async()=>{sends++;throw new Error('旧链不应发送')}}).run('a')
 const account=await store.read('a')
 expect(sends).toBe(0);expect(account.queue).toHaveLength(3);expect(account.queue.every(i=>i.frozen&&i.legacy_source?.family_id==='unknown')).toBe(true)
 expect(account.queue.map(i=>JSON.stringify(i.operation))).toEqual(before);expect(account.local.some(e=>e.id===id)).toBe(false)
})

test('新恢复域接纳较低会员版本，撤旧共同缓存并保留私人待办与两种Blob',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice')
 const oldShared={...data,id:'old-shared',owner_id:'a',scope:'shared' as const,family_id:family.id,version:1}
 await store.snapshot('a',{...snapshot({...context,membership_version:3},[oldShared]),restore_id:'before',images:[{id:'synced',expense_id:oldShared.id,mime_type:'image/jpeg',size_bytes:3}]})
 await store.save('a',{...data,purpose:'私人物品'},'private',undefined,undefined,undefined,{add:[{id:'private-blob',blob:new Blob(['private'])}],remove:[]})
 await store.save('a',{...data,purpose:'旧共同待办'},'shared',undefined,undefined,undefined,{add:[{id:'shared-blob',blob:new Blob(['shared'])}],remove:[]})
 await store.snapshot('a',{...snapshot({family:null,membership_version:0}),restore_id:'after',restored_at:'2026-10-30T01:00:00Z',images:[]},'before')
 const account=await store.read('a')
 expect(account.restore_id).toBe('after');expect(account.family).toBeNull();expect(account.membership_version).toBe(0)
 expect(account.snapshot).toHaveLength(0);expect(account.local.map(e=>e.scope)).toEqual(['private'])
 expect(account.queue[0]?.restore_review).toBe(true);expect(account.queue[0]?.frozen).toBeUndefined()
 expect(account.queue[1]?.frozen).toBe(true)
 expect(await account.images.find(i=>i.id==='private-blob')?.blob?.text()).toBe('private')
 expect(account.images.find(i=>i.id==='private-blob')?.restore_review).toBe(true)
 expect(await account.images.find(i=>i.id==='shared-blob')?.blob?.text()).toBe('shared')
 expect(account.images.find(i=>i.id==='shared-blob')?.frozen).toBe(true)
 expect(account.images.some(i=>i.id==='synced')).toBe(false)
})

test('同家恢复可显式审阅并重绑共同待办、分类和Blob到新版本',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice')
 const older={family,membership_version:3}
 await store.snapshot('a',{...snapshot(older),restore_id:'before'})
 const created=await store.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'photo',blob:new Blob(['original'])}],remove:[]})
 await store.addCategory('a','shared','新分类',older)
 const before=(await store.read('a')).queue[0]!.operation.operation_id
 await store.snapshot('a',{...snapshot(context),restore_id:'after',restored_at:'2026-10-30T01:00:00Z',images:[]},'before')
 let account=await store.read('a')
 expect(account.queue[0]).toMatchObject({restore_review:true,family_id:family.id,source:{relation_epoch:3}})
 expect(account.queue[0]?.frozen).toBeUndefined()
 expect(account.categoryQueue[0]?.restore_review).toBe(true)
 expect(account.images[0]?.restore_review).toBe(true)
 expect(await account.images[0]?.blob?.text()).toBe('original')
 await store.resolve('a',created.id,true,'after')
 account=await store.read('a')
 expect(account.queue[0]?.source?.relation_epoch).toBe(1)
 expect(account.queue[0]?.operation.operation_id).not.toBe(before)
 await store.acknowledge('a',{operation_id:account.queue[0]!.operation.operation_id,expense:{...created,family_id:family.id}},'after')
 await store.snapshot('a',{...snapshot(context,[{...created,family_id:family.id}]),restore_id:'after',images:[]},'after')
 await store.retryImage('a','photo',false,'after')
 await store.retryCategory('a',{scope:'shared',name:'新分类'},false,'after')
 account=await store.read('a')
 expect(account.images[0]).toMatchObject({source:{relation_epoch:1},pending:'upload'})
 expect(account.images[0]?.error).toBeUndefined()
 expect(account.categoryQueue[0]?.source?.relation_epoch).toBe(1)
 expect(account.categoryQueue[0]?.error).toBeUndefined()
})

test('旧恢复域的迟到家庭响应不能覆盖新域',async()=>{
 const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice')
 await store.snapshot('a',{...snapshot({...context,membership_version:3}),restore_id:'before'})
 await store.snapshot('a',{...snapshot({family:null,membership_version:0}),restore_id:'after',restored_at:'2026-10-30T01:00:00Z'},'before')
 await expect(store.transition('a',{family,membership_version:4},'before')).rejects.toBeInstanceOf(RestoreChanged)
 const account=await store.read('a');expect(account.restore_id).toBe('after');expect(account.family).toBeNull();expect(account.membership_version).toBe(0)
})
