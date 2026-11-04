import 'fake-indexeddb/auto'
import { IDBObjectStore } from 'fake-indexeddb'
import { afterEach, expect, test, vi } from 'vitest'
import { LedgerStore } from './demoFixture'
import { pendingCount } from '../src/store'
import { SyncEngine } from './demoFixture'
import { ApiError } from '../src/api'
import type { Expense, ImageMeta, Operation, Snapshot } from '../src/types'
const lock=async<T>(_name:string,body:()=>Promise<T>):Promise<T>=>body()
const user={id:'a',display_name:'甲'}
const data={amount_cents:3850,purpose:'测试支出',occurred_at:'2026-10-30T00:00:00Z',location:'',category:'宠物'}
async function setup(){const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'alice');return store}
const blob=()=>new Blob(['fictional image'],{type:'image/jpeg'})
afterEach(()=>vi.restoreAllMocks())
test('自定义分类无需记账即可持久保存，分别保留共同/私人范围，待同步时不能退出',async()=>{
 const s=await setup();await s.addCategory('a','private','  宠物  ');await s.addCategory('a','shared','房租');await s.addCategory('a','private','宠物')
 const a=await new LedgerStore(s.name,lock).read('a');expect(a.categoryQueue).toMatchObject([{scope:'private',name:'宠物'},{scope:'shared',name:'房租'}]);expect(a.queue).toHaveLength(0)
 await expect(s.deactivate('a')).rejects.toThrow('待同步');await expect(s.activate({id:'b',display_name:'乙'},'bob')).rejects.toThrow('账号已变化')
})
test('账目和图片Blob同事务提交，重开恢复，存储失败不提交任一部分',async()=>{
 const s=await setup();const image=blob();await s.save('a',data,'private',undefined,undefined,undefined,{add:[{id:'image-1',blob:image}],remove:[]})
 const a=await new LedgerStore(s.name,lock).read('a');expect(a.queue).toHaveLength(1);expect(await a.images[0]!.blob!.text()).toBe(await image.text());expect(a.images[0]!.pending).toBe('upload')
 vi.spyOn(IDBObjectStore.prototype,'put').mockImplementationOnce(()=>{throw new DOMException('full','QuotaExceededError')})
 await expect(s.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'image-2',blob:image}],remove:[]})).rejects.toThrow('full')
 expect((await s.read('a')).images.map(i=>i.id)).toEqual(['image-1']);expect((await s.read('a')).queue).toHaveLength(1)
})
test('图片上限拒绝整个本地账目保存，不留下半笔记录',async()=>{
 const s=await setup();await expect(s.save('a',data,'private',undefined,undefined,undefined,{add:Array.from({length:4},(_,n)=>({id:String(n),blob:blob()})),remove:[]})).rejects.toThrow('最多3张')
 expect((await s.read('a')).local).toHaveLength(0);expect((await s.read('a')).images).toHaveLength(0)
})
test('分类字段变化也会拒绝过时表单，同版不同分类不得覆盖',async()=>{
 const s=await setup();const e:Expense={...data,id:'e',owner_id:'a',scope:'shared',version:1};await s.snapshot('a',{user,users:[user],expenses:[e],synced_at:''});await s.snapshot('a',{user,users:[user],expenses:[{...e,category:'医疗'}],synced_at:''})
 await expect(s.save('a',data,'shared',e.id,1,e)).rejects.toThrow('已变化')
})
test('账目确认后才上传，图片丢回执重试保留相同ID且不重复记账',async()=>{
 const s=await setup();await s.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'image-1',blob:blob()}],remove:[]})
 let expense:Expense|undefined;let meta:ImageMeta|undefined;let drop=true;const sent:Operation[]=[];const uploaded:string[]=[]
 const api={me:async()=>({user}),snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:expense?[expense]:[],images:meta?[meta]:[],synced_at:''}),send:async(_id:string,op:Operation)=>{sent.push(op);expense={...op.data,id:op.expense_id,owner_id:'a',scope:'shared',version:1};return{operation_id:op.operation_id,expense}},uploadImage:async(_id:string,eid:string,iid:string,b:Blob)=>{expect(expense?.id).toBe(eid);uploaded.push(iid);meta={id:iid,expense_id:eid,mime_type:b.type,size_bytes:b.size};if(drop){drop=false;throw new TypeError('lost image receipt')}return meta},deleteImage:async()=>{}}
 const engine=new SyncEngine(s,api);await expect(engine.run('a')).rejects.toThrow('lost image receipt');expect((await s.read('a')).queue).toHaveLength(0);expect(pendingCount(await s.read('a'))).toBe(1)
 await engine.run('a');expect(sent).toHaveLength(1);expect(uploaded).toEqual(['image-1','image-1']);expect(pendingCount(await s.read('a'))).toBe(0)
})
test('图片永久失败保留Blob并提供放弃出口，其余账目正常确认',async()=>{
 const s=await setup();const e=await s.save('a',data,'shared',undefined,undefined,undefined,{add:[{id:'bad',blob:blob()}],remove:[]});await s.save('a',{...data,purpose:'另一笔'},'private');const saved:Expense[]=[]
 const api={me:async()=>({user}),snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:saved,images:[],synced_at:''}),send:async(_id:string,op:Operation)=>{const expense:Expense={...op.data,id:op.expense_id,owner_id:'a',scope:op.kind==='create'?op.data.scope:'shared',version:1};saved.push(expense);return{operation_id:op.operation_id,expense}},uploadImage:async()=>{throw new ApiError(409,'image_limit')},deleteImage:async()=>{}}
 await new SyncEngine(s,api).run('a');const a=await s.read('a');expect(a.queue).toHaveLength(0);expect(a.snapshot).toHaveLength(2);expect(a.images[0]?.error).toContain('3张');expect(a.images[0]?.blob).toBeInstanceOf(Blob)
 await s.retryImage('a','bad',true);expect((await s.read('a')).images[0]?.pending).toBe('delete');await new SyncEngine(s,api).run('a');expect((await s.read('a')).images).toHaveLength(0);expect((await s.read('a')).local.some(r=>r.id===e.id)).toBe(true)
})
test('替换满3张图片先删除再新增，迟到上传回执不能取消本地删除',async()=>{
 const s=await setup();const e:Expense={...data,id:'e',owner_id:'a',scope:'shared',version:1};let metas:ImageMeta[]=[1,2,3].map(n=>({id:String(n),expense_id:'e',mime_type:'image/jpeg',size_bytes:14}));await s.snapshot('a',{user,users:[user],expenses:[e],images:metas,synced_at:''})
 const updated=await s.save('a',data,'shared','e',1,e,{add:[{id:'4',blob:blob()}],remove:['1']});const order:string[]=[]
 const api={me:async()=>({user}),snapshot:async():Promise<Snapshot>=>({user,users:[user],expenses:[e],images:metas,synced_at:''}),send:async(_id:string,op:Operation)=>({operation_id:op.operation_id,expense:updated}),deleteImage:async(_id:string,_eid:string,iid:string)=>{order.push('delete');metas=metas.filter(i=>i.id!==iid)},uploadImage:async(_id:string,eid:string,iid:string,b:Blob)=>{order.push('upload');expect(metas).toHaveLength(2);const m={id:iid,expense_id:eid,mime_type:b.type,size_bytes:b.size};metas.push(m);return m}}
 await new SyncEngine(s,api).run('a');expect(order).toEqual(['delete','upload'])
 const sent={...metas[2]!,pending:'upload' as const,blob:blob()};await s.mutate('a',a=>{a.images.find(i=>i.id==='4')!.pending='delete'});await s.imageResult('a',sent,metas[2]);expect((await s.read('a')).images.find(i=>i.id==='4')?.pending).toBe('delete')
})
test('离线私人图片缓存不泄漏到新账号，迟到下载不能落入新分区',async()=>{
 const s=await setup();const e:Expense={...data,id:'e',owner_id:'a',scope:'private',version:1};await s.snapshot('a',{user,users:[user],expenses:[e],images:[{id:'i',expense_id:'e',mime_type:'image/jpeg',size_bytes:14}],synced_at:''});await s.cacheImage('a','i',blob());await s.deactivate('a');await s.activate({id:'b',display_name:'乙'},'bob');await expect(s.cacheImage('a','i',blob())).rejects.toThrow('账号已变化');expect((await s.read('b')).images).toHaveLength(0)
})
