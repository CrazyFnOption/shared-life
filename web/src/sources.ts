import type { Account, AccountContext, AccountSource, FamilyContext, LegacySource, Scope, ScopeRef, Source } from './types'

export function sourceKey(source:Source|LegacySource):string {
  return JSON.stringify([source.protocol_version,source.user_id,source.restore_id,source.scope,source.family_id,source.protocol_version===2?source.relation_epoch:source.membership_version])
}
export function sameSource(a:Source|LegacySource|undefined,b:Source|LegacySource|undefined):boolean{return !!a&&!!b&&sourceKey(a)===sourceKey(b)}
export function sourceFor(account:Pick<Account,'user'|'restore_id'|'context'>,ref:ScopeRef):Source {
  if(!account.restore_id)throw new Error('尚未核对服务端来源，请先联网同步')
  const family=ref.scope==='shared'?account.context?.families.find(f=>f.id===ref.family_id):undefined
  if(ref.scope==='shared'&&(!family||!family.relation_epoch))throw new Error('家庭访问已变化，请刷新后重试')
  return {...ref,protocol_version:2,user_id:account.user.id,restore_id:account.restore_id,relation_epoch:family?.relation_epoch??0}
}
export function sourceValid(account:Pick<Account,'user'|'restore_id'|'context'>,source:Source):boolean {
  return source.protocol_version===2&&source.user_id===account.user.id&&source.restore_id===account.restore_id&&(source.scope==='private'?source.family_id===null&&source.relation_epoch===0:!!account.context?.families.some(f=>f.id===source.family_id&&f.relation_epoch===source.relation_epoch))
}
export function requireSource(value:Source|FamilyContext|undefined):Source {
  const source=value&&('protocol_version' in value?value:value.source)
  if(!source||source.protocol_version!==2||!source.user_id||!source.restore_id||source.scope==='shared'&&(!source.family_id||!Number.isInteger(source.relation_epoch)||source.relation_epoch<=0)||source.scope==='private'&&(source.family_id!==null||source.relation_epoch!==0))throw new Error('source_context_required')
  return source
}
export function accountSource(account:Pick<Account,'user'|'restore_id'>):AccountSource {if(!account.restore_id)throw new Error('尚未核对账号恢复域');return{user_id:account.user.id,restore_id:account.restore_id}}
export function sourcesFor(context:AccountContext,userId:string):Source[]{return[{protocol_version:2,user_id:userId,restore_id:context.restore_id,scope:'private',family_id:null,relation_epoch:0},...context.families.map(f=>({protocol_version:2 as const,user_id:userId,restore_id:context.restore_id,scope:'shared' as const,family_id:f.id,relation_epoch:f.relation_epoch!}))]}
export function refMatches(a:ScopeRef,b:ScopeRef):boolean{return a.scope===b.scope&&a.family_id===b.family_id}
export function expenseMatches(expense:{scope:Scope;family_id?:string|null},source:Source|LegacySource):boolean{return expense.scope===source.scope&&(expense.scope==='private'||expense.family_id===source.family_id)}
