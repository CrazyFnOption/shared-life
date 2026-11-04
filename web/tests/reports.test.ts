import { expect,test } from 'vitest'
import { ReportGate,reportContext,reportStatus } from '../src/reports'
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
