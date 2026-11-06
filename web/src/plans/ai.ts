import { apiRequest, ApiError } from '../api'
import { sameSource } from '../sources'
import type { Source } from '../types'
import type { PlanAttachment, PlanRecord } from './types'
export interface AiSource {title:string;url:string;checked_at:string|null;verified:boolean}
export interface AiMaterial {id:string;name:string;mime_type:string;size_bytes:number}
export interface AiMessage {id:string;op_id?:string;role:'user'|'assistant';author_id:string|null;author_name:string;content:string;created_at:string;status:string;cost_status?:'reserved'|'settled'|'unknown'|null;error?:string;sources:AiSource[];chosen_materials:AiMaterial[]}
export interface AiPlanItem {id:string;kind:'stage'|'task';title:string;body_markdown:string;stage_key:string|null;assignee_ids:string[];due_date:string|null;adopted:boolean}
export interface AiPlanDraft {id:string;turn_id:string;base_version:number;items:AiPlanItem[]}
export interface AiConversation {source:Source;record_id:string;configured:boolean;status:'unconfigured'|'ready'|'running'|'failed';messages:AiMessage[];drafts:AiPlanDraft[];chosen_materials:AiMaterial[];budget:{month:string;limit_micro_usd:number;committed_micro_usd:number;remaining_micro_usd:number;confirmed_micro_usd:number;reserved_micro_usd:number;unknown_micro_usd:number;has_unknown:boolean;blocked:boolean}}
export interface AiSendInput {op_id:string;content:string;attachment_ids:string[];allow_web:boolean;base_version:number}
export interface AiSubmittedInput {content:string;attachment_ids:string[];text_edited:boolean;materials_edited:boolean;acknowledged:boolean}
export interface AiInputDraft {content:string;attachment_ids:string[];allow_web:boolean;pending_request?:AiSendInput;pending_input?:AiSubmittedInput}
export interface AiAdoptInput {op_id:string;draft_id:string;item_ids:string[];base_version:number;target_plan_id:string|null}
export interface AiAdoptReceipt {source:Source;receipt:{plan_id:string;adopted_item_ids:string[];record:PlanRecord}}
const path=(recordId:string)=>`/plans/records/${encodeURIComponent(recordId)}/ai`
async function checked<T extends {source:Source}>(promise:Promise<T>,source:Source):Promise<T>{const result=await promise;if(!sameSource(result.source,source))throw new ApiError(409,'response_source_mismatch');return result}
export const AI_READ_TIMEOUT_MS=15000,AI_SEND_TIMEOUT_MS=330000
async function boundedAiRequest<T>(run:(signal:AbortSignal)=>Promise<T>,milliseconds:number,external?:AbortSignal):Promise<T>{
 const controller=new AbortController();let timedOut=false
 const cancel=()=>controller.abort();if(external?.aborted)cancel();else external?.addEventListener('abort',cancel,{once:true})
 const timer=setTimeout(()=>{timedOut=true;controller.abort()},milliseconds)
 try{return await run(controller.signal)}catch(error){if(timedOut)throw new ApiError(408,'ai_request_pending');throw error}finally{clearTimeout(timer);external?.removeEventListener('abort',cancel)}
}
export const planAiApi={
 read:(source:Source,id:string,signal?:AbortSignal)=>boundedAiRequest(inner=>checked(apiRequest<AiConversation>(path(id),source.user_id,undefined,undefined,undefined,source,inner),source),AI_READ_TIMEOUT_MS,signal),
 send:(source:Source,id:string,input:AiSendInput,signal?:AbortSignal)=>boundedAiRequest(inner=>checked(apiRequest<AiConversation>(path(id)+'/messages',source.user_id,input,undefined,undefined,source,inner),source),AI_SEND_TIMEOUT_MS,signal),
 adopt:(source:Source,id:string,input:AiAdoptInput)=>checked(apiRequest<AiAdoptReceipt>(path(id)+'/adopt',source.user_id,input,undefined,undefined,source),source),
}
export const AI_CONTENT_LIMIT=8000,AI_MATERIAL_LIMIT=5
export function validateAiInput(input:AiInputDraft):string{if([...input.content].length>AI_CONTENT_LIMIT)return '发言最多 8,000 字，请精简后再发送。';if(input.attachment_ids.length>AI_MATERIAL_LIMIT)return '每次最多选入 5 项资料，请取消部分选择。';return ''}
export function supportedAiMaterial(file:PlanAttachment):boolean{return file.kind==='photo'&&['image/jpeg','image/png','image/webp'].includes(file.mime_type)||['application/pdf','text/plain'].includes(file.mime_type)}
export function selectionWithStages(draft:AiPlanDraft,selected:string[]):AiPlanItem[]{
 const ids=new Set(selected),byId=new Map(draft.items.map(item=>[item.id,item]))
 for(const id of selected){const item=byId.get(id);if(item?.kind==='task'&&item.stage_key){const stage=byId.get(item.stage_key);if(stage&&!stage.adopted)ids.add(stage.id)}}
 return draft.items.filter(item=>ids.has(item.id)&&!item.adopted)
}
export function aiBudgetNotice(budget:Pick<AiConversation['budget'],'blocked'>):string{return budget.blocked?'本月 AI 剩余额度不足，暂不能发起新请求；现有讨论、计划和输入保留。':''}
export function aiCostNotice(value:Pick<AiConversation,'messages'|'budget'>):string{if(value.messages.some(message=>message.status==='ready'&&message.cost_status==='unknown'))return '讨论结果已保存，可正常查看和采纳；部分请求费用尚未确认，原预留继续占用额度，不代表已扣费。不会自动重新发送，新请求仍受剩余额度限制。';return value.budget.has_unknown?'有费用尚未确认，暂按原预留占用额度，不代表已扣费；剩余额度内可主动发起新请求。':''}
export function aiUsd(value:number|undefined):string{return typeof value==='number'?'$'+(value/1000000).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:6}):'待核对'}
export function requestTurn(value:AiConversation,opId:string,userId:string):AiMessage|undefined{return value.messages.find(message=>message.role==='user'&&message.op_id===opId&&message.author_id===userId)}

