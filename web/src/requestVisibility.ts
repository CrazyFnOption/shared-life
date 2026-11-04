import type { Account, Expense, RequestInput } from './types'
export interface RequestDraftVisibility { original:Expense;submitted?:RequestInput }
export function requestWithdrawn(account:Account|undefined,id:string):boolean {
  return account?.withdrawn_request_ids?.includes(id)??false
}
export function canKeepRequestDraft(account:Account|undefined,draft:RequestDraftVisibility):boolean {
  if(!account)return false
  if(draft.submitted && requestWithdrawn(account,draft.submitted.id))return false
  return account.local.some(e=>e.id===draft.original.id && e.scope==='shared' && !e.voided && e.owner_id!==account.user.id)
}
export function canShowRequestResult(account:Account|undefined,id:string):boolean {
  return !requestWithdrawn(account,id) && !!account?.requests?.some(item=>item.id===id)
}
