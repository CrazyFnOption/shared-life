import type { Account, Report } from './types'
// Online-only response guard. Its state never enters LedgerStore or IndexedDB.
export function reportContext(account:Account):string{return JSON.stringify([account.user.id,account.restore_id??'',account.source?.scope??null,account.source?.family_id??account.family?.id??null,account.source?.relation_epoch??account.membership_version,account.synced_at,account.snapshot,account.local])}
export interface ReportRead {epoch:number;context:string;id:string;scope:Report['scope'];owner:string|null;version:string;status:Report['status']}
export class ReportGate {
  private epoch=0
  invalidate(){this.epoch++}
  begin(context:string,report:Report):ReportRead{return{epoch:this.epoch,context,id:report.id,scope:report.scope,owner:report.owner_id,version:report.data_version,status:report.status}}
  accepts(token:ReportRead,context:string,userId:string,report:Report):boolean{
    return token.epoch===this.epoch&&token.context===context&&token.id===report.id&&token.scope===report.scope&&token.owner===report.owner_id&&token.version===report.data_version&&token.status===report.status&&(report.scope==='shared'||report.owner_id===userId)
  }
}
export function reportStatus(status:Report['status']):string{return{pending:'等待生成',running:'正在生成',ready:'已更新',stale:'内容已过期，等待重算',failed:'生成失败',unconfigured:'等待配置模型'}[status]}

export function reportErrorMessage(code:string):string{return {ai_budget_unknown:'本次费用尚未确认，原预留仍保留，不代表已扣费；剩余额度内可继续新请求，已有账目和评价保留。',ai_budget_exceeded:'本月 AI 共用额度不足，已暂停新的 AI 请求，已有账目和评价保留。',ai_paused:'恢复后 AI 已暂停，请先核对恢复及本月费用；已有账目和评价保留。',ai_unconfigured:'AI 尚未配置，已有账目和评价保留。'}[code]??'AI 分析暂未完成，已有账目和评价保留，请稍后重试。'}

export function reportCostNotice(report:Pick<Report,'status'|'content'|'cost_status'>):string{if(report.cost_status!=='unknown')return '';return report.status==='ready'&&report.content?'本次月报已保存，可正常阅读；部分步骤费用尚未确认，原预留继续占用额度，不代表已扣费，不会自动重新生成。':'本次费用尚未确认，原预留继续占用额度，不代表已扣费；已停止自动生成，已有分析进度保留，可主动重试。'}
export function reportDisplayError(report:Pick<Report,'error'|'cost_status'>):string{if(!report.error)return '';if(report.cost_status==='unknown'&&['ai_budget_unknown','本次模型结果与费用未知，原预留已保留；已停止自动重试，可主动重试'].includes(report.error))return '';return reportErrorMessage(report.error)}
