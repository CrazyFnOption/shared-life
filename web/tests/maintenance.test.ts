import 'fake-indexeddb/auto'
import { expect, test } from 'vitest'
import { LedgerStore } from './demoFixture'
import { pendingCount } from '../src/store'
import { SyncEngine } from './demoFixture'
import { applyOperation, netAmount, normalizeExpense } from '../src/operations'
import type { ChangeRequest, Expense, Maintenance, Operation, Snapshot } from '../src/types'
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
const user={id:'a',display_name:'甲'}
const data={amount_cents:20000,purpose:'原款',occurred_at:'2026-09-10T00:00:00Z',location:'',category:'购物'}
const original:Expense={...data,id:'expense',owner_id:'a',scope:'shared',version:1}
const refund=(id='refund',amount=5000):Maintenance=>({kind:'refund_create',data:{refund_id:id,amount_cents:amount,occurred_at:'2026-10-30T00:00:00Z',reason:'部分退货'}})
const snapshot=(expenses:Expense[],extra:Partial<Snapshot>={}):Snapshot=>({user,users:[user],expenses,synced_at:'now',...extra})
async function setup(){const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');await store.snapshot('a',snapshot([original]));return store}
async function current(store:LedgerStore){return (await store.read('a')).local[0]!}
test('旧本机缓存补默认新字段但旧队列原始payload保持字节内容',async()=>{
 const store=await setup();await store.save('a',data,'shared',original.id,1,original)
 const before=JSON.stringify((await store.read('a')).queue[0]!.operation)
 const reopened=new LedgerStore(store.name,lock);await reopened.snapshot('a',snapshot([original]))
 const account=await reopened.read('a');expect(JSON.stringify(account.queue[0]!.operation)).toBe(before)
 expect(account.local[0]).toMatchObject({voided:false,rating:null,rating_reason:'',refunds:[],net_amount_cents:20000})
 expect(account.queue[0]!.operation.data).not.toHaveProperty('rating')
})
test('离线退款、退款更正、评价及scope操作链关闭重开仍保留完整内容',async()=>{
 const store=await setup();let e=await store.maintain('a',await current(store),refund())
 e=await store.maintain('a',e,{kind:'refund_update',data:{refund_id:'refund',amount_cents:7000,occurred_at:'2026-10-30T01:00:00Z',reason:'更正金额'}})
 e=await store.maintain('a',e,{kind:'review',data:{rating:5,rating_reason:'使用满意'}})
 await store.maintain('a',e,{kind:'scope',data:{scope:'private'}})
 const reopened=new LedgerStore(store.name,lock);await reopened.snapshot('a',snapshot([original]));const account=await reopened.read('a')
 expect(account.local[0]).toMatchObject({amount_cents:20000,net_amount_cents:13000,scope:'private',rating:5,rating_reason:'使用满意',version:5})
 expect(account.local[0]!.refunds![0]).toMatchObject({amount_cents:7000,reason:'更正金额',version:2})
 expect(account.local[0]!.occurred_at).toBe(data.occurred_at)
 expect(account.queue.map(p=>p.operation.kind)).toEqual(['refund_create','refund_update','review','scope'])
})
test('多次退款、全额退款、作废退款及父作废正确计算；作废后拒绝修改',async()=>{
 const store=await setup();let e=await store.maintain('a',await current(store),refund())
 e=await store.maintain('a',e,refund('refund-2',15000));expect(netAmount(e)).toBe(0)
 e=await store.maintain('a',e,{kind:'refund_void',data:{refund_id:'refund'}});expect(netAmount(e)).toBe(5000);expect(e.refunds![0]!.voided).toBe(true)
 e=await store.maintain('a',e,{kind:'void',data:{}});expect(netAmount(e)).toBe(0)
 await expect(store.maintain('a',e,{kind:'scope',data:{scope:'private'}})).rejects.toThrow('已作废')
 expect((await store.read('a')).queue).toHaveLength(4)
})
test('超额退款与下调原付款拒绝整个事务，不丢原队列',async()=>{
 const store=await setup();const e=await store.maintain('a',await current(store),refund('r',15000));const before=await store.read('a')
 await expect(store.maintain('a',e,refund('r2',5001))).rejects.toThrow('不能超过原付款')
 await expect(store.save('a',{...data,amount_cents:14000},'shared',e.id,e.version,e)).rejects.toThrow('不能超过原付款')
 expect(await store.read('a')).toEqual(before)
})
test.each([
 {rating:0,rating_reason:'原因'},{rating:6,rating_reason:'原因'},
 {rating:null,rating_reason:'仍有原因'},{rating:3.5,rating_reason:'原因'}
])('评分必须为成组有效内容 %j',async review=>{
 const store=await setup();await expect(store.maintain('a',await current(store),{kind:'review',data:review})).rejects.toThrow();expect((await store.read('a')).queue).toHaveLength(0)
})
test('省略评分保留原评价、明确null清除、不注入真实3分',async()=>{
 const store=await setup();let e=await store.maintain('a',await current(store),{kind:'review',data:{rating:4,rating_reason:'好用'}})
 e=await store.save('a',{...data,purpose:'修改用途'},'shared',e.id,e.version,e);expect(e.rating).toBe(4)
 e=await store.maintain('a',e,{kind:'review',data:{rating:null,rating_reason:''}});expect(e.rating).toBeNull();expect(e.rating_reason).toBe('')
})
test.each(['refunds','rating','voided'] as const)('同版本新字段%s不同也拒绝过时表单',async field=>{
 const store=await setup();const changed:Expense=field==='refunds'?{...original,refunds:[{id:'r',amount_cents:100,occurred_at:data.occurred_at,reason:'退货',voided:false,version:1}]}:field==='rating'?{...original,rating:5,rating_reason:'很好'}:{...original,voided:true}
 await store.snapshot('a',snapshot([changed]));await expect(store.save('a',data,'shared',original.id,1,original)).rejects.toThrow('已变化')
})
test('退款/评分/scope冲突逐项重建原动作，不压成update',async()=>{
 const store=await setup();let e=await store.maintain('a',await current(store),refund())
 e=await store.maintain('a',e,{kind:'review',data:{rating:4,rating_reason:'质量好'}})
 await store.maintain('a',e,{kind:'scope',data:{scope:'private'}})
 const before=await store.read('a');const conflict={...original,version:2,purpose:'服务端更正用途'}
 await store.markFailed('a',before.queue[0]!.operation.operation_id,'版本冲突',conflict)
 await store.resolve('a',original.id,true);const after=await store.read('a')
 expect(after.queue.map(p=>p.operation.kind)).toEqual(['refund_create','review','scope'])
 expect(after.queue.map(p=>p.operation.base_version)).toEqual([2,3,4])
 expect(after.queue.every(p=>!before.queue.some(old=>old.operation.operation_id===p.operation.operation_id))).toBe(true)
 expect(after.local[0]).toMatchObject({purpose:'服务端更正用途',net_amount_cents:15000,rating:4,scope:'private'})
})
test('无法安全重建的重复退款/已作废冲突保留草稿和旧操作ID',async()=>{
 const store=await setup();const e=await store.maintain('a',await current(store),refund())
 const operation=(await store.read('a')).queue[0]!.operation
 await store.markFailed('a',operation.operation_id,'冲突',{...e,version:3});const before=await store.read('a')
 await expect(store.resolve('a',original.id,true)).rejects.toThrow('退款已存在');expect(await store.read('a')).toEqual(before)
 await store.markFailed('a',operation.operation_id,'冲突',{...original,version:3,voided:true});const voidBefore=await store.read('a')
 await expect(store.resolve('a',original.id,true)).rejects.toThrow('已作废');expect(await store.read('a')).toEqual(voidBefore)
})
test('采用服务器版显式清除关联待上传Blob与操作',async()=>{
 const store=await setup();const e=await store.save('a',data,'shared',original.id,1,original,{add:[{id:'image',blob:new Blob(['fiction'])}],remove:[]})
 const op=(await store.read('a')).queue[0]!.operation;await store.markFailed('a',op.operation_id,'冲突',{...original,version:2});await store.resolve('a',e.id,false)
 expect((await store.read('a')).queue).toHaveLength(0);expect((await store.read('a')).images).toHaveLength(0)
})
test('撤回对方Expense图片Blob与申请正文；再次共享不复活旧请求',async()=>{
 const store=await setup();const other={...original,owner_id:'b'}
 const change:ChangeRequest={id:'request',expense_id:other.id,requester_id:'a',owner_id:'b',base_version:1,kind:'void',data:{},reason:'申请',status:'pending',created_at:'now',decided_at:null,before:other}
 await store.snapshot('a',snapshot([other],{images:[{id:'image',expense_id:other.id,mime_type:'image/jpeg',size_bytes:1}],requests:[change]}))
 await store.cacheImage('a','image',new Blob(['secret']),1)
 await store.snapshot('a',snapshot([],{images:[],requests:[]}));let account=await store.read('a');expect(account.local).toHaveLength(0);expect(account.images).toHaveLength(0);expect(account.requests).toHaveLength(0)
 await store.cacheImage('a','image',new Blob(['late']),1)
 await store.snapshot('a',snapshot([{...other,version:3}],{images:[{id:'image',expense_id:other.id,mime_type:'image/jpeg',size_bytes:1}],requests:[]}))
 await store.cacheImage('a','image',new Blob(['late from old shared version']),1)
 account=await store.read('a');expect(account.images[0]!.blob).toBeUndefined();expect(account.requests).toHaveLength(0)
})
test('退款回执丢失后的重放不导致本机投影丢评分或双扣',async()=>{
 const store=await setup();const reviewed={...original,rating:5,rating_reason:'很好'};await store.snapshot('a',snapshot([reviewed]));const e=await store.maintain('a',await current(store),refund())
 const op=(await store.read('a')).queue[0]!.operation
 await store.snapshot('a',snapshot([e]));expect(netAmount(await current(store))).toBe(15000)
 await store.acknowledge('a',{operation_id:op.operation_id,expense:e});expect(await current(store)).toMatchObject({rating:5,rating_reason:'很好',net_amount_cents:15000});expect((await store.read('a')).queue).toHaveLength(0)
})
test('原款/退款净额组合200+100-50=250，与私人和退款实际月份无关',()=>{
 const first=applyOperation(original,{...refund(),operation_id:'o',expense_id:original.id,base_version:1} as Operation,'a')
 const second=normalizeExpense({...original,id:'second',owner_id:'b',amount_cents:10000})
 expect(netAmount(first)+netAmount(second)).toBe(25000);expect(first.occurred_at).toBe(data.occurred_at)
})
test('冲突链中的显式评分不会因下一笔旧update省略评分而丢失',async()=>{
 const store=await setup();const first=await store.save('a',{...data,rating:5,rating_reason:'值得'},'shared',original.id,1,original)
 await store.save('a',{...data,purpose:'随后改用途'},'shared',first.id,first.version,first)
 const operation=(await store.read('a')).queue[0]!.operation
 await store.markFailed('a',operation.operation_id,'冲突',{...original,version:2,location:'服务端地点'})
 await store.resolve('a',original.id,true)
 expect(await current(store)).toMatchObject({rating:5,rating_reason:'值得',purpose:'随后改用途'})
 expect((await store.read('a')).queue.map(p=>p.operation.kind)).toEqual(['update','update'])
})

test.each(['upload','delete'] as const)('远端作废时待%s附件保留Blob并标出放弃出口，解除退出阻塞',async pending=>{
 const store=await setup()
 const meta={id:'pending-image',expense_id:original.id,mime_type:'image/jpeg',size_bytes:7}
 const blob=new Blob(['fiction'],{type:'image/jpeg'})
 await store.mutate('a',account=>{account.images.push({...meta,blob,pending,family_id:account.family?.id,membership_version:account.membership_version})})
 const remote=snapshot([{...original,version:2,voided:true}],{images:pending==='delete'?[meta]:[]})
 let transfers=0
 const engine=new SyncEngine(store,{
   me:async()=>({user}),snapshot:async()=>remote,
   send:async()=>{throw new Error('没有账目待办，不应发送')},
   uploadImage:async()=>{transfers++;return meta},deleteImage:async()=>{transfers++}
 })
 await engine.run('a')
 let account=await new LedgerStore(store.name,lock).read('a')
 expect(account.queue).toHaveLength(0)
 expect(account.images[0]).toMatchObject({pending,error:expect.stringContaining('可放弃附件变更')})
 expect(await account.images[0]!.blob!.text()).toBe('fiction')
 expect(pendingCount(account)).toBe(1)
 expect(transfers).toBe(0)
 // Retrying cannot make the attachment silently hang again: the fresh snapshot restores its explanation.
 await store.retryImage('a',meta.id)
 await engine.run('a')
 account=await store.read('a')
 expect(account.images[0]!.error).toContain('记录已作废')
 expect(await account.images[0]!.blob!.text()).toBe('fiction')
 await store.retryImage('a',meta.id,true)
 account=await store.read('a')
 expect(account.images).toHaveLength(0)
 expect(pendingCount(account)).toBe(0)
 expect(transfers).toBe(0)
 await expect(store.deactivate('a')).resolves.toBeUndefined()
})
test('永久撤回ID持久保存，晚到旧snapshot不能重新缓存旧申请正文',async()=>{
 const store=await setup();const other={...original,owner_id:'b'}
 const change:ChangeRequest={id:'withdrawn',expense_id:other.id,requester_id:'a',owner_id:'b',base_version:1,kind:'void',data:{},reason:'旧申请原因',status:'pending',created_at:'now',decided_at:null,before:other}
 await store.snapshot('a',snapshot([other],{requests:[change]}))
 await store.snapshot('a',snapshot([{...other,version:3}],{requests:[],withdrawn_request_ids:['withdrawn']}))
 let account=await new LedgerStore(store.name,lock).read('a')
 expect(account.withdrawn_request_ids).toEqual(['withdrawn']);expect(account.requests).toEqual([])
 await store.snapshot('a',snapshot([other],{requests:[change],withdrawn_request_ids:[]}))
 account=await store.read('a');expect(account.withdrawn_request_ids).toEqual(['withdrawn']);expect(account.requests).toEqual([])
})

test('新记录默认无感，五档满意度均可省略原因并保留离线重开', async()=>{
 const store=await setup();const created=await store.save('a',data,'private')
 expect(created.rating).toBe(3);expect(created.rating_reason).toBe('')
 let expense=created
 for(const rating of [1,2,3,4,5])expense=await store.maintain('a',expense,{kind:'review',data:{rating,rating_reason:''}})
 const reopened=new LedgerStore(store.name,lock)
 expect((await reopened.read('a')).local.find(e=>e.id===created.id)).toMatchObject({rating:5,rating_reason:''})
})
