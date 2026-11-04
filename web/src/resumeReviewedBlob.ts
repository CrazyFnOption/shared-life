import { api, ApiError } from './api'
import type { LedgerStore } from './store'
import type { AccountContext, Source } from './types'
import type { ModuleIssue } from './moduleIssues'
import { sameSource, sourceFor } from './sources'
import { matchReviewAttachment, sameAttachment } from './blobRestoreReview'
import type { ModuleBlobReview, VerifiedBlobReview } from './blobRestoreReview'
import { blobHash, verifyMemoDownload } from './memos/files'
import { memoApi } from './memos/transport'
import { planApi } from './planTransport'

/** Resume one explicitly selected old-domain Blob, never a record or operation chain. */
export async function resumeReviewedBlob(store:LedgerStore,userId:string,issue:ModuleIssue,confirm:(message:string)=>boolean,contextChanged:(context:AccountContext)=>Promise<void>):Promise<'cancelled'|'pending'|'uploaded'>{
 if(!issue.original_source||!issue.attachment||!issue.review)throw new Error('这不是可核对的恢复附件；原文件已保留。')
 if(!navigator.onLine)throw new Error('需要联网核对原记录和附件；原文件已保留。')
 const original=await store.moduleBlobForReview(userId,issue.key,issue.original_source)
 const load=async(expectedTarget?:Source):Promise<VerifiedBlobReview>=>{
  const before=await store.read(userId),context=await api.context(userId)
  await store.reconcile(userId,context,before.restore_id??'');await contextChanged(context)
  const account=await store.read(userId),target=sourceFor(account,{scope:original.original_source.scope,family_id:original.original_source.family_id} as Source)
  if(expectedTarget&&!sameSource(expectedTarget,target))throw new Error('核对期间账号、恢复域或家庭关系又有变化；原文件已保留，请重新核对。')
  const snapshot=original.module==='memo'?await memoApi.snapshot(userId,target):await planApi.snapshot(userId,target)
  const match=matchReviewAttachment(original,target,snapshot)
  const current=await store.moduleBlobForReview(userId,issue.key,original.original_source,match.dependencies.map(dependency=>dependency.id))
  if(!sameAttachment(current.blob,original.blob)||current.parent_id!==original.parent_id||current.blob.blob.size!==original.blob.blob.size||current.blob.blob.type!==original.blob.blob.type)throw new Error('本机附件在核对期间已变化；原件未被替换。')
  if(current.blob.blob.size!==current.blob.size_bytes||await blobHash(current.blob.blob)!==current.blob.sha256)throw new Error('本机附件实际字节与原声明不一致；原件已保留，未上传。')
  if(match.state==='uploaded'){
   const remote=original.module==='memo'?await memoApi.download(target,current.parent_id,current.blob.id):await planApi.download(target,current.parent_id,current.blob.id)
   await verifyMemoDownload(remote,match.manifest)
  }
  // This is the existing authorized sync cache, not a new parent record.
  if(original.module==='memo')await store.memoSnapshot(userId,snapshot as Awaited<ReturnType<typeof memoApi.snapshot>>,target)
  else await store.planSnapshot(userId,snapshot as Awaited<ReturnType<typeof planApi.snapshot>>,target)
  await store.verifySource(userId,target)
  return{...current,target_source:{...target},match,verified_blob:current.blob.blob,checked_at:new Date().toISOString()}
 }
 try{
  const checked=await load(),account=await store.read(userId),name=checked.target_source.scope==='private'?'私人':account.context?.families.find(family=>family.id===checked.target_source.family_id)?.name??'原家庭'
  if(!confirm(`已联网核对“${checked.blob.name}”与原记录的附件声明一致。仅将这份本机原件绑定到恢复后的“${name}”，需要时继续上传；服务端已有相同字节时只核实同步。不更改原记录或文件内容。继续？`))return'cancelled'
  const fresh=await load(checked.target_source)
  if(fresh.match.dependencies.length!==checked.match.dependencies.length||fresh.match.dependencies.some(dependency=>!checked.match.dependencies.some(previous=>previous.kind===dependency.kind&&previous.id===dependency.id&&previous.version===dependency.version)))throw new Error('原记录、阶段或计划在确认期间已变化；原文件已保留，请重新核对。')
  return await store.commitModuleBlobReview(userId,fresh)
 }catch(error){
  if(error instanceof ApiError){
   if(error.status===401||error.code==='account_changed')throw new Error('账号登录状态已变化，请恢复原账号后再核对；原文件已保留。')
   if(error.code==='restore_changed')throw new Error('服务端恢复域已变化，请先同步后重新核对；原文件已保留。')
   if(['relation_changed','family_changed'].includes(error.code))throw new Error('原家庭访问权限已变化；原文件保留且不会改投其他范围。')
   if(error.status===404)throw new Error('原记录或附件已不存在、已删除或无访问权限；原文件已保留。')
   throw new Error('服务端未接受附件核对；原文件已保留，请稍后重试。')
  }
  if(error instanceof TypeError)throw new Error('暂时无法联网或读取文件进行核对；原件已保留，未上传。')
  if(error instanceof DOMException&&['QuotaExceededError','AbortError'].includes(error.name))throw new Error('本机保存未完成，原来源与附件字节已保留；请处理存储问题后再试。')
  throw error
 }
}
