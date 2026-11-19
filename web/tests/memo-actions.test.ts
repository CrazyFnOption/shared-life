import 'fake-indexeddb/auto'
import {expect,test} from 'vitest'
import {LedgerStore} from '../src/store'
import {sourceFor} from '../src/sources'
import {checklistView,copyChecklist,projectMemos,messageOccurrenceAt} from '../src/memos/domain'
import type {MemoRecord, MemoOperation, MemoMessage} from '../src/memos/domain'
import type {AccountContext} from '../src/types'
const user={id:'member',display_name:'虚构成员'}
const context:AccountContext={protocol_version:2,restore_id:'restore',context_version:1,families:[],default_scope:{scope:'private',family_id:null,version:1},default_invalidated_family_id:null,legacy_context_mappings:[]}
const lock=async<T>(_name:string,body:()=>Promise<T>)=>body()
test('清单离线保存冷重开和回执同步，状态深拷贝且全勾不完成',async()=>{
 const name=crypto.randomUUID(),store=new LedgerStore(name,lock);await store.activate(user,'fixture');await store.reconcile(user.id,context)
 const source=sourceFor(await store.read(user.id),{scope:'private',family_id:null}),item:MemoRecord={id:'memo',scope:'private',family_id:null,author_id:user.id,updated_by:user.id,category_id:null,title:'准备',body_markdown:'- 普通列表不转换',attachments:[],completed:false,version:1,created_at:'2026-11-17T00:00:00Z',updated_at:'2026-11-17T00:00:00Z',checklist:{items:[{id:'key',text:'钥匙'}],states:{}}}
 await store.memoSnapshot(user.id,{source,categories:[],memos:[item],attachments:[],users:[user]},source)
 const operation:MemoOperation={op_id:crypto.randomUUID(),entity_type:'memo',entity_id:item.id,kind:'check_item',base_version:1,data:{item_id:'key',checked:true}}
 await store.memoSave(user.id,source,operation)
 const reopened=new LedgerStore(name,lock),partition=await reopened.memoRead(user.id,source);projectMemos(partition)
 expect(checklistView(partition.local[0]!).count).toBe(1);expect(partition.local[0]!.completed).toBe(false);expect(partition.snapshot[0]!.checklist!.states).toEqual({})
 const saved=partition.local[0]!;await reopened.memoAcknowledge(user.id,{op_id:operation.op_id,entity_type:'memo',entity_id:item.id,version:2,record:saved},source)
 expect((await reopened.memoRead(user.id,source)).queue).toHaveLength(0)
 const copied=copyChecklist(saved.checklist);copied.items[0]!.text='改变副本';copied.states.whole!.checked_ids.length=0
 expect(saved.checklist!.items[0]!.text).toBe('钥匙');expect(checklistView(saved).count).toBe(1)
})
test('循环子项键始终原时点，历史冻结清单不套未来模板',()=>{
 const old='2026-11-17T09:00:00+08:00',next='2026-11-18T09:00:00+08:00',checklist={items:[{id:'new',text:'新增项'}],states:{[old]:{checked_ids:['old'],items_snapshot:[{id:'old',text:'旧名称'}]}}}
 const history=checklistView({checklist,occurrence_at:old,archived:true});expect(history.items[0]!.text).toBe('旧名称');expect(history.count).toBe(1)
 expect(checklistView({checklist,occurrence_at:next}).count).toBe(0)
 expect(checklistView({checklist,occurrence_at:'2026-11-16T09:00:00+08:00',archived:true}).items).toEqual([])
})

test('消息详情目标沿完成范围：一次性保持whole，循环保留消息原时点',()=>{
 const source={user_id:user.id,restore_id:'restore',scope:'private' as const,family_id:null,relation_epoch:0},at='2026-11-17T09:00:00+08:00',message:MemoMessage={id:'event',kind:'reminder',target_type:'memo',target_id:'memo',record_id:'memo',title:'原提醒',created_at:at,occurrence_at:at,completion_scope:'whole',source}
 expect(messageOccurrenceAt(message)).toBeUndefined()
 expect(checklistView({checklist:{items:[{id:'key',text:'钥匙'}],states:{whole:{checked_ids:['key']}}},occurrence_at:messageOccurrenceAt(message)}).count).toBe(1)
 expect(messageOccurrenceAt({...message,completion_scope:'occurrence'})).toBe(at)
})
