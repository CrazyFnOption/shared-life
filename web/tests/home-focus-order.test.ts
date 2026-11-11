import { expect, test } from 'vitest'
import { newMemoPartition } from '../src/memos/domain'
import { homeFocusedRecords, memoRecordViews } from '../src/memos/view'
import type { MemoView } from '../src/memos/types'

test('首页混合关注项按原创建时间倒序，编辑及重新关注旧条目不置顶',()=>{
 const source={protocol_version:2 as const,user_id:'fictional-user',restore_id:'fictional-restore',scope:'private' as const,family_id:null,relation_epoch:0},p=newMemoPartition(source)
 p.local=[{id:'old-memo',kind:'memo',scope:'private',family_id:null,author_id:source.user_id,updated_by:source.user_id,category_id:null,title:'旧备忘',body_markdown:'',attachments:[],completed:false,version:1,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z'},{id:'new-memo',kind:'memo',scope:'private',family_id:null,author_id:source.user_id,updated_by:source.user_id,category_id:null,title:'新备忘',body_markdown:'',attachments:[],completed:false,version:1,created_at:'2026-11-12T00:00:00Z',updated_at:'2026-11-12T00:00:00Z'}]
 p.local_focus={id:'fictional-focus',version:1,targets:[{kind:'memo',id:'old-memo'},{kind:'plan_task',id:'plan-task'},{kind:'memo',id:'new-memo'}]}
 const task:MemoView={id:'plan-task',version:1,kind:'memo',origin:'plan_task',title:'计划任务',created_at:'2026-11-11T00:00:00Z',body:'',creator_id:source.user_id,creator_name:'虚构成员',attachments:[],category_id:'',completed:false,focused:true,focus_position:1}
 const unknown={...task,id:'old-unknown-task',created_at:null,focus_position:0}
 const order=()=>homeFocusedRecords([unknown,...memoRecordViews(p),task]).map(record=>record.id)
 expect(order()).toEqual(['new-memo','plan-task','old-memo','old-unknown-task'])
 p.local[0]!.updated_at='2026-12-01T00:00:00Z';p.local[0]!.title='编辑旧备忘';p.local_focus.targets=[{kind:'memo',id:'new-memo'},{kind:'plan_task',id:'plan-task'},{kind:'memo',id:'old-memo'}];p.local_focus.version++
 expect(order()).toEqual(['new-memo','plan-task','old-memo','old-unknown-task'])
})
