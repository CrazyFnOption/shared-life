import 'fake-indexeddb/auto'
import { expect, test } from 'vitest'
import { LedgerStore } from '../src/store'
import { sourceFor } from '../src/sources'
import { emptyPlanSnapshot } from '../src/plans/types'
import { memoRecordViews } from '../src/memos/view'
import { schedulesForDay } from '../src/memos/dates'
import { mergeCalendarCache } from '../src/memos/calendarCache'
import { parseNotificationIntent, safeNotificationUrl } from '../src/notificationLinks'
import type { AccountContext } from '../src/types'
import type { MemoRecord } from '../src/memos/domain'
import type { ScheduleView } from '../src/memos/types'
import type { PlanRecord } from '../src/plans/types'
const user={id:'fixture-user',display_name:'虚构成员'}
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
function context(version=1):AccountContext{return{protocol_version:2,restore_id:'fixture-restore',context_version:version,families:[{id:'fixture-family',name:'虚构家',owner_id:user.id,revision:1,relation_epoch:1,members:[user]}],default_scope:{scope:'private',family_id:null,version:1},default_invalidated_family_id:null,legacy_context_mappings:[]}}
async function setup(){const store=new LedgerStore(crypto.randomUUID(),lock);await store.activate(user,'fixture');await store.reconcile(user.id,context());const a=await store.read(user.id);return{store,privateSource:sourceFor(a,{scope:'private',family_id:null}),sharedSource:sourceFor(a,{scope:'shared',family_id:'fixture-family'})}}
const manifest=(id:string,size:number)=>({id,kind:'file' as const,name:'虚构.txt',mime_type:'text/plain',size_bytes:size,sha256:'a'.repeat(64)})
function wish(id='wish'):PlanRecord{return{id,kind:'wish',scope:'private',family_id:null,author_id:user.id,updated_by:user.id,title:'虚构愿望',body_markdown:'private synced body',wish_kind:'未来旅行',target_date:null,source_wish_id:null,realized:false,archived:false,deleted:false,version:1,created_at:'now',updated_at:'now',attachments:[]}}
async function raw(store:LedgerStore,name:string){return store.transaction('readonly',async(_a,_s,l)=>new Promise<any[]>((resolve,reject)=>{const r=l.transaction.objectStore(name).getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)}))}
test('退出有冻结家庭备忘时清掉其他范围已同步正文与Blob，只保原冻结操作和字节',async()=>{
 const{store,privateSource,sharedSource}=await setup(),bytes=new Blob(['frozen original']),file=manifest('frozen-file',bytes.size),id='frozen-record',op={op_id:crypto.randomUUID(),entity_type:'memo' as const,entity_id:id,kind:'create',base_version:0,data:{category_id:'category',title:'家庭待同步',body_markdown:'frozen body',attachments:[file]}}
 await store.memoSave(user.id,sharedSource,op,[{...file,memo_id:id,source:sharedSource,blob:bytes,pending:true}])
 const snap=emptyPlanSnapshot(privateSource),record=wish();record.attachments=[manifest('private-cached-file',5)];snap.records=[record];await store.planSnapshot(user.id,snap,privateSource);await store.planCacheBlob(user.id,privateSource,record.id,{...record.attachments[0]!,parent_id:record.id,source:privateSource,blob:new Blob(['cache']),pending:false})
 const revoked=context(2);revoked.families=[];await store.reconcile(user.id,revoked);await store.deactivate(user.id)
 expect(await store.active()).toBeUndefined();expect(await raw(store,'plans_partitions')).toEqual([])
 const memos=await raw(store,'memo_partitions');expect(memos).toHaveLength(1);expect(memos[0].snapshot).toEqual([]);expect(memos[0].local).toEqual([]);expect(memos[0].queue[0].operation.op_id).toBe(op.op_id);expect(memos[0].queue[0].frozen).toBe(true);expect(memos[0].blobs[0].id).toBe(file.id);expect(await memos[0].blobs[0].blob.text()).toBe('frozen original')
})
test('同源current DTO可对照原文；确认重提才生成新op_id和最新base，附件ID/字节保留',async()=>{
 const{store,privateSource}=await setup(),snap=emptyPlanSnapshot(privateSource);snap.records=[wish()];await store.planSnapshot(user.id,snap,privateSource)
 const blob=new Blob(['local attachment']),file=manifest('local-file',blob.size),operation={op_id:crypto.randomUUID(),entity_type:'wish' as const,entity_id:'wish',kind:'update',base_version:1,data:{title:'local title',body_markdown:'local original body',wish_kind:'未来旅行',target_date:null,attachments:[file]}}
 await store.planSave(user.id,privateSource,operation,[{...file,parent_id:'wish',source:privateSource,blob,pending:true}])
 const remote={...snap.records[0]!,version:2,body_markdown:'remote current body'};snap.records=[remote];await store.planSnapshot(user.id,snap,privateSource);await store.planFail(user.id,privateSource,operation.op_id,'有共同编辑冲突','plan_conflict',remote)
 const issue=(await store.moduleIssues(user.id,privateSource))[0]!;expect(issue.local_text).toContain('local original body');expect(issue.server_text).toContain('remote current body');expect(issue.can_rebase).toBe(true)
 await store.moduleIssueAction(user.id,privateSource,issue.key,'rebase');const p=await store.planRead(user.id,privateSource)
 expect(p.queue[0]!.operation.op_id).not.toBe(operation.op_id);expect(p.queue[0]!.operation.base_version).toBe(2);expect(p.queue[0]!.operation.data).toEqual(operation.data);expect(p.local.records[0]!.version).toBe(3);expect(p.local.records[0]!.body_markdown).toBe('local original body');expect(p.blobs[0]!.id).toBe(file.id);expect(await p.blobs[0]!.blob.text()).toBe('local attachment')
})
test('日程缓存仅按原记录版本显示；迟到旧版本不盖新日期，跨日重复发生保原ID且不合并',async()=>{
 const{store,privateSource}=await setup(),record:MemoRecord={id:'schedule',kind:'schedule',scope:'private',family_id:null,author_id:user.id,updated_by:user.id,category_id:null,title:'跨日循环',body_markdown:'',attachments:[],completed:false,version:2,created_at:'now',updated_at:'now',schedule:{start_date:'2026-11-04',end_date:'2026-11-06',start_time:'22:30',end_time:'00:00'},recurrence:{frequency:'daily',interval:1,calendar:'solar',start_date:'2026-11-04',until:null,lunar_month:null,lunar_day:null,lunar_leap:false}}
 await store.memoSnapshot(user.id,{source:privateSource,categories:[],memos:[record],attachments:[],users:[user]},privateSource)
 const first={memo_id:record.id,record_version:2,date:'2026-11-05',start_date:'2026-11-04',end_date:'2026-11-06',start_time:'22:30',end_time:'00:00'},second={...first,start_date:'2026-11-05',end_date:'2026-11-07'}
 await store.memoCalendar(user.id,privateSource,'2026-11',{source:privateSource,versions:{schedule:2},occurrences:[first,second]});const p=await store.memoRead(user.id,privateSource),view=memoRecordViews(p)[0] as ScheduleView
 const occurrences=schedulesForDay([view],'2026-11-05');expect(occurrences).toHaveLength(2);expect(occurrences.map(r=>r.id)).toEqual(['schedule','schedule']);expect(new Set(occurrences.map(r=>r.occurrence_key)).size).toBe(2)
 const merged=mergeCalendarCache(p.calendar!['2026-11'],{source:privateSource,versions:{schedule:1},occurrences:[{...first,record_version:1,date:'2026-11-08'}]});expect(merged.versions.schedule).toBe(2);expect(merged.occurrences.map(o=>o.date)).toEqual(['2026-11-05','2026-11-05'])
 await store.memoSnapshot(user.id,{source:privateSource,categories:[],memos:[{...record,version:3}],attachments:[],users:[user]},privateSource);expect(schedulesForDay([memoRecordViews(await store.memoRead(user.id,privateSource))[0] as ScheduleView],'2026-11-05')).toEqual([])
})
test('通知URL只接受本源根路径、精确UUID和匹配的私人/家庭来源参数',()=>{
 const origin='https://fictional.example',id=crypto.randomUUID(),userId=crypto.randomUUID(),restore=crypto.randomUUID(),family=crypto.randomUUID(),url=`/?notification=${id}&scope=shared&family_id=${family}&user_id=${userId}&restore_id=${restore}&relation_epoch=2`
 expect(parseNotificationIntent(url,origin)?.source.family_id).toBe(family);expect(parseNotificationIntent(url.replace('scope=shared','scope=private'),origin)).toBeUndefined();expect(parseNotificationIntent(url+'&user_id='+userId,origin)).toBeUndefined();expect(parseNotificationIntent(url.replace('relation_epoch=2','relation_epoch=0'),origin)).toBeUndefined()
 for(const value of ['//evil.example/', '/\\evil.example/','https://evil.example/','javascript:alert(1)','/api/context',origin.replace('https://','https://user:password@')+'/'])expect(safeNotificationUrl(value,origin)).toBeUndefined()
 expect(parseNotificationIntent(`/?notification=${id}&scope=private&family_id=none&user_id=${userId}&restore_id=${restore}&relation_epoch=0`,origin)?.source.scope).toBe('private')
})