// A persisted user turn acknowledges the submitted fields, independently of AI completion.
export function acknowledgedAiInput(current:Pick<AiInputDraft,'content'|'attachment_ids'>,sent:AiSubmittedInput):Pick<AiInputDraft,'content'|'attachment_ids'>{
 return {content:!sent.acknowledged&&!sent.text_edited&&current.content===sent.content?'':current.content,attachment_ids:!sent.acknowledged&&!sent.materials_edited&&JSON.stringify(current.attachment_ids)===JSON.stringify(sent.attachment_ids)?[]:[...current.attachment_ids]}
}

export function aiError(error:unknown):string{
 if(error instanceof ApiError){const messages:Record<string,string>={ai_request_pending:'等待响应较久，正在核对服务端记录；尚不能确认本次结果，输入仍保留。',ai_invalid:'AI 返回内容未通过校验，已保存的发言仍在讨论中，可恢复原文后主动重试；不会自动重新发送。',ai_transient:'本次 AI 连接未完成，已保存的发言仍在讨论中；不会自动重新发送。',ai_result_unknown:'本次 AI 结果尚未确认，已保存的发言仍在讨论中；原费用预留待核对，不会自动重发。',ai_paused:'恢复后 AI 已暂停，请先核对恢复及本月费用；历史讨论与手动计划仍可使用。',ai_unconfigured:'AI 尚未配置，现有计划与输入草稿仍保留。',ai_budget_exceeded:'本月 AI 共用额度不足，现有计划与输入草稿仍保留。',ai_budget_unknown:'本次费用尚未确认，原预留仍保留，不代表已扣费；剩余额度内可主动发起新请求。',plan_conflict:'记录已更新，请刷新讨论并核对最新计划后重试。',ai_draft_stale:'这份草案已过期，请重新讨论后选择；原计划未改动。',response_source_mismatch:'返回内容的来源不一致，已停止显示。',restore_changed:'服务端恢复状态已变化，请先同步；输入草稿仍保留。',account_changed:'登录账号已变化，请恢复原账号。'};return messages[error.code]??(error.status===401?'请先重新登录。':error.status===404?'记录已不可访问，请返回查看。':'请求暂未完成，输入草稿仍保留，请联网后重试。')}
 return '请求暂未完成，输入草稿仍保留，请联网后重试。'
}
export function sourceHref(url:string):string|undefined{try{const parsed=new URL(url);return ['http:','https:'].includes(parsed.protocol)?parsed.href:undefined}catch{return undefined}}
