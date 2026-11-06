<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Icon from '../Icon.vue'
import { ApiError } from '../api'
import { sameSource, sourceKey } from '../sources'
import type { Source } from '../types'
import type { PlanRecord, PlanUser } from './types'
import { acknowledgedAiInput, aiError, aiBudgetNotice, aiUsd, requestTurn, planAiApi, selectionWithStages, sourceHref, supportedAiMaterial, validateAiInput, AI_CONTENT_LIMIT, AI_MATERIAL_LIMIT } from './ai'
import type { AiConversation, AiInputDraft, AiPlanDraft, AiSendInput, AiSubmittedInput, AiMessage } from './ai'
import './ai.css'
const props=defineProps<{source:Source;record:PlanRecord;targetPlan?:PlanRecord;users:PlanUser[];inputDraft?:AiInputDraft;writable:boolean}>()
const emit=defineEmits<{close:[];'draft-change':[value:{source:Source;record_id:string;draft:AiInputDraft}];adopted:[value:{source:Source;plan_id:string}];'edit-materials':[]}>()
const conversation=ref<AiConversation>(),tab=ref<'discussion'|'proposal'>('discussion'),loading=ref(false),busy=ref(false),error=ref(''),notice=ref(''),confirming=ref(false),selected=ref<string[]>([]),draftId=ref(''),input=ref(props.inputDraft?.content??''),attachmentIds=ref<string[]>([...props.inputDraft?.attachment_ids??[]]),allowWeb=ref(props.inputDraft?.allow_web??true),composing=ref(false),heading=ref<HTMLElement>()
let epoch=0,alive=true
// Pending operations remain tracked after their persisted user message clears the composer.
const initialRequest=props.inputDraft?.pending_request
const requestFingerprint=(request:AiSendInput)=>JSON.stringify([request.content,request.attachment_ids,request.allow_web,request.base_version])
const initialInput=initialRequest?props.inputDraft?.pending_input??{content:props.inputDraft?.content?.trim()===initialRequest.content?props.inputDraft.content:initialRequest.content,attachment_ids:[...initialRequest.attachment_ids],text_edited:props.inputDraft?.content?.trim()!==initialRequest.content,materials_edited:JSON.stringify(props.inputDraft?.attachment_ids)!==JSON.stringify(initialRequest.attachment_ids),acknowledged:false}:undefined
let attempt:{fingerprint:string;request:AiSendInput;input:AiSubmittedInput}|undefined=initialRequest?{fingerprint:requestFingerprint(initialRequest),request:{...initialRequest,attachment_ids:[...initialRequest.attachment_ids]},input:{...initialInput!,attachment_ids:[...initialInput!.attachment_ids]}}:undefined
let adoptAttempt:{fingerprint:string;request:import('./ai').AiAdoptInput}|undefined
let poll:ReturnType<typeof setInterval>|undefined
const pendingOutcome=ref<'waiting'|'running'|'unknown'|undefined>(initialRequest?'unknown':undefined),reconciling=ref(false),confirmedFailure=ref(false)
type ActiveSend={request:AiSendInput;input:AiSubmittedInput;controller:AbortController;settled:boolean}
let activeSend:ActiveSend|undefined=initialRequest?{request:initialRequest,input:attempt!.input,controller:new AbortController(),settled:false}:undefined,readSequence=0
let statusController:AbortController|undefined
const domain=computed(()=>sourceKey(props.source)+':'+props.record.id)
const drafts=computed(()=>conversation.value?.drafts??[]),activeDraft=computed(()=>drafts.value.find(d=>d.id===draftId.value)??drafts.value.at(-1)),preview=computed(()=>activeDraft.value?selectionWithStages(activeDraft.value,selected.value):[])
const availableMaterials=computed(()=>props.record.attachments.filter(supportedAiMaterial)),blocked=computed(()=>!props.writable||busy.value||loading.value||!conversation.value?.configured||conversation.value?.budget.blocked||conversation.value?.status==='running'||!!pendingOutcome.value)
const target=computed(()=>props.targetPlan??(props.record.kind==='plan'?props.record:undefined))
const author=computed(()=>props.users.find(u=>u.id===props.source.user_id)?.display_name??'我')
function date(value:string|null|undefined){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未核实'}
function member(id:string){return props.users.find(u=>u.id===id)?.display_name??'原成员'}
function emitDraft(){emit('draft-change',{source:{...props.source},record_id:props.record.id,draft:{content:input.value,attachment_ids:[...attachmentIds.value],allow_web:allowWeb.value,...(attempt?{pending_request:{...attempt.request,attachment_ids:[...attempt.request.attachment_ids]},pending_input:{...attempt.input,attachment_ids:[...attempt.input.attachment_ids]}}:{})}})}
watch(input,()=>{if(activeSend&&!activeSend.input.acknowledged)activeSend.input.text_edited=true},{flush:'sync'})
watch(attachmentIds,()=>{if(activeSend&&!activeSend.input.acknowledged)activeSend.input.materials_edited=true},{deep:true,flush:'sync'})
watch([input,attachmentIds,allowWeb],emitDraft,{deep:true})
function accepted(current:number,captured:Source){return alive&&current===epoch&&sameSource(captured,props.source)}
function apply(value:AiConversation){if(value.record_id!==props.record.id)throw new Error('record_mismatch');conversation.value=value;if(!drafts.value.some(d=>d.id===draftId.value))draftId.value=drafts.value.at(-1)?.id??'';selected.value=selected.value.filter(id=>activeDraft.value?.items.some(item=>item.id===id&&!item.adopted))}
async function load(){const current=++epoch,captured={...props.source};loading.value=true;error.value='';try{const value=await planAiApi.read(captured,props.record.id);if(accepted(current,captured)){if(!activeSend||!receiveSend(value,activeSend,true))apply(value)}}catch(cause){if(accepted(current,captured))error.value=aiError(cause)}finally{if(accepted(current,captured))loading.value=false}}
function failureText(code?:string){return aiError(new ApiError(503,code??'ai_failed'))}
function receiveSend(value:AiConversation,sent:ActiveSend,fromRead:boolean):boolean{
 const own=requestTurn(value,sent.request.op_id,props.source.user_id)
 // A read of an older turn or another member's turn is never this request's receipt.
 if(!own)return false
 apply(value)
 if(!sent.input.acknowledged){const cleared=acknowledgedAiInput({content:input.value,attachment_ids:attachmentIds.value},sent.input);sent.input.acknowledged=true;input.value=cleared.content;attachmentIds.value=cleared.attachment_ids;emitDraft()}
 const status=own?.status??value.status
 if(status==='running'){pendingOutcome.value='running';return false}
 sent.settled=true;activeSend=undefined;pendingOutcome.value=undefined;busy.value=false
 if(status==='ready'){
  attempt=undefined;confirmedFailure.value=false
  notice.value=value.drafts.some(draft=>draft.turn_id===own?.id)?'本次方案草稿已更新，可到“方案”逐项选择。':'已读取本次讨论结果。'
  error.value=''
 }else{confirmedFailure.value=true;error.value=failureText(own?.error??(status==='unconfigured'?'ai_unconfigured':'ai_result_unknown'));notice.value='本次结果已核对，发言仍在讨论中；没有自动重新发送。'}
 emitDraft();if(fromRead)sent.controller.abort()
 return true
}
async function reconcileSend(){
 const sent=activeSend;if(!sent||reconciling.value)return
 const current=epoch,captured={...props.source},sequence=++readSequence,controller=new AbortController();statusController=controller;reconciling.value=true
 try{const value=await planAiApi.read(captured,props.record.id,controller.signal);if(!accepted(current,captured)||activeSend!==sent||sequence!==readSequence)return;if(!receiveSend(value,sent,true)){if(!requestTurn(value,sent.request.op_id,captured.user_id)){notice.value='暂未找到本次请求的服务端记录，正在继续核对；不会自动重新发送。'}else notice.value='本次请求仍在处理，可能需要数分钟；可稍后回来查看。'}}
 catch(cause){if(accepted(current,captured)&&activeSend===sent&&sequence===readSequence)error.value=aiError(cause)}
 finally{if(statusController===controller){statusController=undefined;reconciling.value=false}}
}
async function send(){
 if(blocked.value||composing.value)return
 const content=input.value.trim();if(!content){error.value='先写下你的想法或对方案的反馈。';return}
 const validation=validateAiInput({content,attachment_ids:attachmentIds.value,allow_web:allowWeb.value});if(validation){error.value=validation;return}
 if(confirmedFailure.value){attempt=undefined;confirmedFailure.value=false}
 const current=epoch,captured={...props.source},fingerprint=JSON.stringify([content,attachmentIds.value,allowWeb.value,props.record.version])
 if(attempt?.fingerprint!==fingerprint)attempt={fingerprint,request:{op_id:crypto.randomUUID(),content,attachment_ids:[...attachmentIds.value],allow_web:allowWeb.value,base_version:props.record.version},input:{content:input.value,attachment_ids:[...attachmentIds.value],text_edited:false,materials_edited:false,acknowledged:false}}
 const sent:ActiveSend={request:attempt.request,input:attempt.input,controller:new AbortController(),settled:false};activeSend=sent;pendingOutcome.value='waiting';busy.value=true;error.value='';notice.value='';emitDraft()
 try{const value=await planAiApi.send(captured,props.record.id,sent.request,sent.controller.signal);if(!accepted(current,captured)||sent.settled||activeSend!==sent)return;receiveSend(value,sent,false)}
 catch(cause){if(accepted(current,captured)&&activeSend===sent&&!sent.settled){pendingOutcome.value='unknown';error.value=aiError(cause);notice.value='正在只读核对本次请求，没有自动重新发送。'}}
 finally{if(accepted(current,captured)&&activeSend===sent){busy.value=false;void reconcileSend()}}
}
function restoreMessage(item:AiMessage){if(activeSend||busy.value||input.value.trim()||item.author_id!==props.source.user_id)return;input.value=item.content;attachmentIds.value=item.chosen_materials.map(file=>file.id).filter(id=>availableMaterials.value.some(file=>file.id===id));notice.value='已恢复这次发言，可修改后主动发送新请求。'}
function stopPending(){if(activeSend){activeSend.settled=true;activeSend.controller.abort();activeSend=undefined}statusController?.abort();statusController=undefined;readSequence++;pendingOutcome.value=undefined;reconciling.value=false}
function refreshDiscussion(){if(activeSend)void reconcileSend();else void load()}

function chooseDraft(draft:AiPlanDraft){draftId.value=draft.id;selected.value=[];confirming.value=false}
function confirmPreview(){if(!preview.value.length)return;confirming.value=true;void nextTick(()=>heading.value?.focus())}
async function adopt(){if(!activeDraft.value||!preview.value.length||busy.value)return;const current=epoch,captured={...props.source},body={draft_id:activeDraft.value.id,item_ids:preview.value.map(item=>item.id),base_version:target.value?.version??props.record.version,target_plan_id:target.value?.id??null},fingerprint=JSON.stringify(body);if(adoptAttempt?.fingerprint!==fingerprint)adoptAttempt={fingerprint,request:{op_id:crypto.randomUUID(),...body}};const request=adoptAttempt.request;busy.value=true;error.value='';try{const result=await planAiApi.adopt(captured,props.record.id,request);if(!accepted(current,captured))return;adoptAttempt=undefined;confirming.value=false;selected.value=[];emit('adopted',{source:captured,plan_id:result.receipt.plan_id})}catch(cause){if(accepted(current,captured))error.value=aiError(cause)}finally{if(accepted(current,captured))busy.value=false}}
function leave(){epoch++;stopPending();emit('close')}
watch(domain,()=>{epoch++;stopPending();conversation.value=undefined;selected.value=[];draftId.value='';confirming.value=false;input.value=props.inputDraft?.content??'';attachmentIds.value=[...props.inputDraft?.attachment_ids??[]];allowWeb.value=props.inputDraft?.allow_web??true;busy.value=false;attempt=undefined;confirmedFailure.value=false;void load()})
onMounted(()=>{poll=setInterval(()=>{if(navigator.onLine){if(activeSend)void reconcileSend();else if(conversation.value?.status==='running'&&!busy.value&&!loading.value)void load()}},3000)})
onUnmounted(()=>{clearInterval(poll);alive=false;epoch++;stopPending()})
void load()
</script>
<template>
<section class="plan-ai" aria-label="AI 规划">
 <button type="button" class="wp-back" @click="confirming?confirming=false:leave()"><Icon name="back"/> {{ confirming?'返回方案':record.title }}</button>
 <h2 ref="heading" tabindex="-1">{{ confirming?'确认采纳内容':'AI 规划' }}</h2>
 <p class="ai-meta">{{ source.scope==='shared'?'家庭共用讨论 · 发言保留姓名':'私人讨论 · 仅本人可见' }}</p>
 <p v-if="loading" role="status" class="ai-notice">正在读取讨论…</p>
 <p v-if="error" role="alert" class="ai-error">{{ error }}</p>
 <p v-if="conversation&&!conversation.configured" class="ai-notice">AI 尚未配置，手动计划可继续使用，输入草稿会保留。</p>
 <p v-else-if="conversation?.budget.blocked" class="ai-notice">{{ aiBudgetNotice(conversation.budget) }}</p>
 <template v-if="conversation"><p v-if="conversation.budget.has_unknown" class="ai-meta">有费用尚未确认，暂按原预留占用额度，不代表已扣费；剩余额度内可主动发起新请求。</p><details class="ai-budget"><summary>本月 AI 用量与预留</summary><dl><div><dt>已确认用量</dt><dd>{{ aiUsd(conversation.budget.confirmed_micro_usd) }}</dd></div><div><dt>处理中预留</dt><dd>{{ aiUsd(conversation.budget.reserved_micro_usd) }}</dd></div><div><dt>费用未知预留</dt><dd>{{ aiUsd(conversation.budget.unknown_micro_usd) }}</dd></div><div><dt>剩余可用</dt><dd>{{ aiUsd(conversation.budget.remaining_micro_usd) }} / {{ aiUsd(conversation.budget.limit_micro_usd) }}</dd></div></dl><p class="ai-meta">两项 AI 共用月额度。预留不是已确认扣费，新请求仍需有足够的单次预留空间。</p></details></template>
 <p v-if="pendingOutcome" role="status" class="ai-meta">{{ pendingOutcome==='unknown'?'本次结果待确认，正在核对服务端状态。':'本次请求正在处理，可能需要数分钟；可以稍后回来查看，不会自动重新发送。' }}</p>
 <button v-if="(error||pendingOutcome)&&!loading" class="wp-link" type="button" :disabled="reconciling" @click="refreshDiscussion">{{ reconciling?'正在核对…':pendingOutcome?'核对本次请求':'刷新讨论' }}</button>
 <template v-if="confirming">
  <p>将写入「{{ target?.title??record.title }}」{{ target?'':'的新计划' }}。</p>
  <article v-for="item in preview" :key="item.id" class="ai-preview"><span class="ai-meta">新增{{ item.kind==='stage'?'阶段':'任务' }}{{ !selected.includes(item.id)?' · 随任务一并加入':'' }}</span><h3>{{ item.title }}</h3><p class="ai-prose">{{ item.body_markdown }}</p><p v-if="item.kind==='task'" class="ai-meta">{{ item.assignee_ids.length?item.assignee_ids.map(member).join('、'):'负责人待定' }} · {{ item.due_date??'日期待定' }}</p></article>
  <p class="ai-meta">只新增本次选择；原有人工修改与完成状态保留。未选内容仍在草案。</p>
  <div class="ai-actions"><button type="button" class="wp-link" :disabled="busy" @click="confirming=false">取消</button><button type="button" class="wp-save" :disabled="busy" @click="adopt">{{ busy?'正在采纳…':'确认采纳' }}</button></div>
 </template>
 <template v-else>
  <div class="ai-tabs" role="tablist" aria-label="AI 规划内容"><button id="plan-ai-discussion-tab" type="button" role="tab" aria-controls="plan-ai-discussion" :aria-selected="tab==='discussion'" @click="tab='discussion'">讨论</button><button id="plan-ai-proposal-tab" type="button" role="tab" aria-controls="plan-ai-proposal" :aria-selected="tab==='proposal'" @click="tab='proposal'">方案 <span v-if="drafts.length">· {{ drafts.length }}</span></button></div>
  <section id="plan-ai-discussion" role="tabpanel" aria-labelledby="plan-ai-discussion-tab" v-show="tab==='discussion'">
   <p v-if="conversation&&!conversation.messages.length" class="ai-empty">说说想实现什么、有哪些条件。AI 会追问必要缺口，并把讨论整理成阶段、任务与完成标准。</p>
   <article v-for="item in conversation?.messages??[]" :key="item.id" class="ai-message" :class="{'is-assistant':item.role==='assistant'}"><p class="ai-meta">{{ item.role==='assistant'?'AI 规划助手':item.author_name }} · {{ date(item.created_at) }}</p><p class="ai-prose">{{ item.content }}</p><p v-if="item.status==='failed'" class="ai-meta">{{ failureText(item.error) }}</p><button v-if="item.role==='user'&&item.author_id===source.user_id&&item.status==='failed'&&!input.trim()&&!pendingOutcome" type="button" class="wp-link" :disabled="busy" @click="restoreMessage(item)">恢复这次发言</button><p v-if="item.chosen_materials?.length" class="ai-meta">本轮选入：{{ item.chosen_materials.map(file=>file.name).join('、') }}</p><details v-if="item.sources?.length" class="ai-sources"><summary>参考来源 · {{ item.sources.length }} 项</summary><div v-for="(source,index) in item.sources" :key="index"><a v-if="sourceHref(source.url)" :href="sourceHref(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title }}</a><strong v-else>{{ source.title }}</strong><p class="ai-meta">{{ source.verified?'已引用':'参考链接' }} · {{ date(source.checked_at) }}</p></div></details></article>
   <div class="ai-compose"><label for="plan-ai-input">{{ author }}发言</label><textarea id="plan-ai-input" v-model="input" rows="4" :maxlength="AI_CONTENT_LIMIT" placeholder="说说条件，或继续讨论已有方案…" @compositionstart="composing=true" @compositionend="composing=false"></textarea><p class="ai-meta">发言最多 8,000 字；资料每次最多 5 项。</p><details class="ai-materials"><summary>选择本条资料 <span v-if="attachmentIds.length">· 已选 {{ attachmentIds.length }} 项</span></summary><p class="ai-meta">每次最多选入 5 项照片、PDF 或纯文本附件，只发送主动勾选的资料。正文目标和本次讨论会用于规划。</p><label v-for="file in availableMaterials" :key="file.id" class="ai-check"><input v-model="attachmentIds" type="checkbox" :value="file.id" :disabled="busy || attachmentIds.length>=AI_MATERIAL_LIMIT && !attachmentIds.includes(file.id)"><span>{{ file.name }}<small>{{ file.mime_type }} · {{ Math.ceil(file.size_bytes/1024) }} KiB</small></span></label><p v-if="!availableMaterials.length" class="ai-meta">本条还没有可选资料。</p><button type="button" class="wp-link" :disabled="busy" @click="emit('edit-materials')">返回详情添加资料</button></details><p v-if="confirmedFailure" class="ai-meta">如需继续，请主动发起新请求；系统不会重发原请求，新请求会单独预留费用。</p><div class="ai-compose-footer"><label class="ai-check"><input v-model="allowWeb" type="checkbox" :disabled="busy">允许按需联网核实</label><button type="button" class="wp-save" :disabled="blocked||!input.trim()||composing" @click="send">{{ pendingOutcome==='unknown'?'结果待确认':busy||pendingOutcome||conversation?.status==='running'?'正在讨论…':confirmedFailure?'以新请求继续':'发送' }}</button></div></div>
  </section>
  <section id="plan-ai-proposal" role="tabpanel" aria-labelledby="plan-ai-proposal-tab" v-show="tab==='proposal'">
   <p v-if="!activeDraft" class="ai-empty">还没有方案草稿。先在讨论中说说你的目标与条件。</p>
   <template v-else><label v-if="drafts.length>1" class="ai-draft-picker">方案版本<select :value="activeDraft.id" @change="chooseDraft(drafts.find(d=>d.id===($event.target as HTMLSelectElement).value)!)"><option v-for="(draft,index) in drafts" :key="draft.id" :value="draft.id">第 {{ index+1 }} 版</option></select></label><p class="ai-meta">逐项选择后一次确认；任务需要的阶段会在预览中一并列出。</p><label v-for="item in activeDraft.items" :key="item.id" class="ai-choice" :class="{'is-stage':item.kind==='stage'}"><input v-model="selected" type="checkbox" :value="item.id" :disabled="item.adopted||busy" :checked="item.adopted||selected.includes(item.id)"><span><small>{{ item.adopted?'已采纳':item.kind==='stage'?'新增阶段':'新增任务' }}</small><strong>{{ item.title }}</strong><p class="ai-prose">{{ item.body_markdown }}</p><small v-if="item.kind==='task'">{{ item.assignee_ids.length?item.assignee_ids.map(member).join('、'):'负责人待定' }} · {{ item.due_date??'日期待定' }}</small></span></label><button type="button" class="wp-save ai-wide" :disabled="!preview.length||busy||!writable" @click="confirmPreview">预览采纳 {{ preview.length }} 项</button></template>
  </section>
 </template>
 <p v-if="notice" role="status" class="ai-notice">{{ notice }}</p>
</section>
</template>
