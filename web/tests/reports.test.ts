import { expect,test } from 'vitest'
import { ReportGate,reportContext,reportStatus,reportErrorMessage,reportCostNotice,reportDisplayError } from '../src/reports'
import type { Account,Report } from '../src/types'
const account:Account={user:{id:'a',display_name:'甲'},username:'alice',restore_id:'old',snapshot:[],local:[],queue:[],users:[],synced_at:'now',categories:[],categoryQueue:[],images:[]}
const report:Report={id:'r',scope:'shared',owner_id:null,month:'2026-09',status:'ready',data_version:'v1',generated_at:'now',provider:'simulated',model:'test',error:null,summary:{total_cents:0,by_payer:[],unrated_count:0},content:{items:[],summary:'旧共同正文'}}
test('报告在撤回同步后立即失效，再次共享也不能接纳旧迟到正文',()=>{
 const gate=new ReportGate(),context=reportContext(account),token=gate.begin(context,report);expect(gate.accepts(token,context,'a',report)).toBe(true);gate.invalidate();expect(gate.accepts(token,context,'a',report)).toBe(false)
 const newToken=gate.begin(context,report);expect(gate.accepts(newToken,context,'a',{...report,data_version:'v2'})).toBe(false);expect(gate.accepts(newToken,context,'a',{...report,status:'stale',content:null})).toBe(false)
})
test('切号、恢复和范围变化均拒绝迟到报告，包括同月同ID',()=>{
 const gate=new ReportGate(),context=reportContext(account),token=gate.begin(context,report)
 expect(gate.accepts(token,reportContext({...account,user:{id:'b',display_name:'乙'}}),'b',report)).toBe(false)
 expect(gate.accepts(token,reportContext({...account,restore_id:'new'}),'a',report)).toBe(false)
 expect(gate.accepts(token,context,'a',{...report,scope:'private',owner_id:'a'})).toBe(false)
 const privateReport={...report,scope:'private' as const,owner_id:'b'};expect(gate.accepts(gate.begin(context,privateReport),context,'a',privateReport)).toBe(false)
})
test('账目/本机归属变化与重新同步更换在线报告上下文',()=>{
 const before=reportContext(account);expect(reportContext({...account,synced_at:'later'})).not.toBe(before)
 expect(reportContext({...account,local:[{id:'e',owner_id:'a',scope:'private',version:1,amount_cents:100,purpose:'虚构',location:'',occurred_at:'now'}]})).not.toBe(before)
 expect(reportStatus('unconfigured')).toBe('等待配置模型');expect(reportStatus('stale')).toContain('过期')
})

test('月报费用未知、额度不足和恢复暂停均显示各自原因，保留账目评价而不展示机器码',()=>{for(const code of ['ai_budget_unknown','ai_budget_exceeded','ai_paused']){expect(reportErrorMessage(code)).not.toContain(code);expect(reportErrorMessage(code)).toContain('账目和评价保留');}expect(reportErrorMessage('ai_budget_unknown')).toContain('不代表已扣费');expect(reportErrorMessage('ai_budget_unknown')).toContain('剩余额度内可继续');expect(reportErrorMessage('ai_budget_unknown')).not.toContain('额度不足');expect(reportErrorMessage('ai_budget_exceeded')).toContain('额度不足');expect(reportErrorMessage('ai_paused')).toContain('恢复后 AI 已暂停');})


test('月报有效summary与未知费用独立：ready正文仍可读，费用提示不把结果说成生成失败',()=>{const ready:Report={...report,cost_status:'unknown'};const gate=new ReportGate(),context=reportContext(account);expect(gate.accepts(gate.begin(context,ready),context,'a',ready)).toBe(true);expect(ready.content?.summary).toBe('旧共同正文');expect(reportStatus(ready.status)).toBe('已更新');expect(reportCostNotice(ready)).toContain('月报已保存');expect(reportCostNotice(ready)).toContain('不代表已扣费');expect(reportCostNotice(ready)).not.toContain('失败');expect(reportDisplayError(ready)).toBe('');expect(reportCostNotice({...ready,cost_status:null})).toBe('');})

test('月报未知的未完成步骤沿原失败状态与显式重试，费用只提示一次且真失败原因保留',()=>{const partial:Report={...report,status:'failed',content:null,cost_status:'unknown',error:'本次模型结果与费用未知，原预留已保留；已停止自动重试，可主动重试'};expect(reportCostNotice(partial)).toContain('进度保留');expect(reportCostNotice(partial)).toContain('可主动重试');expect(reportDisplayError(partial)).toBe('');expect(reportDisplayError({...partial,error:'ai_paused'})).toContain('恢复后 AI 已暂停');expect(reportDisplayError({...partial,cost_status:null,error:'ai_budget_unknown'})).toContain('不代表已扣费');})
