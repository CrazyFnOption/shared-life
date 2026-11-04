import 'fake-indexeddb/auto'
import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../src/api'
import { categoryApprovalCount, categoryContext, categoryNames, eligibleSelection } from '../src/categoryChanges'
import { LedgerStore } from './demoFixture'
import { sharedSource,apiResponse } from './sourceFixture'
import type { Account, CategoryChange, Expense, Snapshot } from '../src/types'
const user={id:'a',display_name:'甲'}
const family={id:'family-a',name:'家',owner_id:'a',members:[user,{id:'b',display_name:'乙'}]}
const expense:Expense={id:'e',owner_id:'a',scope:'shared',family_id:family.id,version:1,amount_cents:100,purpose:'虚构支出',occurred_at:'2026-11-01T00:00:00Z',location:'',category:'已删分类'}
const change:CategoryChange={preview_id:'preview',id:'batch',requester_id:'a',scope:'shared',kind:'delete',source_name:'已删分类',target_name:'',reason:'',status:'pending',created_at:'now',submitted_at:'now',decided_at:null,total_count:2,own_count:1,other_owners:[{owner_id:'b',display_name:'乙',count:1}],approvals:[{owner_id:'b',status:'pending',decided_at:null,count:1}],items:[{id:'e',owner_id:'a',base_version:1,category:'已删分类'},{id:'other',owner_id:'b',base_version:1,category:'已删分类'}]}
const snapshot=(extra:Partial<Snapshot>={}):Snapshot=>({user,users:family.members,family,membership_version:1,restore_id:'restore-a',expenses:[expense],categories:[{scope:'shared',name:'餐饮'}],category_changes:[change],synced_at:'now',...extra})
async function setup(){const store=new LedgerStore(crypto.randomUUID(),async(_name,body)=>body());await store.activate(user,'test-a');await store.snapshot(user.id,snapshot());return store}
afterEach(()=>vi.unstubAllGlobals())
test('可选目录仅来自服务端和本机有效新增，不从静态默认或旧账复活',async()=>{
 const store=await setup();await store.mutate('a',account=>{account.categoryQueue=[{scope:'shared',name:'本机新增'},{scope:'shared',name:'失败旧名',error:'已删除'},{scope:'shared',name:'旧家分类',frozen:true},{scope:'private',name:'私人分类'}]})
 const account=await store.read('a')
 expect(categoryNames(account,'shared')).toEqual(['餐饮','本机新增'])
 expect(categoryNames(account,'private')).toEqual(['私人分类'])
 expect(categoryNames(undefined,'shared')).toEqual([])
})
test('分类审批badge每个待我审批批次只计一次，不计发起人自动同意及他人待审批',async()=>{
 const store=await setup();let account=await store.read('a');expect(categoryApprovalCount(account)).toBe(0)
 account={...account,user:{id:'b',display_name:'乙'}};expect(categoryApprovalCount(account)).toBe(1)
 account.category_changes=[change,{...change,id:'batch-2',approvals:[{owner_id:'b',count:8,status:'pending',decided_at:null}]}]
 expect(categoryApprovalCount(account)).toBe(2)
 account.category_changes=[{...change,status:'applied'},{...change,id:'accepted',approvals:[{owner_id:'b',status:'accepted',count:1,decided_at:'now'}]}]
 expect(categoryApprovalCount(account)).toBe(0)
})
test('分类批次快照替换后撤回旧内容，同步缺少批次字段也不保留旧正文',async()=>{
 const store=await setup();expect((await store.read('a')).category_changes).toHaveLength(1)
 await store.snapshot('a',snapshot({category_changes:[]}));expect((await store.read('a')).category_changes).toEqual([])
 await store.snapshot('a',snapshot());await store.snapshot('a',snapshot({category_changes:undefined}));expect((await store.read('a')).category_changes).toEqual([])
})
test('离组立即清除分类批次缓存，换家上下文和恢复均撤回旧批次',async()=>{
 const store=await setup(),before=categoryContext(await store.read('a'))
 await store.transition('a',{family:null,membership_version:2},'restore-a');let account=await store.read('a')
 expect(account.category_changes).toEqual([]);expect(categoryContext(account)).not.toBe(before)
 await store.snapshot('a',snapshot({family:{...family,id:'family-b'},membership_version:3,category_changes:[],expenses:[]}));expect((await store.read('a')).category_changes).toEqual([])
 await store.snapshot('a',snapshot({family:{...family,id:'family-b'},membership_version:3,restore_id:'restore-b',restored_at:'now',category_changes:[],expenses:[]}));account=await store.read('a')
 expect(account.category_changes).toEqual([]);expect(categoryContext(account)).toContain('restore-b')
})
test('旧IDB无批次字段仍能打开且原离线队列不被升级清空',async()=>{
 const store=await setup();await store.save('a',{...expense,purpose:'离线输入'},'shared','e',1,(await store.read('a')).local[0],undefined,'restore-a',{family,membership_version:1})
 const payload=JSON.stringify((await store.read('a')).queue)
 await store.mutate('a',account=>{delete account.category_changes})
 const reopened=new LedgerStore(store.name,async(_name,body)=>body()),account=await reopened.read('a')
 expect(account.category_changes).toEqual([]);expect(JSON.stringify(account.queue)).toBe(payload)
})
test('批量选择排除其他账本、作废、已撤回和未同步记录；跨页ID不丢失',async()=>{
 const store=await setup();const account=await store.read('a')
 account.local=[expense,{...expense,id:'page-2'},{...expense,id:'private',scope:'private'},{...expense,id:'void',voided:true},{...expense,id:'pending'}]
 account.queue=[{operation:{operation_id:'op',expense_id:'pending',base_version:0,kind:'create',data:{...expense,scope:'shared'}}}]
 expect(eligibleSelection(account,'shared',['e','page-2','private','void','pending','withdrawn'])).toEqual(['e','page-2'])
})
test('分类API使用change id审批、固定账号家庭恢复头与原提交字段',async()=>{
 const calls:{url:string;options:RequestInit}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{calls.push({url,options});return apiResponse('{}',options)}))
 const context={family,membership_version:3,source:{...sharedSource,family_id:family.id}}
 await api.previewCategories('a',{kind:'reclassify',scope:'shared',target_name:'',expense_ids:['e','other']},'restore-a',context)
 await api.submitCategories('a',{id:'client-uuid',preview_id:'preview',reason:''},'restore-a',context)
 await api.decideCategories('a','batch id','decision-uuid','accept','restore-a',context)
 await api.addCategory('a',{scope:'shared',name:'旧名',restore_deleted:true},'restore-a',context)
 expect(calls.map(c=>c.url)).toEqual(['/api/v2/category-changes/preview','/api/v2/category-changes','/api/v2/category-changes/batch%20id/decision','/api/v2/categories'])
 expect(calls.map(c=>JSON.parse(c.options.body as string))).toEqual([{kind:'reclassify',scope:'shared',target_name:'',expense_ids:['e','other']},{id:'client-uuid',preview_id:'preview',reason:''},{decision_id:'decision-uuid',decision:'accept'},{scope:'shared',name:'旧名',restore_deleted:true}])
 expect(calls.every(c=>(c.options.headers as Record<string,string>)['X-Expected-User']==='a'&&(c.options.headers as Record<string,string>)['X-Restore-Id']==='restore-a'&&(c.options.headers as Record<string,string>)['X-Family-Id']===family.id&&(c.options.headers as Record<string,string>)['X-Relation-Epoch']==='3')).toBe(true)
})

