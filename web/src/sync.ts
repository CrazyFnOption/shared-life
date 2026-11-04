import { localDay } from './memos/dates'
import type { MemoCalendarSnapshot } from './memos/calendarCache'
import type { PlanOperation, PlanReceipt, PlanSnapshot } from './plans/types'
import { verifyMemoDownload } from './memos/files'
import type { MemoOperation, MemoReceipt, MemoSnapshot } from './memos/domain'
import { ApiError, setRestoreId } from './api'
import { AccountChanged, LedgerStore, RestoreChanged } from './store'
import { sameSource, sourceValid, sourcesFor } from './sources'
import type { AccountContext, Category, ImageMeta, Operation, Receipt, Snapshot, Source, User } from './types'
export interface SyncApi{
 memoCalendar?(userId:string,source:Source,month:string):Promise<MemoCalendarSnapshot>
 planSnapshot?(userId:string,source:Source):Promise<PlanSnapshot>
 planSend?(userId:string,operation:PlanOperation,source:Source):Promise<{source:Source;receipt:PlanReceipt}>
 planUpload?(source:Source,parent:string,id:string,blob:Blob):Promise<unknown>
 planDownload?(source:Source,parent:string,id:string):Promise<Blob>
 memoSnapshot?(userId:string,source:Source):Promise<MemoSnapshot>
 memoSend?(userId:string,operation:MemoOperation,source:Source):Promise<{source:Source;receipt:MemoReceipt}>
 memoUpload?(source:Source,memoId:string,id:string,blob:Blob):Promise<unknown>
 memoDownload?(source:Source,memoId:string,id:string):Promise<Blob>
 context(userId:string):Promise<AccountContext>
 addCategory?(userId:string,category:Category,restoreId:string,source:Source):Promise<Category>
 uploadImage?(userId:string,expenseId:string,imageId:string,blob:Blob,restoreId:string,source:Source):Promise<ImageMeta>
 deleteImage?(userId:string,expenseId:string,imageId:string,restoreId:string,source:Source):Promise<void>
 me():Promise<{user:User}>
 snapshot(userId:string,source:Source):Promise<Snapshot>
 send(userId:string,operation:Operation,restoreId:string,source:Source):Promise<Receipt>
}
export class SyncEngine{
 private running=false
 constructor(private store:LedgerStore,private api:SyncApi,private changed:(confirmedContext?:AccountContext)=>void=()=>{}){}
 private async context(userId:string,check:()=>void){
  const before=await this.store.read(userId),result=await this.api.context(userId);check()
  await this.store.reconcile(userId,result,before.restore_id??'')
  const latest=await this.store.read(userId);check();setRestoreId(userId,latest.restore_id);this.changed(latest.context);return latest
 }
 private async snapshot(userId:string,source:Source,check:()=>void){
  await this.store.verifySource(userId,source)
  const result=await this.api.snapshot(userId,source);check()
  if(!sameSource(result.source,source))throw new ApiError(409,'response_source_mismatch')
  await this.store.snapshot(userId,result,source.restore_id)
  await this.store.read(userId);check();this.changed()
 }
 private async memos(userId:string,sources:Source[],check:()=>void,handle:(error:unknown)=>Promise<boolean>){
  if(!this.api.memoSnapshot||!this.api.memoSend||!this.api.memoUpload)return
  for(const source of sources){
   try{
    const download=async()=>{await this.store.verifySource(userId,source);const snapshot=await this.api.memoSnapshot!(userId,source);check();await this.store.memoSnapshot(userId,snapshot,source);this.changed();return snapshot}
    await download()
    const blocked=new Set<string>()
    for(;;){
     const partition=await this.store.memoRead(userId,source),heads=new Map<string,typeof partition.queue[number]>()
     for(const item of partition.queue)if(!heads.has(item.operation.entity_id))heads.set(item.operation.entity_id,item)
     const pending=[...heads.values()].find(p=>!p.frozen&&!p.restore_review&&!p.error&&!blocked.has(p.operation.entity_id))
     if(!pending)break
     try{const result=await this.api.memoSend(userId,pending.operation,pending.source);check();if(!sameSource(result.source,pending.source))throw new ApiError(409,'response_source_mismatch');await this.store.memoAcknowledge(userId,result.receipt,pending.source)}
     catch(error){if(await handle(error)){blocked.add(pending.operation.entity_id);continue}if(permanent(error)){await this.store.memoFail(userId,pending.source,pending.operation.op_id,error.code==='memo_conflict'?'这条备忘已在其他设备修改；本机输入保留，请核对后处理冲突。':'这条备忘未被服务端接受；本机输入与附件保留，请处理同步问题。',error.code,error.current as never);blocked.add(pending.operation.entity_id)}else throw error}
     check();this.changed()
    }
    const partition=await this.store.memoRead(userId,source)
    for(const file of partition.blobs.filter(b=>b.pending&&!b.frozen&&!b.restore_review&&!b.error)){
     const current=await this.store.memoRead(userId,source)
     if(current.queue.some(q=>q.operation.entity_id===file.memo_id)||!current.snapshot.some(m=>m.id===file.memo_id&&m.attachments.some(a=>a.id===file.id)))continue
     try{await this.api.memoUpload(file.source,file.memo_id,file.id,file.blob);check();await this.store.memoBlobResult(userId,file.source,file.id)}
     catch(error){if(await handle(error))continue;if(permanent(error))await this.store.memoBlobResult(userId,file.source,file.id,'附件尚未上传成功，请重试；原文件仍保存在本机。');else throw error}
     this.changed()
    }
    const snapshot=await download()
    if(this.api.memoCalendar){const month=localDay().slice(0,7),calendar=await this.api.memoCalendar(userId,source,month);check();await this.store.memoCalendar(userId,source,month,calendar);this.changed()}
    if(this.api.memoDownload)for(const file of snapshot.attachments.filter(a=>a.state==='uploaded')){
     const current=await this.store.memoRead(userId,source)
     if(current.blobs.some(b=>b.id===file.id)||!current.local.some(m=>m.id===file.memo_id&&m.attachments.some(a=>a.id===file.id)))continue
     try{const blob=await verifyMemoDownload(await this.api.memoDownload(source,file.memo_id,file.id),file);check();await this.store.memoCacheBlob(userId,source,file.memo_id,{...file,source,blob,pending:false});this.changed()}
     catch(error){if(await handle(error))break;if(error instanceof ApiError&&[404,409].includes(error.status))continue;throw error}
    }
   }catch(error){if(!await handle(error))throw error}
  }
 }
 private async plans(userId:string,sources:Source[],check:()=>void,handle:(error:unknown)=>Promise<boolean>){
  if(!this.api.planSnapshot||!this.api.planSend||!this.api.planUpload)return
  for(const source of sources)try{
   const download=async()=>{await this.store.verifySource(userId,source);const snapshot=await this.api.planSnapshot!(userId,source);check();await this.store.planSnapshot(userId,snapshot,source);this.changed();return snapshot}
   await download();const blocked=new Set<string>()
   for(;;){const partition=await this.store.planRead(userId,source),heads=new Map<string,typeof partition.queue[number]>();for(const q of partition.queue)if(!heads.has(q.operation.entity_id))heads.set(q.operation.entity_id,q);const pending=[...heads.values()].find(q=>!q.frozen&&!q.restore_review&&!q.error&&!blocked.has(q.operation.entity_id));if(!pending)break
    try{const result=await this.api.planSend(userId,pending.operation,pending.source);check();if(!sameSource(result.source,pending.source))throw new ApiError(409,'response_source_mismatch');await this.store.planAcknowledge(userId,result.receipt,pending.source)}catch(error){if(await handle(error)){blocked.add(pending.operation.entity_id);continue}if(permanent(error)){
      let current:unknown=error.current
      await this.store.planFail(userId,pending.source,pending.operation.op_id,error.code==='plan_conflict'?'记录已在其他设备修改；本机输入保留，请核对两个版本后选择。':'这条记录未被服务端接受；本机输入与附件保留。',error.code,current);blocked.add(pending.operation.entity_id)}else throw error}check();this.changed()}
   const partition=await this.store.planRead(userId,source)
   for(const file of partition.blobs.filter(b=>b.pending&&!b.frozen&&!b.restore_review&&!b.error)){
    const current=await this.store.planRead(userId,source),parent=[...current.snapshot.records,...current.snapshot.tasks].find(r=>r.id===file.parent_id&&!r.deleted)
    if(current.queue.some(q=>q.operation.entity_id===file.parent_id)||!parent?.attachments.some(a=>a.id===file.id))continue
    try{await this.api.planUpload(file.source,file.parent_id,file.id,file.blob);check();await this.store.planBlobResult(userId,file.source,file.id)}catch(error){if(await handle(error))continue;if(permanent(error))await this.store.planBlobResult(userId,file.source,file.id,'附件尚未上传成功，原文件仍在本机。');else throw error}this.changed()
   }
   const snapshot=await download()
   if(this.api.planDownload)for(const file of snapshot.attachments.filter(a=>a.state==='uploaded'&&a.parent_id)){
    const current=await this.store.planRead(userId,source),parent=[...current.local.records,...current.local.tasks].find(r=>r.id===file.parent_id&&!r.deleted)
    if(current.blobs.some(b=>b.id===file.id)||!parent?.attachments.some(a=>a.id===file.id))continue
    try{const blob=await verifyMemoDownload(await this.api.planDownload(source,file.parent_id!,file.id),file);check();await this.store.planCacheBlob(userId,source,file.parent_id!,{...file,parent_id:file.parent_id!,source,blob,pending:false});this.changed()}catch(error){if(await handle(error))break;if(error instanceof ApiError&&[404,409].includes(error.status))continue;throw error}
   }
  }catch(error){if(!await handle(error))throw error}
 }
 async run(userId:string,check:()=>void=()=>{}):Promise<void>{
  if(this.running)return;this.running=true
  try{await this.store.lock(`${this.store.name}:sync:${userId}`,async()=>{
   await this.store.read(userId);const identity=await this.api.me();check();if(identity.user.id!==userId)throw new AccountChanged()
   const initial=await this.context(userId,check)
   const restoreId=initial.restore_id!
   const handle=async(error:unknown)=>{
    if(error instanceof ApiError&&['restore_changed','relation_changed','family_changed'].includes(error.code)){
     const current=await this.context(userId,check);if(error.code==='restore_changed'||current.restore_id!==restoreId)throw new RestoreChanged();return true
    }
    return false
   }
   for(const source of sourcesFor(initial.context!,userId))try{await this.snapshot(userId,source,check)}catch(error){if(!await handle(error))throw error}
   if(this.api.addCategory){const batch=await this.store.read(userId)
    for(const category of batch.categoryQueue.filter(c=>!c.error&&!c.frozen&&!c.restore_review&&c.source)){
     const source=category.source!,latest=await this.store.read(userId);if(latest.restore_id!==restoreId)throw new RestoreChanged()
     if(!sourceValid(latest,source)||!latest.categoryQueue.some(c=>c.name===category.name&&sameSource(c.source,source)&&!c.error&&!c.frozen))continue
     try{await this.api.addCategory(userId,{scope:category.scope,name:category.name},restoreId,source);await this.store.categoryResult(userId,category,undefined,restoreId)}
     catch(error){if(await handle(error))continue;if(permanent(error))await this.store.categoryResult(userId,category,error.code==='category_deleted'?'此分类已删除；如要重新新增同名分类，请明确确认':'分类未被接受，请重试或放弃',restoreId);else throw error}
     check();this.changed()
    }
   }
   const blocked=new Set<string>()
   for(;;){
    const account=await this.store.read(userId);if(account.restore_id!==restoreId)throw new RestoreChanged()
    const heads=new Map<string,typeof account.queue[number]>()
    for(const item of account.queue)if(!heads.has(item.operation.expense_id))heads.set(item.operation.expense_id,item)
    const pending=[...heads.values()].find(p=>!blocked.has(p.operation.expense_id)&&!p.error&&!p.frozen&&!p.restore_review&&p.source&&sourceValid(account,p.source))
    if(!pending)break
    const source=pending.source!,operation=pending.operation
    try{const receipt=await this.api.send(userId,operation,restoreId,source);await this.store.acknowledge(userId,receipt,restoreId,source)}
    catch(error){
     if(await handle(error)){blocked.add(operation.expense_id);continue}
     if(permanent(error)){const message=error.code==='category_deleted'?'此分类已删除，草稿和图片仍保留；请修正分类或明确恢复同名后重试':error.code==='version_conflict'?'这笔记录在其他设备上有修改，请核对两个版本':error.status===422?'内容未被服务端接受，请保留草稿并核对':error.status===404?'记录不存在或当前账号无权修改':'此笔同步未完成，请处理本地变更后重试';await this.store.markFailed(userId,operation.operation_id,message,error.current,restoreId,error.code,source);blocked.add(operation.expense_id)}else throw error
    }
    check();this.changed()
   }
   if(this.api.uploadImage&&this.api.deleteImage){
    const account=await this.store.read(userId)
    const pending=account.images.filter(i=>i.pending&&!i.error&&!i.frozen&&!i.restore_review&&account.snapshot.some(e=>e.id===i.expense_id&&!e.voided)&&!account.queue.some(p=>p.operation.expense_id===i.expense_id)).sort((a,b)=>Number(b.pending==='delete')-Number(a.pending==='delete'))
    for(const image of pending){
     const latest=await this.store.read(userId),source=image.destination_source??image.source,original=image.original_source??image.source
     if(latest.restore_id!==restoreId)throw new RestoreChanged()
     if(!source||!original||!sourceValid(latest,source)||!sourceValid(latest,original))continue
     const current=latest.images.find(i=>i.id===image.id&&sameSource(i.original_source??i.source,original));if(!current||current.pending!==image.pending||current.error||current.frozen)continue
     try{if(image.pending==='delete'){await this.api.deleteImage(userId,image.expense_id,image.id,restoreId,source);await this.store.imageResult(userId,image,undefined,undefined,restoreId)}else if(image.blob){const meta=await this.api.uploadImage(userId,image.expense_id,image.id,image.blob,restoreId,source);await this.store.imageResult(userId,image,meta,undefined,restoreId)}}
     catch(error){if(await handle(error))continue;if(permanent(error))await this.store.imageResult(userId,image,undefined,imageError(error),restoreId);else throw error}
     check();this.changed()
    }
   }
   await this.plans(userId,sourcesFor((await this.store.read(userId)).context!,userId),check,handle)
   await this.memos(userId,sourcesFor((await this.store.read(userId)).context!,userId),check,handle)
   const latest=await this.context(userId,check);if(latest.restore_id!==restoreId)throw new RestoreChanged()
   for(const source of sourcesFor(latest.context!,userId))try{await this.snapshot(userId,source,check)}catch(error){if(!await handle(error))throw error}
  })}finally{this.running=false}
 }
}
function permanent(error:unknown):error is ApiError{return error instanceof ApiError&&error.status>=400&&error.status<500&&![401,408,429].includes(error.status)&&!['account_changed','restore_changed','family_changed','relation_changed','response_source_mismatch'].includes(error.code)}
function imageError(error:ApiError){return error.code==='image_limit'?'服务端已有3张图片，请移除不需要的图片后重试':error.code==='image_deleted'?'这张图片已在另一处移除，请放弃该附件；如需重新添加，请重新选择图片':error.status===413?'图片超过大小限制，请移除后选择较小图片':error.status===404?'记录或图片已不可访问，请放弃此附件变更':'图片未被接受，可重试或放弃此附件变更'}
