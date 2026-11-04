import { matchReviewAttachment, sameAttachment } from './blobRestoreReview'
import type { ModuleBlobReview, VerifiedBlobReview } from './blobRestoreReview'
import { applyOperation, normalizeExpense, sameExpense } from './operations'
import { mergeCalendarCache } from './memos/calendarCache'
import type { MemoCalendarSnapshot } from './memos/calendarCache'
import type { ModuleIssue, ModuleIssueAction } from './moduleIssues'
import { toRaw } from 'vue'
import { applyPlanReceipt, projectOperation } from './plans/domain'
import { newPlanPartition, planPendingSummary, projectPlans, reconcilePlans } from './planPersistence'
import type { PlanBlob, PlanPartition } from './planPersistence'
import type { PlanOperation, PlanReceipt, PlanSnapshot } from './plans/types'
import { applyMemoOperation, memoPendingSummary, memoProjection, newMemoPartition, projectMemos, reconcileMemos } from './memos/domain'
import type { MemoBlob, MemoCategory, MemoData, MemoOperation, MemoPartition, MemoPending, MemoFocus, MemoCategoryOrder, MemoDeletionRequest, MemoReceipt, MemoRecord, MemoSnapshot } from './memos/domain'
import { expenseMatches, sameSource, sourceFor, sourceKey, sourceValid, sourcesFor } from './sources'
import type { Account, AccountContext, AttachmentChanges, Category, Expense, ExpenseData, FamilyContext, ImageMeta, LedgerPartition, LegacySource, LocalImage, Maintenance, Operation, Pending, PendingCategory, Receipt, Scope, Source, Snapshot, User } from './types'
export type LockRunner = <T>(name:string,body:()=>Promise<T>)=>Promise<T>
export const browserLock:LockRunner=async(name,body)=>{
 if(!navigator.locks)throw new Error('当前浏览器暂不支持离线记账，请换用新版 Safari、Chrome 或 Edge')
 return navigator.locks.request(name,body)
}
export class AccountChanged extends Error{constructor(){super('账号已变化，请恢复原账号登录')}}
export class RestoreChanged extends Error{constructor(){super('服务端已恢复，旧请求结果已忽略，请同步后逐项核对本机草稿')}}
export class RecordChanged extends Error{constructor(){super('这笔记录已变化，输入已保留。请核对最新记录后重新打开修改')}}
export class FamilyChanged extends Error{constructor(){super('家庭访问已变化，旧共同表单不能保存；请刷新后重新填写')}}
export const restoreReviewMessage='服务端已从备份恢复，请核对服务器快照和本机草稿后逐项选择，尚未重放'
export const frozenMessage='旧家庭共同待办已冻结，不会写入现在的家庭；可显式放弃本机副本。'
export const pendingCount=(a:Account)=>a.queue.length+a.categoryQueue.length+a.images.filter(i=>i.pending).length+(a.memo_pending?.active??0)+(a.memo_pending?.frozen??0)+(a.plan_pending?.active??0)+(a.plan_pending?.frozen??0)
export const activePendingCount=(a:Account)=>a.pending_summary?.active??a.queue.filter(i=>!i.frozen).length+a.categoryQueue.filter(i=>!i.frozen).length+a.images.filter(i=>i.pending&&!i.frozen).length+(a.memo_pending?.active??0)+(a.plan_pending?.active??0)
export const frozenCount=(a:Account)=>a.pending_summary?.frozen??a.queue.filter(i=>i.frozen).length+a.categoryQueue.filter(i=>i.frozen).length+a.images.filter(i=>i.pending&&i.frozen).length+(a.memo_pending?.frozen??0)+(a.plan_pending?.frozen??0)
const request=<T>(req:IDBRequest<T>)=>new Promise<T>((resolve,reject)=>{req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error)})
// IndexedDB's structured clone cannot accept Vue proxies. Copy DTO containers at
// the persistence boundary while preserving Blob and other native cloneable values.
export function storageValue<T>(value:T):T{
 const raw=toRaw(value)
 if(Array.isArray(raw))return raw.map(storageValue) as T
 if(raw&&typeof raw==='object'&&Object.getPrototypeOf(raw)===Object.prototype)return Object.fromEntries(Object.entries(raw).map(([key,item])=>[key,storageValue(item)])) as T
 return raw
}
const empty=()=>({snapshot:[],local:[],users:[],synced_at:null,categories:[],categoryQueue:[],images:[],requests:[],category_changes:[],withdrawn_request_ids:[]})
type State={account:Account;ledgers:LedgerPartition[];memos:MemoPartition[];plans:PlanPartition[]}
function normalized(a:Account):Account{a.snapshot??=[];a.local??=[];a.queue??=[];a.categories??=[];a.categoryQueue??=[];a.images??=[];a.users??=[a.user];a.requests??=[];a.category_changes??=[];a.withdrawn_request_ids??=[];return a}
function freeze(item:Pending|PendingCategory|LocalImage){item.frozen=true;item.error??=frozenMessage}
function legacyKey(source:LegacySource){return sourceKey(source)}
function ledger(state:State,source:Source|LegacySource):LedgerPartition{
 const key=sourceKey(source),found=state.ledgers.find(p=>p.key===key)
 if(found)return found
 const created:LedgerPartition={...empty(),key,user_id:state.account.user.id,...(source.protocol_version===2?{source}:{legacy_source:source}),users:[state.account.user]}
 state.ledgers.push(created);return created
}
function allImages(state:State){return state.ledgers.flatMap(p=>p.images)}
function allCategories(state:State){return state.ledgers.flatMap(p=>p.categoryQueue)}
function actualSource(item:{source?:Source;legacy_source?:LegacySource}){return item.source??item.legacy_source}
function valid(state:State,source:Source|LegacySource){
 if(source.protocol_version===2)return sourceValid(state.account,source)
 return source.user_id===state.account.user.id&&source.restore_id===(state.account.restore_id??'')&&!state.account.context&&(source.scope==='private'||source.family_id===state.account.family?.id&&source.membership_version===state.account.membership_version)
}
function assertSource(state:State,source:Source|LegacySource){if(source.restore_id!==(state.account.restore_id??''))throw new RestoreChanged();if(!valid(state,source))throw new FamilyChanged()}
function localExpense(state:State,id:string){return state.ledgers.flatMap(p=>p.local).find(e=>e.id===id)}
function snapshotExpense(state:State,id:string){return state.ledgers.flatMap(p=>p.snapshot).find(e=>e.id===id)}
function expectedSource(state:State,scope:Scope,context?:FamilyContext|Source):Source|LegacySource{
 if(context&&'protocol_version' in context){assertSource(state,context);return context}
 const explicit=context?.source??context?.legacy_source
 if(explicit){assertSource(state,explicit);return explicit}
 if(scope==='shared')throw new Error('请先创建或加入家庭，并明确选择家庭，再保存共同账')
 return sourceFor(state.account,{scope:'private',family_id:null})
}
function freezeChains(state:State){
 const ids=new Set(state.account.queue.filter(p=>p.frozen).map(p=>p.operation.expense_id))
 for(const p of state.account.queue)if(ids.has(p.operation.expense_id))freeze(p)
 for(const p of state.ledgers){
  for(const image of p.images)if(image.pending&&ids.has(image.expense_id))freeze(image)
  p.local=p.local.filter(e=>!ids.has(e.id)||p.snapshot.some(s=>s.id===e.id))
 }
}
function project(state:State){
 const prior=new Map(state.ledgers.flatMap(p=>p.local).map(e=>[e.id,e]))
 const current=new Map(state.ledgers.flatMap(p=>p.snapshot.map(e=>[e.id,{expense:normalizeExpense(e),partition:p}] as const)))
 const blocked=new Set<string>()
 for(const pending of state.account.queue){
  const {operation}=pending,id=operation.expense_id
  if(pending.frozen){blocked.add(id);continue}
  if(blocked.has(id))continue
  const source=actualSource(pending)
  if(!source)continue
  if(pending.restore_review){const draft=prior.get(id);if(draft){let target=source;try{target=sourceFor(state.account,{scope:draft.scope,family_id:draft.scope==='shared'?draft.family_id??source.family_id:null} as Source)}catch{}current.set(id,{expense:draft,partition:ledger(state,target)})}continue}
  try{
   const next=applyOperation(current.get(id)?.expense,operation,state.account.user.id)
   if(operation.kind==='scope'||operation.kind==='create')next.family_id=next.scope==='shared'?source.family_id:null
   const target=next.scope==='private'?(source.protocol_version===2?{...source,scope:'private' as const,family_id:null,relation_epoch:0}:{...source,scope:'private' as const,family_id:null}):source
   current.set(id,{expense:next,partition:ledger(state,target)})
  }catch{const draft=prior.get(id);if(draft)current.set(id,{expense:draft,partition:ledger(state,source)})}
 }
 for(const p of state.ledgers)p.local=[]
 for(const {expense,partition} of current.values())partition.local.push(expense)
}
function aggregate(state:State):Account{
 const a=state.account,ps=state.ledgers
 return{...a,memo_pending:memoPendingSummary(state.memos),plan_pending:planPendingSummary(state.plans),snapshot:ps.flatMap(p=>p.snapshot.map(normalizeExpense)),local:ps.flatMap(p=>p.local.map(normalizeExpense)),categories:ps.flatMap(p=>p.categories),categoryQueue:allCategories(state),images:allImages(state),users:[...new Map([a.user,...ps.flatMap(p=>p.users)].map(u=>[u.id,u])).values()],requests:ps.flatMap(p=>p.requests),category_changes:ps.flatMap(p=>p.category_changes),withdrawn_request_ids:[...new Set(ps.flatMap(p=>p.withdrawn_request_ids))],synced_at:ps.map(p=>p.synced_at).filter(Boolean).sort().at(-1)??null}
}
function scrubModuleCaches(state:State,store:IDBObjectStore){
 for(const p of state.memos){
  p.queue=p.queue.filter(q=>q.frozen);p.blobs=p.blobs.filter(b=>b.pending&&b.frozen)
  p.snapshot=[];p.local=[];p.categories=[];p.local_categories=[];p.users=[];p.attachments=[];p.focus=undefined;p.local_focus=undefined;p.category_order=undefined;p.local_category_order=undefined;p.deletion_requests=[];p.local_deletion_requests=[];p.messages=[];p.calendar={}
  const target=store.transaction.objectStore('memo_partitions');if(p.queue.length||p.blobs.length)target.put(storageValue(p),[p.user_id,p.key]);else target.delete([p.user_id,p.key])
 }
 for(const p of state.plans){
  p.queue=p.queue.filter(q=>q.frozen);p.blobs=p.blobs.filter(b=>b.pending&&b.frozen);p.snapshot=newPlanPartition(p.source).snapshot;p.local=newPlanPartition(p.source).local
  const target=store.transaction.objectStore('plans_partitions');if(p.queue.length||p.blobs.length)target.put(storageValue(p),[p.user_id,p.key]);else target.delete([p.user_id,p.key])
 }
}
function summary(state:State){const a=aggregate(state);return{active:activePendingCount(a),frozen:frozenCount(a)}}
function exactLegacy(account:Account,fid:string|null|undefined,version:number|undefined,scope:Scope):LegacySource|undefined{
 if(scope==='private')return{protocol_version:1,user_id:account.user.id,restore_id:account.restore_id??'',scope,family_id:null,membership_version:version??account.membership_version??0}
 if(!account.restore_id||!fid||fid==='unknown'||!Number.isInteger(version))return
 return{protocol_version:1,user_id:account.user.id,restore_id:account.restore_id,scope,family_id:fid,membership_version:version!}
}
function mapLegacy(source:LegacySource,context:AccountContext):Source|undefined{
 if(source.scope==='private'&&!source.restore_id)return{protocol_version:2,user_id:source.user_id,restore_id:context.restore_id,scope:'private',family_id:null,relation_epoch:0}
 if(source.restore_id!==context.restore_id)return
 if(source.scope==='private')return{protocol_version:2,user_id:source.user_id,restore_id:source.restore_id,scope:'private',family_id:null,relation_epoch:0}
 const mapping=context.legacy_context_mappings.find(m=>m.family_id===source.family_id&&m.membership_version===source.membership_version)
 if(!mapping||!context.families.some(f=>f.id===source.family_id&&f.relation_epoch===mapping.relation_epoch))return
 return{protocol_version:2,user_id:source.user_id,restore_id:source.restore_id,scope:'shared',family_id:source.family_id!,relation_epoch:mapping.relation_epoch}
}
function migrateLegacy(state:State,context?:AccountContext){
 const a=normalized(state.account)
 if(a.migration_version===2)return
 const sharedIds=new Set([...a.snapshot,...a.local].filter(e=>e.scope==='shared').map(e=>e.id))
 for(const p of a.queue)if(p.family_id||p.operation.kind==='create'&&p.operation.data.scope==='shared'||p.operation.kind==='scope')sharedIds.add(p.operation.expense_id)
 for(const i of a.images)if(i.family_id||i.recovery_scope==='shared')sharedIds.add(i.expense_id)
 const chainSources=new Map<string,Source|LegacySource>()
 const unknown:LegacySource={protocol_version:1,user_id:a.user.id,restore_id:a.restore_id??'',scope:'shared',family_id:'unknown',membership_version:-1}
 for(const id of new Set([...a.local.map(e=>e.id),...a.queue.map(p=>p.operation.expense_id)])){
  const chain=a.queue.filter(p=>p.operation.expense_id===id),shared=sharedIds.has(id)
  const tagged=chain.filter(p=>p.family_id)
  const candidates=tagged.map(p=>exactLegacy(a,p.family_id,p.membership_version,'shared'))
  const exact=shared?(candidates.length&&candidates.every(c=>!!c&&sameSource(c,candidates[0]))?candidates[0]:!chain.length?exactLegacy(a,a.family?.id,a.membership_version,'shared'):undefined):exactLegacy(a,null,a.membership_version,'private')
  const privateReview=!!context&&exact?.scope==='private'&&!exact.restore_id
  const mapped=exact&&context?(mapLegacy(exact,context)??(privateReview?{protocol_version:2 as const,user_id:a.user.id,restore_id:context.restore_id,scope:'private' as const,family_id:null,relation_epoch:0}:undefined)):undefined
  const staging=!!exact&&!context&&(exact.scope==='private'||exact.family_id===a.family?.id&&exact.membership_version===a.membership_version)
  const origin=mapped??exact??unknown
  chainSources.set(id,origin)
  let beforeScope=a.snapshot.find(e=>e.id===id)?.scope??(chain[0]?.operation.kind==='create'?chain[0].operation.data.scope:shared?'shared':'private')
  for(const item of chain){
   const operation=item.operation,scope=operation.kind==='scope'?'shared':operation.kind==='create'?operation.data.scope:beforeScope
   const fixed=mapped?(scope==='private'?{...mapped,scope:'private' as const,family_id:null,relation_epoch:0}:mapped):exact?(scope==='private'?{...exact,scope:'private' as const,family_id:null}:exact):undefined
   if(fixed?.protocol_version===2)item.source=fixed;else item.legacy_source=fixed??unknown
   if(privateReview){item.legacy_source=exact;item.restore_review=true;item.error??=restoreReviewMessage}
   if(operation.kind==='scope'||operation.kind==='create')beforeScope=operation.data.scope
   if(!exact||context&&!mapped||!staging&&!mapped||item.frozen)freeze(item)
  }
 }
 for(const e of a.snapshot){let source=chainSources.get(e.id)??exactLegacy(a,e.family_id??a.family?.id,a.membership_version,e.scope)??unknown;if(e.scope==='private')source=source.protocol_version===2?{...source,scope:'private',family_id:null,relation_epoch:0}:{...source,scope:'private',family_id:null};ledger(state,source).snapshot.push(normalizeExpense({...e,family_id:e.scope==='shared'?source.family_id:null}))}
 for(const e of a.local){let source=chainSources.get(e.id)??unknown;if(e.scope==='private')source=source.protocol_version===2?{...source,scope:'private',family_id:null,relation_epoch:0}:{...source,scope:'private',family_id:null};ledger(state,source).local.push(normalizeExpense({...e,family_id:e.scope==='shared'?source.family_id:null}))}
 for(const category of a.categories){const legacy=exactLegacy(a,category.scope==='shared'?a.family?.id:null,a.membership_version,category.scope)??unknown;ledger(state,context?mapLegacy(legacy,context)??legacy:legacy).categories.push(category)}
 for(const item of a.categoryQueue){const legacy=exactLegacy(a,item.family_id,item.membership_version,item.scope),mapped=legacy&&context?mapLegacy(legacy,context):undefined;if(mapped)item.source=mapped;else item.legacy_source=legacy??unknown;if(legacy?.scope==='private'&&!legacy.restore_id&&context){item.legacy_source=legacy;item.restore_review=true;item.error??=restoreReviewMessage}if(item.frozen||!legacy||context&&!mapped)freeze(item);ledger(state,mapped??legacy??unknown).categoryQueue.push(item)}
 for(const image of a.images){
  const parent=a.local.find(e=>e.id===image.expense_id)??a.snapshot.find(e=>e.id===image.expense_id),chain=chainSources.get(image.expense_id)
  const scope=image.family_id===null?'private':image.family_id?'shared':image.recovery_scope==='shared'?'shared':sharedIds.has(image.expense_id)?undefined:parent?.scope
  const legacy=scope?exactLegacy(a,scope==='shared'?image.family_id: null,image.membership_version,scope):undefined
  const privateReview=!!context&&legacy?.scope==='private'&&!legacy.restore_id
  const mapped=legacy&&context?(mapLegacy(legacy,context)??(privateReview?{protocol_version:2 as const,user_id:a.user.id,restore_id:context.restore_id,scope:'private' as const,family_id:null,relation_epoch:0}:undefined)):undefined
  if(privateReview){image.legacy_source=legacy;image.restore_review=true;image.error??=restoreReviewMessage}
  if(mapped){image.source=mapped;image.original_source=mapped}else image.legacy_source=legacy??unknown
  const lastScope=a.queue.filter(q=>q.operation.expense_id===image.expense_id&&q.operation.kind==='scope').at(-1)
  if(image.pending&&mapped&&chain?.protocol_version===2&&lastScope&&parent){image.destination_source=parent.scope==='private'?{...mapped,scope:'private',family_id:null,relation_epoch:0}:chain;image.parent_scope_operation_id=lastScope.operation.operation_id}
  if(image.frozen||!parent||!legacy||context&&!mapped)freeze(image)
  ledger(state,image.destination_source??mapped??legacy??unknown).images.push(image)
 }
 // Old server caches are not an authorization proof. Only pending local copies survive mapping failures.
 for(const p of state.ledgers){p.users=a.users;p.synced_at=a.synced_at;if(context&&!p.source){p.snapshot=[];p.local=[];p.categories=[];p.images=p.images.filter(i=>i.pending)}}
 a.migration_version=2;a.legacy_staging=!context;Object.assign(a,empty());freezeChains(state);project(state)
}

