import 'fake-indexeddb/auto'
import { afterEach, expect, test, vi } from 'vitest'
import { LedgerStore } from '../src/store'
import { sourceFor } from '../src/sources'
import { applyMemoOperation, newMemoPartition, projectMemos } from '../src/memos/domain'
import type { MemoOperation, MemoRecord } from '../src/memos/domain'
import type { AccountContext } from '../src/types'
import type { ScheduleView } from '../src/memos/types'
import { archivePage, compareArchived, memoRecordViews, scheduleDeadline } from '../src/memos/view'
import { scheduleOnDay, schedulesForDay } from '../src/memos/dates'
const user={id:'fictional-member',display_name:'虚构成员'}
const context:AccountContext={protocol_version:2,restore_id:'fictional-restore',context_version:1,families:[],default_scope:{scope:'private',family_id:null,version:1},default_invalidated_family_id:null,legacy_context_mappings:[]}
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
async function setup(){const name=crypto.randomUUID(),store=new LedgerStore(name,lock);await store.activate(user,'fixture');await store.reconcile(user.id,context);return{name,store,source:sourceFor(await store.read(user.id),{scope:'private',family_id:null})}}
function record(id='fixture',schedule=false):MemoRecord{return{id,kind:schedule?'schedule':'memo',scope:'private',family_id:null,author_id:user.id,updated_by:user.id,category_id:schedule?null:'category',title:'虚构内容',body_markdown:'# 正文',completed:false,archived:false,version:1,created_at:'2090-11-05T00:00:00Z',updated_at:'2090-11-05T00:00:00Z',attachments:[],...(schedule?{schedule:{start_date:'2090-11-05',end_date:null,start_time:'09:00',end_time:null}}:{})}}
function operation(id:string,base:number,kind:string,data:MemoOperation['data']):MemoOperation{return{op_id:crypto.randomUUID(),entity_type:'memo',entity_id:id,base_version:base,kind,data}}
afterEach(()=>vi.useRealTimers())
test('离线完成立即归档，本机关闭重开、重新投影时间稳定，重开和同步回执链保持',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2090-11-05T04:00:00Z'))
 const{name,store,source}=await setup(),item=record();await store.memoSnapshot(user.id,{source,categories:[],memos:[item],attachments:[],users:[user]},source)
 const complete=operation(item.id,1,'complete',{completed:true});await store.memoSave(user.id,source,complete)
 const first=(await store.memoRead(user.id,source)).local[0]!,timestamp=first.archived_at
 expect(first.archived).toBe(true);expect(first.completed).toBe(true)
 vi.setSystemTime(new Date('2090-11-06T04:00:00Z'))
 const reopenedStore=new LedgerStore(name,lock),partition=await reopenedStore.memoRead(user.id,source);projectMemos(partition)
 expect(partition.local[0]!.archived_at).toBe(timestamp)
 await reopenedStore.memoSave(user.id,source,operation(item.id,2,'complete',{completed:false}))
 let current=await reopenedStore.memoRead(user.id,source);expect(current.local[0]!.archived).toBe(false);expect(current.local[0]!.completed).toBe(false)
 await reopenedStore.memoAcknowledge(user.id,{op_id:complete.op_id,entity_type:'memo',entity_id:item.id,version:2,record:{...first,archived_at:'2090-11-05T04:00:03+00:00'}},source)
 current=await reopenedStore.memoRead(user.id,source);expect(current.local[0]!.version).toBe(3);expect(current.local[0]!.archived).toBe(false);expect(current.queue).toHaveLength(1)
})
test('旧队列无local_at仍可重投影，删除/恢复不改变既有归档时间',async()=>{
 const{source}=await setup(),p=newMemoPartition(source);p.snapshot=[{...record(),archived:true,completed:true,archived_at:'2090-11-05T04:00:00Z'}];p.local=p.snapshot.map(r=>({...r}))
 p.queue=[{source,operation:operation('fixture',1,'delete',{})}];projectMemos(p);expect(p.local[0]!.archived_at).toBe(p.snapshot[0]!.archived_at);expect(p.queue[0]!.local_at).toBeTruthy()
 applyMemoOperation(p,operation('fixture',2,'restore',{}),user.id);expect(p.local[0]!.archived_at).toBe(p.snapshot[0]!.archived_at)
})
test.each([
 [{start_date:'2090-11-05',end_date:null,start_time:'09:00',end_time:null},'2090-11-06T00:00:00+08:00'],
 [{start_date:'2090-11-05',end_date:'2090-11-07',start_time:null,end_time:null},'2090-11-08T00:00:00+08:00'],
 [{start_date:'2090-11-05',end_date:'2090-11-07',start_time:'09:00',end_time:'00:00'},'2090-11-07T00:00:00+08:00'],
])('旧时间区间过期仍保留未完成备忘 %j',async(schedule,boundary)=>{
 const{source}=await setup(),p=newMemoPartition(source);p.local=[{...record('date',true),schedule}]
 const end=Date.parse(boundary);expect(memoRecordViews(p,end-1)[0]!.archived).toBe(false);expect(memoRecordViews(p,end)[0]!.archived).toBe(false)
 applyMemoOperation(p,operation('date',1,'archive',{archived:false}),user.id);expect(memoRecordViews(p,end+86400000)[0]!.archived).toBe(false)
})
test('重复日程离线完成单次保未来缓存，归档可读正文/附件，恢复不被再次到期吞掉',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2090-11-05T04:00:00Z'))
 const{store,source}=await setup(),item=record('schedule',true);item.recurrence={frequency:'daily',interval:1,calendar:'solar',start_date:'2090-11-05',until:null,lunar_month:null,lunar_day:null,lunar_leap:false}
 const file={id:'file',kind:'file' as const,name:'虚构附件.txt',mime_type:'text/plain',size_bytes:1,sha256:'a'.repeat(64)};item.attachments=[file]
 await store.memoSnapshot(user.id,{source,categories:[],memos:[item],attachments:[],users:[user]},source)
 const occurrences=['2090-11-05','2090-11-06'].map(day=>({memo_id:item.id,record_version:1,date:day,start_date:day,end_date:null,start_time:'09:00',end_time:null}))
 await store.memoCalendar(user.id,source,'2090-11',{source,versions:{schedule:1},occurrences})
 await store.memoSave(user.id,source,operation(item.id,1,'complete',{completed:true,occurrence_date:'2090-11-05'}))
 let views=memoRecordViews(await store.memoRead(user.id,source)),history=views.find(v=>v.archived)!
 expect(history.body).toBe('# 正文');expect(history.attachments[0]!.id).toBe(file.id);expect(history.completed).toBe(true)
 expect(schedulesForDay(views as ScheduleView[],'2090-11-05')).toEqual([]);expect(schedulesForDay(views as ScheduleView[],'2090-11-06')).toHaveLength(1)
 await store.memoSave(user.id,source,operation(item.id,2,'complete',{completed:false,occurrence_date:'2090-11-05'}))
 views=memoRecordViews(await store.memoRead(user.id,source),Date.parse('2090-11-06T04:00:00Z'))
 expect(views.some(v=>v.archived&&v.kind==='schedule'&&v.occurrence_date==='2090-11-05')).toBe(false)
 expect(schedulesForDay(views as ScheduleView[],'2090-11-05')).toHaveLength(1)
})
test('按归档实际时刻降序和相同时间ID排序，45条分页无丢失重复',async()=>{
 const{source}=await setup(),p=newMemoPartition(source);p.local=Array.from({length:45},(_,n)=>({...record(String(n).padStart(2,'0')),archived:true,archived_at:n%2?'2090-11-06T00:00:00+08:00':'2090-11-05T17:00:00Z'}))
 const sorted=memoRecordViews(p).sort(compareArchived),pages=[1,2,3].map(n=>archivePage(sorted,n))
 expect(pages.map(page=>page.length)).toEqual([20,20,5]);expect(sorted[0]!.id).toBe('00');expect(sorted[23]!.id).toBe('01');expect(new Set(pages.flat().map(r=>r.id)).size).toBe(45)
})

