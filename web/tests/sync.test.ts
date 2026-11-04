import 'fake-indexeddb/auto'
import { expect, test, vi } from 'vitest'
import { LedgerStore } from './demoFixture'
import { SyncEngine } from './demoFixture'
import { api as rawApi, ApiError, setFamilyContext, setRestoreId } from '../src/api'
import type { Expense, Operation, Snapshot } from '../src/types'
const locks = async <T>(_name: string, body: () => Promise<T>): Promise<T> => body()
const user = {id:'a',display_name:'甲'}
const data = {amount_cents:29,purpose:'早餐',occurred_at:'2026-10-30T00:00:00Z',location:''}
test('离线创建和连续修改逐条提交，回执丢失重试使用原操作', async () => {
  const store = new LedgerStore(crypto.randomUUID(), locks)
  await store.activate(user,'alice')
  const expense = await store.save('a',data,'shared')
  const edited = await store.save('a',{...data,amount_cents:199},'shared',expense.id,1,expense)
  await store.save('a',{...data,amount_cents:299},'shared',expense.id,2,edited)
  const sent: Operation[] = []
  let current: Expense | undefined
  let dropReceipt = true
  const receipts = new Map<string,Expense>()
  const api = {
    me: async () => ({user}),
    snapshot: async (): Promise<Snapshot> => ({user,users:[user],expenses:current ? [current] : [],synced_at:'2026-10-30T00:00:00Z'}),
    send: async (_userId:string, op:Operation) => {
      sent.push(structuredClone(op))
      if (!receipts.has(op.operation_id)) {
        expect(op.base_version).toBe(current?.version ?? 0)
        current = {...op.data,id:op.expense_id,owner_id:'a',scope:'shared',version:op.base_version+1}
        receipts.set(op.operation_id,current)
      }
      if (dropReceipt) { dropReceipt=false; throw new TypeError('connection lost') }
      return {operation_id:op.operation_id,expense:receipts.get(op.operation_id)!}
    }
  }
  const engine = new SyncEngine(store,api)
  await expect(engine.run('a')).rejects.toThrow('connection lost')
  expect((await store.read('a')).queue).toHaveLength(3)
  await engine.run('a')
  expect(sent.map(op => op.base_version)).toEqual([0,0,1,2])
  expect(sent[0]).toEqual(sent[1])
  const account = await store.read('a')
  expect(account.queue).toHaveLength(0)
  expect(account.snapshot[0]?.amount_cents).toBe(299)
})
test('服务端暂时失败不会封死草稿，下次同步沿用原操作', async () => {
  const store = new LedgerStore(crypto.randomUUID(),locks)
  await store.activate(user,'alice')
  await store.save('a',data,'private')
  const original = (await store.read('a')).queue[0]!.operation
  let down = true
  let current: Expense | undefined
  const api = {
    me:async()=>({user}),
    snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:current?[current]:[],synced_at:'2026-10-30T00:00:00Z'}),
    send:async (_id:string,op:Operation)=>{
      expect(op).toEqual(original)
      if (down) throw new ApiError(503,'invalid_request')
      current={...op.data,id:op.expense_id,owner_id:'a',scope:'private',version:1}
      return {operation_id:op.operation_id,expense:current}
    }
  }
  const engine = new SyncEngine(store,api)
  await expect(engine.run('a')).rejects.toThrow()
  expect((await store.read('a')).queue[0]?.error).toBeUndefined()
  down=false
  await engine.run('a')
  expect((await store.read('a')).queue).toHaveLength(0)
})
test('一笔冲突保留整条草稿链，其他记录继续，核对后按所见版本重新提交', async () => {
  const store=new LedgerStore(crypto.randomUUID(),locks)
  await store.activate(user,'alice')
  const original:Expense={...data,id:'expense-a',owner_id:'a',scope:'shared',version:1}
  await store.snapshot('a',{user,users:[user],expenses:[original],synced_at:'2026-10-30T00:00:00Z'})
  const edited = await store.save('a',{...data,amount_cents:500},'shared',original.id,1,original)
  await store.save('a',{...data,amount_cents:600},'shared',original.id,2,edited)
  const unrelated = await store.save('a',{...data,purpose:'地铁'},'private')
  let current:Expense={...original,amount_cents:700,version:2}
  let other:Expense | undefined
  const api={
    me:async()=>({user}),
    snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:other ? [current,other] : [current],synced_at:'2026-10-30T00:00:00Z'}),
    send:async (_id:string,op:Operation)=>{
      if (op.expense_id===original.id && op.base_version!==current.version) throw new ApiError(409,'version_conflict',current)
      const expense:Expense={...op.data,id:op.expense_id,owner_id:'a',scope:op.expense_id===original.id?'shared':'private',version:op.base_version+1}
      if (expense.id===original.id) current=expense
      else other=expense
      return {operation_id:op.operation_id,expense}
    }
  }
  const engine=new SyncEngine(store,api)
  await engine.run('a')
  let account=await store.read('a')
  expect(account.queue).toHaveLength(2)
  expect(account.queue[0]?.conflict?.version).toBe(2)
  expect(account.local.find(e=>e.id===original.id)?.amount_cents).toBe(600)
  expect(account.snapshot.find(e=>e.id===original.id)?.amount_cents).toBe(700)
  expect(account.snapshot.find(e=>e.id===unrelated.id)?.purpose).toBe('地铁')
  await store.resolve('a',original.id,true)
  account=await store.read('a')
  expect(account.queue).toHaveLength(1)
  expect(account.queue[0]?.operation.base_version).toBe(2)
  await engine.run('a')
  expect((await store.read('a')).snapshot.find(e=>e.id===original.id)?.amount_cents).toBe(600)
})
test('切换账号后到达的旧快照不能写入新账号分区', async () => {
  const store=new LedgerStore(crypto.randomUUID(),locks)
  await store.activate(user,'alice')
  let finish!:(value:Snapshot)=>void
  let requested!:()=>void
  const snapshotStarted = new Promise<void>(resolve=>{requested=resolve})
  const snapshotResponse = new Promise<Snapshot>(resolve=>{finish=resolve})
  const engine=new SyncEngine(store,{
    me:async()=>({user}),
    snapshot:()=>{requested();return snapshotResponse},
    send:async()=>{throw new Error('不应发送')}
  })
  const sync=engine.run('a')
  const rejected=expect(sync).rejects.toThrow('账号已变化')
  await snapshotStarted
  await store.activate({id:'b',display_name:'乙'},'bob')
  finish({user,users:[user],expenses:[{...data,id:'old-private',owner_id:'a',scope:'private',version:1}],synced_at:'2026-10-30T00:00:00Z'})
  await rejected
  expect((await store.read('b')).local).toHaveLength(0)
})
test('网络同步持锁期间仍可保存本机新记录', async () => {
  const tails=new Map<string,Promise<unknown>>()
  const mutex=async<T>(name:string,body:()=>Promise<T>):Promise<T>=>{
    const previous=tails.get(name)??Promise.resolve()
    const next=previous.catch(()=>{}).then(body)
    tails.set(name,next)
    return next
  }
  const store=new LedgerStore(crypto.randomUUID(),mutex)
  await store.activate(user,'alice')
  let requested!:()=>void
  let finish!:(value:Snapshot)=>void
  const snapshotStarted=new Promise<void>(resolve=>{requested=resolve})
  let slow=true
  const snapshotResponse=new Promise<Snapshot>(resolve=>{finish=resolve})
  const saved:Expense[]=[]
  const api={
    me:async()=>({user}),
    snapshot:async()=>{if(slow){requested();return snapshotResponse}return {user,users:[user],expenses:saved,synced_at:'2026-10-30T00:00:00Z'}},
    send:async(_id:string,op:Operation)=>{const expense:Expense={...op.data,id:op.expense_id,owner_id:'a',scope:'shared',version:op.base_version+1};saved.push(expense);return {operation_id:op.operation_id,expense}}
  }
  const engine=new SyncEngine(store,api)
  const sync=engine.run('a')
  await snapshotStarted
  // 此保存必须在网络请求解除前完成；若误用同一把锁，此测试将超时。
  await store.save('a',data,'shared')
  expect((await store.read('a')).queue).toHaveLength(1)
  slow=false
  finish({user,users:[user],expenses:[],synced_at:'2026-10-30T00:00:00Z'})
  await sync
  expect((await store.read('a')).queue).toHaveLength(0)
})
test('本轮来源在读取完成后的微任务失效时，不更新请求上下文缓存或通知根',async()=>{
  const store=new LedgerStore(crypto.randomUUID(),locks)
  await store.activate(user,'alice')
  const read=store.read.bind(store)
  let reads=0,valid=true,changed=0
  store.read=async id=>{
    const value=await read(id)
    if(++reads===3)queueMicrotask(()=>{valid=false})
    return value
  }
  const engine=new SyncEngine(store,{
    me:async()=>({user}),
    snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:[],synced_at:'2026-10-30T00:00:00Z',restore_id:'late-restore'}),
    send:async()=>{throw new Error('不应发送')},
  },()=>{changed++})
  setRestoreId('a','current-restore')
  setFamilyContext('a',{family:null,membership_version:77})
  const fetch=vi.fn(async()=>new Response(null,{status:204}))
  vi.stubGlobal('fetch',fetch)
  try{
    await expect(engine.run('a',()=>{if(!valid)throw new Error('retired run')})).rejects.toThrow('retired run')
    expect(changed).toBe(0)
    await rawApi.logout('a')
    expect(fetch.mock.calls[0]?.[1]?.headers).toMatchObject({'X-Restore-Id':'current-restore'})
    // The same engine really unwound, so another run can execute normally.
    valid=true
    await engine.run('a')
    expect(changed).toBe(6)
  }finally{vi.unstubAllGlobals();setRestoreId('a');setFamilyContext('a')}
})
