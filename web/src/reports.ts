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