test('归档旧发生次后改series起点再恢复，原跨日发生次仍在列表和日历可达且未来不重复',async()=>{
 const{store,source}=await setup(),item=record('historical',true)
 item.schedule={start_date:'2090-11-05',end_date:'2090-11-07',start_time:'09:00',end_time:'11:00'}
 item.recurrence={frequency:'daily',interval:1,calendar:'solar',start_date:'2090-11-05',until:null,lunar_month:null,lunar_day:null,lunar_leap:false}
 await store.memoSnapshot(user.id,{source,categories:[],memos:[item],attachments:[],users:[user]},source)
 async function saveAndSync(op:MemoOperation){
  await store.memoSave(user.id,source,op)
  const saved=(await store.memoRead(user.id,source)).local.find(r=>r.id===item.id)!
  await store.memoAcknowledge(user.id,{op_id:op.op_id,entity_type:'memo',entity_id:item.id,version:saved.version,record:saved},source)
 }
 await saveAndSync(operation(item.id,1,'complete',{completed:true,occurrence_date:'2090-11-05'}))
 await saveAndSync(operation(item.id,2,'update',{kind:'schedule',category_id:null,title:item.title,body_markdown:item.body_markdown,attachments:[],schedule:{start_date:'2090-11-06',end_date:'2090-11-06',start_time:'09:00',end_time:'11:00'},recurrence:{...item.recurrence,start_date:'2090-11-06'}}))
 await saveAndSync(operation(item.id,3,'archive',{archived:false,occurrence_date:'2090-11-05'}))
 const occurrences=['2090-11-06','2090-11-07','2090-11-08'].map(day=>({memo_id:item.id,record_version:4,date:day,start_date:day,end_date:day,start_time:'09:00',end_time:'11:00'}))
 await store.memoCalendar(user.id,source,'2090-11',{source,versions:{historical:4},occurrences})
 let views=memoRecordViews(await store.memoRead(user.id,source),Date.parse('2090-11-06T10:00:00+08:00')) as ScheduleView[]
 const restored=views.find(v=>v.occurrence_key==='historical:2090-11-05')!
 expect(restored).toMatchObject({id:item.id,start_date:'2090-11-05',end_date:'2090-11-07',start_time:'09:00',end_time:'11:00',archived:false,completed:false,auto_archive_suppressed:true})
 expect(views).toHaveLength(2)
 expect(scheduleOnDay(restored,'2090-11-05')).toBe(true)
 expect(scheduleOnDay({...restored,archived:true},'2090-11-05')).toBe(false)
 expect(schedulesForDay(views,'2090-11-05').map(v=>v.occurrence_date)).toEqual(['2090-11-05'])
 expect(schedulesForDay(views,'2090-11-06').map(v=>v.occurrence_date).sort()).toEqual(['2090-11-05','2090-11-06'])
 expect(schedulesForDay(views,'2090-11-07').map(v=>v.occurrence_date).sort()).toEqual(['2090-11-05','2090-11-07'])
 expect(schedulesForDay(views,'2090-11-08').map(v=>v.occurrence_date)).toEqual(['2090-11-08'])
 views=memoRecordViews(await store.memoRead(user.id,source),Date.parse('2090-11-08T12:00:00+08:00')) as ScheduleView[]
 expect(schedulesForDay(views,'2090-11-05')).toHaveLength(1)
 // An unchanged series cache already represents a normal restore; no second
 // standalone row or duplicate day entry should be introduced in that case.
 const unchanged={...item,version:4,archived_occurrences:[{start_date:'2090-11-05',archived_at:null,completed:false,suppressed:true,schedule:item.schedule}]}
 const p=newMemoPartition(source);p.local=[unchanged];p.calendar={'2090-11':{versions:{historical:4},occurrences:[{memo_id:item.id,record_version:4,date:'2090-11-05',...item.schedule!}]}}
 const ordinary=memoRecordViews(p,Date.parse('2090-11-05T10:00:00+08:00')) as ScheduleView[]
 expect(ordinary).toHaveLength(1);expect(schedulesForDay(ordinary,'2090-11-05')).toHaveLength(1)
})
