import 'fake-indexeddb/auto'
import { IDBObjectStore } from 'fake-indexeddb'
import { afterEach, expect, test, vi } from 'vitest'
import { LedgerStore } from './demoFixture'
const locks = async <T>(_name: string, body: () => Promise<T>): Promise<T> => body()
const data = {amount_cents: 29, purpose: '早餐', occurred_at: '2026-10-30T00:00:00Z', location: ''}
async function setup() { const store = new LedgerStore(crypto.randomUUID(), locks); await store.activate({id:'a',display_name:'甲'},'alice'); return store }
afterEach(() => vi.restoreAllMocks())
test('保存创建和修改后重开，内容和操作链一起恢复', async () => {
  const store = await setup()
  const expense = await store.save('a', data, 'private')
  await store.save('a',{...data,amount_cents:1230},'private',expense.id,expense.version,expense)
  const reopened = new LedgerStore(store.name, locks)
  const account = await reopened.read('a')
  expect(account.local[0]?.amount_cents).toBe(1230)
  expect(account.queue.map(item => item.operation.base_version)).toEqual([0,1])
})
test('写入失败保留此前内容，不能出现仅内容或仅队列提交', async () => {
  const store = await setup()
  const expense = await store.save('a',data,'shared')
  const before = await store.read('a')
  vi.spyOn(IDBObjectStore.prototype,'put').mockImplementationOnce(() => { throw new DOMException('storage full','QuotaExceededError') })
  await expect(store.save('a',{...data,amount_cents:500},'shared',expense.id,expense.version,expense)).rejects.toThrow('storage full')
  expect(await store.read('a')).toEqual(before)
})
test('有待办不能切换账号，恢复原账号保留待办', async () => {
  const store = await setup()
  await store.save('a',data,'private')
  await expect(store.activate({id:'b',display_name:'乙'},'bob')).rejects.toThrow('账号已变化')
  await store.activate({id:'a',display_name:'甲'},'alice')
  expect((await store.read('a')).queue).toHaveLength(1)
})
test('打开编辑后后台更新版本，旧表单不能借新版本静默覆盖金额', async () => {
  const store=await setup()
  const user={id:'a',display_name:'甲'}
  const opened={...data,id:'expense-1',owner_id:'a',scope:'shared' as const,version:1,amount_cents:10000}
  await store.snapshot('a',{user,users:[user],expenses:[opened],synced_at:'2026-10-30T00:00:00Z'})
  const draft={...data,amount_cents:10000,purpose:'用户只改用途'}
  await store.snapshot('a',{user,users:[user],expenses:[{...opened,version:2,amount_cents:20000}],synced_at:'2026-10-30T00:01:00Z'})
  await expect(store.save('a',draft,'shared',opened.id,opened.version,opened)).rejects.toThrow('已变化')
  expect(draft).toEqual({...data,amount_cents:10000,purpose:'用户只改用途'})
  const account=await store.read('a')
  expect(account.local[0]?.amount_cents).toBe(20000)
  expect(account.queue).toHaveLength(0)
})
test('丢回执重放的旧确认清除待办，但不能回退已缓存的正式金额', async () => {
  const store=await setup()
  const user={id:'a',display_name:'甲'}
  const expense=await store.save('a',{...data,amount_cents:10000},'shared')
  const operation=(await store.read('a')).queue[0]!.operation
  await store.snapshot('a',{user,users:[user],expenses:[{...expense,version:2,amount_cents:20000}],synced_at:'2026-10-30T00:01:00Z'})
  await store.acknowledge('a',{operation_id:operation.operation_id,expense})
  const account=await store.read('a')
  expect(account.queue).toHaveLength(0)
  expect(account.snapshot[0]?.version).toBe(2)
  expect(account.snapshot[0]?.amount_cents).toBe(20000)
  expect(account.local[0]?.amount_cents).toBe(20000)
})
test.each([false,true])('冲突处理保留较新正式快照，保留本地=%s时仍使用用户所见冲突版本', async keepLocal => {
  const store=await setup()
  const user={id:'a',display_name:'甲'}
  const original={...data,id:'expense-1',owner_id:'a',scope:'shared' as const,version:1,amount_cents:10000}
  const conflict={...original,version:2,amount_cents:20000}
  await store.snapshot('a',{user,users:[user],expenses:[original],synced_at:'2026-10-30T00:00:00Z'})
  await store.save('a',{...data,amount_cents:15000},'shared',original.id,1,original)
  const operation=(await store.read('a')).queue[0]!.operation
  await store.markFailed('a',operation.operation_id,'版本冲突',conflict)
  await store.snapshot('a',{user,users:[user],expenses:[{...original,version:3,amount_cents:30000}],synced_at:'2026-10-30T00:02:00Z'})
  await store.resolve('a',original.id,keepLocal)
  const account=await store.read('a')
  expect(account.snapshot[0]?.version).toBe(3)
  expect(account.snapshot[0]?.amount_cents).toBe(30000)
  if (keepLocal) {
    expect(account.queue[0]?.operation.base_version).toBe(2)
    expect(account.local[0]?.amount_cents).toBe(15000)
  } else {
    expect(account.queue).toHaveLength(0)
    expect(account.local[0]?.amount_cents).toBe(30000)
  }
})
test('乐观本地和服务器版本号相同但内容已变化，旧编辑仍拒绝保存', async () => {
  const store=await setup()
  const user={id:'a',display_name:'甲'}
  const initial={...data,id:'expense-1',owner_id:'a',scope:'shared' as const,version:1,amount_cents:10000}
  await store.snapshot('a',{user,users:[user],expenses:[initial],synced_at:'2026-10-30T00:00:00Z'})
  const opened=await store.save('a',{...data,amount_cents:15000},'shared',initial.id,initial.version,initial)
  const operation=(await store.read('a')).queue[0]!.operation
  const server={...initial,version:2,amount_cents:20000}
  await store.markFailed('a',operation.operation_id,'版本冲突',server)
  await store.resolve('a',initial.id,false)
  const draft={...data,amount_cents:15000,purpose:'仅改用途'}
  await expect(store.save('a',draft,'shared',opened.id,opened.version,opened)).rejects.toThrow('已变化')
  const account=await store.read('a')
  expect(account.local[0]?.amount_cents).toBe(20000)
  expect(account.queue).toHaveLength(0)
  expect(draft.amount_cents).toBe(15000)
})