test('分类待处理与历史独立分页，新到旧各10条且缩短夹紧',async()=>{
 const {categoryChangePage}=await import('../src/categoryChanges')
 const changes=Array.from({length:24},(_,i)=>({id:String(i),status:i<12?'pending':'applied',created_at:String(i).padStart(2,'0')})) as import('../src/types').CategoryChange[]
 expect(categoryChangePage(changes,true,1).items.map(c=>c.id)).toEqual(['11','10','9','8','7','6','5','4','3','2'])
 expect(categoryChangePage(changes,false,2).items.map(c=>c.id)).toEqual(['13','12'])
 expect(categoryChangePage(changes.slice(0,3),true,9)).toMatchObject({page:1,pages:1,total:3})
 expect(categoryChangePage([],false,2)).toMatchObject({page:1,pages:1,total:0,items:[]})
})

test('显式合并使用兼容rename协议且旧批次仍展示普通改名',async()=>{
 const {categoryAction}=await import('../src/categoryChanges')
 const renamed={...change,kind:'rename' as const,source_name:'电器',target_name:'日用百货'}
 expect(categoryAction(renamed)).toBe('「电器」改名为「日用百货」')
 expect(categoryAction({...renamed,merge:true})).toBe('「电器」合并到「日用百货」并删除源分类')
 const calls:RequestInit[]=[]
 vi.stubGlobal('fetch',vi.fn(async(_url:string,options:RequestInit)=>{calls.push(options);return apiResponse('{}',options)}))
 await api.previewCategories('a',{kind:'rename',scope:'shared',source_name:'电器',target_name:'日用百货',merge:true},'restore-a',{family,membership_version:3,source:{...sharedSource,family_id:family.id}})
 expect(JSON.parse(calls[0]!.body as string)).toEqual({kind:'rename',scope:'shared',source_name:'电器',target_name:'日用百货',merge:true})
})
