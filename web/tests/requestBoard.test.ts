import { afterEach,expect,test,vi } from 'vitest'
import { effectScope,ref,nextTick } from 'vue'
import { decisionBody,freezeDecision,needsAttention,useRequestBoard } from '../src/requestBoard'
import { api } from '../src/api'
import { sharedSource,apiResponse } from './sourceFixture'
afterEach(()=>vi.unstubAllGlobals())
test('拒绝决定冻结ID/类型/原因，网络重试不能改变内容；空原因保持旧payload',()=>{
 const first=freezeDecision(undefined,'reject','  需先商量  ')
 expect(first).toMatchObject({decision:'reject',rejection_reason:'需先商量'})
 expect(freezeDecision(first,'reject','改了原因')).toBe(first)
 expect(()=>freezeDecision(first,'accept')).toThrow('上次决定结果未知')
 expect(decisionBody(first.id,first.decision,first.rejection_reason)).toEqual({decision_id:first.id,decision:'reject',rejection_reason:'需先商量'})
 expect(decisionBody('old','reject','')).toEqual({decision_id:'old',decision:'reject'})
 expect(decisionBody('old','accept','')).toEqual({decision_id:'old',decision:'accept'})
 expect(()=>freezeDecision(undefined,'accept','不能带原因')).toThrow('接受决定不能附拒绝原因')
 expect(()=>freezeDecision(undefined,'reject','长'.repeat(501))).toThrow('最多500')
})
test('默认待办只有pending及本人未确认的拒绝，不将已接受/applied/stale放回待办',()=>{
 const item={id:'one',status:'pending',requester_id:'a'}
 expect(needsAttention(item,'b',[],'single')).toBe(true)
 for(const status of ['accepted','applied','stale'])expect(needsAttention({...item,status},'a',[],'single')).toBe(false)
 expect(needsAttention({...item,status:'rejected'},'a',[],'single')).toBe(true)
 expect(needsAttention({...item,status:'rejected'},'b',[],'single')).toBe(false)
 expect(needsAttention({...item,status:'rejected'},'a',['single:one'],'single')).toBe(false)
 expect(needsAttention({...item,status:'rejected'},'a',['single:one'],'category')).toBe(true)
})
test('手动确认仅记UI ID；重开共享确认，账号/家庭/restore上下文隔离且成功时收起历史',async()=>{
 const memory=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>memory.get(key)??null,setItem:(key:string,value:string)=>memory.set(key,value)})
 const scope=effectScope(),context=ref('account-family-restore-'+crypto.randomUUID())
 const original=context.value
 const first=scope.run(()=>useRequestBoard(context))!;first.historyOpen.value=true
 const oldReset=first.resetKey.value
 expect(first.acknowledge(['single:rejected','category:rejected'])).toBe(true)
 expect(first.historyOpen.value).toBe(false);expect(first.resetKey.value).toBeGreaterThan(oldReset)
 const reopened=scope.run(()=>useRequestBoard(context))!;expect(reopened.acknowledged.value).toEqual(['single:rejected','category:rejected'])
 context.value+='-other-family';await nextTick();expect(first.acknowledged.value).toEqual([])
 context.value=original+'-other-restore';await nextTick();expect(first.acknowledged.value).toEqual([])
 context.value=original;await nextTick();expect(first.acknowledged.value).toEqual(['single:rejected','category:rejected'])
 expect([...memory.values()].every(v=>!v.includes('reason'))).toBe(true)
 scope.stop()
})
test('浏览器拒绝持久化时明确返回false，本页仍收起，不影响别的上下文',()=>{
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('blocked')}})
 const scope=effectScope(),context=ref(crypto.randomUUID()),state=scope.run(()=>useRequestBoard(context))!
 expect(state.acknowledge(['single:x'])).toBe(false);expect(state.acknowledged.value).toEqual(['single:x']);scope.stop()
})
test('单笔与分类API仅在非空拒绝原因时增加字段，空值旧回执请求不变',async()=>{
 const calls:RequestInit[]=[];vi.stubGlobal('fetch',vi.fn(async(_url:string,options:RequestInit)=>{calls.push(options);return apiResponse('{}',options)}))
 await api.decide('a','one','d1','reject','r','不同意',sharedSource)
 await api.decide('a','one','d-old','reject','r','',sharedSource)
 await api.decideCategories('a','batch','d2','reject','r',sharedSource,'需重新商量')
 await api.decideCategories('a','batch','d3','accept','r',sharedSource)
 expect(calls.map(c=>JSON.parse(c.body as string))).toEqual([{decision_id:'d1',decision:'reject',rejection_reason:'不同意'},{decision_id:'d-old',decision:'reject'},{decision_id:'d2',decision:'reject',rejection_reason:'需重新商量'},{decision_id:'d3',decision:'accept'}])
})
