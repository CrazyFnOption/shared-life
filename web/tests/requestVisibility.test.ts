import { expect, test } from 'vitest'
import { canKeepRequestDraft, canShowRequestResult } from '../src/requestVisibility'
import type { Account, ChangeRequest, Expense, RequestInput } from '../src/types'
const old:Expense={id:'expense',owner_id:'b',scope:'shared',version:1,amount_cents:20000,purpose:'旧用途',occurred_at:'2026-10-30T00:00:00Z',location:''}
const input:RequestInput={id:'request',expense_id:old.id,base_version:1,kind:'update',data:{amount_cents:20000,purpose:'申请修改用途',occurred_at:old.occurred_at,location:''},reason:'旧申请原因'}
function account(withdrawn:string[]=[]):Account{return{user:{id:'a',display_name:'甲'},username:'a',snapshot:[{...old,version:3}],local:[{...old,version:3}],queue:[],users:[],synced_at:'now',categories:[],categoryQueue:[],images:[],requests:[],withdrawn_request_ids:withdrawn}}
test('已提交丢回执申请在scope往返后被永久撤回，清除表单依据不依赖父仍shared',()=>{
 const draft={original:old,submitted:input}
 expect(canKeepRequestDraft(account(),draft)).toBe(true)
 expect(canKeepRequestDraft(account(['request']),draft)).toBe(false)
 expect(canShowRequestResult(account(['request']),'request')).toBe(false)
})
test('没有撤回ID的首次未送达草稿保留，不能因requests为空或父版本增加而误删',()=>{
 const neverDelivered={original:old,submitted:input}
 expect(canKeepRequestDraft(account(),neverDelivered)).toBe(true)
 expect(canKeepRequestDraft(account(['other-request']),neverDelivered)).toBe(true)
 expect(canKeepRequestDraft(account(['request']),{original:old})).toBe(true)
})
test('迟到成功结果即使有正文和当前共同父，也须服从永久撤回ID',()=>{
 const cached=account(['request'])
 cached.requests=[{...input,requester_id:'a',owner_id:'b',status:'pending',created_at:'now',decided_at:null,before:old} as ChangeRequest]
 expect(canShowRequestResult(cached,'request')).toBe(false)
 expect(canKeepRequestDraft(cached,{original:old,submitted:input})).toBe(false)
 cached.withdrawn_request_ids=[]
 expect(canShowRequestResult(cached,'request')).toBe(true)
})
