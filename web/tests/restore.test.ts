import 'fake-indexeddb/auto'
import { expect, test } from 'vitest'
import { LedgerStore } from './demoFixture'
import { RestoreChanged, pendingCount } from '../src/store'
import { SyncEngine } from './demoFixture'
import { ApiError } from '../src/api'
import { applyOperation } from '../src/operations'
import type { Expense, Operation, Snapshot } from '../src/types'
const user={id:'a',display_name:'甲'},lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
const data={amount_cents:20000,purpose:'虚构用途',occurred_at:'2026-09-10T00:00:00Z',location:''}
const original:Expense={...data,id:'expense',owner_id:'a',scope:'shared',version:1}
const snapshot=(expenses:Expense[],restore_id='old',restored_at:string|null=null):Snapshot=>({user,users:[user],expenses,images:[],requests:[],synced_at:'now',restore_id,restored_at})
async function setup(legacy=false){const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',legacy?{...snapshot([original]),restore_id:undefined}:snapshot([original]));return store}
function deferred<T>(){let resolve!:(result:T)=>void;const promise=new Promise<T>(yes=>resolve=yes);return{promise,resolve}}
test.each([false,true])('恢复隔离账务/分类/Blob，冷重开仍保留且零自动写入，legacy=%s',async legacy=>{
 const store=await setup(legacy)
 const edited=await store.save('a',{...data,purpose:'离线草稿'},'shared',original.id,1,original,{add:[{id:'image',blob:new Blob(['retained'],{type:'image/jpeg'})}],remove:[]})
 await store.maintain('a',edited,{kind:'refund_create',data:{refund_id:'r',amount_cents:5000,occurred_at:data.occurred_at,reason:'退款草稿'}})
 await store.addCategory('a','private','新分类')
 const before=await store.read('a');let writes=0
 const remote=snapshot([{...original,amount_cents:18000}],'restored','2026-10-30T00:00:00Z')
 const api={me:async()=>({user}),snapshot:async()=>remote,send:async()=>{writes++;throw new Error('不应发送')},addCategory:async()=>{writes++;return{scope:'private' as const,name:'新分类'}},uploadImage:async()=>{writes++;throw new Error('不应上传')},deleteImage:async()=>{writes++}}
 await new SyncEngine(store,api).run('a')
 const reopened=new LedgerStore(store.name,lock);await new SyncEngine(reopened,api).run('a');const after=await reopened.read('a')
 expect(writes).toBe(0);expect(after.queue.map(p=>p.operation)).toEqual(before.queue.map(p=>p.operation));expect(after.queue.every(p=>p.restore_review&&p.error)).toBe(true)
 expect(after.local[0]?.purpose).toBe('离线草稿');expect(after.local[0]?.net_amount_cents).toBe(15000);expect(after.snapshot[0]?.amount_cents).toBe(18000)
 expect(await after.images[0]?.blob?.text()).toBe('retained');expect(after.images[0]?.restore_review).toBe(true);expect(after.categoryQueue[0]?.restore_review).toBe(true);expect(pendingCount(after)).toBe(4)
})
test('首次无待办账号直接采纳恢复ID，新操作带新ID，报告不写入IDB',async()=>{
 const store=await setup();await store.snapshot('a',snapshot([],'new','date'));await store.save('a',data,'private');let current:Expense[]=[];const sent:string[]=[]
 await new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>snapshot(current,'new','date'),send:async(_id,op,restoreId)=>{sent.push(restoreId!);const expense=applyOperation(undefined,op,'a');current=[expense];return{operation_id:op.operation_id,expense}}}).run('a')
 expect(sent).toEqual(['new']);expect((await store.read('a')).queue).toHaveLength(0);expect(await store.read('a')).not.toHaveProperty('reports')
})
test('恢复后父不存在create显式重建整个操作链，旧ID不用；分类和图片须各自重试',async()=>{
 const store=await setup();const created=await store.save('a',data,'private',undefined,undefined,undefined,{add:[{id:'i',blob:new Blob(['blob'])}],remove:[]});await store.save('a',{...data,purpose:'最终草稿'},'private',created.id,created.version,created);await store.addCategory('a','private','保留分类')
 const oldIds=(await store.read('a')).queue.map(p=>p.operation.operation_id)
 await store.snapshot('a',snapshot([],'new','date'))
 await store.resolve('a',created.id,true,'new');let account=await store.read('a')
 expect(account.queue.map(p=>p.operation.kind)).toEqual(['create','update']);expect(account.queue.map(p=>p.operation.base_version)).toEqual([0,1]);expect(account.queue.every(p=>!oldIds.includes(p.operation.operation_id)&&!p.error)).toBe(true)
 expect(account.images[0]?.restore_review).toBe(true);expect(account.categoryQueue[0]?.restore_review).toBe(true)
 await expect(store.retryImage('a','i')).rejects.toThrow('先核对并同步父记录')
 await store.snapshot('a',snapshot([account.local[0]!],'new','date'));await store.retryImage('a','i');await store.retryCategory('a',{scope:'private',name:'保留分类'});account=await store.read('a');expect(account.images[0]?.error).toBeUndefined();expect(account.categoryQueue[0]?.error).toBeUndefined()
})
test('恢复后已有create绝不覆盖/双记；采用服务器仍保留附件等待显式放弃',async()=>{
 const store=await setup();const created=await store.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'i',blob:new Blob(['unique'])}],remove:[]});await store.snapshot('a',snapshot([created],'new','date'))
 const before=await store.read('a');await expect(store.resolve('a',created.id,true)).rejects.toThrow('不能重复创建或覆盖');expect(await store.read('a')).toEqual(before)
 await store.resolve('a',created.id,false);expect((await store.read('a')).queue).toHaveLength(0);expect(await (await store.read('a')).images[0]?.blob?.text()).toBe('unique')
 await store.retryImage('a','i',true);expect((await store.read('a')).images).toHaveLength(0)
})
test('恢复后退款ID重复和退款上限逐项验证，失败不丢原草稿',async()=>{
 const store=await setup();const refunded=await store.maintain('a',original,{kind:'refund_create',data:{refund_id:'r',amount_cents:5000,occurred_at:data.occurred_at,reason:'退款'}})
 await store.snapshot('a',snapshot([refunded],'new','date'));const before=await store.read('a');await expect(store.resolve('a',original.id,true)).rejects.toThrow('退款已存在');expect(await store.read('a')).toEqual(before)
 await store.mutate('a',a=>{a.queue[0]!.conflict={...original,amount_cents:4000}});await expect(store.resolve('a',original.id,true)).rejects.toThrow('不能超过原付款');expect((await store.read('a')).queue[0]?.restore_review).toBe(true)
})
test('恢复后的未找到父记录附件即使没有队列仍保留Blob与显式放弃出口',async()=>{
 const store=await setup();await store.mutate('a',a=>a.images.push({id:'orphan',expense_id:'expense',blob:new Blob(['only']),size_bytes:4,mime_type:'image/jpeg',pending:'upload'}));await store.snapshot('a',snapshot([],'new','date'));await store.snapshot('a',snapshot([],'new','date'))
 expect(await (await store.read('a')).images[0]?.blob?.text()).toBe('only');await store.retryImage('a','orphan',true);expect((await store.read('a')).images).toHaveLength(0)
})
test('发送途中restore_changed先取新快照，不能把恢复当普通冲突或继续后续写入',async()=>{
 const store=await setup();await store.maintain('a',original,{kind:'review',data:{rating:4,rating_reason:'原因'}});await store.save('a',data,'private')
 let restored=false,writes=0
 await expect(new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>restored?snapshot([original],'new','date'):snapshot([original]),send:async(_id,_op,id)=>{expect(id).toBe('old');writes++;restored=true;throw new ApiError(409,'restore_changed',undefined,'new')}}).run('a')).rejects.toBeInstanceOf(RestoreChanged)
 const account=await store.read('a');expect(account.restore_id).toBe('new');expect(account.queue.every(p=>p.restore_review)).toBe(true);expect(writes).toBe(1)
})
test('其他标签采纳新ID后，迟到旧快照拒绝回退恢复状态和账本',async()=>{
 const store=await setup(),result=deferred<Snapshot>(),started=deferred<void>()
 const running=new SyncEngine(store,{me:async()=>({user}),snapshot:()=>{started.resolve();return result.promise},send:async()=>{throw new Error('不应发送')}}).run('a');const rejected=expect(running).rejects.toBeInstanceOf(RestoreChanged)
 await started.promise;await new LedgerStore(store.name,lock).snapshot('a',snapshot([],'new','date'));result.resolve(snapshot([original]));await rejected;expect((await store.read('a')).restore_id).toBe('new');expect((await store.read('a')).snapshot).toHaveLength(0)
})
test('其他标签恢复后，迟到旧账务回执不能清队列或复活服务器正文',async()=>{
 const store=await setup();const local=await store.maintain('a',original,{kind:'review',data:{rating:5,rating_reason:'原原因'}}),result=deferred<{operation_id:string;expense:Expense}>(),started=deferred<Operation>()
 const running=new SyncEngine(store,{me:async()=>({user}),snapshot:async()=>snapshot([original]),send:async(_id,op)=>{started.resolve(op);return result.promise}}).run('a');const rejected=expect(running).rejects.toBeInstanceOf(RestoreChanged)
 const operation=await started.promise;await new LedgerStore(store.name,lock).snapshot('a',snapshot([],'new','date'));result.resolve({operation_id:operation.operation_id,expense:local});await rejected
 const account=await store.read('a');expect(account.snapshot).toHaveLength(0);expect(account.queue).toHaveLength(1);expect(account.queue[0]?.restore_review).toBe(true)
})
test('迟到分类/图片回执/图片下载不能解除恢复核对或污染恢复后缓存',async()=>{
 const store=await setup();await store.addCategory('a','private','类别');const meta={id:'i',expense_id:'expense',size_bytes:4,mime_type:'image/jpeg'};await store.mutate('a',a=>a.images.push({...meta,pending:'upload',blob:new Blob(['old'])}));const sent=(await store.read('a')).images[0]!
 await store.snapshot('a',{...snapshot([original],'new','date'),images:[meta]})
 await expect(store.categoryResult('a',{scope:'private',name:'类别'},undefined,'old')).rejects.toBeInstanceOf(RestoreChanged)
 await expect(store.imageResult('a',sent,meta,undefined,'old')).rejects.toBeInstanceOf(RestoreChanged)
 await expect(store.cacheImage('a','i',new Blob(['late']),1,'old')).rejects.toBeInstanceOf(RestoreChanged)
 expect((await store.read('a')).categoryQueue[0]?.restore_review).toBe(true);expect(await (await store.read('a')).images[0]?.blob?.text()).toBe('old')
})
test('恢复前打开的同版本同内容编辑也不能绕过恢复核对',async()=>{
 const store=await setup();await store.snapshot('a',snapshot([original],'new','date'));await expect(store.save('a',data,'shared',original.id,1,original,undefined,'old')).rejects.toBeInstanceOf(RestoreChanged);await expect(store.maintain('a',original,{kind:'void',data:{}},'old')).rejects.toBeInstanceOf(RestoreChanged);expect((await store.read('a')).queue).toHaveLength(0)
})
