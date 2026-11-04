import type { Account, CategoryChange, Scope } from './types'

export function categoryNames(account:Account|undefined,scope:Scope):string[]{
  return [...new Set([...(account?.categories??[]),...(account?.categoryQueue??[]).filter(c=>!c.frozen&&!c.error)].filter(c=>c.scope===scope).map(c=>c.name))]
}
export function categoryContext(account:Account):string{
  return JSON.stringify([account.user.id,account.restore_id??'',account.source?.scope??account.legacy_source?.scope??null,account.source?.family_id??account.family?.id??null,account.source?.relation_epoch??account.membership_version??0])
}
export function categoryApprovalCount(account:Account|undefined):number{
  return (account?.category_changes??[]).filter(c=>c.status==='pending'&&c.approvals.some(a=>a.owner_id===account?.user.id&&a.status==='pending')).length
}
export function categoryStatus(status:CategoryChange['status']):string{
  return {preview:'待确认',pending:'等待全部审批',applied:'已统一完成',rejected:'已拒绝 · 全部未改',stale:'记录或分类已变化 · 全部未改'}[status]
}
export function categoryAction(change:Pick<CategoryChange,'kind'|'source_name'|'target_name'|'merge'>):string{
  const target=change.target_name||'未分类'
  return change.kind==='rename'&&change.merge?`「${change.source_name}」合并到「${target}」并删除源分类`:change.kind==='rename'?`「${change.source_name}」改名为「${target}」`:change.kind==='delete'?`删除「${change.source_name}」，原记录改为未分类`:`批量改为「${target}」`
}
export function eligibleSelection(account:Account,scope:Scope,ids:string[]):string[]{
  const pending=new Set(account.queue.map(p=>p.operation.expense_id))
  return ids.filter(id=>account.local.some(e=>e.id===id&&e.scope===scope&&!e.voided)&&!pending.has(id))
}

export function categoryChangePage(changes:CategoryChange[],pending:boolean,page:number){
  const sorted=changes.filter(c=>(c.status==='pending')===pending).sort((a,b)=>b.created_at.localeCompare(a.created_at))
  const pages=Math.max(1,Math.ceil(sorted.length/10)),current=Math.max(1,Math.min(page,pages))
  return {items:sorted.slice((current-1)*10,current*10),total:sorted.length,pages,page:current}
}
