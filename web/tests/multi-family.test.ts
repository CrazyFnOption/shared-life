import 'fake-indexeddb/auto'
import { IDBObjectStore } from 'fake-indexeddb'
import { expect,test,vi } from 'vitest'
import { LedgerStore,FamilyChanged } from '../src/store'
import { SyncEngine } from '../src/sync'
import { sourceFor,sameSource } from '../src/sources'
import type { AccountContext,Source,Snapshot,Operation } from '../src/types'
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
const user={id:'user-a',username:'alice',display_name:'虚构甲'}
const data={amount_cents:123,purpose:'虚构早餐',occurred_at:'2026-11-01T00:00:00Z',location:'',category:'餐饮'}
const context=(version=1):AccountContext=>({protocol_version:2,restore_id:'restore-a',context_version:version,families:[{id:'family-a',name:'甲',owner_id:user.id,revision:1,relation_epoch:3,members:[{...user,relation_epoch:3}]},{id:'family-b',name:'乙',owner_id:'user-b',revision:1,relation_epoch:7,members:[{...user,relation_epoch:7}]}],default_scope:{scope:'shared',family_id:'family-a',version:1},default_invalidated_family_id:null,legacy_context_mappings:[{family_id:'family-a',membership_version:5,relation_epoch:3}]})
async function setup(){const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.reconcile(user.id,context());const a=await store.read(user.id);return{store,privateSource:sourceFor(a,{scope:'private',family_id:null}),alpha:sourceFor(a,{scope:'shared',family_id:'family-a'}),beta:sourceFor(a,{scope:'shared',family_id:'family-b'})}}
const snapshot=(source:Source,expenses:Snapshot['expenses']=[]):Snapshot=>({source,user,users:[user],expenses,synced_at:'now',categories:[],images:[]})
test('三范围正文和同名分类独立；账户待办统计涵盖所有范围',async()=>{
 const {store,alpha,beta,privateSource}=await setup()
 for(const source of [alpha,beta,privateSource]){await store.save(user.id,data,source.scope,undefined,undefined,undefined,undefined,source.restore_id,source);await store.addCategory(user.id,source.scope,'餐饮',source)}
 for(const source of [alpha,beta,privateSource]){const view=await store.readProjection(user.id,source);expect(view.local).toHaveLength(1);expect(view.categoryQueue).toHaveLength(1);expect(view.pending_summary).toEqual({active:6,frozen:0});expect(view.local[0]?.family_id).toBe(source.family_id)}
 await store.snapshot(user.id,snapshot(alpha),alpha.restore_id)
 expect((await store.readProjection(user.id,beta)).local).toHaveLength(1)
 await expect(store.deactivate(user.id)).rejects.toThrow('待同步')
})
test('普通导航只读投影，甲撤权只冻结甲，乙与私人可继续；重入不解冻',async()=>{
 const {store,alpha,beta,privateSource}=await setup()
 for(const source of [alpha,beta,privateSource])await store.save(user.id,data,source.scope,undefined,undefined,undefined,{add:[{id:crypto.randomUUID(),blob:new Blob([source.family_id??'private'])}],remove:[]},source.restore_id,source)
 const before=await store.read(user.id);await store.readProjection(user.id,beta);expect((await store.read(user.id)).queue).toEqual(before.queue)
 const next=context(2);next.families=next.families.filter(f=>f.id!==alpha.family_id);await store.reconcile(user.id,next)
 await expect(store.readProjection(user.id,alpha)).rejects.toBeInstanceOf(FamilyChanged)
 expect((await store.readProjection(user.id,beta)).local).toHaveLength(1);expect((await store.readProjection(user.id,privateSource)).local).toHaveLength(1)
 const revoked=await store.read(user.id);expect(revoked.queue.filter(p=>p.frozen)).toHaveLength(1);expect(revoked.images.filter(i=>i.frozen)).toHaveLength(1)
 const rejoin=context(3);rejoin.families[0]!.relation_epoch=5;await store.reconcile(user.id,rejoin)
 expect((await store.read(user.id)).queue.find(p=>p.source?.family_id==='family-a')?.frozen).toBe(true)
 await expect(store.save(user.id,data,'shared',undefined,undefined,undefined,undefined,alpha.restore_id,alpha)).rejects.toBeInstanceOf(FamilyChanged)
})
test('private→甲→private canonical链严格串行，回执原子迁移，无双计与改投',async()=>{
 const {store,alpha,privateSource}=await setup()
 const first=await store.save(user.id,data,'private',undefined,undefined,undefined,{add:[{id:'image-a',blob:new Blob(['original bytes'])}],remove:[]},privateSource.restore_id,privateSource)
 const shared=await store.maintain(user.id,first,{kind:'scope',data:{scope:'shared'}},alpha.restore_id,alpha)
 const final=await store.maintain(user.id,shared,{kind:'scope',data:{scope:'private'}},alpha.restore_id,alpha)
 const before=await store.read(user.id)
 expect(before.queue.map(p=>p.source?.scope)).toEqual(['private','shared','shared'])
 expect((await store.readProjection(user.id,privateSource)).local[0]?.version).toBe(3)
 expect((await store.readProjection(user.id,alpha)).local).toHaveLength(0)
 expect(before.images[0]).toMatchObject({original_source:privateSource,destination_source:privateSource,parent_scope_operation_id:before.queue[2]!.operation.operation_id})
 const receipts=[first,shared,final]
 for(let index=0;index<3;index++){const p=(await store.read(user.id)).queue[0]!;await store.acknowledge(user.id,{operation_id:p.operation.operation_id,expense:receipts[index]!},alpha.restore_id,p.source);expect((await store.read(user.id)).local.filter(e=>e.id===first.id)).toHaveLength(1)}
 expect((await store.read(user.id)).queue).toHaveLength(0)
 expect(await (await store.read(user.id)).images[0]?.blob?.text()).toBe('original bytes')
})
test('跨分区回执事务失败保留完整队列与两分区，不能提前报告成功',async()=>{
 const {store,alpha,privateSource}=await setup();const first=await store.save(user.id,data,'private',undefined,undefined,undefined,undefined,alpha.restore_id,privateSource),next=await store.maintain(user.id,first,{kind:'scope',data:{scope:'shared'}},alpha.restore_id,alpha)
 const before=await store.read(user.id),pending=before.queue[0]!
 const spy=vi.spyOn(IDBObjectStore.prototype,'put').mockImplementationOnce(()=>{throw new DOMException('full','QuotaExceededError')})
 await expect(store.acknowledge(user.id,{operation_id:pending.operation.operation_id,expense:first},alpha.restore_id,privateSource)).rejects.toThrow('full');spy.mockRestore()
 expect(await store.read(user.id)).toEqual(before);expect((await store.readProjection(user.id,alpha)).local[0]).toEqual(next)
})
test('单Engine对三范围按固定来源同步，导航不改变发送来源',async()=>{
 const {store,alpha,beta,privateSource}=await setup()
 const first=await store.save(user.id,data,'private',undefined,undefined,undefined,undefined,alpha.restore_id,privateSource)
 await store.maintain(user.id,first,{kind:'scope',data:{scope:'shared'}},alpha.restore_id,alpha)
 await store.save(user.id,data,'shared',undefined,undefined,undefined,undefined,beta.restore_id,beta)
 const sent:{op:Operation;source:Source}[]=[],server=new Map<string,Snapshot['expenses'][number]>()
 const engine=new SyncEngine(store,{me:async()=>({user}),context:async()=>context(),snapshot:async(_id,source)=>snapshot(source,[...server.values()].filter(e=>e.scope===source.scope&&(e.scope==='private'||e.family_id===source.family_id))),send:async(_id,op,_restore,source)=>{sent.push({op,source});await store.readProjection(user.id,beta);const {applyOperation}=await import('../src/operations');const e=applyOperation(server.get(op.expense_id),op,user.id);e.family_id=e.scope==='shared'?source.family_id:null;server.set(e.id,e);return{operation_id:op.operation_id,expense:e}}})
 await engine.run(user.id)
 expect(sent.map(s=>s.source.family_id)).toEqual([null,'family-a','family-b']);expect((await store.read(user.id)).queue).toHaveLength(0)
})
async function oldDatabase(name:string,frozen=false){await new Promise<void>((resolve,reject)=>{const open=indexedDB.open(name,1);open.onupgradeneeded=()=>{open.result.createObjectStore('accounts');open.result.createObjectStore('settings')};open.onerror=()=>reject(open.error);open.onsuccess=()=>{const db=open.result,tx=db.transaction(['accounts','settings'],'readwrite'),operation={operation_id:'unchanged-op',expense_id:'legacy-expense',base_version:0,kind:'create',data:{...data,scope:'shared'}};tx.objectStore('accounts').put({user,username:'alice',restore_id:'restore-a',family:context().families[0],membership_version:5,snapshot:[],local:[{...data,id:'legacy-expense',owner_id:user.id,scope:'shared',family_id:'family-a',version:1}],queue:[{operation,family_id:'family-a',membership_version:5,...(frozen?{frozen:true,error:'old frozen'}:{})}],users:[user],synced_at:null,categories:[],categoryQueue:[],images:[{id:'legacy-image',expense_id:'legacy-expense',mime_type:'image/jpeg',size_bytes:6,pending:'upload',blob:new Blob(['legacy']),family_id:'family-a',membership_version:5}]},user.id);tx.objectStore('settings').put(user.id,'active');tx.oncomplete=()=>{db.close();resolve()};tx.onabort=()=>reject(tx.error)}})}
test('真实v1 shape原子升级，exact映射保持旧JSON/id/Blob，原frozen绝不解冻',async()=>{
 for(const frozen of [false,true]){const name=crypto.randomUUID();await oldDatabase(name,frozen);const store=new LedgerStore(name,lock),before=await store.read(user.id),json=JSON.stringify(before.queue[0]!.operation);await store.reconcile(user.id,context());const after=await store.read(user.id);expect(JSON.stringify(after.queue[0]!.operation)).toBe(json);expect(after.queue[0]?.frozen===true).toBe(frozen);expect(after.queue[0]?.source?.relation_epoch).toBe(3);expect(await after.images[0]?.blob?.text()).toBe('legacy');await store.reconcile(user.id,context());expect((await store.read(user.id)).queue).toHaveLength(1)}
})
test('首次离线精确旧来源staging可继续保存，联网只按精确mapping绑定，unknown冻结',async()=>{
 const name=crypto.randomUUID();await oldDatabase(name);const store=new LedgerStore(name,lock),before=await store.read(user.id),legacy={protocol_version:1 as const,user_id:user.id,restore_id:'restore-a',scope:'shared' as const,family_id:'family-a',membership_version:5}
 await store.save(user.id,{...data,purpose:'离线继续'},'shared',undefined,undefined,undefined,undefined,'restore-a',{family:before.family!,membership_version:5,legacy_source:legacy})
 expect((await store.read(user.id)).queue.every(p=>p.legacy_source&&!p.source&&!p.frozen)).toBe(true)
 await store.reconcile(user.id,context());expect((await store.read(user.id)).queue.every(p=>sameSource(p.source,{...legacy,protocol_version:2,relation_epoch:3}))).toBe(true)
 const missing=context(2);missing.legacy_context_mappings=[];const name2=crypto.randomUUID();await oldDatabase(name2);const second=new LedgerStore(name2,lock);await second.reconcile(user.id,missing);expect((await second.read(user.id)).queue[0]?.frozen).toBe(true)
})
test('context迟到旧版本不撤新关系，已撤家旧回执不结清待办',async()=>{
 const {store,alpha}=await setup();const e=await store.save(user.id,data,'shared',undefined,undefined,undefined,undefined,alpha.restore_id,alpha),op=(await store.read(user.id)).queue[0]!.operation
 const newer=context(4);await store.reconcile(user.id,newer);const old=context(2);old.families=[];await store.reconcile(user.id,old);expect((await store.read(user.id)).context?.families).toHaveLength(2)
 const revoked=context(5);revoked.families=revoked.families.filter(f=>f.id!==alpha.family_id);await store.reconcile(user.id,revoked)
 await expect(store.acknowledge(user.id,{operation_id:op.operation_id,expense:e},alpha.restore_id,alpha)).rejects.toThrow();expect((await store.read(user.id)).queue).toHaveLength(1)
})
async function seedAccount(name:string,account:any){await new Promise<void>((resolve,reject)=>{const open=indexedDB.open(name,1);open.onupgradeneeded=()=>{open.result.createObjectStore('accounts');open.result.createObjectStore('settings')};open.onsuccess=()=>{const db=open.result,tx=db.transaction(['accounts','settings'],'readwrite');tx.objectStore('accounts').put(account,user.id);tx.objectStore('settings').put(user.id,'active');tx.oncomplete=()=>{db.close();resolve()};tx.onabort=()=>reject(tx.error)}})}
test('v1跨scope完整旧链精确映射后保留每条原来源；原附件与目标显式绑定',async()=>{
 const name=crypto.randomUUID(),id='legacy-flip',operations:Operation[]=[{operation_id:'legacy-create',expense_id:id,base_version:0,kind:'create',data:{...data,scope:'private'}},{operation_id:'legacy-shared',expense_id:id,base_version:1,kind:'scope',data:{scope:'shared'}},{operation_id:'legacy-private',expense_id:id,base_version:2,kind:'scope',data:{scope:'private'}}]
 await seedAccount(name,{user,username:'alice',restore_id:'restore-a',family:context().families[0],membership_version:5,snapshot:[],local:[{...data,id,owner_id:user.id,scope:'private',family_id:null,version:3}],queue:operations.map(operation=>({operation,family_id:operation.kind==='create'?null:'family-a',membership_version:5})),users:[user],synced_at:null,categories:[],categoryQueue:[],images:[{id:'legacy-flip-image',expense_id:id,mime_type:'image/jpeg',size_bytes:6,pending:'upload',blob:new Blob(['legacy']),family_id:null,membership_version:5}]})
 const store=new LedgerStore(name,lock);await store.reconcile(user.id,context());const a=await store.read(user.id)
 expect(a.queue.map(p=>p.source?.scope)).toEqual(['private','shared','shared']);expect(a.queue.map(p=>JSON.stringify(p.operation))).toEqual(operations.map(JSON.stringify));expect(a.queue.some(p=>p.frozen)).toBe(false)
 const privateSource=sourceFor(a,{scope:'private',family_id:null});expect((await store.readProjection(user.id,privateSource)).local).toHaveLength(1)
 expect(a.images[0]).toMatchObject({original_source:privateSource,destination_source:privateSource,parent_scope_operation_id:'legacy-private'})
})
test('原共同附件失权即冻结，即使显式目标已改私人，不能借目标绕过',async()=>{
 const {store,alpha,privateSource}=await setup(),first=await store.save(user.id,data,'shared',undefined,undefined,undefined,{add:[{id:'alpha-blob',blob:new Blob(['keep bytes'])}],remove:[]},alpha.restore_id,alpha)
 let pending=(await store.read(user.id)).queue[0]!;await store.acknowledge(user.id,{operation_id:pending.operation.operation_id,expense:first},alpha.restore_id,alpha)
 const moved=await store.maintain(user.id,first,{kind:'scope',data:{scope:'private'}},alpha.restore_id,alpha);pending=(await store.read(user.id)).queue[0]!;await store.acknowledge(user.id,{operation_id:pending.operation.operation_id,expense:moved},alpha.restore_id,alpha)
 const revoked=context(2);revoked.families=revoked.families.filter(f=>f.id!==alpha.family_id);await store.reconcile(user.id,revoked)
 expect((await store.read(user.id)).images[0]?.frozen).toBe(true);expect((await store.readProjection(user.id,privateSource)).images).toHaveLength(0);expect(await (await store.read(user.id)).images[0]?.blob?.text()).toBe('keep bytes')
})
test('旧IDB迁移配额失败整体回滚；原操作JSON/Blob和active仍可重试',async()=>{
 const name=crypto.randomUUID();await oldDatabase(name);const store=new LedgerStore(name,lock),before=await store.read(user.id)
 const spy=vi.spyOn(IDBObjectStore.prototype,'put').mockImplementationOnce(()=>{throw new DOMException('migration full','QuotaExceededError')})
 await expect(store.reconcile(user.id,context())).rejects.toThrow('migration full');spy.mockRestore();expect(await store.read(user.id)).toEqual(before);expect(await store.active()).toBe(user.id)
 await store.reconcile(user.id,context());expect(await (await store.read(user.id)).images[0]?.blob?.text()).toBe('legacy')
})
test('blocked保留旧库并在关闭旧页后继续；versionchange关连接后拒绝继续写，旧v1打不开v2',async()=>{
 const name=crypto.randomUUID();await oldDatabase(name)
 const old=await new Promise<IDBDatabase>((resolve,reject)=>{const open=indexedDB.open(name,1);open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error)})
 const store=new LedgerStore(name,lock),blocked=new Promise<string>(resolve=>{store.onStatus=value=>{if(value)resolve(value)}}),pending=store.read(user.id)
 expect(await blocked).toContain('请关闭其它旧版页面');old.close();expect((await pending).queue[0]?.operation.operation_id).toBe('unchanged-op')
 const legacyError=await new Promise<string>(resolve=>{const open=indexedDB.open(name,1);open.onerror=()=>resolve(open.error!.name)})
 expect(legacyError).toBe('VersionError')
 const upgraded=await new Promise<IDBDatabase>((resolve,reject)=>{const open=indexedDB.open(name,4);open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error)})
 expect(store.status).toContain('请重新打开');await expect(store.read(user.id)).rejects.toThrow('应用已更新');upgraded.close()
})
test('独立旧私人链缺恢复域可离线续存，联网后逐项review保留且绝不自动发送',async()=>{
 const name=crypto.randomUUID(),id='old-private',operation:Operation={operation_id:'old-private-op',expense_id:id,base_version:0,kind:'create',data:{...data,scope:'private'}}
 await seedAccount(name,{user,username:'alice',snapshot:[],local:[{...data,id,owner_id:user.id,scope:'private',version:1}],queue:[{operation,family_id:null}],users:[user],synced_at:null,categories:[],categoryQueue:[],images:[]})
 const store=new LedgerStore(name,lock),legacy={protocol_version:1 as const,user_id:user.id,restore_id:'',scope:'private' as const,family_id:null,membership_version:0}
 await store.save(user.id,{...data,purpose:'offline continuation'},'private',undefined,undefined,undefined,undefined,'',{family:null,membership_version:0,legacy_source:legacy})
 expect((await store.readProjection(user.id,legacy)).local).toHaveLength(2)
 let sends=0;await new SyncEngine(store,{me:async()=>({user}),context:async()=>context(),snapshot:async(_id,s)=>snapshot(s),send:async()=>{sends++;throw new Error('no replay')}}).run(user.id)
 const a=await store.read(user.id);expect(sends).toBe(0);expect(a.queue.every(p=>p.restore_review&&!p.frozen)).toBe(true);expect(a.local).toHaveLength(2);expect(a.queue[0]?.operation.operation_id).toBe('old-private-op')
})
test('Vue响应式来源与账图数据在持久化边界变为可克隆DTO，原JSON与Blob保持',async()=>{
 const {reactive}=await import('vue'),{storageValue}=await import('../src/store'),{store,alpha}=await setup()
 const source=reactive(alpha),input=reactive({...data}),blob=new Blob(['original reactive photo bytes']),attachments=reactive({add:[{id:'vue-photo',blob}],remove:[] as string[]})
 const expense=await store.save(user.id,input,'shared',undefined,undefined,undefined,attachments,alpha.restore_id,source)
 const after=await store.read(user.id),operation=after.queue[0]!.operation
 expect(operation.data).toEqual({...data,scope:'shared'});expect(after.queue[0]?.source).toEqual(alpha);expect(await after.images[0]?.blob?.text()).toBe('original reactive photo bytes')
 expect((await store.readProjection(user.id,alpha)).local[0]?.id).toBe(expense.id)
 const copy=storageValue(reactive({operation,source,blob}));expect(JSON.stringify(copy.operation)).toBe(JSON.stringify(operation));expect(copy.blob).toBe(blob);expect(()=>structuredClone(copy)).not.toThrow()
})
test('仅scope→private的旧链仍涉及共同，缺精确来源时冻结零send且原JSON/Blob不变',async()=>{
 const name=crypto.randomUUID(),id='unknown-shared-before',operation:Operation={operation_id:'unknown-private-scope',expense_id:id,base_version:2,kind:'scope',data:{scope:'private'}}
 await seedAccount(name,{user,username:'alice',restore_id:'restore-a',snapshot:[],local:[{...data,id,owner_id:user.id,scope:'private',version:3}],queue:[{operation}],users:[user],synced_at:null,categories:[],categoryQueue:[],images:[{id:'unknown-before-photo',expense_id:id,mime_type:'image/jpeg',size_bytes:14,pending:'upload',blob:new Blob(['original bytes'])}]})
 const store=new LedgerStore(name,lock);let sends=0
 await new SyncEngine(store,{me:async()=>({user}),context:async()=>context(),snapshot:async(_id,source)=>snapshot(source),send:async()=>{sends++;throw new Error('unknown source must not send')}}).run(user.id)
 const after=await store.read(user.id);expect(sends).toBe(0);expect(after.queue[0]?.frozen).toBe(true);expect(after.queue[0]?.source).toBeUndefined();expect(JSON.stringify(after.queue[0]?.operation)).toBe(JSON.stringify(operation));expect(after.local.some(e=>e.id===id)).toBe(false)
 expect(after.images).toHaveLength(1);expect(after.images[0]?.frozen).toBe(true);expect(after.images[0]?.expense_id).toBe(id);expect(await after.images[0]?.blob?.text()).toBe('original bytes')
})