export class LedgerStore{
 private database:Promise<IDBDatabase>
 private closed=false
 private activeTransactions=new Set<Promise<void>>()
 status=''
 onStatus?:(message:string)=>void
 constructor(readonly name='shared-expense-v1',readonly lock:LockRunner=browserLock){
  this.database=new Promise((resolve,reject)=>{
   const open=indexedDB.open(name,3)
   open.onupgradeneeded=()=>{for(const name of ['accounts','settings','ledgers','memo_partitions','plans_partitions'])if(!open.result.objectStoreNames.contains(name))open.result.createObjectStore(name)}
   open.onblocked=()=>this.notice('请关闭其它旧版页面后重开，本机待办保留；升级完成前不能保存新内容。')
   open.onerror=()=>{this.notice(open.error?.name==='VersionError'?'本机数据已由新版升级，请关闭旧页面并重新打开；不要清除网站数据。':'本机数据暂时无法打开，请重开页面；不要清除网站数据。');reject(open.error)}
   open.onsuccess=()=>{const db=open.result;db.onversionchange=()=>{this.closed=true;this.notice('应用已更新，请重新打开；本机待办保留。');void Promise.allSettled([...this.activeTransactions]).then(()=>db.close())};this.notice('');resolve(db)}
  })
 }
 private notice(message:string){this.status=message;this.onStatus?.(message)}
 async transaction<T>(mode:IDBTransactionMode,body:(accounts:IDBObjectStore,settings:IDBObjectStore,ledgers:IDBObjectStore)=>Promise<T>):Promise<T>{
  const db=await this.database;if(this.closed)throw new Error('应用已更新，请重新打开；本机待办保留')
  const tx=db.transaction(['accounts','settings','ledgers','memo_partitions','plans_partitions'],mode)
  const done=new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error??new Error('本机事务已中止'));tx.onerror=()=>{}})
  this.activeTransactions.add(done)
  try{const result=await body(tx.objectStore('accounts'),tx.objectStore('settings'),tx.objectStore('ledgers'));await done;return result}
  catch(error){try{tx.abort()}catch{}await done.catch(()=>{});throw error}
  finally{this.activeTransactions.delete(done)}
 }
 private async load(userId:string,accounts:IDBObjectStore,settings:IDBObjectStore,ledgers:IDBObjectStore):Promise<State>{
  if(await request(settings.get('active'))!==userId)throw new AccountChanged()
  const account=await request<Account|undefined>(accounts.get(userId));if(!account)throw new AccountChanged()
  return{account:normalized(account),ledgers:(await request<LedgerPartition[]>(ledgers.getAll())).filter(p=>p.user_id===userId),memos:(await request<MemoPartition[]>(ledgers.transaction.objectStore('memo_partitions').getAll())).filter(p=>p.user_id===userId),plans:(await request<PlanPartition[]>(ledgers.transaction.objectStore('plans_partitions').getAll())).filter(p=>p.user_id===userId)}
 }
 private persist(state:State,accounts:IDBObjectStore,ledgers:IDBObjectStore){
  const meta={...state.account};Object.assign(meta,empty());delete meta.pending_summary;delete meta.memo_pending;delete meta.plan_pending;accounts.put(storageValue(meta),meta.user.id)
  for(const p of state.ledgers)ledgers.put(storageValue(p),[p.user_id,p.key])
  for(const p of state.memos)ledgers.transaction.objectStore('memo_partitions').put(storageValue(p),[p.user_id,p.key])
  for(const p of state.plans)ledgers.transaction.objectStore('plans_partitions').put(storageValue(p),[p.user_id,p.key])
 }
 private write<T>(userId:string,body:(state:State)=>T,expectedRestore?:string):Promise<T>{
  return this.lock(`${this.name}:local`,()=>this.transaction('readwrite',async(accounts,settings,ledgers)=>{
   const state=await this.load(userId,accounts,settings,ledgers)
   if(expectedRestore!==undefined&&(state.account.restore_id??'')!==expectedRestore)throw new RestoreChanged()
   if(state.account.migration_version!==2)migrateLegacy(state)
   const result=body(state);this.persist(state,accounts,ledgers);return result
  }))
 }
 active():Promise<string|undefined>{return this.transaction('readonly',async(_a,s)=>request(s.get('active')))}
 read(userId:string):Promise<Account>{return this.transaction('readonly',async(a,s,l)=>{const state=await this.load(userId,a,s,l);return state.account.migration_version===2?aggregate(state):normalized(state.account)})}
 async readProjection(userId:string,source:Source|LegacySource):Promise<Account>{
  return this.transaction('readonly',async(a,s,l)=>{
   const state=await this.load(userId,a,s,l)
   if(state.account.migration_version!==2){const legacy=state.account;if(source.protocol_version!==1)throw new FamilyChanged();const local=legacy.local.filter(e=>expenseMatches(e,source));return{...legacy,source:undefined,legacy_source:source,local,snapshot:legacy.snapshot.filter(e=>expenseMatches(e,source)),queue:legacy.queue.filter(p=>local.some(e=>e.id===p.operation.expense_id)),images:legacy.images.filter(i=>!i.frozen&&local.some(e=>e.id===i.expense_id)),categories:legacy.categories.filter(c=>c.scope===source.scope),categoryQueue:legacy.categoryQueue.filter(c=>c.scope===source.scope),pending_summary:{active:activePendingCount(legacy),frozen:frozenCount(legacy)}}}
   assertSource(state,source)
   const partition=state.ledgers.find(p=>p.key===sourceKey(source)),p=partition??{...empty(),users:[state.account.user]}
   const family=source.scope==='shared'?state.account.context?.families.find(f=>f.id===source.family_id)??state.account.family:null
   const ids=new Set(p.local.map(e=>e.id))
   return{...state.account,...p,family,membership_version:source.protocol_version===2?source.relation_epoch:source.membership_version,...(source.protocol_version===2?{source}:{legacy_source:source}),queue:state.account.queue.filter(q=>!q.frozen&&ids.has(q.operation.expense_id)),images:p.images.filter(i=>!i.frozen),pending_summary:summary(state)}
  })
 }
 async verifySource(userId:string,source:Source){const a=await this.read(userId);if(!sourceValid(a,source))throw source.restore_id!==a.restore_id?new RestoreChanged():new FamilyChanged()}
 activate(user:User,username:string):Promise<void>{return this.lock(`${this.name}:local`,()=>this.transaction('readwrite',async(a,s,l)=>{
  const active=await request<string|undefined>(s.get('active'))
  if(active&&active!==user.id){const state=await this.load(active,a,s,l),all=state.account.migration_version===2?aggregate(state):state.account;if(activePendingCount(all))throw new AccountChanged();if(!frozenCount(all)){a.delete(active);for(const p of state.ledgers)l.delete([active,p.key]);for(const p of state.memos)l.transaction.objectStore('memo_partitions').delete([active,p.key]);for(const p of state.plans)l.transaction.objectStore('plans_partitions').delete([active,p.key])}else scrubModuleCaches(state,l)}
  const previous=await request<Account|undefined>(a.get(user.id));a.put(storageValue(previous?{...previous,user,username}:{...empty(),queue:[],user,username,migration_version:2}),user.id);s.put(user.id,'active')
 }))}
 reconcile(userId:string,context:AccountContext,expectedRestore?:string):Promise<void>{
  if(context.protocol_version!==2||!context.restore_id||!Number.isInteger(context.context_version)||context.families.some(f=>!f.relation_epoch||!f.revision))return Promise.reject(new Error('服务端需升级，尚未发送本机待办'))
  return this.lock(`${this.name}:local`,()=>this.transaction('readwrite',async(a,s,l)=>{
   const state=await this.load(userId,a,s,l),meta=state.account
   if(expectedRestore!==undefined&&(meta.restore_id??'')!==expectedRestore)throw new RestoreChanged()
   if(meta.context&&meta.restore_id===context.restore_id&&(context.context_version<meta.context.context_version||context.default_scope.version<meta.context.default_scope.version))return
   const restored=!!meta.restore_id&&meta.restore_id!==context.restore_id
   if(meta.migration_version!==2)migrateLegacy(state,context)
   else for(const p of [...state.ledgers])if(p.legacy_source){
    const mapped=mapLegacy(p.legacy_source,context),review=p.legacy_source.scope==='private'&&!p.legacy_source.restore_id
    const destination=mapped?ledger(state,mapped):undefined
    for(const q of meta.queue)if(sameSource(q.legacy_source,p.legacy_source)){if(mapped&&!q.frozen){q.source=mapped;if(review){q.restore_review=true;q.error??=restoreReviewMessage}else delete q.legacy_source}else freeze(q)}
    for(const q of p.categoryQueue){if(mapped&&!q.frozen){q.source=mapped;if(review){q.restore_review=true;q.error??=restoreReviewMessage}else delete q.legacy_source;destination!.categoryQueue.push(q)}else freeze(q)}
    for(const image of p.images){if(mapped&&!image.frozen){
     image.source=mapped;image.original_source=mapped;if(review){image.restore_review=true;image.error??=restoreReviewMessage}else delete image.legacy_source
     const last=meta.queue.filter(q=>q.operation.expense_id===image.expense_id&&q.operation.kind==='scope').at(-1),parent=localExpense(state,image.expense_id)
     const related=last?.source??(last?.legacy_source?mapLegacy(last.legacy_source,context):undefined)
     if(last&&parent&&related){image.destination_source=parent.scope==='private'?{...related,scope:'private',family_id:null,relation_epoch:0}:related;image.parent_scope_operation_id=last.operation.operation_id}
     ledger(state,image.destination_source??mapped).images.push(image)
    }else if(image.pending)freeze(image)}
    if(destination){destination.snapshot.push(...p.snapshot);destination.local.push(...p.local);destination.categories.push(...p.categories)}
    p.snapshot=[];p.local=[];p.categories=[];p.categoryQueue=p.categoryQueue.filter(q=>q.frozen);p.images=p.images.filter(i=>i.pending&&i.frozen)
   }
   const previous=meta.context
   meta.restore_id=context.restore_id;meta.restored_at=context.restored_at;meta.context=context;meta.legacy_staging=false
   for(const p of state.ledgers){
    const source=p.source
    if(!source)continue
    const sameRelation=source.scope==='private'||context.families.some(f=>f.id===source.family_id&&(source.restore_id===context.restore_id?f.relation_epoch===source.relation_epoch:true))
    if(!sameRelation){for(const q of meta.queue)if(sameSource(q.source,source))freeze(q);for(const q of p.categoryQueue)freeze(q);for(const image of p.images)if(image.pending)freeze(image)}
    if(restored||!sourceValid(meta,source)){
     p.snapshot=[];if(!restored||!sameRelation)p.local=[];p.users=[meta.user];p.categories=[];p.requests=[];p.category_changes=[];p.withdrawn_request_ids=[];p.images=p.images.filter(i=>i.pending)
     if(restored&&sameRelation){for(const q of meta.queue)if(sameSource(q.source,source)&&!q.frozen){q.restore_review=true;q.error=restoreReviewMessage}for(const q of p.categoryQueue)if(!q.frozen){q.restore_review=true;q.error=restoreReviewMessage}for(const image of p.images)if(!image.frozen){image.restore_review=true;image.error=restoreReviewMessage}}
    }
   }
   if(restored)for(const p of [...state.ledgers])if(p.source?.restore_id!==context.restore_id&&p.source){
    const old=p.source
    if(old.scope==='shared'&&!context.families.some(f=>f.id===old.family_id))continue
    const next=sourceFor(meta,{scope:old.scope,family_id:old.family_id} as Source),destination=ledger(state,next)
    const categories=p.categoryQueue.filter(c=>!c.frozen),images=p.images.filter(i=>!i.frozen)
    destination.categoryQueue.push(...categories);destination.images.push(...images)
    p.categoryQueue=p.categoryQueue.filter(c=>c.frozen);p.images=p.images.filter(i=>i.frozen)
   }

   if(restored&&previous)for(const q of meta.queue)if(q.source?.restore_id!==context.restore_id&&!q.frozen){q.restore_review=true;q.error=restoreReviewMessage}
   for(const image of allImages(state))if(image.pending&&!image.frozen){
    for(const original of [image.original_source??image.source,image.destination_source])if(original&&original.restore_id===context.restore_id&&!sourceValid(meta,original))freeze(image)
   }
   reconcileMemos(state.memos,meta,restored,previous);reconcilePlans(state.plans,meta,restored,previous);freezeChains(state);project(state);this.persist(state,a,l)
  }))
 }
 transition(userId:string,context:AccountContext,expectedRestore?:string){return this.reconcile(userId,context,expectedRestore)}
 save(userId:string,data:ExpenseData,scope:Scope,expenseId?:string,expectedVersion?:number,expectedOriginal?:Expense,attachments?:AttachmentChanges,expectedRestore?:string,expectedFamily?:FamilyContext|Source):Promise<Expense>{return this.write(userId,state=>{
  const source=expectedSource(state,scope,expectedFamily);if(source.scope!==scope)throw new FamilyChanged()
  const partition=ledger(state,source),previous=expenseId?partition.local.find(e=>e.id===expenseId):undefined
  if(expenseId&&(!previous||previous.owner_id!==userId))throw new Error('只能修改本人已有的记录')
  if(previous&&(previous.version!==expectedVersion||!expectedOriginal||!sameExpense(previous,expectedOriginal)))throw new RecordChanged()
  if(expenseId&&state.account.queue.some(p=>p.operation.expense_id===expenseId&&p.error))throw new Error('请先处理这笔记录的同步问题')
  const id=expenseId??crypto.randomUUID(),operation:Operation=previous?{operation_id:crypto.randomUUID(),expense_id:id,kind:'update',base_version:previous.version,data}:{operation_id:crypto.randomUUID(),expense_id:id,kind:'create',base_version:0,data:{...data,scope}}
  const expense=applyOperation(previous,operation,userId);expense.family_id=scope==='shared'?source.family_id:null
  const tagged=source.protocol_version===2?{source}:{legacy_source:source}
  state.account.queue.push({operation,...tagged,family_id:source.family_id,membership_version:source.protocol_version===1?source.membership_version:undefined})
  partition.local=partition.local.filter(e=>e.id!==id).concat(expense)
  if(attachments){
   for(const imageId of attachments.remove){const image=partition.images.find(i=>i.id===imageId&&i.expense_id===id);if(image){image.pending='delete';delete image.error}}
   for(const image of attachments.add){if(image.blob.size>2*1024*1024)throw new Error('图片压缩后仍超过2MB，请换一张图片');if(allImages(state).some(i=>i.id===image.id))throw new Error('图片编号已存在，请重新选择');partition.images.push({id:image.id,expense_id:id,blob:image.blob,mime_type:image.blob.type,size_bytes:image.blob.size,pending:'upload',...tagged,...(source.protocol_version===2?{original_source:source}:{}),family_id:source.family_id})}
   if(partition.images.filter(i=>i.expense_id===id&&i.pending!=='delete').length>3)throw new Error('每笔最多3张图片，请重新核对图片')
  }
  return expense
 },expectedRestore)}
 maintain(userId:string,expected:Expense,action:Maintenance,expectedRestore?:string,expectedFamily?:FamilyContext|Source):Promise<Expense>{return this.write(userId,state=>{
  const source=expectedSource(state,action.kind==='scope'?'shared':expected.scope,expectedFamily)
  const current=localExpense(state,expected.id);if(!current||current.owner_id!==userId)throw new Error('只能维护本人记录');if(!sameExpense(current,expected))throw new RecordChanged()
  if(state.account.queue.some(p=>p.operation.expense_id===current.id&&p.error))throw new Error('请先处理这笔记录的同步问题')
  if(action.kind==='scope'){if(source.scope!=='shared'||current.scope==='shared'&&current.family_id!==source.family_id||action.data.scope===current.scope)throw new FamilyChanged()}
  else if(!expenseMatches(current,source))throw new FamilyChanged()
  const operation={...action,operation_id:crypto.randomUUID(),expense_id:current.id,base_version:current.version} as Operation,next=applyOperation(current,operation,userId)
  if(action.kind==='scope')next.family_id=next.scope==='shared'?source.family_id:null
  const destination=next.scope==='private'?(source.protocol_version===2?{...source,scope:'private' as const,family_id:null,relation_epoch:0}:{...source,scope:'private' as const,family_id:null}):source
  state.account.queue.push({operation,...(source.protocol_version===2?{source}:{legacy_source:source}),family_id:source.family_id})
  for(const p of state.ledgers)p.local=p.local.filter(e=>e.id!==current.id)
  const target=ledger(state,destination);target.local.push(next)
  if(action.kind==='scope')for(const p of state.ledgers){const moving=p.images.filter(i=>i.expense_id===current.id);p.images=p.images.filter(i=>i.expense_id!==current.id);for(const image of moving){if(image.pending&&source.protocol_version===2&&destination.protocol_version===2){image.original_source??=image.source;image.destination_source=destination;image.parent_scope_operation_id=operation.operation_id}target.images.push(image)}}
  if(next.voided)for(const image of allImages(state))if(image.expense_id===next.id&&image.pending)image.error='记录已作废，不能同步附件变更；本机图片仍保留，可放弃附件变更'
  return next
 },expectedRestore)}
 snapshot(userId:string,snapshot:Snapshot,expectedRestore?:string):Promise<void>{return this.write(userId,state=>{
  if(snapshot.user.id!==userId)throw new AccountChanged()
  const source=snapshot.source;if(!source)throw new Error('source_context_required');assertSource(state,source)
  const p=ledger(state,source)
  if(snapshot.expenses.some(e=>!expenseMatches(e,source)))throw new Error('快照来源不一致')
  p.snapshot=snapshot.expenses.map(normalizeExpense);for(const item of state.account.queue)if(item.restore_review&&!item.frozen){const current=snapshot.expenses.find(e=>e.id===item.operation.expense_id);if(current)item.conflict=current}p.users=snapshot.users;p.synced_at=snapshot.synced_at;p.categories=snapshot.categories??p.categories
  p.withdrawn_request_ids=[...new Set([...p.withdrawn_request_ids,...snapshot.withdrawn_request_ids??[]])];p.requests=(snapshot.requests??[]).filter(r=>!p.withdrawn_request_ids.includes(r.id));p.category_changes=snapshot.category_changes??[]
  if(snapshot.images){const previous=new Map(p.images.map(i=>[i.id,i]));p.images=snapshot.images.map(meta=>{const old=previous.get(meta.id);return old?.pending?old:{...meta,blob:old?.blob,source,original_source:source}});for(const image of previous.values())if(image.pending&&!p.images.some(i=>i.id===image.id))p.images.push(image)}
  project(state)
  const ids=new Set(p.local.map(e=>e.id));p.images=p.images.filter(i=>ids.has(i.expense_id)||i.pending)
  const voided=new Set(p.snapshot.filter(e=>e.owner_id===userId&&e.voided).map(e=>e.id));for(const image of p.images)if(image.pending&&voided.has(image.expense_id))image.error='记录已作废，不能同步附件变更；本机图片仍保留，可放弃附件变更'
 },expectedRestore)}
 acknowledge(userId:string,receipt:Receipt,expectedRestore?:string,source?:Source):Promise<void>{return this.write(userId,state=>{
  const pending=state.account.queue.find(p=>p.operation.operation_id===receipt.operation_id)
  if(!source||!pending||pending.frozen||pending.restore_review||!sameSource(pending.source,source)||receipt.expense.id!==pending.operation.expense_id||receipt.expense.owner_id!==userId)throw new Error('同步回执与待办或来源不一致')
  assertSource(state,source)
  if(state.account.queue.find(p=>p.operation.expense_id===receipt.expense.id)!==pending)throw new Error('回执越过了更早的操作')
  const scope=pending.operation.kind==='create'||pending.operation.kind==='scope'?pending.operation.data.scope:source.scope
  if(receipt.expense.scope!==scope||receipt.expense.scope==='shared'&&receipt.expense.family_id!==source.family_id)throw new Error('同步回执与目标账本不一致')
  const destination=receipt.expense.scope==='private'?{...source,scope:'private' as const,family_id:null,relation_epoch:0}:source
  const current=snapshotExpense(state,receipt.expense.id)
  if(!current||receipt.expense.version>=current.version){for(const p of state.ledgers)p.snapshot=p.snapshot.filter(e=>e.id!==receipt.expense.id);ledger(state,destination).snapshot.push(normalizeExpense(receipt.expense))}
  state.account.queue=state.account.queue.filter(p=>p!==pending);project(state)
  if(pending.operation.kind==='scope')for(const p of state.ledgers)for(const image of p.images)if(image.expense_id===receipt.expense.id&&!image.pending){image.source=destination;image.original_source=destination}
 },expectedRestore)}
 markFailed(userId:string,operationId:string,error:string,conflict?:Expense,expectedRestore?:string,errorCode?:string,expectedFamily?:FamilyContext|Source):Promise<void>{return this.write(userId,state=>{
  const pending=state.account.queue.find(p=>p.operation.operation_id===operationId);if(!pending||pending.frozen)return
  const source=expectedFamily&&'protocol_version' in expectedFamily?expectedFamily:expectedFamily?.source
  if(!source||!sameSource(source,pending.source)||!sourceValid(state.account,source))return
  if(conflict&&!expenseMatches(conflict,source))throw new Error('冲突正文来源不一致')
  pending.error=error;pending.error_code=errorCode;pending.conflict=conflict
 },expectedRestore)}
 addCategory(userId:string,scope:Scope,input:string,expectedFamily?:FamilyContext|Source):Promise<void>{return this.write(userId,state=>{
  const name=input.trim();if(!name||[...name].length>24)throw new Error('分类名称应为1～24个字')
  const source=expectedSource(state,scope,expectedFamily);if(source.scope!==scope)throw new FamilyChanged();const p=ledger(state,source)
  if(![...p.categories,...p.categoryQueue.filter(c=>!c.frozen)].some(c=>c.name===name))p.categoryQueue.push({scope,name,...(source.protocol_version===2?{source}:{legacy_source:source}),family_id:source.family_id})
 })}
 categoryResult(userId:string,category:Category&{source?:Source},error?:string,expectedRestore?:string):Promise<void>{return this.write(userId,state=>{
  const source=category.source;if(!source||!sourceValid(state.account,source))return
  const p=ledger(state,source),pending=p.categoryQueue.find(c=>c.name===category.name&&c.scope===category.scope&&sameSource(c.source,source));if(!pending||pending.frozen)return
  if(error)pending.error=error;else{p.categoryQueue=p.categoryQueue.filter(c=>c!==pending);if(!p.categories.some(c=>c.name===category.name))p.categories.push({scope:category.scope,name:category.name})}
 },expectedRestore)}
 retryCategory(userId:string,category:Category&{source?:Source},discard=false,expectedRestore?:string):Promise<void>{return this.write(userId,state=>{
  if(!category.source)throw new FamilyChanged();const p=ledger(state,category.source),item=p.categoryQueue.find(c=>c.name===category.name&&(sameSource(c.source,category.source)||c.restore_review&&c.source?.scope===category.source!.scope&&c.source?.family_id===category.source!.family_id));if(!item)return;if(item.frozen&&!discard)throw new FamilyChanged();if(discard)p.categoryQueue=p.categoryQueue.filter(c=>c!==item);else{assertSource(state,category.source);if(item.restore_review){item.source=category.source;delete item.legacy_source}delete item.error;delete item.restore_review}
 },expectedRestore)}
 imageResult(userId:string,sent:LocalImage,meta?:ImageMeta,error?:string,expectedRestore?:string):Promise<void>{return this.write(userId,state=>{
  const source=sent.destination_source??sent.source;if(!source||!sourceValid(state.account,source)||sent.original_source&&!sourceValid(state.account,sent.original_source))return
  for(const p of state.ledgers){const image=p.images.find(i=>i.id===sent.id&&sameSource(i.original_source??i.source,sent.original_source??sent.source));if(!image||image.frozen||image.pending!==sent.pending)continue;if(error){image.error=error;return}if(sent.pending==='delete')p.images=p.images.filter(i=>i!==image);else if(meta){Object.assign(image,meta);image.source=source;image.original_source=source;delete image.pending;delete image.error;delete image.destination_source;delete image.parent_scope_operation_id}return}
 },expectedRestore)}
 retryImage(userId:string,imageId:string,discard=false,expectedRestore?:string,source?:Source):Promise<void>{return this.write(userId,state=>{
  for(const p of state.ledgers){const image=p.images.find(i=>i.id===imageId);if(!image)continue;if(!source||!sameSource(source,p.source))throw new FamilyChanged();assertSource(state,source);if(image.frozen&&!discard)throw new Error('旧家庭图片不能重试，只能放弃本机副本');if(discard){if(image.pending==='upload'&&p.snapshot.some(e=>e.id===image.expense_id&&!e.voided)&&!image.restore_review){image.pending='delete';delete image.error}else p.images=p.images.filter(i=>i!==image)}else{if(image.restore_review){if(!p.snapshot.some(e=>e.id===image.expense_id&&e.owner_id===userId&&!e.voided)||state.account.queue.some(q=>q.operation.expense_id===image.expense_id&&q.restore_review))throw new Error('请先核对并同步父记录，再重试该附件；图片仍保存在本机');image.source=source;image.original_source=source;if(image.destination_source)image.destination_source=source;delete image.restore_review}delete image.error}return}
 },expectedRestore)}
 cacheImage(userId:string,imageId:string,blob:Blob,expectedVersion?:number,expectedRestore?:string,source?:Source):Promise<void>{return this.write(userId,state=>{
  if(!source)throw new FamilyChanged();assertSource(state,source);const p=ledger(state,source),image=p.images.find(i=>i.id===imageId);if(image&&!image.pending&&!image.frozen&&(expectedVersion===undefined||p.local.some(e=>e.id===image.expense_id&&e.version===expectedVersion)))image.blob=blob
 },expectedRestore)}
 discardFrozen(userId:string):Promise<void>{return this.write(userId,state=>{state.account.queue=state.account.queue.filter(p=>!p.frozen);for(const p of state.ledgers){p.categoryQueue=p.categoryQueue.filter(c=>!c.frozen);p.images=p.images.filter(i=>!i.frozen)}for(const p of state.memos){p.queue=p.queue.filter(q=>!q.frozen);p.blobs=p.blobs.filter(b=>!b.frozen);projectMemos(p)}for(const p of state.plans){p.queue=p.queue.filter(q=>!q.frozen);p.blobs=p.blobs.filter(b=>!b.frozen);projectPlans(p)}project(state)})}
 resolve(userId:string,expenseId:string,keepLocal:boolean,expectedRestore?:string,source?:Source):Promise<void>{return this.write(userId,state=>{
  const chain=state.account.queue.filter(p=>p.operation.expense_id===expenseId),pending=chain.find(p=>p.error);if(!pending)throw new Error('这笔记录的问题已变化，请刷新后再处理')
  if(keepLocal&&chain.some(p=>p.frozen))throw new Error('旧家庭待办不能重提，只能放弃本机副本')
  if(!source)throw new FamilyChanged();assertSource(state,source)
  const local=localExpense(state,expenseId),current=pending.conflict,rebuilt:Pending[]=[]
  if(keepLocal){
   if(!local||!current&&!(pending.restore_review&&pending.operation.kind==='create'))throw new Error('请先核对服务器当前记录；父记录不存在时只能重提本机新建')
   let next=current
   for(let index=0;index<chain.length;index++){const item=chain[index]!,old=item.operation,following=chain[index+1]?.operation;if(old.kind==='update'&&following?.kind==='update'&&(old.data.rating===undefined||following.data.rating!==undefined))continue;if(old.kind==='create'&&(current||!pending.restore_review))throw new Error('服务器已有此创建记录，不能重复创建或覆盖；请选择服务器版本，本机草稿仍在')
    const rebound=item.source&&item.source.restore_id!==state.account.restore_id?sourceFor(state.account,{scope:item.source.scope,family_id:item.source.family_id} as Source):item.source
    if(!rebound||!sourceValid(state.account,rebound))throw new FamilyChanged();const operation={...old,operation_id:crypto.randomUUID(),base_version:next?.version??0} as Operation;next=applyOperation(next,operation,userId);rebuilt.push({operation,source:rebound,family_id:rebound.family_id})
   }
  }
  if(current){for(const p of state.ledgers)p.snapshot=p.snapshot.filter(e=>e.id!==current.id||e.version>current.version);const p=ledger(state,source);if(!snapshotExpense(state,current.id))p.snapshot.push(current)}
  state.account.queue=state.account.queue.filter(p=>p.operation.expense_id!==expenseId).concat(rebuilt);project(state)
  if(!keepLocal)for(const p of state.ledgers)p.images=p.images.filter(i=>i.expense_id!==expenseId||!i.pending||i.restore_review)
 },expectedRestore)}
 retryLegacyCreate(userId:string,operationId:string,expectedOriginal:Expense,expectedRestore:string,expectedFamily:FamilyContext):Promise<void>{return this.write(userId,state=>{
  const source=expectedSource(state,expectedOriginal.scope,expectedFamily),failed=state.account.queue.find(p=>p.operation.operation_id===operationId)
  if(!failed||!failed.error||failed.error_code||failed.conflict||failed.frozen||failed.restore_review||failed.operation.kind!=='create'||!sameSource(actualSource(failed),source))throw new Error('仅旧版未标明原因的新建失败可按原内容重试，请刷新核对')
  const local=localExpense(state,failed.operation.expense_id);if(!local||!sameExpense(local,expectedOriginal))throw new RecordChanged();if(state.account.queue.find(p=>p.operation.expense_id===local.id)!==failed)throw new Error('请先处理这笔记录更早的待办');delete failed.error
 },expectedRestore)}
 retryDeletedCategory(userId:string,operationId:string,expectedOriginal:Expense,targetCategory:string|undefined,expectedRestore:string,expectedFamily:FamilyContext):Promise<void>{return this.write(userId,state=>{
  const source=expectedSource(state,expectedOriginal.scope,expectedFamily),failed=state.account.queue.find(p=>p.operation.operation_id===operationId)
  if(!failed||failed.error_code!=='category_deleted'||!failed.error||failed.frozen||failed.restore_review||failed.conflict||(failed.operation.kind!=='create'&&failed.operation.kind!=='update'))throw new Error('此分类失败已变化，不能直接重试；请刷新并核对草稿')
  const local=localExpense(state,failed.operation.expense_id);if(!local||local.owner_id!==userId||!sameExpense(local,expectedOriginal))throw new RecordChanged();const chain=state.account.queue.filter(p=>p.operation.expense_id===local.id);if(chain[0]!==failed)throw new Error('请先处理这笔记录更早的待办')
  const oldCategory=failed.operation.data.category??'',category=targetCategory===undefined?oldCategory:targetCategory.trim();if(!oldCategory||[...category].length>24)throw new Error('请选择有效分类或未分类')
  let projected=snapshotExpense(state,local.id)
  for(const item of chain){if(item.frozen||item.restore_review||item!==failed&&item.error)throw new Error('待办链还有其他问题，请先核对；全部草稿仍保留');const origin=actualSource(item);if(!origin||!valid(state,origin))throw new FamilyChanged();if(item.operation.base_version!==(projected?.version??0))throw new RecordChanged();const old=item.operation,operation=(old.kind==='create'||old.kind==='update')&&old.data.category===oldCategory&&category!==oldCategory?{...old,operation_id:crypto.randomUUID(),data:{...old.data,category}} as Operation:old;projected=applyOperation(projected,operation,userId);if(projected.category&&!ledger(state,source).categories.some(c=>c.name===projected!.category))throw new Error('待办中仍有已删除或未同步的分类，请先核对目录；草稿未改变');item.operation=operation;if(item===failed){delete item.error;delete item.error_code;delete item.conflict}}
  project(state)
 },expectedRestore)}

 async memoRead(userId:string,source:Source):Promise<MemoPartition>{
  return this.transaction('readonly',async(a,s,l)=>{const state=await this.load(userId,a,s,l);assertSource(state,source);return memoProjection(state.memos,source)})
 }
 memoSave(userId:string,source:Source,operation:MemoOperation,blobs:MemoBlob[]=[],following:MemoOperation[]=[]):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source)
  if(operation.base_version<0||!Number.isInteger(operation.base_version))throw new Error('备忘版本无效')
  let partition=state.memos.find(p=>sameSource(p.source,source))
  if(!partition){partition=newMemoPartition(source);state.memos.push(partition)}
  if(!partition.users.some(user=>user.id===state.account.user.id))partition.users.push(state.account.user)
  if(operation.entity_type==='category'&&operation.kind==='delete'&&partition.queue.some(q=>q.operation.entity_type==='memo'&&partition!.local.some(m=>m.id===q.operation.entity_id&&m.category_id===operation.entity_id)))throw new Error('此分类还有未同步记录，请先完成同步再整体移入删除。')
  if(partition.queue.some(q=>q.operation.entity_id===operation.entity_id&&(q.frozen||q.restore_review||q.error)))throw new Error('请先处理这条备忘的同步问题；输入已保留。')
  if(partition.queue.some(q=>q.operation.op_id===operation.op_id))throw new Error('这次保存已经提交，请刷新核对。')
  applyMemoOperation(partition,operation,userId)
  partition.queue.push({operation,source:{...source}})
  for(const next of following){if(partition.queue.some(q=>q.operation.entity_id===next.entity_id&&(q.error||q.frozen||q.restore_review)))throw new Error('关注顺序还有同步问题，请先处理。');applyMemoOperation(partition,next,userId);partition.queue.push({operation:next,source:{...source}})}
  for(const blob of blobs){if(!sameSource(blob.source,source)||blob.memo_id!==operation.entity_id||blob.blob.size!==blob.size_bytes)throw new Error('附件来源或字节不一致，未保存。');if(state.memos.some(p=>p.blobs.some(b=>b.id===blob.id)))throw new Error('附件编号已存在，请重新选择文件。');partition.blobs.push(blob)}

 },source.restore_id)}
 memoCalendar(userId:string,source:Source,month:string,result:MemoCalendarSnapshot):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);if(!sameSource(result.source,source))throw new FamilyChanged()
  for(const item of result.occurrences)if(!item.date.startsWith(month+'-')||item.record_version!==result.versions[item.memo_id]||!Number.isSafeInteger(item.record_version)||item.record_version<1)throw new Error('日程发生日期版本不一致，未写入缓存。')
  if(Object.values(result.versions).some(version=>!Number.isSafeInteger(version)||version<1))throw new Error('日程版本不一致，未写入缓存。')
  let p=state.memos.find(p=>sameSource(p.source,source));if(!p){p=newMemoPartition(source);state.memos.push(p)}p.calendar??={};p.calendar[month]=mergeCalendarCache(p.calendar[month],result)
 },source.restore_id)}
 memoSnapshot(userId:string,snapshot:MemoSnapshot,source:Source):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);if(!sameSource(snapshot.source,source))throw new FamilyChanged()
  let partition=state.memos.find(p=>sameSource(p.source,source));if(!partition){partition=newMemoPartition(source);state.memos.push(partition)}
  for(const item of [...snapshot.memos,...snapshot.categories])if(item.scope!==source.scope||item.family_id!==source.family_id)throw new FamilyChanged()
  partition.focus=snapshot.focus;partition.category_order=snapshot.category_order;partition.deletion_requests=snapshot.deletion_requests??[];partition.messages=snapshot.messages??[];partition.snapshot=snapshot.memos;partition.categories=snapshot.categories;partition.attachments=snapshot.attachments;partition.users=snapshot.users;partition.synced_at=new Date().toISOString();projectMemos(partition)
  const allowed=new Set(partition.local.flatMap(m=>m.attachments.map(a=>a.id)))
  partition.blobs=partition.blobs.filter(b=>b.pending||allowed.has(b.id))
 },source.restore_id)}
 memoAcknowledge(userId:string,receipt:MemoReceipt,source:Source):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);const partition=state.memos.find(p=>sameSource(p.source,source)),pending=partition?.queue.find(q=>q.operation.op_id===receipt.op_id)
  if(!partition||!pending||pending.frozen||pending.restore_review||!sameSource(pending.source,source))return
  const op=pending.operation,record=receipt.record
  if(receipt.entity_id!==op.entity_id||receipt.entity_type!==op.entity_type||record.id!==op.entity_id||('scope' in record&&(record.scope!==source.scope||record.family_id!==source.family_id)))throw new FamilyChanged()
  if(receipt.entity_type==='focus')partition.focus=record as MemoFocus
  else if(receipt.entity_type==='category_order')partition.category_order=record as MemoCategoryOrder
  else if(receipt.entity_type==='deletion_request')partition.deletion_requests=(partition.deletion_requests??[]).filter(r=>r.id!==record.id).concat(record as MemoDeletionRequest)
  else if(receipt.entity_type==='category'){const prior=partition.categories.find(c=>c.id===record.id);if(!prior||prior.version<=record.version)partition.categories=partition.categories.filter(c=>c.id!==record.id).concat(record as MemoCategory)}
  else{const prior=partition.snapshot.find(m=>m.id===record.id);if(!prior||prior.version<=record.version)partition.snapshot=partition.snapshot.filter(m=>m.id!==record.id).concat(record as MemoRecord)}
  for(const record of receipt.effects?.memos??[]){if(record.scope!==source.scope||record.family_id!==source.family_id)throw new FamilyChanged();partition.snapshot=partition.snapshot.filter(m=>m.id!==record.id).concat(record)}
  for(const category of receipt.effects?.categories??[])partition.categories=partition.categories.filter(c=>c.id!==category.id).concat(category)
  if(receipt.effects?.category_order)partition.category_order=receipt.effects.category_order
  partition.queue=partition.queue.filter(q=>q.operation.op_id!==receipt.op_id);projectMemos(partition)
  const active=new Set(partition.local.flatMap(m=>m.attachments.map(a=>a.id)))
  partition.blobs=partition.blobs.filter(b=>active.has(b.id)||partition.queue.some(q=>q.operation.entity_id===b.memo_id))
 },source.restore_id)}
 memoFail(userId:string,source:Source,opId:string,message:string,code?:string,current?:MemoRecord|MemoCategory):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);const pending=state.memos.find(p=>sameSource(p.source,source))?.queue.find(q=>q.operation.op_id===opId);if(!pending||pending.frozen)return;pending.error=message;pending.error_code=code;pending.conflict=current
 },source.restore_id)}
 memoBlobResult(userId:string,source:Source,attachmentId:string,error?:string):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);const blob=state.memos.find(p=>sameSource(p.source,source))?.blobs.find(b=>b.id===attachmentId)
  if(!blob||blob.frozen||blob.restore_review)return
  if(error)blob.error=error;else{blob.pending=false;delete blob.error}
 },source.restore_id)}
 memoCacheBlob(userId:string,source:Source,memoId:string,manifest:MemoBlob):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);const partition=state.memos.find(p=>sameSource(p.source,source)),record=partition?.local.find(m=>m.id===memoId)
  if(!partition||!record?.attachments.some(a=>a.id===manifest.id&&a.sha256===manifest.sha256)||!sameSource(manifest.source,source))throw new FamilyChanged()
  const current=partition.blobs.find(b=>b.id===manifest.id);if(current?.pending)return
  partition.blobs=partition.blobs.filter(b=>b.id!==manifest.id).concat(manifest)
 },source.restore_id)}

 async planRead(userId:string,source:Source):Promise<PlanPartition>{return this.transaction('readonly',async(a,s,l)=>{const state=await this.load(userId,a,s,l);assertSource(state,source);return state.plans.find(p=>sameSource(p.source,source))??newPlanPartition(source)})}
 planSave(userId:string,source:Source,operation:PlanOperation,blobs:PlanBlob[]=[]):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);let partition=state.plans.find(p=>sameSource(p.source,source));if(!partition){partition=newPlanPartition(source);state.plans.push(partition)}
  if(partition.queue.some(q=>q.operation.entity_id===operation.entity_id&&(q.error||q.frozen||q.restore_review)))throw new Error('请先处理这条计划记录的同步问题；输入已保留。')
  if(partition.queue.some(q=>q.operation.op_id===operation.op_id))throw new Error('这次保存已经提交，请刷新核对。')
  partition.local=projectOperation(partition.local,operation);if(!partition.local.users.some(u=>u.id===userId))partition.local.users.push(state.account.user);partition.queue.push({operation,source:{...source}})
  for(const blob of blobs){if(!sameSource(blob.source,source)||blob.parent_id!==operation.entity_id||blob.size_bytes!==blob.blob.size)throw new Error('计划附件来源或字节不一致。');const prior=state.plans.flatMap(p=>p.blobs).find(b=>b.id===blob.id);if(prior){if(prior.parent_id!==blob.parent_id||prior.sha256!==blob.sha256||!sameSource(prior.source,blob.source))throw new Error('附件编号已绑定其他记录。');continue}partition.blobs.push(blob)}
 },source.restore_id)}
 planSnapshot(userId:string,snapshot:PlanSnapshot,source:Source):Promise<void>{return this.write(userId,state=>{assertSource(state,source);if(!sameSource(snapshot.source,source)||snapshot.records.some(r=>r.scope!==source.scope||r.family_id!==source.family_id))throw new FamilyChanged();let p=state.plans.find(p=>sameSource(p.source,source));if(!p){p=newPlanPartition(source);state.plans.push(p)}p.snapshot=snapshot;p.synced_at=new Date().toISOString();projectPlans(p);const allowed=new Set([...p.local.records,...p.local.tasks].filter(r=>!r.deleted).flatMap(r=>r.attachments.map(a=>a.id)));p.blobs=p.blobs.filter(b=>b.pending||allowed.has(b.id))},source.restore_id)}
 planAcknowledge(userId:string,receipt:PlanReceipt,source:Source):Promise<void>{return this.write(userId,state=>{assertSource(state,source);const p=state.plans.find(p=>sameSource(p.source,source)),q=p?.queue.find(q=>q.operation.op_id===receipt.op_id);if(!p||!q||q.frozen||q.restore_review)return;if(q.operation.entity_id!==receipt.entity_id||q.operation.entity_type!==receipt.entity_type||receipt.record.id!==receipt.entity_id)throw new FamilyChanged();const before=[...p.snapshot.records,...p.snapshot.stages,...p.snapshot.tasks,...p.snapshot.deletion_requests].find(r=>r.id===receipt.entity_id);if(!before||before.version<=receipt.version)p.snapshot=applyPlanReceipt(p.snapshot,receipt);p.queue=p.queue.filter(q=>q.operation.op_id!==receipt.op_id);projectPlans(p);const active=new Set([...p.local.records,...p.local.tasks].filter(r=>!r.deleted).flatMap(r=>r.attachments.map(a=>a.id)));p.blobs=p.blobs.filter(b=>active.has(b.id)||p.queue.some(q=>q.operation.entity_id===b.parent_id))},source.restore_id)}
 planFail(userId:string,source:Source,opId:string,error:string,code?:string,current?:unknown):Promise<void>{return this.write(userId,state=>{assertSource(state,source);const q=state.plans.find(p=>sameSource(p.source,source))?.queue.find(q=>q.operation.op_id===opId);if(q&&!q.frozen){q.error=error;q.error_code=code;q.conflict=current}},source.restore_id)}
 planBlobResult(userId:string,source:Source,id:string,error?:string):Promise<void>{return this.write(userId,state=>{assertSource(state,source);const b=state.plans.find(p=>sameSource(p.source,source))?.blobs.find(b=>b.id===id);if(!b||b.frozen||b.restore_review)return;if(error)b.error=error;else{b.pending=false;delete b.error}},source.restore_id)}
 planCacheBlob(userId:string,source:Source,parent:string,blob:PlanBlob):Promise<void>{return this.write(userId,state=>{assertSource(state,source);const p=state.plans.find(p=>sameSource(p.source,source)),record=[...(p?.local.records??[]),...(p?.local.tasks??[])].find(r=>r.id===parent&&!r.deleted);if(!p||!record?.attachments.some(a=>a.id===blob.id&&a.sha256===blob.sha256)||!sameSource(blob.source,source))throw new FamilyChanged();if(p.blobs.some(b=>b.id===blob.id&&b.pending))return;p.blobs=p.blobs.filter(b=>b.id!==blob.id).concat(blob)},source.restore_id)}

 async moduleBlobForReview(userId:string,key:string,originalSource:Source,dependencyIds:string[]=[]):Promise<ModuleBlobReview>{return this.transaction('readonly',async(a,s,l)=>{
  const state=await this.load(userId,a,s,l),[module,type,id,...extra]=key.split(':')
  if((module!=='memo'&&module!=='plan')||type!=='blob'||!id||extra.length)throw new Error('附件核对入口无效，原文件已保留。')
  const parts=module==='memo'?state.memos:state.plans,p=parts.find(p=>sameSource(p.source,originalSource)&&p.blobs.some(blob=>blob.id===id)),blob=p?.blobs.find(blob=>blob.id===id)
  if(!p||!blob||!['private','shared'].includes(blob.source.scope)||blob.frozen||!blob.pending||!blob.restore_review||blob.source.protocol_version!==2||blob.source.user_id!==userId||!sameSource(blob.source,p.source)||blob.source.restore_id===state.account.restore_id)throw new Error('此附件不是可核对的恢复副本；冻结文件不会自动解冻。')
  const parent='memo_id'in blob?blob.memo_id:blob.parent_id
  if(parts.some(part=>part.source.scope===blob.source.scope&&part.source.family_id===blob.source.family_id&&part.queue.some(q=>q.operation.entity_id===parent||dependencyIds.includes(q.operation.entity_id))))throw new Error('原记录仍有待处理操作，请先核对原记录；附件字节已保留。')
  return{module,key,parent_id:parent,original_source:{...blob.source},blob}
 })}
 commitModuleBlobReview(userId:string,review:VerifiedBlobReview):Promise<'pending'|'uploaded'>{return this.write(userId,state=>{
  const targetSource=review.target_source,originalSource=review.original_source;assertSource(state,targetSource)
  if(originalSource.user_id!==userId||originalSource.scope!==targetSource.scope||originalSource.family_id!==targetSource.family_id||originalSource.restore_id===targetSource.restore_id)throw new Error('附件来源已变化，原文件已保留。')
  const parts=review.module==='memo'?state.memos:state.plans,old=parts.find(p=>sameSource(p.source,originalSource)),blob=old?.blobs.find(blob=>blob.id===review.blob.id),target=parts.find(p=>sameSource(p.source,targetSource))
  if(!old||!blob||!target||blob.frozen||!blob.restore_review||!blob.pending||!sameSource(blob.source,originalSource)||('memo_id'in blob?blob.memo_id:blob.parent_id)!==review.parent_id||!sameAttachment(blob,review.blob)||blob.blob.size!==review.verified_blob.size||blob.blob.type!==review.verified_blob.type)throw new Error('本机附件已处理、冻结或变化，未改变任何原文件。')
  const snapshot=review.module==='memo'?{source:targetSource,categories:(target as MemoPartition).categories,memos:(target as MemoPartition).snapshot,attachments:(target as MemoPartition).attachments,users:(target as MemoPartition).users}:(target as PlanPartition).snapshot
  const current=matchReviewAttachment(review,targetSource,snapshot)
  if(current.state!==review.match.state||current.dependencies.length!==review.match.dependencies.length||current.dependencies.some(dependency=>!review.match.dependencies.some(checked=>checked.kind===dependency.kind&&checked.id===dependency.id&&checked.version===dependency.version)))throw new Error('原记录或附件在核对期间已变化，请重新核对；原件仍保留。')
  const dependencyIds=new Set(current.dependencies.map(dependency=>dependency.id))
  if(parts.some(p=>p.source.scope===originalSource.scope&&p.source.family_id===originalSource.family_id&&p.queue.some(item=>dependencyIds.has(item.operation.entity_id))))throw new Error('原记录、阶段或计划还有待同步操作，请先处理；附件已保留。')
  const existing=target.blobs.find(item=>item.id===blob.id)
  if(existing&&(existing.frozen||existing.restore_review||existing.pending||!sameSource(existing.source,targetSource)||('memo_id'in existing?existing.memo_id:existing.parent_id)!==review.parent_id||!sameAttachment(existing,blob)))throw new Error('恢复后的分区已有不同或待处理的同编号附件，原文件均已保留。')
  const rebound={...blob,blob:review.verified_blob,source:{...targetSource},original_source:{...(blob.original_source??originalSource)},restore_binding:{from:{...originalSource},to:{...targetSource},checked_at:review.checked_at,server_state:current.state},pending:current.state==='pending'}
  delete rebound.restore_review;delete rebound.error
  // Removal and insertion settle together; aborted writes retain the original partition and bytes.
  old.blobs=old.blobs.filter(item=>item!==blob) as never
  target.blobs=target.blobs.filter(item=>item.id!==blob.id) as never
  ;(target.blobs as (MemoBlob|PlanBlob)[]).push(rebound)
  return current.state
 },review.target_source.restore_id)}
 async moduleIssues(userId:string,source:Source):Promise<ModuleIssue[]>{return this.transaction('readonly',async(a,s,l)=>{
  const state=await this.load(userId,a,s,l);assertSource(state,source);const issues:ModuleIssue[]=[]
  for(const module of ['memo','plan'] as const)for(const p of module==='memo'?state.memos:state.plans){
   if(p.source.scope!==source.scope||p.source.family_id!==source.family_id)continue
   const seen=new Set<string>()
   for(const q of p.queue){if(q.frozen||!q.error&&!q.restore_review||seen.has(q.operation.entity_id))continue;seen.add(q.operation.entity_id)
    const local=module==='memo'?(p as MemoPartition).local.find(r=>r.id===q.operation.entity_id):[...(p as PlanPartition).local.records,...(p as PlanPartition).local.tasks].find(r=>r.id===q.operation.entity_id)
    const data=q.operation.data as Record<string,unknown>,title=local?.title??String(data.title??data.name??(module==='memo'?'备忘变更':'计划变更'))
    issues.push({key:module+':'+q.operation.op_id,module,entity_id:q.operation.entity_id,title,message:q.error??'恢复后待核对',local_text:JSON.stringify(local??q.operation.data,null,2),server_text:q.conflict?JSON.stringify(q.conflict,null,2):'',can_rebase:!q.restore_review&&!!q.conflict&&q.operation.kind!=='create',review:!!q.restore_review,attachment:false})
   }
   for(const b of p.blobs.filter(b=>b.pending&&!b.frozen&&(b.error||b.restore_review)))issues.push({key:module+':blob:'+b.id,module,entity_id:'memo_id'in b?b.memo_id:b.parent_id,title:b.name,message:b.error??'恢复后附件待核对',local_text:`${b.size_bytes} 字节 · SHA256 ${b.sha256}`,server_text:'',can_rebase:false,review:!!b.restore_review,attachment:true,original_source:{...b.source}})
  }return issues
 })}
 moduleIssueAction(userId:string,source:Source,key:string,action:ModuleIssueAction):Promise<void>{return this.write(userId,state=>{
  assertSource(state,source);const [module,type,id]=key.split(':'),parts=module==='memo'?state.memos:state.plans
  if(module!=='memo'&&module!=='plan')throw new Error('同步问题已变化，请刷新。')
  const p=parts.find(p=>p.source.scope===source.scope&&p.source.family_id===source.family_id&&(type==='blob'?p.blobs.some(b=>b.id===id):p.queue.some(q=>q.operation.op_id===type)))
  if(!p)throw new Error('同步问题已变化，请刷新。')
  if(type==='blob'){
   const blob=p.blobs.find(b=>b.id===id);if(!blob||blob.frozen)throw new FamilyChanged()
   if(action==='discard'){const parents=module==='memo'?(p as MemoPartition).local:[...(p as PlanPartition).local.records,...(p as PlanPartition).local.tasks];if(parents.some(r=>r.attachments.some(a=>a.id===blob.id)))throw new Error('请先编辑原记录、移除这个附件并保存，再放弃未引用的本机文件。');p.blobs=p.blobs.filter(b=>b!==blob) as never;return}
   if(blob.restore_review)throw new Error('请先核对并重试父记录，再加载或重新选择附件；原字节仍在本机。')
   if(!sameSource(blob.source,source))throw new FamilyChanged();delete blob.error;return
  }
  const q=p.queue.find(q=>q.operation.op_id===type);if(!q||q.frozen)throw new FamilyChanged()
  const entity=q.operation.entity_id,chain=p.queue.filter(q=>q.operation.entity_id===entity)
  if(chain.some(q=>q.frozen))throw new FamilyChanged()
  if(action==='discard'){
   p.queue=p.queue.filter(q=>q.operation.entity_id!==entity) as never
   if(module==='memo'){projectMemos(p as MemoPartition);const active=new Set((p as MemoPartition).local.flatMap(r=>r.attachments.map(a=>a.id)));p.blobs=p.blobs.filter(b=>!b.pending||active.has(b.id)) as never}
   else{projectPlans(p as PlanPartition);const active=new Set([...(p as PlanPartition).local.records,...(p as PlanPartition).local.tasks].flatMap(r=>r.attachments.map(a=>a.id)));p.blobs=p.blobs.filter(b=>!b.pending||active.has(b.id)) as never}return
  }
  if(action==='review'){
   if(!q.restore_review||p.source.restore_id===source.restore_id||p.source.relation_epoch!==source.relation_epoch)throw new Error('旧关系来源不匹配，不能重绑定；本机副本保留。')
   let target=parts.find(target=>sameSource(target.source,source));if(!target){target=module==='memo'?newMemoPartition(source):newPlanPartition(source);(parts as (MemoPartition|PlanPartition)[]).push(target)}
   if(target.queue.some(item=>item.operation.entity_id===entity))throw new Error('此记录已有新恢复域的待办，请先核对。')
   for(const item of chain){item.source={...source};delete item.restore_review;delete item.error;delete item.error_code;delete item.conflict;(target.queue as (typeof q)[]).push(item)}
   p.queue=p.queue.filter(item=>!chain.includes(item as never)) as never
   const blobs=p.blobs.filter(blob=>('memo_id'in blob?blob.memo_id:blob.parent_id)===entity&&!blob.frozen)
   for(const blob of blobs){const cached=target.blobs.find(b=>b.id===blob.id);if(cached&&cached.sha256!==blob.sha256)throw new Error('同编号附件字节声明不同，本机原件保留。');target.blobs=target.blobs.filter(b=>b.id!==blob.id) as never;blob.source={...source};delete blob.restore_review;delete blob.error;(target.blobs as (typeof blob)[]).push(blob)}p.blobs=p.blobs.filter(blob=>!blobs.includes(blob as never)) as never
   module==='memo'?projectMemos(target as MemoPartition):projectPlans(target as PlanPartition);return
  }
  if(!sameSource(p.source,source)||q.restore_review)throw new FamilyChanged()
  if(action==='rebase'){
   const current=q.conflict as {version?:number}|undefined;if(!current?.version||q.operation.kind==='create')throw new Error('请先核对最新版本。')
   let version=current.version
   for(const item of chain){item.operation={...item.operation,op_id:crypto.randomUUID(),base_version:version++} as never;delete item.error;delete item.error_code;delete item.conflict}
  }else{delete q.error;delete q.error_code}
  module==='memo'?projectMemos(p as MemoPartition):projectPlans(p as PlanPartition)
 },source.restore_id)}
 // Explicit test/diagnostic mutation; production paths use fixed-source methods above.
 mutate<T>(userId:string,body:(account:Account)=>T,expectedRestore?:string):Promise<T>{return this.write(userId,state=>{
  const a=aggregate(state),result=body(a),oldCategories=state.ledgers.flatMap(p=>p.categories.map(c=>({category:c,source:p.source})))
  state.account={...a,...empty(),queue:a.queue};delete state.account.pending_summary
  for(const p of state.ledgers){p.snapshot=[];p.local=[];p.images=[];p.categoryQueue=[];p.categories=[];p.requests=[];p.category_changes=[];p.withdrawn_request_ids=[]}
  const origin=(scope:Scope,fid?:string|null)=>sourceFor(state.account,{scope,family_id:scope==='shared'?fid??state.account.context?.families[0]?.id!:null} as Source)
  for(const expense of a.snapshot)ledger(state,origin(expense.scope,expense.family_id)).snapshot.push(expense)
  for(const expense of a.local)ledger(state,origin(expense.scope,expense.family_id)).local.push(expense)
  for(const image of a.images){const source=actualSource(image);if(!source)throw new FamilyChanged();ledger(state,image.destination_source??source).images.push(image)}
  for(const item of a.categoryQueue){const source=actualSource(item);if(!source)throw new FamilyChanged();ledger(state,source).categoryQueue.push(item)}
  for(const category of a.categories){const source=oldCategories.find(c=>c.category.scope===category.scope&&c.category.name===category.name)?.source??origin(category.scope);ledger(state,source).categories.push(category)}
  for(const item of a.requests??[])ledger(state,origin('shared',item.before.family_id)).requests.push(item)
  for(const item of a.category_changes??[])ledger(state,origin(item.scope)).category_changes.push(item)
  for(const p of state.ledgers)if(p.source?.scope==='shared')p.withdrawn_request_ids=a.withdrawn_request_ids??[]
  return result
 },expectedRestore)}

 deactivate(userId:string):Promise<void>{return this.lock(`${this.name}:local`,()=>this.transaction('readwrite',async(a,s,l)=>{
  const state=await this.load(userId,a,s,l),all=state.account.migration_version===2?aggregate(state):state.account
  if(activePendingCount(all))throw new Error('还有待同步记录，请先同步或处理同步问题')
  if(!frozenCount(all)){a.delete(userId);for(const p of state.ledgers)l.delete([userId,p.key])}
  else for(const p of state.ledgers){p.snapshot=[];p.local=[];p.categories=[];p.users=[];p.requests=[];p.category_changes=[];p.images=p.images.filter(i=>i.frozen);l.put(storageValue(p),[userId,p.key])}
  scrubModuleCaches(state,l);s.delete('active')
 }))}
}
