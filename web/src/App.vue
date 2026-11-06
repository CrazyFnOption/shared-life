<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { version as appVersion } from '../package.json'
import PlansWorkspace from './plans/PlansWorkspace.vue'
import { taskReference, taskCompleteOperation } from './plans/domain'
import type { PlanFocusCommand } from './plans/types'
import { planDisplaySnapshot } from './planPersistence'
import type { PlanPartition, PlanBlob } from './planPersistence'
import type { PlanCommand, PlanCommandResult, PlanDraftChange, PlanAttachmentCommand, PlanEditorDraft } from './plans/types'
import { copyPlanDraft } from './plans/types'
import { planApi } from './planTransport'
import { parseNotificationIntent } from './notificationLinks'
import type { NotificationIntent } from './notificationLinks'
import { resumeReviewedBlob } from './resumeReviewedBlob'
import ModuleSyncIssues from './ModuleSyncIssues.vue'
import type { ModuleIssue, ModuleIssueAction } from './moduleIssues'
import MessageInbox from './memos/MessageInbox.vue'
import MemosWorkspace from './memos/MemosWorkspace.vue'
import MemoMarkdown from './memos/MemoMarkdown.vue'
import { memoApi } from './memos/transport'
import { prepareMemoFiles, verifyMemoDownload } from './memos/files'
import { memoRecordViews } from './memos/view'
import { copyDraft } from './memos/types'
import type { MemoCategoryCommand, MemoCommandResult, MemoCompleteCommand, MemoDraft, MemoDraftChange, MemoSaveCommand, MemoCommandOrigin, MemoAttachmentView, MemoFocusCommand, MemoOperationCommand, MemoView } from './memos/types'
import type { MemoMessage, MemoOperation, FocusTarget, MemoPartition } from './memos/domain'
import { api, apiWrites, ApiError, setRestoreId } from './api'
import { AccountChanged, activePendingCount, frozenCount, LedgerStore, RestoreChanged } from './store'
import { SyncEngine } from './sync'
import { accountSource, sameSource, sourceFor, sourceKey, sourceValid } from './sources'
import { money, moneyInput, parseMoney } from './money'
import Icon from './Icon.vue'
import { ratingDraft, ratingLabels, ratingReasonVisible, reviewText, matchesRating, type RatingFilter } from './ratings'
import ExpenseFacts from './ExpenseFacts.vue'
import ReviewPanel from './ReviewPanel.vue'
import AccountPanel from './AccountPanel.vue'
import FamilyPanel from './FamilyPanel.vue'
import DeviceNotifications from './DeviceNotifications.vue'
import { parseAppRoute, type AppRoute } from './appNavigation'
import { financialDraftFingerprint } from './financialDrafts'
import CategoryPanel from './CategoryPanel.vue'
import CategoryChangeCards from './CategoryChangeCards.vue'
import DecisionButtons from './DecisionButtons.vue'
import RequestBoardToolbar from './RequestBoardToolbar.vue'
import { freezeDecision, needsAttention, useRequestBoard, type DecisionAttempt } from './requestBoard'
import { categoryApprovalCount, categoryContext, categoryNames, categoryStatus, eligibleSelection } from './categoryChanges'
import { shanghaiDay, shanghaiClock, monthDates } from './recordDates'
import { prepareImage } from './images'
import { ImageUrls } from './imageUrls'
import { applyOperation, netAmount, sameExpense } from './operations'
import { canKeepRequestDraft, canShowRequestResult, requestWithdrawn } from './requestVisibility'
import type { Account, AccountContext, Category, CategoryChange, ChangeRequest, Expense, FamilyContext, FamilyInvite, LocalImage, Maintenance, Operation, Pending, Proposal, RequestInput, Revision, Scope, Source, LegacySource } from './types'

const store = new LedgerStore()
const account = ref<Account>()
const planPartition=ref<PlanPartition>()
const planResult=ref<PlanCommandResult>()
const planBusy=ref(false)
const planError=ref('')
const planDraftDirty=ref<Record<string,boolean>>({})
const planDrafts=ref<Record<string,PlanEditorDraft>>({})
const planDraft=computed(()=>account.value?.source?planDrafts.value[sourceKey(account.value.source)]:undefined)
const planSnapshot=computed(()=>account.value?.source?planDisplaySnapshot(planPartition.value,account.value.source):undefined)
function planDraftChanged(value:PlanDraftChange){const key=sourceKey(value.source);planDraftDirty.value[key]=value.dirty;if(value.draft)planDrafts.value[key]=copyPlanDraft(value.draft);else delete planDrafts.value[key]}
async function planCommand(command:PlanCommand){const id=userId.value,epoch=generation;if(!id||planBusy.value)return;planBusy.value=true;try{
 const files=command.operation.data.attachments as import('./plans/types').PlanAttachment[]|undefined,blobs:PlanBlob[]=[]
 for(const file of command.blobs??[]){const manifest=files?.find(m=>m.id===file.id);if(!manifest)throw new Error('附件声明不完整，未保存。');const blob=file.blob.type===manifest.mime_type?file.blob:new Blob([await file.blob.arrayBuffer()],{type:manifest.mime_type});blobs.push({...manifest,parent_id:file.parent_id,source:{...command.source},blob,pending:true})}
 await store.lock(`${store.name}:identity`,()=>store.planSave(id,command.source,command.operation,blobs));await refresh();signal();if(id===userId.value&&epoch===generation&&sameSource(account.value?.source,command.source))planResult.value={request_id:command.request_id,source:command.source,status:'saved',record_id:command.operation.entity_id};void sync()
}catch(error){if(id===userId.value&&epoch===generation)planResult.value={request_id:command.request_id,source:command.source,status:'error',error:error instanceof Error?error.message:'未保存，输入与附件已保留。'}}finally{planBusy.value=false}}
async function reviewChanged(){await refresh();signal();void sync()}
async function planAiAdopted(value:{source:Source;plan_id:string}){const id=userId.value,epoch=generation;if(!id||id!==value.source.user_id)return;try{const snapshot=await planApi.snapshot(id,value.source);await store.verifySource(id,value.source);await store.planSnapshot(id,snapshot,value.source);if(id===userId.value&&epoch===generation){await refresh();signal()}}catch(error){if(id===userId.value&&epoch===generation&&sameSource(account.value?.source,value.source))planError.value='正式计划已在服务端采纳，本机暂未完成同步；请联网后重试同步。'}}
async function planAttachmentRequested(command:PlanAttachmentCommand){const id=userId.value,epoch=generation;if(!id)return;try{const p=await store.planRead(id,command.source),record=[...p.local.records,...p.local.tasks].find(r=>r.id===command.parent_id&&!r.deleted),manifest=record?.attachments.find(a=>a.id===command.attachment.id);if(!manifest)throw new Error('附件已不可用。');const blob=await verifyMemoDownload(await planApi.download(command.source,command.parent_id,manifest.id),manifest);if(id!==userId.value||epoch!==generation)return;await store.planCacheBlob(id,command.source,command.parent_id,{...manifest,parent_id:command.parent_id,source:command.source,blob,pending:false});await refresh()}catch(error){if(id===userId.value&&epoch===generation)planError.value=error instanceof Error?error.message:'附件暂时无法读取。'}}
const moduleSyncIssues=ref<ModuleIssue[]>([]),moduleIssueBusy=ref(false),moduleIssuesSource=ref<Source>()
const currentModuleIssues=computed(()=>sameSource(moduleIssuesSource.value,account.value?.source)?moduleSyncIssues.value:[])
async function moduleIssueAction(issue:ModuleIssue,action:ModuleIssueAction){const id=userId.value,source=account.value?.source;if(!id||!source||moduleIssueBusy.value||!sameSource(moduleIssuesSource.value,source)||!currentModuleIssues.value.includes(issue))return;if(action==='discard'&&!confirm('放弃这项尚未同步的本机变更？服务端已确认的内容保留。'))return;if(action==='rebase'&&!confirm('已核对差异，将本机修改按服务端最新版本重新提交？'))return;moduleIssueBusy.value=true;try{if(issue.attachment&&issue.review&&action==='review'){const result=await resumeReviewedBlob(store,id,issue,window.confirm.bind(window),context=>refresh(context));if(result==='cancelled')return;message.value=result==='uploaded'?'已核对服务端相同字节，附件已同步，本机原件保留。':'附件已按原范围核对并保存到本机，正在继续上传。'}else await store.moduleIssueAction(id,source,issue.key,action);await refresh();signal();void sync()}catch(error){message.value=error instanceof Error?error.message:'处理未完成，本机内容仍保留。';await refresh()}finally{moduleIssueBusy.value=false}}
const memoPartition=ref<MemoPartition>()
const memoResult=ref<MemoCommandResult>()
const memoBusy=ref(false)
const memoError=ref('')
const calendarLoading=ref(false),calendarError=ref('')
let calendarRequest=0
async function memoCalendarMonth(month:string){const id=userId.value,source=account.value?.source,request=++calendarRequest;if(!id||!source)return;calendarLoading.value=true;calendarError.value='';try{const result=await memoApi.calendar(id,source,month);await store.memoCalendar(id,source,month,result);if(request===calendarRequest&&sameSource(account.value?.source,source))await refresh()}catch(error){if(request===calendarRequest){const cached=!!memoCurrent.value?.calendar?.[month];calendarError.value=cached?'当前显示已缓存日程，暂未完成联网更新。':'此月的重复日程尚未缓存，请联网后再查看。'}}finally{if(request===calendarRequest)calendarLoading.value=false}}
const memoDrafts=ref<Record<string,{draft:MemoDraft;baseline:string}>>({})
const memoFingerprint=(draft:MemoDraft)=>JSON.stringify({...draft,attachments:draft.attachments.map(file=>({id:file.id,size:file.size_bytes}))})
const memoDraft=computed(()=>account.value?.source?memoDrafts.value[sourceKey(account.value.source)]?.draft:undefined)
const memoDirty=computed(()=>Object.values(memoDrafts.value).some(item=>memoFingerprint(item.draft)!==item.baseline))
const memoCurrent=computed(()=>sameSource(account.value?.source,memoPartition.value?.source)?memoPartition.value:undefined)
const memoViews=computed(()=>memoRecordViews(memoCurrent.value))
const memoCategories=computed(()=>{const p=memoCurrent.value,order=p?.local_category_order?.category_ids??[];return p?.local_categories.map((category,index)=>({id:category.id,name:category.name,version:category.version,position:order.includes(category.id)?order.indexOf(category.id):order.length+index}))??[]})
const memoMembers=computed(()=>account.value?.source?.scope==='shared'?account.value.family?.members??[]:account.value?[account.value.user]:[])
const focusedPlanTasks=computed<MemoView[]>(()=>{const snapshot=planSnapshot.value;if(!snapshot)return[];return(memoCurrent.value?.local_focus?.targets??[]).flatMap((target,index)=>{if(target.kind!=='plan_task')return[];const task=taskReference(snapshot,target.id);if(!task||task.completed)return[];return[{id:task.id,version:task.version,kind:'memo' as const,origin:'plan_task' as const,title:task.title,body:task.body_markdown,creator_id:task.author_id,creator_name:snapshot.users.find(u=>u.id===task.author_id)?.display_name??'原记录人',attachments:[],category_id:'',completed:task.completed,focused:true,focus_position:index}]})})
const memosWorkspaceRef=ref<{openTarget:(id:string)=>boolean;resetNavigation:()=>void}>()
const plansWorkspaceRef=ref<{openTarget:(id:string)=>boolean;resetNavigation:()=>void}>()
async function memoOperation(command:MemoOperationCommand){const id=userId.value,epoch=generation;if(!id||memoBusy.value)return;memoBusy.value=true;try{await store.memoSave(id,command.source,command.operation);await refresh();signal();if(id===userId.value&&epoch===generation)memoResult.value={request_id:command.request_id,source:command.source,status:'saved',record_id:command.operation.entity_id};void sync()}catch(error){if(id===userId.value&&epoch===generation)memoResult.value={request_id:command.request_id,source:command.source,status:'error',error:error instanceof Error?error.message:'未保存，输入保留。'}}finally{memoBusy.value=false}}
function focusOperation(source:Source,target:FocusTarget,focused:boolean,requestId:string=crypto.randomUUID()):MemoOperation{const focus=memoPartition.value?.local_focus;if(!focus||!sameSource(memoPartition.value?.source,source))throw new Error('请先联网核对当前关注列表。');const targets=focus.targets.filter(t=>t.kind!==target.kind||t.id!==target.id);if(focused)targets.push(target);return{op_id:requestId,entity_type:'focus',entity_id:focus.id,kind:'update',base_version:focus.version,data:{targets}}}
function memoFocused(command:MemoFocusCommand){try{void memoOperation({...command,operation:focusOperation(command.source,{kind:'memo',id:command.record_id},command.focused,command.request_id)})}catch(error){memoResult.value={...command,status:'error',error:error instanceof Error?error.message:'关注未保存。'}}}
function planFocused(command:PlanFocusCommand){try{void memoOperation({...command,operation:focusOperation(command.source,{kind:'plan_task',id:command.task_id},command.focused,command.request_id)})}catch(error){planError.value=error instanceof Error?error.message:'关注未保存。'}}
function memoReorderFocus(keys:string[]){const focus=memoPartition.value?.local_focus,source=account.value?.source;if(!focus||!source)return;let index=0;const targets=focus.targets.map(target=>keys.includes(`${target.kind}:${target.id}`)?(()=>{const key=keys[index++]!,split=key.indexOf(':');return{kind:key.slice(0,split) as FocusTarget['kind'],id:key.slice(split+1)}})():target),request_id=crypto.randomUUID();void memoOperation({request_id,source,operation:{op_id:request_id,entity_type:'focus',entity_id:focus.id,kind:'update',base_version:focus.version,data:{targets}}})}
function memoReorderCategories(ids:string[]){const order=memoPartition.value?.local_category_order,source=account.value?.source;if(!order||!source)return;const request_id=crypto.randomUUID();void memoOperation({request_id,source,operation:{op_id:request_id,entity_type:'category_order',entity_id:order.id,kind:'update',base_version:order.version,data:{category_ids:ids}}})}
function openPlanTask(id:string){navigate('/plans');void nextTick(()=>plansWorkspaceRef.value?.openTarget(id))}
function completePlanTask(id:string){const task=planSnapshot.value&&taskReference(planSnapshot.value,id),source=account.value?.source;if(!task||!source)return;const operation=taskCompleteOperation(task,true);void planCommand({request_id:operation.op_id,source,operation})}

function memoDraftChanged(value:MemoDraftChange){const key=sourceKey(value.source);if(!value.draft){delete memoDrafts.value[key];return}const prior=memoDrafts.value[key];memoDrafts.value[key]={draft:copyDraft(value.draft),baseline:prior?.baseline??memoFingerprint(value.draft)}}
async function memoSaved(command:MemoSaveCommand){
 const id=userId.value,epoch=generation;if(!id||memoBusy.value)return;memoBusy.value=true;memoError.value=''
 try{
  if([...command.draft.title.trim()].length>200||new TextEncoder().encode(command.draft.body).length>200*1024)throw new Error('标题最多200字，正文最多200KB。')
  const recordId=command.draft.id??crypto.randomUUID(),files=await prepareMemoFiles(command.draft.attachments,command.source,recordId)
  const focus=memoPartition.value?.local_focus,wasFocused=focus?.targets.some(t=>t.kind==='memo'&&t.id===recordId)??false,following:MemoOperation[]=command.draft.kind==='memo'&&focus&&wasFocused!==command.draft.focused?[focusOperation(command.source,{kind:'memo',id:recordId},command.draft.focused)]:[]
  await store.lock(`${store.name}:identity`,()=>store.memoSave(id,command.source,{op_id:command.request_id,entity_type:'memo',entity_id:recordId,kind:command.draft.id?'update':'create',base_version:command.draft.base_version,data:{kind:command.draft.kind,category_id:command.draft.kind==='memo'?command.draft.category_id:null,title:command.draft.title.trim(),body_markdown:command.draft.body,attachments:files.manifest,schedule:command.draft.kind==='schedule'?{start_date:command.draft.start_date,end_date:command.draft.has_end?command.draft.end_date:null,start_time:command.draft.all_day?null:command.draft.start_time,end_time:command.draft.all_day||!command.draft.has_end?null:command.draft.end_time}:null,reminders:command.draft.reminders??[],recurrence:command.draft.recurrence?{...command.draft.recurrence,until:command.draft.recurrence.until||null}:null}},files.blobs,following))
  await refresh();signal()
  if(id===userId.value&&epoch===generation&&sameSource(account.value?.source,command.source))memoResult.value={request_id:command.request_id,source:command.source,status:'saved',record_id:recordId}
  void sync()
 }catch(error){if(id===userId.value&&epoch===generation)memoResult.value={request_id:command.request_id,source:command.source,status:'error',error:error instanceof Error?error.message:'未保存，本机输入已保留。'}}finally{memoBusy.value=false}
}
async function memoCategoryCreated(command:MemoCategoryCommand){const id=userId.value;if(!id||memoBusy.value)return;memoBusy.value=true;try{
 if([...command.name].length>40)throw new Error('分类名称最多40字。')
 await store.memoSave(id,command.source,{op_id:command.request_id,entity_type:'category',entity_id:crypto.randomUUID(),kind:'create',base_version:0,data:{name:command.name}});await refresh();signal();memoResult.value={...command,status:'saved'};void sync()
}catch(error){memoResult.value={...command,status:'error',error:error instanceof Error?error.message:'分类未保存。'}}finally{memoBusy.value=false}}
async function memoCompleted(command:MemoCompleteCommand){const id=userId.value;if(!id||memoBusy.value)return;memoBusy.value=true;try{
 await store.memoSave(id,command.source,{op_id:command.request_id,entity_type:'memo',entity_id:command.record_id,kind:'complete',base_version:command.base_version,data:{completed:command.completed}});await refresh();signal();memoResult.value={...command,status:'saved'};void sync()
}catch(error){memoResult.value={...command,status:'error',error:error instanceof Error?error.message:'状态未保存。'}}finally{memoBusy.value=false}}
async function memoAttachmentRequested(command:MemoCommandOrigin&{record_id:string;attachment:MemoAttachmentView}){
 const id=userId.value,epoch=generation;if(!id)return
 try{const partition=await store.memoRead(id,command.source),manifest=partition.local.find(m=>m.id===command.record_id)?.attachments.find(a=>a.id===command.attachment.id);if(!manifest)throw new Error('附件已不可用。');const blob=await verifyMemoDownload(await memoApi.download(command.source,command.record_id,manifest.id),manifest);if(id!==userId.value||epoch!==generation)return;await store.memoCacheBlob(id,command.source,command.record_id,{...manifest,memo_id:command.record_id,source:command.source,blob,pending:false});await refresh()}catch(error){if(id===userId.value&&epoch===generation)memoError.value=error instanceof Error?error.message:'附件暂时无法读取，请联网重试。'}
}

const userId = ref<string>()
const ready = ref(false)
const serverUnsupported=ref(false)
const locked = ref(false)
const syncing = ref(false)
const syncOpen=ref(false)
const syncTrigger=ref<HTMLButtonElement>()
const networkOnline=ref(navigator.onLine)
const syncProblem=ref('')
const syncSummary=ref({active:0,frozen:0,financial:0,memo:0,plan:0,lastSync:null as string|null,issues:[] as string[]})
const syncNeedsAttention=computed(()=>syncing.value||!networkOnline.value||!!syncProblem.value||syncSummary.value.active>0||syncSummary.value.frozen>0||syncSummary.value.issues.length>0||!syncSummary.value.lastSync)
const syncStatus=computed(()=>syncing.value?'正在同步':!networkOnline.value?'当前离线':syncProblem.value?'同步未完成':syncSummary.value.issues.length?'有内容需要核对':syncSummary.value.active?'有内容待同步':syncSummary.value.frozen?'有旧内容已冻结':!syncSummary.value.lastSync?'尚未完成首次同步':'已同步到服务端')
function closeSyncStatus(){syncOpen.value=false;void nextTick(()=>{(syncTrigger.value??document.querySelector<HTMLElement>('.life-brand'))?.focus()})}

const contextChanging=ref(false)
const saving = ref(false)
const loggingIn = ref(false)
const loggingOut=ref(false)
const logoutError=ref('')
const registering=ref(false)
const displayName=ref('')
const message = ref('')
const formError = ref('')
const loginError = ref('')
const localWarning = ref('')
store.onStatus=value=>{localWarning.value=value;ready.value=true}
const username = ref('')
const password = ref('')
const tab = ref<Scope>('private')
const currentFamilyId=ref<string|null>(null)
const transferFamilyId=ref('')
watch(userId,()=>{syncProblem.value='';syncOpen.value=false;syncSummary.value={active:0,frozen:0,financial:0,memo:0,plan:0,lastSync:null,issues:[]}})
const currentSource=()=>account.value?.source??account.value?.legacy_source
function businessSource():Source{const source=account.value?.source;if(!source)throw new Error('请先联网升级服务端并核对来源，本机待办保留');return source}
async function readCurrent(id:string,source=currentSource()){if(!source)throw new Error('尚未核对当前范围');return store.readProjection(id,source)}
// Navigation never owns a store, sync engine, financial draft or image cache.
const route=ref<AppRoute>(parseAppRoute(window.location.hash))
const inboxOpen=ref(false)
const inboxMessages=ref<MemoMessage[]>([]),inboxIgnored=ref(false),inboxLoading=ref(false),inboxBusy=ref(false),inboxError=ref('')
let inboxRequest=0
async function loadInbox(){const id=userId.value,source=account.value?.source,request=++inboxRequest;if(!id||!source)return;inboxLoading.value=true;inboxError.value='';try{await store.verifySource(id,source);const result=await memoApi.messages(id,source,inboxIgnored.value);await store.verifySource(id,source);if(request===inboxRequest&&id===userId.value&&sameSource(account.value?.source,source))inboxMessages.value=result.messages}catch(error){if(request===inboxRequest)inboxError.value=error instanceof Error?error.message:'消息暂时无法核对，请联网重试。'}finally{if(request===inboxRequest)inboxLoading.value=false}}
watch(()=>account.value?.source?sourceKey(account.value.source):'',()=>{inboxRequest++;inboxOpen.value=false;inboxMessages.value=[];inboxError.value='';inboxIgnored.value=false;moduleSyncIssues.value=[];moduleIssuesSource.value=undefined;calendarRequest++;calendarLoading.value=false;calendarError.value=''},{flush:'sync'})
watch(inboxOpen,open=>{if(open){inboxMessages.value=memoPartition.value?.messages??[];void loadInbox()}})
async function changeInboxIgnored(ignored:boolean){inboxIgnored.value=ignored;await loadInbox()}
async function ignoreMessage(item:MemoMessage,restore=false){const id=userId.value,source=account.value?.source;if(!id||!source||inboxBusy.value||!sameSource(item.source,source))return;inboxBusy.value=true;inboxError.value='';try{await store.verifySource(id,source);await memoApi.ignore(id,source,item.id,restore);await store.verifySource(id,source);await loadInbox();await sync()}catch(error){inboxError.value=error instanceof Error?error.message:'消息操作未确认，请联网重试。'}finally{inboxBusy.value=false}}
async function openMessage(item:MemoMessage){const id=userId.value,source=account.value?.source;if(!id||!source||inboxBusy.value||!sameSource(item.source,source))return;inboxBusy.value=true;inboxError.value='';try{
 await sync();await store.verifySource(id,source);if(!sameSource(account.value?.source,source))return
 const latest=await memoApi.messages(id,source,true),live=latest.messages.find(m=>m.id===item.id);if(!live)throw new Error('这条消息已处理或不再可访问。')
 if(live.target_type==='family_invite'){openFamily('received');return}
 if(live.target_type==='expense_change_request'||live.target_type==='expense_category_change'){if(!openFinancialRequests())throw new Error('请先保存或取消当前输入，再查看这条消息。');await nextTick();if(live.target_type==='expense_change_request'){const index=boardRequests.value.findIndex(r=>r.id===live.target_id);if(index>=0)requestPage.value=Math.floor(index/20)+1;await nextTick();document.getElementById(`request-${live.target_id}`)?.scrollIntoView({block:'center'})}return}
 if(live.target_type==='plan_deletion_request'){const p=await store.planRead(id,source);if(![...p.local.records,...p.local.tasks].some(r=>r.id===live.record_id&&!r.deleted))throw new Error('原记录已不可访问。');navigate('/plans');await nextTick();if(!plansWorkspaceRef.value?.openTarget(live.record_id))throw new Error('请先保存或取消当前编辑，再查看这条消息。');return}
 const p=await store.memoRead(id,source);if(!p.local.some(r=>r.id===live.record_id&&!r.deleted))throw new Error('原记录已不可访问。');navigate('/memos');await nextTick();if(!memosWorkspaceRef.value?.openTarget(live.record_id))throw new Error('请先保存或取消当前编辑，再查看这条消息。')
}catch(error){inboxError.value=error instanceof Error?error.message:'消息暂时无法打开。';message.value=inboxError.value}finally{inboxBusy.value=false}}

const financialNavigationPaused=ref(false)
function resetFinancialNavigation(){
  // Keep every draft and its original source; only its presentation is parked.
  const preserve=!!(form.value||refundForm.value||requestForm.value||hasUnsavedFinancialInputs.value)
  financialNavigationPaused.value=preserve
  if(!preserve){
    detailId.value=undefined;history.value=undefined;requestBoard.value=false
    reviewOpen.value=false;categoryPanel.value=undefined;previewId.value=undefined
  }
  datePickerOpen.value=false;filtersOpen.value=false;closeAccountTools()
}
function resetModuleNavigation(target:AppRoute){
  if(target==='/memos')memosWorkspaceRef.value?.resetNavigation?.()
  else if(target==='/plans')plansWorkspaceRef.value?.resetNavigation?.()
  else if(target==='/accounting')resetFinancialNavigation()
}
function resumeFinancialNavigation(){financialNavigationPaused.value=false;void nextTick(focusDialog)}
function resumeParkedFinancialInput(){
  if(!financialNavigationPaused.value)return false
  resumeFinancialNavigation();message.value='已恢复原范围的未保存输入，请先保存或取消。';return true
}
watch(route,(next,previous)=>{
  if(previous==='/memos'||previous==='/plans')resetModuleNavigation(previous)
  // Reset synchronously, before explicit deep links schedule openTarget.
  resetModuleNavigation(next)
  void nextTick(()=>window.scrollTo({top:0,behavior:'instant'}))
},{flush:'sync'})
const scopeTrigger=ref<HTMLButtonElement>()
const inboxTrigger=ref<HTMLButtonElement>()
const profileTrigger=ref<HTMLButtonElement>()

let pendingNotification:NotificationIntent|undefined=parseNotificationIntent(location.href,location.origin)
const notificationWaiting=ref(false)
let notificationOpening=false
if(new URL(location.href).searchParams.has('notification'))window.history.replaceState(window.history.state,'',location.pathname+location.hash)
async function processNotification(){
 const id=userId.value,intent=pendingNotification
 if(!id||locked.value||!intent||notificationOpening)return
 notificationOpening=true;notificationWaiting.value=false
 try{
  if(intent.source.user_id!==id)throw new Error('通知对应其他账号，请先通过正常账号入口处理；当前内容未改变。')
  const before=await store.read(id),context=await api.context(id);await store.reconcile(id,context,before.restore_id??'');await refresh(context);const latest=await store.read(id)
  if(!sourceValid(latest,intent.source))throw new Error('这条通知的账号、恢复域或家庭权限已变化，未打开旧缓存。')
  if(financialInputsBusy.value||hasUnsavedFinancialInputs.value||memoDraft.value||planDraft.value){notificationWaiting.value=true;message.value='有一条通知待打开，请先保存或取消当前编辑。';return}
  if(!sameSource(account.value?.source,intent.source))await switchBook(intent.source.scope,intent.source.family_id)
  if(!sameSource(account.value?.source,intent.source)){notificationWaiting.value=true;message.value='当前输入已保留；处理后可再打开这条通知。';return}
  const result=await memoApi.notification(id,intent.source,intent.id);await store.verifySource(id,intent.source)
  if(!sameSource(result.source,intent.source)||!sameSource(result.message.source,intent.source))throw new Error('通知来源已变化，未打开旧缓存。')
  pendingNotification=undefined;await openMessage(result.message)
 }catch(error){
  if(error instanceof TypeError||error instanceof ApiError&&([408,429].includes(error.status)||error.status>=500)){pendingNotification=intent;notificationWaiting.value=true;message.value='这条通知需要联网核对；当前输入保留，联网后可再打开。'}
  else if(error instanceof ApiError&&(error.status===401||error.code==='account_changed')){pendingNotification=intent;locked.value=true;message.value='请恢复通知对应账号登录后再打开；本机输入仍保留。'}
  else{pendingNotification=undefined;message.value=error instanceof Error?error.message:'这条通知暂时无法核对，请联网后从消息查看。'}
 }finally{notificationOpening=false}
}
function notificationMessage(event:MessageEvent){if(event.data?.type!=='notification-open'||typeof event.data.url!=='string')return;const intent=parseNotificationIntent(event.data.url,location.origin);if(!intent)return;pendingNotification=intent;notificationWaiting.value=false;void processNotification()}
const scopeName=computed(()=>tab.value==='shared'?account.value?.family?.name??'家庭':'私人')
function navigate(target:AppRoute){
  closeAccountTools();bookPicker.value=false;inboxOpen.value=false
  if(route.value!==target){window.location.hash=target;route.value=target}else resetModuleNavigation(target)
}
function hashChanged(){
  route.value=parseAppRoute(window.location.hash)
  closeAccountTools();bookPicker.value=false;inboxOpen.value=false
  void nextTick(()=>{if(visibleDialog())focusDialog();else document.querySelector<HTMLElement>(route.value==='/home'?'.life-eyebrow':route.value==='/accounting'?'.accounting-heading h1':'.life-settings h1, .config-page-head h1, .config-page-head h2')?.focus()})
}
function returnFromConfig(){navigate('/settings')}
function openFinancialRequests(){navigate('/accounting');if(resumeParkedFinancialInput())return false;requestBoard.value=true;requestMessage.value='';return true}
function openFinancialReview(){if(resumeParkedFinancialInput())return;reviewOpen.value=true}

const filtersOpen=ref(false)
const month = ref(shanghaiMonth(new Date().toISOString()))
const keyword = ref('')
const categoryFilter=ref('')
const dateFilter=ref('')
const datePickerOpen=ref(false)
const datePickerTime=ref(new Date())
function toggleDates(){datePickerTime.value=new Date();datePickerOpen.value=!datePickerOpen.value}
const dateToggle=ref<HTMLButtonElement>()
function chooseDate(day:string){dateFilter.value=day;datePickerOpen.value=false;void nextTick(()=>dateToggle.value?.focus())}
const view=ref<'list'|'stats'>('stats')
const recordFilter=ref<'active'|'voided'>('active')
const ratingFilter=ref<RatingFilter>('')
const accountTools=ref<HTMLDetailsElement>()
function closeAccountTools(restoreFocus=false){if(accountTools.value){accountTools.value.open=false;if(restoreFocus)accountTools.value.querySelector<HTMLElement>('summary')?.focus()}}
function financialMenuKeys(event:KeyboardEvent){
  const menu=accountTools.value;if(!menu)return
  if(event.key==='Escape'){event.preventDefault();closeAccountTools(true);return}
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return
  event.preventDefault();menu.open=true
  const items=[...menu.querySelectorAll<HTMLButtonElement>('.account-tools-menu>button:not(:disabled)')]
  const index=items.indexOf(document.activeElement as HTMLButtonElement)
  const next=event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='ArrowUp'?(index<=0?items.length-1:index-1):(index+1)%items.length
  items[next]?.focus()
}
function dismissFinancialMenu(event:PointerEvent){if(accountTools.value?.open&&!accountTools.value.contains(event.target as Node))closeAccountTools()}
const detailId=ref<string>()
const detail=computed(()=>account.value?.local.find(e=>e.id===detailId.value))
const detailError=ref('')
const history=ref<Revision[]>()
const historyLoading=ref(false)
const maintenanceBusy=ref(false)
const requestBoard=ref(false)
const categoryPanelRef=ref<{hasUnsavedChanges:boolean;isBusy:boolean}>()
const requestCardsRef=ref<{hasUnsavedChanges:boolean}>()
const singleDecisionRefs=ref<{hasUnsavedChanges:boolean}[]>([])
const categoryPanel=ref<{mode:'manage'|'batch';scope:Scope;ids?:string[]}>()
const selecting=ref(false)
const selectedIds=ref<string[]>([])
const categoryDecisions=ref<Record<string,DecisionAttempt>>({})
function openCategoryManager(){if(resumeParkedFinancialInput())return;categoryPanel.value={mode:'manage',scope:tab.value}}
function selectRecord(id:string,checked:boolean){selectedIds.value=checked?[...new Set([...selectedIds.value,id])]:selectedIds.value.filter(item=>item!==id)}
function openBatch(){if(resumeParkedFinancialInput())return;if(selectedIds.value.length)categoryPanel.value={mode:'batch',scope:tab.value,ids:[...selectedIds.value]}}
function clearSelection(){selecting.value=false;selectedIds.value=[]}
async function categoriesChanged(){await refresh();signal()}

const reviewPanelRef=ref<{hasUnsavedChanges:boolean;isBusy:boolean}>()
const familyPanelRef=ref<{isBusy:boolean}>()
const familyOpenSequence=ref(0)
const reviewOpen=ref(false)
const familyOpen=computed(()=>route.value==='/settings/family')
const familyInitialTab=ref<'received'|'sent'>('received')
const receivedInvites=ref<FamilyInvite[]>([])
const sentInvites=ref<FamilyInvite[]>([])
const invitesLoading=ref(false)
const inviteNotice=ref('')
const pendingInvites=computed(()=>receivedInvites.value.filter(i=>i.status==='pending').length)
let seenInviteIds=new Set<string>()
let inviteCheckRunning=false,inviteCheckAgain=false
function openFamily(tab:'received'|'sent'='received'){familyInitialTab.value=tab;familyOpenSequence.value++;navigate('/settings/family');inviteNotice.value='';void checkFamilyAndInvites()}
watch([userId,locked],()=>{
  inboxRequest++;inboxMessages.value=[];inboxError.value='';inboxOpen.value=false;bookPicker.value=false
  if(locked.value){
    moduleSyncIssues.value=[];planPartition.value=undefined;planDraftDirty.value={};planDrafts.value={};planResult.value=undefined;memoPartition.value=undefined;memoDrafts.value={};memoResult.value=undefined
    sessionGeneration++;syncSource?.controller.abort();generation++;form.value=undefined;detailId.value=undefined;history.value=undefined
    refundForm.value=undefined;requestForm.value=undefined;previewId.value=undefined
    reviewOpen.value=false;requestBoard.value=false;categoryPanel.value=undefined
    imageUrlRevision++;imageUrlCache.clear();imageUrls.value={}
  }
},{flush:'sync'})
watch([userId,locked],()=>{receivedInvites.value=[];sentInvites.value=[];inviteNotice.value='';seenInviteIds=new Set()},{flush:'sync'})
watch([userId,locked,()=>account.value?.restore_id,()=>account.value?.family?.id,()=>account.value?.membership_version],()=>{logoutError.value=''},{flush:'sync'})
const requestPage=ref(1)
const requestBusy=ref(false)
const categoryRecoveryBusy=ref(false)
const categoryRepairTargets=ref<Record<string,string>>({})
const requestMessage=ref('')
const decisions=ref<Record<string,DecisionAttempt>>({})
const refundForm=ref<{restore_id:string;family_context:FamilyContext;original:Expense;refund_id:string;editing:boolean;amount:string;time:string;reason:string;request:boolean}>()
const requestForm=ref<{original:Expense;id:string;kind:Proposal['kind'];reason:string;amount:string;purpose:string;time:string;location:string;category:string;refund_id:string;refund_amount:string;refund_time:string;refund_reason:string;submitted?:RequestInput}>()
const requestList=computed(()=>(account.value?.requests??[]).slice().sort((a,b)=>b.created_at.localeCompare(a.created_at)))
const requestContext=computed(()=>account.value?categoryContext(account.value):'')
const {historyOpen:requestHistoryOpen,resetKey:requestResetKey,acknowledged:requestAcknowledged,acknowledge:acknowledgeRequests}=useRequestBoard(requestContext)
const boardRequests=computed(()=>requestList.value.filter(item=>requestHistoryOpen.value||needsAttention(item,userId.value??'',requestAcknowledged.value,'single')))
const requestHistoryCount=computed(()=>requestList.value.filter(r=>r.status!=='pending').length+(account.value?.category_changes??[]).filter(c=>c.status!=='pending').length)
const boardCategoryCount=computed(()=>(account.value?.category_changes??[]).filter(c=>requestHistoryOpen.value||needsAttention(c,userId.value??'',requestAcknowledged.value,'category')).length)
watch(requestResetKey,()=>{requestPage.value=1})
watch(requestBoard,open=>{if(open){requestHistoryOpen.value=false;requestPage.value=1;requestResetKey.value++}})
const awaitingApproval=computed(()=>requestList.value.filter(r=>r.status==='pending' && r.owner_id===userId.value).length+categoryApprovalCount(account.value))
const homeMessageCount=computed(()=>memoCurrent.value?.messages?.filter(m=>!m.ignored).length??0)
const requestPages=computed(()=>Math.max(1,Math.ceil(boardRequests.value.length/20)))
watch(requestPages,n=>{if(requestPage.value>n)requestPage.value=n})
const page=ref(1)
const pageSize=20
const issuePage=ref(1)
const issuePageSize=5
const bookPicker=ref(false)
const newCategory=ref('')
const categoryEditor=ref(false)
const addingCategory=ref(false)
const preparingPhotos=ref(false)
const photoInput=ref<HTMLInputElement>()
const imageUrls=ref<Record<string,string>>({})
const imageUrlCache=new ImageUrls()
let imageUrlsReady:Promise<void>=Promise.resolve()
let imageUrlRevision=0
const previewId=ref<string>()
const loadingImage=ref<string>()
function familyContext():FamilyContext{return{family:account.value?.family??null,membership_version:account.value?.membership_version??0,source:account.value?.source,legacy_source:account.value?.legacy_source}}
function categories(scope:Scope):string[]{return categoryNames(account.value,scope)}

const form = ref<{restore_id:string;family_context:FamilyContext;scope:Scope;id?:string;version?:number;original?:Expense;amount:string;purpose:string;time:string;location:string;category:string;rating:string;rating_reason:string;ratingTouched:boolean;existingReason:boolean;added:{id:string;blob:Blob;url:string}[];removed:string[]}>()


const formInitial=ref(''),refundInitial=ref(''),requestInitial=ref('')
watch(form,draft=>{formInitial.value=financialDraftFingerprint(draft)},{flush:'sync'})
watch(refundForm,draft=>{refundInitial.value=financialDraftFingerprint(draft)},{flush:'sync'})
watch(requestForm,draft=>{requestInitial.value=financialDraftFingerprint(draft)},{flush:'sync'})
const hasUnsavedFinancialInputs=computed(()=>
  !!form.value&&financialDraftFingerprint(form.value)!==formInitial.value ||
  !!refundForm.value&&financialDraftFingerprint(refundForm.value)!==refundInitial.value ||
  !!requestForm.value&&financialDraftFingerprint(requestForm.value)!==requestInitial.value ||
  !!form.value&&!!newCategory.value || !!reviewPanelRef.value?.hasUnsavedChanges || !!categoryPanelRef.value?.hasUnsavedChanges ||
  !!requestCardsRef.value?.hasUnsavedChanges || singleDecisionRefs.value.some(item=>item.hasUnsavedChanges)
)
const financialInputsBusy=computed(()=>moduleIssueBusy.value||inboxBusy.value||planBusy.value||memoBusy.value||saving.value||preparingPhotos.value||addingCategory.value||maintenanceBusy.value||requestBusy.value||categoryRecoveryBusy.value||Math.max(0,apiWrites.value-syncWrites.value)>0||!!categoryPanelRef.value?.isBusy||!!reviewPanelRef.value?.isBusy||!!familyPanelRef.value?.isBusy)
function mayDiscardFinancialInputs(action:'scope'|'logout'){
  if(financialInputsBusy.value){message.value='正在处理当前内容，请稍后再'+(action==='scope'?'切换范围':'退出账号');if(action==='logout')logoutError.value=message.value;return false}
  if(!hasUnsavedFinancialInputs.value&&!(action==='logout'&&(memoDirty.value||Object.values(planDraftDirty.value).some(Boolean))))return true
  return confirm(action==='scope'?'当前范围还有未保存的输入。放弃这些输入并切换范围？选择取消可留在原范围继续。':'还有未保存的输入和图片。放弃这些输入并退出账号？选择取消可保留原账号、范围和输入继续编辑。若退出失败，输入仍保留。')
}
const channel = 'BroadcastChannel' in window ? new BroadcastChannel('shared-expense') : undefined
const supported = !!navigator.locks
let generation = 0
let sessionGeneration = 0
let syncAgain = false
let initialBookPending = false
let initialBookCachedApplied = false
function beginOpeningScope(){initialBookPending=true;initialBookCachedApplied=false}
type SyncSource={userId:string;generation:number;controller:AbortController}
let syncSource:SyncSource|undefined
const syncWrites=ref(0)
class SyncAbandoned extends Error {}
function checkSyncSource(source:SyncSource|undefined):asserts source is SyncSource{
  if(!source||source.controller.signal.aborted||source.userId!==userId.value||source.generation!==sessionGeneration)throw new SyncAbandoned()
}
async function syncRead<T>(request:()=>Promise<T>):Promise<T>{
  const source=syncSource;checkSyncSource(source)
  // Abandon reception of reads; the underlying HTTP request may still finish.
  return new Promise<T>((resolve,reject)=>{
    const signal=source.controller.signal,abandon=()=>reject(new SyncAbandoned())
    signal.addEventListener('abort',abandon,{once:true})
    request().then(value=>{try{checkSyncSource(source);resolve(value)}catch(error){reject(error)}},reject).finally(()=>signal.removeEventListener('abort',abandon))
  })
}
async function syncWrite<T>(request:()=>Promise<T>):Promise<T>{
  const source=syncSource;checkSyncSource(source)
  if(loggingOut.value)throw new SyncAbandoned()
  syncWrites.value++
  try{const result=await request();checkSyncSource(source);return result}
  finally{syncWrites.value--}
}
// This facade shares the one Store/database. Only engine work gets a per-run source
// and cancellable lock wait; lock names and the actual write/receipt protocol stay the same.
const engineStore=Object.create(store) as LedgerStore
engineStore.read=async id=>{const source=syncSource;checkSyncSource(source);const value=await store.read(id);checkSyncSource(source);return value}
const engineLock:typeof store.lock=async(name,body)=>{
  const source=syncSource;checkSyncSource(source)
  return navigator.locks.request(name,{signal:source.controller.signal},async()=>{checkSyncSource(source);return body()})
}
Object.defineProperty(engineStore,'lock',{value:engineLock})
const engine = new SyncEngine(engineStore,{
  planSnapshot:(...args)=>syncRead(()=>planApi.snapshot(...args)),
  planSend:(...args)=>syncWrite(()=>planApi.send(...args)),
  planUpload:(...args)=>syncWrite(()=>planApi.upload(...args)),
  planDownload:(...args)=>syncRead(()=>planApi.download(...args)),
  memoCalendar:(...args)=>syncRead(()=>memoApi.calendar(...args)),
  memoSnapshot:(...args)=>syncRead(()=>memoApi.snapshot(...args)),
  memoSend:(...args)=>syncWrite(()=>memoApi.send(...args)),
  memoUpload:(...args)=>syncWrite(()=>memoApi.upload(...args)),
  memoDownload:(...args)=>syncRead(()=>memoApi.download(...args)),
  me:()=>syncRead(()=>api.me()),
  context:id=>syncRead(()=>api.context(id)),
  snapshot:(id,source)=>syncRead(()=>api.snapshot(id,source)),
  send:(...args)=>syncWrite(()=>api.send(...args)),
  addCategory:(...args)=>syncWrite(()=>api.addCategory(...args)),
  uploadImage:(...args)=>syncWrite(()=>api.uploadImage(...args)),
  deleteImage:(...args)=>syncWrite(()=>api.deleteImage(...args)),
},confirmedContext => {const source=syncSource;if(source&&!source.controller.signal.aborted&&source.generation===sessionGeneration&&source.userId===userId.value)void refresh(confirmedContext)})
const hasPending = computed(() => !!account.value && activePendingCount(account.value)>0)
const frozenPending=computed(()=>account.value?frozenCount(account.value):0)
const pendingIds = computed(() => new Set(account.value?.queue.map(p => p.operation.expense_id)))
const failures = computed(() => account.value?.queue.filter(p => p.error&&!p.frozen && account.value?.local.find(e=>e.id===p.operation.expense_id)?.scope===tab.value) ?? [])
const pendingRecords = computed(() => account.value?.local.filter(e => pendingIds.value.has(e.id) && e.scope===tab.value) ?? [])
const official = computed(() => account.value?.snapshot.filter(e => e.scope === tab.value && !e.voided && shanghaiMonth(e.occurred_at) === month.value) ?? [])
const total = computed(() => official.value.reduce((sum,e) => sum + netAmount(e),0))
const byPayer = computed(() => (account.value?.users ?? []).map(user => ({...user,total:official.value.filter(e => e.owner_id === user.id).reduce((sum,e) => sum + netAmount(e),0)})))
const matchingRecords = computed(() => (account.value?.local ?? []).filter(e => e.scope === tab.value && (recordFilter.value==='voided' ? e.voided && e.owner_id===userId.value : !e.voided) && matchesRating(e.rating,ratingFilter.value) && shanghaiMonth(e.occurred_at) === month.value && e.purpose.includes(keyword.value.trim()) && (!categoryFilter.value || (e.category || '未分类')===categoryFilter.value)).sort((a,b) => b.occurred_at.localeCompare(a.occurred_at)))
const records=computed(()=>matchingRecords.value.filter(e=>!dateFilter.value||shanghaiDay(e.occurred_at)===dateFilter.value))
const dateOptions=computed(()=>monthDates(month.value,matchingRecords.value,datePickerTime.value,dateFilter.value))
const pageCount=computed(()=>Math.max(1,Math.ceil(records.value.length/pageSize)))
const pageRecords=computed(()=>records.value.slice((page.value-1)*pageSize,page.value*pageSize))
const filterTotal=computed(()=>records.value.reduce((sum,e)=>sum+netAmount(e),0))
const categoryStats=computed(()=>[...new Set(official.value.map(e=>e.category||'未分类'))].map(name=>({name,total:official.value.filter(e=>(e.category||'未分类')===name).reduce((n,e)=>n+netAmount(e),0)})).sort((a,b)=>b.total-a.total))
const imageFailures=computed(()=>(account.value?.images??[]).filter(i=>i.error&&!i.frozen && (account.value?.local.find(e=>e.id===i.expense_id)?.scope??i.recovery_scope)===tab.value))
const categoryFailures=computed(()=>(account.value?.categoryQueue??[]).filter(c=>c.error&&!c.frozen&&c.scope===tab.value))
const issueCount=computed(()=>failures.value.length+imageFailures.value.length+categoryFailures.value.length)
const issuePageCount=computed(()=>Math.max(1,Math.ceil(issueCount.value/issuePageSize)))
function issueSlice<T>(items:T[],offset:number):T[]{const start=(issuePage.value-1)*issuePageSize-offset;return items.slice(Math.max(0,start),Math.max(0,start+issuePageSize))}
const visibleFailures=computed(()=>issueSlice(failures.value,0))
const visibleImageFailures=computed(()=>issueSlice(imageFailures.value,failures.value.length))
const visibleCategoryFailures=computed(()=>issueSlice(categoryFailures.value,failures.value.length+imageFailures.value.length))
watch(tab,()=>{issuePage.value=1})
watch(issuePageCount,n=>{if(issuePage.value>n)issuePage.value=n})
const scopePending=computed(()=>pendingRecords.value.length+(account.value?.images??[]).filter(i=>i.pending&&!i.frozen && (account.value?.local.find(e=>e.id===i.expense_id)?.scope??i.recovery_scope)===tab.value).length+(account.value?.categoryQueue??[]).filter(c=>c.scope===tab.value&&!c.frozen).length)
const formImages=computed(()=>form.value?.id ? (account.value?.images??[]).filter(i=>i.expense_id===form.value!.id && i.pending!=='delete' && !form.value!.removed.includes(i.id)) : [])
watch([tab,month,keyword,categoryFilter,recordFilter,ratingFilter,dateFilter],()=>{page.value=1;clearSelection()})
watch([tab,month,userId,locked,()=>account.value?categoryContext(account.value):''],()=>{dateFilter.value='';datePickerOpen.value=false},{flush:'sync'})
watch([userId,locked],()=>{categoryPanel.value=undefined;clearSelection();categoryDecisions.value={};categoryRepairTargets.value={}},{flush:'sync'})
watch(account,next=>{if(next)selectedIds.value=eligibleSelection(next,tab.value,selectedIds.value)})
watch(pageCount,n=>{if(page.value>n)page.value=n})
watch(()=>form.value,(next,old)=>{ if(old&&next!==old)old.added.forEach(i=>URL.revokeObjectURL(i.url)) },{flush:'sync'})
watch([account,locked],()=>{
  const revision=++imageUrlRevision
  const contextKey=account.value?.source?sourceKey(account.value.source):account.value?.legacy_source?sourceKey(account.value.legacy_source):''
  const visibleImages=(account.value?.images??[]).filter(i=>!i.frozen&&account.value?.local.some(e=>e.id===i.expense_id))
  const pending=imageUrlCache.update(locked.value?undefined:account.value?.user.id,contextKey,visibleImages)
  const urls=imageUrlCache.current()
  imageUrls.value=urls
  imageUrlsReady=pending.then(next=>{if(next && revision===imageUrlRevision)imageUrls.value=next},()=>{if(revision===imageUrlRevision)message.value='本机图片暂时无法读取，请联网后点击图片重试'})
  if(locked.value || !account.value){reviewOpen.value=false;requestBoard.value=false;categoryPanel.value=undefined;clearSelection();closeAccountTools()}
  if(locked.value||previewId.value&&!urls[previewId.value])previewId.value=undefined
  if(locked.value || detailId.value&&!account.value?.local.some(e=>e.id===detailId.value)){detailId.value=undefined;history.value=undefined;refundForm.value=undefined}
  if(history.value && detail.value && history.value.at(-1)?.version!==detail.value.version)history.value=undefined
  if(locked.value || requestForm.value&&!canKeepRequestDraft(account.value,requestForm.value))requestForm.value=undefined
})
async function switchBook(scope:Scope,familyId:string|null=null){
  if(scope===tab.value&&familyId===currentFamilyId.value){initialBookPending=false;bookPicker.value=false;return}
  if(scope==='shared'&&!account.value?.context?.families.some(f=>f.id===familyId)){openFamily();return}
  if(!mayDiscardFinancialInputs('scope'))return
  generation++;form.value=undefined;refundForm.value=undefined;requestForm.value=undefined;categoryPanel.value=undefined
  newCategory.value='';detailId.value=undefined;history.value=undefined;previewId.value=undefined;historyDependencies=[]
  imageUrlRevision++;imageUrlCache.clear();imageUrls.value={}
  reviewOpen.value=false;requestBoard.value=false;initialBookPending=false;tab.value=scope;currentFamilyId.value=familyId
  recordFilter.value='active';ratingFilter.value='';categoryFilter.value='';keyword.value='';bookPicker.value=false
  account.value=undefined;await refresh()
}

function changePage(delta:number){page.value=Math.max(1,Math.min(pageCount.value,page.value+delta));document.querySelector('.list-heading')?.scrollIntoView({block:'start'})}
function imagesFor(id:string){return (account.value?.images??[]).filter(i=>i.expense_id===id&&i.pending!=='delete')}
function imageState(id:string){const items=(account.value?.images??[]).filter(i=>i.expense_id===id&&i.pending);return items.length?`图片待同步 ${items.length} 张`:''}
function dayLabel(iso:string){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric'}).format(new Date(iso))}
async function addCategory(){
  if(!form.value||!userId.value||addingCategory.value)return
  const draft=form.value,id=userId.value
  addingCategory.value=true;formError.value=''
  try{await store.addCategory(id,draft.scope,newCategory.value,draft.family_context);await refresh();if(form.value===draft){draft.category=newCategory.value.trim();categoryEditor.value=false;newCategory.value=''}signal();void sync()}
  catch(error){formError.value=error instanceof Error?error.message:'分类未保存，请重试'}
  finally{addingCategory.value=false}
}
async function choosePhotos(event:Event){
  const input=event.target as HTMLInputElement,files=[...(input.files??[])],draft=form.value
  input.value=''
  if(!draft)return
  const room=3-formImages.value.length-draft.added.length
  if(files.length>room){formError.value='每笔最多3张图片，请重新选择';return}
  preparingPhotos.value=true;formError.value=''
  try{for(const file of files){const blob=await prepareImage(file);if(form.value!==draft)return;if(draft.scope==='shared'){const current=await store.readProjection(userId.value!,draft.family_context.source??draft.family_context.legacy_source!);if(current.family?.id!==draft.family_context.family?.id||current.membership_version!==draft.family_context.membership_version)throw new Error('家庭已变化，请在当前家庭重新打开表单选图')}draft.added.push({id:crypto.randomUUID(),blob,url:URL.createObjectURL(blob)})}}
  catch(error){formError.value=error instanceof Error?error.message:'图片处理失败，已选内容仍保留'}
  finally{preparingPhotos.value=false}
}
function removeAdded(id:string){if(!form.value)return;const image=form.value.added.find(i=>i.id===id);if(image)URL.revokeObjectURL(image.url);form.value.added=form.value.added.filter(i=>i.id!==id)}
async function showImage(image:LocalImage){
  const source=currentSource(),id=userId.value,epoch=generation,restoreId=account.value?.restore_id??'',version=account.value?.local.find(e=>e.id===image.expense_id)?.version
  await imageUrlsReady
  const familyId=account.value?.family?.id??null,membershipVersion=account.value?.membership_version
  if(!id||locked.value||version===undefined||epoch!==generation||id!==userId.value||restoreId!==(account.value?.restore_id??'')||!account.value?.images.some(item=>item.id===image.id&&item.pending!=='delete'&&!item.frozen))return
  if(imageUrls.value[image.id]){previewId.value=image.id;return}
  if(!navigator.onLine){message.value='这张图片尚未缓存，请联网后查看';return}
  if(!source||source.protocol_version!==2){message.value='请先联网完成来源核对，再下载未缓存图片';return}
  loadingImage.value=image.id
  try{const blob=await api.getImage(id,image.expense_id,image.id,source);if(epoch!==generation||userId.value!==id||locked.value||familyId!==(account.value?.family?.id??null)||membershipVersion!==account.value?.membership_version)return;await store.cacheImage(id,image.id,blob,version,restoreId,source);await refresh();await nextTick();await imageUrlsReady;if(epoch===generation && imageUrls.value[image.id])previewId.value=image.id}
  catch(error){if(epoch!==generation)return;if(error instanceof AccountChanged || error instanceof ApiError && [401,409].includes(error.status)){locked.value=true;message.value='登录状态已变化，请恢复原账号'}else message.value='图片暂时无法读取，请联网后重试'}
  finally{if(epoch===generation)loadingImage.value=undefined}
}
async function imageAction(id:string,discard=false){if(!userId.value)return;if(discard&&!confirm('放弃这张图片的本地变更？账目和金额会保留。'))return;try{await store.retryImage(userId.value,id,discard,account.value?.restore_id??'',businessSource());await refresh();signal();void sync()}catch(error){message.value=error instanceof Error?error.message:'处理未完成，本机内容仍保留';await refresh()}}
async function categoryAction(category:Category,discard=false){if(!userId.value)return;try{await store.retryCategory(userId.value,{...category,source:businessSource()},discard,account.value?.restore_id??'');await refresh();signal();void sync()}catch(error){message.value=error instanceof Error?error.message:'分类处理未完成，请重试';await refresh()}}
async function restoreCategory(category:Category){
 const id=userId.value,epoch=generation,expected=account.value?categoryContext(account.value):''
 if(!id||!navigator.onLine){message.value='重新新增同名分类需要联网';return}
 if(!confirm(`「${category.name}」已删除。明确重新新增同名分类？旧支出保持当前分类。`))return
 try{await store.lock(`${store.name}:sync:${id}`,async()=>{
   const latest=await readCurrent(id)
   if(epoch!==generation||categoryContext(latest)!==expected)throw new AccountChanged()
   await api.addCategory(id,{scope:category.scope,name:category.name,restore_deleted:true},latest.restore_id??'',{family:latest.family??null,membership_version:latest.membership_version??0,source:latest.source})
   if(epoch!==generation||categoryContext(await readCurrent(id))!==expected)throw new AccountChanged()
   await store.categoryResult(id,{...category,source:latest.source},undefined,latest.restore_id??'')
   await requestSnapshot(id,epoch)
 });if(epoch===generation)message.value='同名分类已重新新增'}catch(error){if(epoch===generation)message.value=onlineError(error)}
}
function shanghaiMonth(iso:string): string { const date = new Date(iso); return `${new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric'}).format(date)}-${new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',month:'2-digit'}).format(date)}` }
function localTime(iso:string): string { const date = new Date(iso); const shifted = new Date(date.getTime()-date.getTimezoneOffset()*60000); return shifted.toISOString().slice(0,16) }
function displayTime(iso:string): string { return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso)) }
function payer(id:string): string { return account.value?.users.find(user => user.id === id)?.display_name ?? '另一位付款人' }
function failure(id:string) { return failures.value.find(p => p.operation.expense_id === id) }
function signal() { channel?.postMessage('changed') }
let historyDependencies:{family_id:string;relation_epoch:number}[]=[]
function clearSensitiveView(){
 moduleSyncIssues.value=[];planPartition.value=undefined;planResult.value=undefined;planError.value='';planDraftDirty.value={};planDrafts.value={};memoPartition.value=undefined;memoResult.value=undefined;memoError.value='';memoDrafts.value={};generation++;requestBusy.value=false;maintenanceBusy.value=false;categoryPanel.value=undefined;clearSelection();categoryDecisions.value={};categoryRepairTargets.value={}
 detailId.value=undefined;history.value=undefined;historyDependencies=[];refundForm.value=undefined;requestForm.value=undefined;requestBoard.value=false;reviewOpen.value=false;form.value=undefined;previewId.value=undefined;decisions.value={};requestMessage.value=''
 imageUrlRevision++;imageUrlCache.clear();imageUrls.value={}
}
async function refresh(confirmedContext?:AccountContext):Promise<void>{
 const id=userId.value,epoch=generation;if(!id)return
 try{
  const meta=await store.read(id);if(userId.value!==id||generation!==epoch)return
  const restored=account.value?.restore_id&&meta.restore_id!==account.value.restore_id
  let revoked=false
  const onlineDefaultConfirmed=!!confirmedContext&&confirmedContext.restore_id===meta.restore_id&&confirmedContext.context_version<=(meta.context?.context_version??0)
  if(initialBookPending&&(!initialBookCachedApplied||onlineDefaultConfirmed)){
   if(meta.context){tab.value=meta.context.default_scope.scope;currentFamilyId.value=meta.context.default_scope.family_id;initialBookCachedApplied=true;if(onlineDefaultConfirmed)initialBookPending=false;if(meta.context.default_invalidated_family_id)message.value='原默认家庭已失效，本次使用私人范围。'}
   else if(!meta.migration_version||meta.legacy_staging){tab.value=meta.family?'shared':'private';currentFamilyId.value=meta.family?.id??null;initialBookCachedApplied=true}
  }
  if(tab.value==='shared'&&meta.context&&!meta.context.families.some(f=>f.id===currentFamilyId.value&&(!account.value?.source||account.value.source.family_id!==f.id||account.value.source.relation_epoch===f.relation_epoch))){revoked=true;tab.value='private';currentFamilyId.value=null;initialBookPending=false}
  if(restored||revoked){clearSensitiveView();message.value=restored?'服务端已从备份恢复，已保存的本机待办和图片保留供核对，不会自动重放。':'你已不在原家庭，旧共同内容已撤回；未同步共同待办已冻结。'}
  if(historyDependencies.some(d=>!meta.context?.families.some(f=>f.id===d.family_id&&f.relation_epoch===d.relation_epoch))){history.value=undefined;historyDependencies=[]}
  let next:Account
  if(meta.context&&meta.restore_id){const source=sourceFor(meta,tab.value==='shared'?{scope:'shared',family_id:currentFamilyId.value!}:{scope:'private',family_id:null});next=await store.readProjection(id,source)}
  else if(!meta.context&&(meta.migration_version!==2||meta.legacy_staging)){const legacy:LegacySource={protocol_version:1,user_id:id,restore_id:meta.restore_id??'',scope:tab.value,family_id:tab.value==='shared'?currentFamilyId.value:null,membership_version:meta.membership_version??0};next=await store.readProjection(id,legacy)}
  else next={...meta,snapshot:[],local:[],queue:[],categories:[],categoryQueue:[],images:[],users:[meta.user],requests:[],category_changes:[],pending_summary:{active:activePendingCount(meta),frozen:frozenCount(meta)}}
  if(userId.value===id&&(generation===epoch||restored||revoked)){
   account.value=next;setRestoreId(id,next.restore_id)
   for(const [key,value] of Object.entries(memoDrafts.value)){const source=JSON.parse(key);if(value&&(!sourceValid(meta,{protocol_version:source[0],user_id:source[1],restore_id:source[2],scope:source[3],family_id:source[4],relation_epoch:source[5]} as Source)))delete memoDrafts.value[key]}
   for(const [key,draft]of Object.entries(planDrafts.value))if(!sourceValid(meta,draft.source)){delete planDrafts.value[key];delete planDraftDirty.value[key]}
   const origin=next.source
   if(origin){
    const sources=[origin,...((meta.memo_pending?.active??0)+(meta.plan_pending?.active??0)>0&&meta.context?[sourceFor(meta,{scope:'private',family_id:null}),...meta.context.families.map(f=>sourceFor(meta,{scope:'shared',family_id:f.id}))].filter(source=>!sameSource(source,origin)):[])]
    const [memo,plan,allIssues]=await Promise.all([store.memoRead(id,origin),store.planRead(id,origin),Promise.all(sources.map(source=>store.moduleIssues(id,source)))])
    if(userId.value===id&&sameSource(account.value?.source,origin)){
     memoPartition.value=memo;planPartition.value=plan;moduleSyncIssues.value=allIssues[0]??[];moduleIssuesSource.value={...origin}
     const memoPending=meta.memo_pending?.active??0,planPending=meta.plan_pending?.active??0,active=activePendingCount(meta)
     const financialIssues=[...meta.queue,...meta.categoryQueue,...meta.images.filter(image=>image.pending)].filter(item=>!item.frozen&&(item.error||('restore_review'in item&&item.restore_review))).map(item=>`共同记账：${item.error||'恢复后内容待核对'}`)
     const moduleIssues=allIssues.flatMap((issues,index)=>issues.map(issue=>`${issue.module==='memo'?'生活备忘':'愿望与计划'} · ${sources[index]?.scope==='private'?'私人':meta.context?.families.find(f=>f.id===sources[index]?.family_id)?.name??'家庭'}：${issue.message}`))
     syncSummary.value={active,frozen:frozenCount(meta),financial:Math.max(0,active-memoPending-planPending),memo:memoPending,plan:planPending,lastSync:[meta.synced_at,memo.synced_at,plan.synced_at].filter((value):value is string=>!!value).sort().at(-1)??null,issues:[...new Set([...financialIssues,...moduleIssues])]}
    }
   }else{memoPartition.value=undefined;const active=activePendingCount(meta);syncSummary.value={active,frozen:frozenCount(meta),financial:active,memo:0,plan:0,lastSync:meta.synced_at,issues:meta.queue.filter(item=>!item.frozen&&item.error).map(item=>item.error!)}}
  }
  if(confirmedContext&&pendingNotification&&!notificationOpening&&!notificationWaiting.value)void processNotification()
 }catch(error){if(error instanceof AccountChanged){locked.value=true;message.value='账号已在其他页面变化，请重新登录'}else if(error instanceof Error)message.value=error.message}
}

async function sync(): Promise<void> {
  const id = userId.value
  const epoch = sessionGeneration
  if (!id || locked.value || loggingOut.value) return
  if (syncing.value) { syncAgain=true; return }
  syncAgain=false
  const source:SyncSource={userId:id,generation:epoch,controller:new AbortController()}
  syncSource=source
  syncing.value = true
  networkOnline.value=navigator.onLine
  try {
    await engine.run(id,()=>checkSyncSource(source));serverUnsupported.value=false
    if (epoch !== sessionGeneration||source.controller.signal.aborted) return
    await refresh()
    if(epoch!==sessionGeneration)return

    syncProblem.value=''
    if(['正在同步','已同步到服务端','暂未同步，本机记录仍在','部分内容仍待同步或处理，请在对应模块查看','已保存到手机，待同步','账目和图片已保存到手机，待同步','退款已保存到手机，待同步'].includes(message.value))message.value=''
  } catch (error) {
    if (epoch !== sessionGeneration||source.controller.signal.aborted) return
    if (error instanceof AccountChanged || error instanceof ApiError && (error.status===401 || error.code==='account_changed')) {
      locked.value = true
      username.value = account.value?.username ?? ''
      message.value = error instanceof ApiError && error.status===401 ? '登录已失效，本机内容已保留。请用原账号重新登录' : '登录账号已变化，本机内容已锁定。请重新登录'
    } else {if(error instanceof ApiError&&[404,426].includes(error.status))serverUnsupported.value=true;syncProblem.value=error instanceof RestoreChanged ? error.message : error instanceof ApiError&&[404,426].includes(error.status)?'服务端需升级，已停止业务同步；本机待办与图片保留。':error instanceof ApiError?'服务端暂未确认同步，本机内容仍保留，请稍后重试。':'网络暂未确认同步，本机内容仍保留。';if(error instanceof RestoreChanged||serverUnsupported.value)message.value=syncProblem.value}
  } finally {
    if(epoch===sessionGeneration&&userId.value===id&&!source.controller.signal.aborted)await refresh()
    if(syncSource===source){
      syncSource=undefined;syncing.value=false;signal()
      const again=syncAgain;syncAgain=false
      if(again&&userId.value&&!locked.value&&!loggingOut.value)void sync()
    }
  }
}
async function login(): Promise<void> {
  if (!supported || loggingIn.value) return
  loggingIn.value=true
  loginError.value=''
  const epoch = generation
  try {
    const active = await store.active()
    const previous = active ? await store.read(active) : undefined
    if (previous && activePendingCount(previous)>0 && username.value.trim() !== previous.username) throw new Error('有待同步记录，请用原账号恢复登录')
    const result = await api.login(username.value.trim(),password.value)
    if (epoch !== generation) return
    if (previous && activePendingCount(previous)>0 && result.user.id !== previous.user.id) throw new Error('有待同步记录，只能恢复原账号')
    await store.activate(result.user,username.value.trim())
    sessionGeneration++;generation++
    detailId.value=undefined;history.value=undefined;refundForm.value=undefined;requestForm.value=undefined;requestBoard.value=false;reviewOpen.value=false;decisions.value={};requestMessage.value=''
    userId.value=result.user.id
    locked.value=false
    password.value=''
    form.value=undefined
    beginOpeningScope()
    await refresh()
    signal()
    void sync()
    void checkFamilyAndInvites()
  } catch (error) { loginError.value=error instanceof ApiError ? error.status===401 ? '账号或密码不正确' : '登录未完成，请检查服务端连接' : error instanceof TypeError ? '无法连接服务端，请联网后重试' : error instanceof Error ? error.message : '登录失败，请重试' }
  finally { loggingIn.value=false }
}
async function register():Promise<void>{
 if(!supported||loggingIn.value)return
 const name=username.value.trim(),display=displayName.value.trim()
 if(!/^[A-Za-z0-9._-]{3,40}$/.test(name)){loginError.value='账号名需为3～40位字母、数字或 ._-';return}
 if(!display||[...display].length>40){loginError.value='显示名需为1～40个字';return}
 if([...password.value].length<8||[...password.value].length>128){loginError.value='密码需为8～128个字符';return}
 loggingIn.value=true;loginError.value=''
 try{
  const active=await store.active(),previous=active?await store.read(active):undefined
  if(previous&&activePendingCount(previous))throw new Error('当前账号还有待同步内容，请先处理后再注册新账号')
  const result=await api.register(name,display,password.value)
  await store.activate(result.user,name)
  sessionGeneration++;generation++;userId.value=result.user.id;locked.value=false;registering.value=false;password.value='';displayName.value='';form.value=undefined;detailId.value=undefined;history.value=undefined
  beginOpeningScope();await refresh();signal();void sync()
  void checkFamilyAndInvites()
 }catch(error){loginError.value=error instanceof ApiError?error.status===409?'账号名已被使用，请换一个':error.status===422?'注册信息未被接受，请核对后重试':'注册未完成，请检查网络和服务端':error instanceof Error?error.message:'注册未完成，请重试'}
 finally{loggingIn.value=false}
}
async function familyChanged(context:AccountContext,source:{user_id:string;restore_id:string}):Promise<void>{
 const id=userId.value;if(!id||source.user_id!==id)return
 try{await store.reconcile(id,context,source.restore_id);await refresh();signal();message.value='家庭设置已更新，当前范围保持不变'}catch(error){message.value=error instanceof Error?error.message:'家庭状态待刷新'}
 void sync();void checkFamilyAndInvites()
}
const familyMetadataChanged=familyChanged

async function checkFamilyAndInvites():Promise<void>{
 if(inviteCheckRunning){inviteCheckAgain=true;return}
 const id=userId.value,originGeneration=generation,restoreId=account.value?.restore_id??''
 if(!id||!account.value||locked.value||loggingOut.value||contextChanging.value||!account.value.restore_id||!navigator.onLine||document.visibilityState!=='visible')return
 inviteCheckRunning=true;invitesLoading.value=true
 const valid=()=>id===userId.value&&originGeneration===generation&&restoreId===(account.value?.restore_id??'')&&!locked.value&&!loggingOut.value
 try{
  const [familyResult,inviteResult]=await Promise.allSettled([api.context(id),api.invites(accountSource(account.value!))])
  if(!valid())return
  if(familyResult.status==='fulfilled'){
   await store.reconcile(id,familyResult.value,restoreId);await refresh()
  }else if(familyResult.reason instanceof ApiError){
   if(familyResult.reason.code==='family_changed'||familyResult.reason.code==='restore_changed')void sync()
   else if(familyResult.reason.status===401||familyResult.reason.code==='account_changed')locked.value=true
  }
  if(!valid())return
  if(inviteResult.status==='fulfilled'){
   const next=inviteResult.value
   if(next.restore_id!==account.value?.restore_id||next.context_version<(account.value?.context?.context_version??0))return
   const newlyPending=next.received.filter(item=>item.status==='pending'&&!seenInviteIds.has(item.id))
   for(const item of next.received)if(item.status==='pending')seenInviteIds.add(item.id)
   receivedInvites.value=next.received;sentInvites.value=next.sent
   if(newlyPending.length&&!familyOpen.value)inviteNotice.value=newlyPending.length===1?`收到来自「${newlyPending[0].family_name}」的家庭邀请`:`收到 ${newlyPending.length} 项新家庭邀请`
   if(!next.received.some(item=>item.status==='pending'))inviteNotice.value=''
  }else if(inviteResult.reason instanceof ApiError){
   if(inviteResult.reason.code==='family_changed'||inviteResult.reason.code==='restore_changed')void sync()
   else if(inviteResult.reason.status===401||inviteResult.reason.code==='account_changed')locked.value=true
  }
 }finally{
  inviteCheckRunning=false;invitesLoading.value=false
  if(inviteCheckAgain){inviteCheckAgain=false;void checkFamilyAndInvites()}
 }
}
async function discardFrozen(){
 const id=userId.value,count=frozenPending.value;if(!id||!count)return
 if(!confirm(`放弃 ${count} 项旧家庭共同待办和图片本机副本？放弃后无法从本机恢复。`))return
 try{await store.discardFrozen(id);await refresh();message.value='已放弃冻结的旧共同待办';signal()}catch(error){message.value=error instanceof Error?error.message:'放弃未完成，请重试'}
}
function openForm(scope:Scope, expense?:Expense) {
  if(resumeParkedFinancialInput())return
  formError.value=''
  if(serverUnsupported.value||!currentSource()){message.value=serverUnsupported.value?'服务端需升级，本机内容保留，升级后再保存。':'正在核对使用范围，请稍后再记账。';return}
  if(scope==='shared'&&!account.value?.family){openFamily();return}
  form.value={restore_id:account.value?.restore_id??'',family_context:familyContext(),scope:expense?.scope ?? scope,id:expense?.id,version:expense?.version,original:expense ? {...expense} : undefined,amount:expense ? moneyInput(expense.amount_cents) : '',purpose:expense?.purpose ?? '',time:localTime(expense?.occurred_at ?? new Date().toISOString()),location:expense?.location ?? '',category:expense?.category??'',...ratingDraft(expense?.rating,expense?.rating_reason),added:[],removed:[]}
  categoryEditor.value=false;newCategory.value=''
}
async function save(): Promise<void> {
  if (!form.value || !userId.value || saving.value || preparingPhotos.value || addingCategory.value) return
  formError.value=''
  const draft = form.value
  let data
  try {
    const purpose=draft.purpose.trim()
    if (!purpose || purpose.length>500) throw new Error('请填写详细用途（1～500字）')
    if (draft.location.length>200) throw new Error('地点最多200字')
    if (!draft.time || Number.isNaN(new Date(draft.time).getTime())) throw new Error('请选择有效的消费时间')
    const rating=Number(draft.rating||3),rating_reason=draft.rating_reason.trim()
    if([...rating_reason].length>500)throw new Error('评价原因最多500字')
    data={amount_cents:parseMoney(draft.amount),purpose,occurred_at:new Date(draft.time).toISOString(),location:draft.location.trim(),category:draft.category.trim(),rating,rating_reason}
  } catch (error) { formError.value=error instanceof Error ? error.message : '请检查输入'; return }
  const id=userId.value
  if(draft.scope==='shared'&&!account.value?.family){formError.value='请先创建或加入家庭，再保存共同账';return}
  if(draft.restore_id!==(account.value?.restore_id??'')){
    if(draft.id){formError.value='服务端已恢复，旧编辑表单不能直接提交。请保留所需输入，关闭后对照服务器记录重新打开。';return}
    if(!confirm('服务端已恢复，请先核对新账本，确认这是一笔尚未存在的新支出后再保存。'))return
    draft.restore_id=account.value?.restore_id??''
  }
  saving.value=true
  try {
    await store.save(id,data,draft.scope,draft.id,draft.version,draft.original,{add:draft.added.map(i=>({id:i.id,blob:i.blob})),remove:draft.removed},draft.restore_id,draft.family_context)
    await refresh()
    if (userId.value!==id) return
    message.value=draft.added.length||draft.removed.length?'账目和图片已保存到手机，待同步':'已保存到手机，待同步'
    form.value=undefined
    signal()
    void sync()
  } catch (error) { formError.value=`未保存：${error instanceof Error ? error.message : '本机存储失败或记录状态已变化，输入已保留，请重试'}` }
  finally { saving.value=false }
}
function pendingCategoryScope(item:Pending):Scope{return item.operation.kind==='create'?item.operation.data.scope:account.value?.snapshot.find(e=>e.id===item.operation.expense_id)?.scope??account.value?.local.find(e=>e.id===item.operation.expense_id)?.scope??tab.value}
function pendingCategoryName(item:Pending):string{return item.operation.kind==='create'||item.operation.kind==='update'?item.operation.data.category??'':''}
function repairCategories(item:Pending):string[]{return(account.value?.categories??[]).filter(c=>c.scope===pendingCategoryScope(item)).map(c=>c.name)}
async function repairPendingCategory(item:Pending,mode:'change'|'restore'|'retry'|'legacy'){
 const id=userId.value,epoch=generation,initial=account.value
 if(!id||!initial||categoryRecoveryBusy.value)return
 if(!navigator.onLine){message.value='重试前需联网核对当前分类与账本，草稿仍保留';return}
 const original=initial.local.find(e=>e.id===item.operation.expense_id),expected=categoryContext(initial),context=familyContext(),restoreId=initial.restore_id??''
 if(!original||original.owner_id!==id)return
 const source=item.operation.kind==='create'||item.operation.kind==='update'?item.operation.data.category??'':''
 const target=mode==='change'?categoryRepairTargets.value[item.operation.operation_id]??'':undefined
 const confirmation=mode==='legacy'?'按原内容和原编号重试这笔旧版新建待办？不改草稿，服务端会核对原请求及当前权限；若分类已删除，随后可明确修正分类。':mode==='restore'?`明确重新新增「${source}」同名分类，并按原内容与原编号重试？草稿字段及后续待办保持不变。`:mode==='retry'?'当前目录已有原分类，按原内容与原编号重试？其他草稿字段与后续待办保持不变。':`将此待办链中的「${source}」改为「${target||'未分类'}」后重试？金额、用途、时间、其他分类及后续操作和图片全部保留。`
 if(!confirm(confirmation))return
 categoryRecoveryBusy.value=true
 try{
  await store.lock(`${store.name}:sync:${id}`,async()=>{
   const valid=async()=>{
    const latest=await readCurrent(id),live=latest.queue.find(p=>p.operation.operation_id===item.operation.operation_id),local=latest.local.find(e=>e.id===item.operation.expense_id)
    if(epoch!==generation||categoryContext(latest)!==expected||!live||live.frozen||live.restore_review||!live.error||live.conflict||!local||local.owner_id!==id||!sameExpense(local,original))throw new Error('账本或草稿已变化，请刷新后重新核对；原输入仍保留')
    return latest
   }
   await valid();await requestSnapshot(id,epoch);await valid()
   if(mode==='restore'){
    await api.addCategory(id,{scope:pendingCategoryScope(item),name:source,restore_deleted:true},restoreId,context)
    await requestSnapshot(id,epoch);await valid()
   }
   if(mode==='legacy')await store.retryLegacyCreate(id,item.operation.operation_id,original,restoreId,context)
   else await store.retryDeletedCategory(id,item.operation.operation_id,original,target,restoreId,context)
  })
  if(epoch===generation){await refresh();signal();message.value='草稿与后续待办已保留，正在按确认内容重试';void sync()}
 }catch(error){if(epoch===generation){message.value=onlineError(error);await refresh()}}
 finally{categoryRecoveryBusy.value=false}
}
async function resolve(expenseId:string, keepLocal:boolean) {
  if (!userId.value) return
  if (keepLocal && !confirm('按原操作逐项重建并提交本机变更？请先核对服务端与草稿的退款、评价、归属和作废状态。')) return
  if (!keepLocal && !confirm('放弃这笔记录的全部本地变更？已同步到服务端的记录会保留。')) return
  try { await store.resolve(userId.value,expenseId,keepLocal,account.value?.restore_id??'',businessSource()); await refresh(); signal(); void sync() }
  catch (error) { message.value=error instanceof Error ? error.message : '处理失败，本机草稿仍在' }
}
async function logout() {
  if (!userId.value||loggingOut.value) return
  logoutError.value=''
  if (hasPending.value) { logoutError.value=message.value='还有待同步记录，请先同步或解决冲突。不能退出账号'; return }
  if(syncWrites.value||apiWrites.value){logoutError.value=message.value='正在提交当前账号的内容，请完成后再退出';return}
  if(!mayDiscardFinancialInputs('logout'))return
  const id=userId.value,restoreId=account.value?.restore_id??'',hadFrozen=!!frozenPending.value,epoch=generation
  loggingOut.value=true
  // No active local pending or in-flight write remains. Stop receiving this run's
  // background reads and release its lock waits without waiting on the network.
  syncSource?.controller.abort();syncAgain=false
  try {
    await store.lock(`${store.name}:identity`,async () => {
      // 再检查一次，防止另一页面刚保存了待办。
      const latest=await readCurrent(id)
      if((latest.restore_id??'')!==restoreId)throw new RestoreChanged()
      if (activePendingCount(latest)) throw new Error('还有待同步记录，请先处理')
      if(syncWrites.value||apiWrites.value)throw new Error('正在提交当前账号的内容，请完成后再退出')
      await api.logout(id,restoreId)
      await store.deactivate(id)
    })
    sessionGeneration++;generation++
    detailId.value=undefined;history.value=undefined;refundForm.value=undefined;requestForm.value=undefined;requestBoard.value=false;reviewOpen.value=false;decisions.value={};requestMessage.value=''
    moduleSyncIssues.value=[];planPartition.value=undefined;planDraftDirty.value={};planDrafts.value={};planResult.value=undefined;memoPartition.value=undefined;memoDrafts.value={};memoResult.value=undefined
    pendingNotification=undefined;notificationWaiting.value=false
    userId.value=undefined
    account.value=undefined
    form.value=undefined;newCategory.value='';categoryPanel.value=undefined
    previewId.value=undefined
    locked.value=false
    username.value=''
    message.value=hadFrozen?'已退出；冻结的旧共同待办仍保存在本机':'已退出，已清除本机账号账本缓存'
    signal()
  } catch (error) { if (error instanceof ApiError && (error.status===401||error.code==='account_changed')) locked.value=true; message.value=error instanceof ApiError || error instanceof TypeError ? '退出未完成，请联网并恢复当前账号后重试' : error instanceof Error ? error.message : '退出失败';if(userId.value===id&&generation===epoch&&!locked.value)logoutError.value=message.value }
  finally{loggingOut.value=false;if(userId.value&&!locked.value)void sync()}
}
async function openDetail(expense:Expense){
  if(resumeParkedFinancialInput())return
  detailId.value=expense.id;detailError.value='';history.value=undefined;historyDependencies=[];transferFamilyId.value=''
  if(navigator.onLine)await sync()
}
async function maintain(action:Maintenance){
  const expected=detail.value,id=userId.value
  if(!expected||!id||maintenanceBusy.value)return
  if(action.kind==='scope' && !confirm(`改为${action.data.scope==='private'?'私人账？同步后对方将无法再读取这笔记录、图片和旧申请；已看过的内容无法撤回。':'共同账？同步后家庭成员可见当前内容，私人修改历史不会公开。'} 离线时，对方权限需同步成功后才会改变。`))return
  if(action.kind==='void'&&!confirm('作废整笔支出？将退出正常列表和统计，保留本人历史且不能恢复。同步成功后对方权限才会撤回。'))return
  if(action.kind==='refund_void'&&!confirm('作废这笔退款？原付款与其余有效退款会重新计算。'))return
  maintenanceBusy.value=true;detailError.value=''
  try{let context=familyContext();if(action.kind==='scope'&&action.data.scope==='shared'){if(!transferFamilyId.value)throw new Error('请为这笔记录明确选择目标家庭');const source=sourceFor(account.value!,{scope:'shared',family_id:transferFamilyId.value});context={family:account.value?.context?.families.find(f=>f.id===source.family_id)??null,membership_version:source.relation_epoch,source}}await store.maintain(id,expected,action,account.value?.restore_id??'',context);await refresh();history.value=undefined;message.value='已保存到手机，待同步';signal();void sync()}
  catch(error){detailError.value=error instanceof Error?error.message:'未保存，本机草稿仍在'}
  finally{maintenanceBusy.value=false}
}
function openRefund(refundId?:string){
  const current=detail.value;if(!current)return
  const old=current.refunds?.find(r=>r.id===refundId)
  refundForm.value={restore_id:account.value?.restore_id??'',family_context:familyContext(),original:JSON.parse(JSON.stringify(current)),refund_id:refundId??crypto.randomUUID(),editing:!!old,amount:old?moneyInput(old.amount_cents):'',time:localTime(old?.occurred_at??new Date().toISOString()),reason:old?.reason??'',request:false};detailError.value=''
}
async function saveRefund(){
  const draft=refundForm.value,id=userId.value;if(!draft||!id||maintenanceBusy.value)return
  detailError.value='';maintenanceBusy.value=true
  try{
    const action:Maintenance={kind:draft.editing?'refund_update':'refund_create',data:{refund_id:draft.refund_id,amount_cents:parseMoney(draft.amount),occurred_at:new Date(draft.time).toISOString(),reason:draft.reason.trim()}}
    await store.maintain(id,draft.original,action,draft.restore_id,draft.family_context);await refresh();refundForm.value=undefined;history.value=undefined;message.value='退款已保存到手机，待同步';signal();void sync()
  }catch(error){detailError.value=error instanceof Error?error.message:'未保存，请核对输入'}finally{maintenanceBusy.value=false}
}
async function loadHistory(){
  const current=detail.value,id=userId.value,epoch=generation;if(!current||!id||!navigator.onLine){detailError.value='历史需联网读取';return}
  historyLoading.value=true;detailError.value=''
  try{const source=businessSource(),result=await api.history(id,current.id,source);await store.verifySource(id,source);const meta=await store.read(id);if(result.source_dependencies.some(d=>!meta.context?.families.some(f=>f.id===d.family_id&&f.relation_epoch===d.relation_epoch)))return;await refresh();if(epoch===generation&&!locked.value&&detail.value?.id===current.id&&detail.value.owner_id===id&&detail.value.version===current.version) {history.value=result.revisions;historyDependencies=result.source_dependencies}}
  catch{if(epoch===generation)detailError.value='历史暂时无法读取，请联网后重试'}finally{if(epoch===generation)historyLoading.value=false}
}
function openRequest(){
  const current=detail.value;if(!current)return
  requestMessage.value='';requestForm.value={original:JSON.parse(JSON.stringify(current)),id:crypto.randomUUID(),kind:'update',reason:'',amount:moneyInput(current.amount_cents),purpose:current.purpose,time:localTime(current.occurred_at),location:current.location,category:current.category??'',refund_id:'',refund_amount:'',refund_time:localTime(new Date().toISOString()),refund_reason:''}
}
function proposal():Proposal{
  const draft=requestForm.value;if(!draft)throw new Error('申请表单已关闭')
  if(draft.kind==='void')return{kind:'void',data:{}}
  if(draft.kind==='update')return{kind:'update',data:{amount_cents:parseMoney(draft.amount),purpose:draft.purpose.trim(),occurred_at:new Date(draft.time).toISOString(),location:draft.location.trim(),category:draft.category}}
  if(draft.kind==='refund_void'){if(!draft.refund_id)throw new Error('请选择退款');return{kind:'refund_void',data:{refund_id:draft.refund_id}}}
  if(draft.kind==='refund_update'&&!draft.refund_id)throw new Error('请选择退款')
  return{kind:draft.kind,data:{refund_id:draft.refund_id||(draft.refund_id=crypto.randomUUID()),amount_cents:parseMoney(draft.refund_amount),occurred_at:new Date(draft.refund_time).toISOString(),reason:draft.refund_reason.trim()}}
}
function chooseRequestRefund(){const draft=requestForm.value;if(!draft)return;const old=draft.original.refunds?.find(r=>r.id===draft.refund_id);if(old){draft.refund_amount=moneyInput(old.amount_cents);draft.refund_time=localTime(old.occurred_at);draft.refund_reason=old.reason}}
async function requestSnapshot(id:string,epoch:number){
  const source=businessSource(),restoreId=source.restore_id
  await store.verifySource(id,source)
  const result=await api.snapshot(id,source)
  if(epoch!==generation||userId.value!==id||locked.value)throw new AccountChanged()
  await store.snapshot(id,result,restoreId);await refresh();signal()
}
async function submitRequest(){
  const draft=requestForm.value,id=userId.value,epoch=generation,restoreId=account.value?.restore_id??'';if(!draft||!id||requestBusy.value)return
  requestMessage.value=''
  if(draft.submitted && requestWithdrawn(account.value,draft.submitted.id)){requestForm.value=undefined;requestMessage.value='申请已撤回，旧内容已清除';return}
  if(!navigator.onLine){requestMessage.value='更正申请需要联网，输入已保留';return}
  requestBusy.value=true
  try{
    const action=draft.submitted?{kind:draft.submitted.kind,data:draft.submitted.data} as Proposal:proposal(),reason=draft.submitted?.reason??draft.reason.trim();if([...reason].length>500)throw new Error('申请原因最多500字')
    applyOperation(draft.original,{...action,operation_id:draft.id,expense_id:draft.original.id,base_version:draft.original.version} as Operation,id)
    const input:RequestInput=draft.submitted??{id:draft.id,expense_id:draft.original.id,base_version:draft.original.version,...action,reason}
    draft.submitted=input
    await store.lock(`${store.name}:sync:${id}`,async()=>{if(epoch!==generation||(await readCurrent(id)).restore_id!== (restoreId||undefined))throw new RestoreChanged();await api.requestChange(id,input,restoreId,businessSource());await requestSnapshot(id,epoch)})
    if(epoch!==generation)return
    if(canShowRequestResult(account.value,draft.id)){if(requestForm.value===draft)requestForm.value=undefined;requestBoard.value=true;requestMessage.value='申请已提交，原账尚未改变'}else{if(requestForm.value===draft)requestForm.value=undefined;requestMessage.value='记录或申请权限已变化，内容已撤回';message.value=requestMessage.value}
  }catch(error){if(epoch===generation){await refresh();requestMessage.value=requestWithdrawn(account.value,draft.id)?'申请已撤回，旧内容已清除':onlineError(error)}}
  finally{if(epoch===generation)requestBusy.value=false}
}
async function decide(item:ChangeRequest,decision:'accept'|'reject',rejectionReason=''){
  const id=userId.value,epoch=generation,restoreId=account.value?.restore_id??'';if(!id||requestBusy.value)return
  if(!navigator.onLine){requestMessage.value='审批需要联网，尚未提交';return}
  const previous=decisions.value[item.id]
  if(previous && previous.decision!==decision){requestMessage.value='上次决定结果未知，请先重试原决定或刷新确认';return}
  const attempt=freezeDecision(previous,decision,rejectionReason);decisions.value[item.id]=attempt
  requestBusy.value=true;requestMessage.value=''
  try{await store.lock(`${store.name}:sync:${id}`,async()=>{if(epoch!==generation||(await readCurrent(id)).restore_id!==(restoreId||undefined))throw new RestoreChanged();await api.decide(id,item.id,attempt.id,attempt.decision,restoreId,attempt.rejection_reason,businessSource());await requestSnapshot(id,epoch)});if(epoch===generation){requestMessage.value=canShowRequestResult(account.value,item.id)?'决定已处理，账本已刷新':'权限已变化，申请内容已撤回';delete decisions.value[item.id]}}
  catch(error){if(epoch===generation){requestMessage.value=onlineError(error);await refresh()}}
  finally{if(epoch===generation)requestBusy.value=false}
}
async function decideCategory(change:CategoryChange,decision:'accept'|'reject',rejectionReason=''){
 const id=userId.value,epoch=generation,restoreId=account.value?.restore_id??'',expected=account.value?categoryContext(account.value):'',changeId=change.id
 if(!id||!changeId||requestBusy.value)return
 if(!navigator.onLine){requestMessage.value='审批需要联网，尚未提交';return}
 const previous=categoryDecisions.value[changeId]
 if(previous&&previous.decision!==decision){requestMessage.value='上次决定结果未知，请先重试原决定或刷新确认';return}
 const attempt=freezeDecision(previous,decision,rejectionReason);categoryDecisions.value[changeId]=attempt
 requestBusy.value=true;requestMessage.value=''
 try{
  await store.lock(`${store.name}:sync:${id}`,async()=>{
   const latest=await readCurrent(id)
   if(epoch!==generation||categoryContext(latest)!==expected)throw new AccountChanged()
   const live=latest.category_changes?.find(c=>c.id===changeId)
   if(!live||live.status!=='pending'||!live.approvals.some(a=>a.owner_id===id&&a.status==='pending'))throw new Error('申请或审批权限已变化，请刷新更正待办')
   await api.decideCategories(id,changeId,attempt.id,attempt.decision,restoreId,{family:latest.family??null,membership_version:latest.membership_version??0,source:latest.source},attempt.rejection_reason)
   await requestSnapshot(id,epoch)
  })
  if(epoch===generation){const result=account.value?.category_changes?.find(c=>c.id===changeId);requestMessage.value=result?categoryStatus(result.status):'申请已不可访问，内容已撤回';delete categoryDecisions.value[changeId]}
 }catch(error){if(epoch===generation){requestMessage.value=onlineError(error);await refresh();void sync()}}
 finally{if(epoch===generation)requestBusy.value=false}
}
async function refreshRequestBoard(){
 const id=userId.value,epoch=generation,expected=requestContext.value
 if(!id||requestBusy.value||syncing.value)return
 if(!navigator.onLine){requestMessage.value='刷新需要联网，原待办提示仍保留';return}
 requestBusy.value=true;requestMessage.value=''
 try{
  await store.lock(`${store.name}:sync:${id}`,async()=>{
   if(epoch!==generation||categoryContext(await readCurrent(id))!==expected)throw new AccountChanged()
   await requestSnapshot(id,epoch)
  })
  if(epoch!==generation||requestContext.value!==expected)return
  const ids=[...requestList.value.filter(r=>r.status==='rejected'&&r.requester_id===id).map(r=>`single:${r.id}`),...(account.value?.category_changes??[]).filter(c=>c.status==='rejected'&&c.requester_id===id).map(c=>`category:${c.id}`)]
  const saved=acknowledgeRequests(ids)
  requestMessage.value=saved?'待办已刷新，已处理项已收起':'待办已刷新；本页已收起，浏览器未能保存此确认'
 }catch(error){if(epoch===generation)requestMessage.value=onlineError(error)}
 finally{if(epoch===generation)requestBusy.value=false}
}
function onlineError(error:unknown){if(error instanceof RestoreChanged)return error.message;if(error instanceof ApiError){if(error.code==='restore_changed'){void sync();return '服务端已恢复，正在获取新快照；本机草稿仍保留'}if(['version_conflict','request_stale'].includes(error.code))return '原账已变化，需要重新确认；原输入已保留，请同步后重新提出申请';if(error.code==='request_decided')return '申请已处理，请同步核对结果';if(error.status===404)return '记录或申请已不可访问，请同步刷新';if(error.status===401||error.code==='account_changed'){locked.value=true;return '登录状态已变化，请恢复原账号'}return '内容未被接受，输入已保留，请核对后重试'}return error instanceof TypeError?'网络未确认结果，输入及原请求编号已保留，请重试':error instanceof Error?error.message:'请求未完成，请重试'}
function statusName(status:ChangeRequest['status']){return {pending:'待处理',accepted:'已接受',rejected:'已拒绝',stale:'需要重新确认'}[status]}
function actionName(kind:Proposal['kind']){return {update:'修改支出',refund_create:'新增退款',refund_update:'更正退款',refund_void:'作废退款',void:'作废支出'}[kind]}
function requestAfter(item:ChangeRequest){try{return applyOperation(item.before,{kind:item.kind,data:item.data,operation_id:item.id,expense_id:item.expense_id,base_version:item.base_version} as Operation,item.owner_id)}catch{return undefined}}
function foreground() { if (document.visibilityState==='visible') { void refresh(); void sync(); void checkFamilyAndInvites() } }
function online() { networkOnline.value=true;void sync(); void checkFamilyAndInvites() }
function offline(){networkOnline.value=false}
function remoteChange() { void refresh() }
const shellBlocked=computed(()=>syncOpen.value||bookPicker.value||inboxOpen.value||route.value==='/accounting'&&!financialNavigationPaused.value&&!!(form.value||detail.value||refundForm.value||requestForm.value||requestBoard.value||reviewOpen.value||categoryPanel.value||previewId.value))
function visibleDialog(){return [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].filter(e=>e.getClientRects().length>0).at(-1)}
let activeDialog:HTMLElement|undefined
const dialogReturnFocus=new WeakMap<HTMLElement,HTMLElement>()
function focusDialog(){
  const panel=visibleDialog()
  if(panel===activeDialog)return
  if(!panel){
    const trigger=activeDialog&&dialogReturnFocus.get(activeDialog)
    activeDialog=undefined
    if(trigger?.isConnected&&trigger.getClientRects().length)trigger.focus()
    return
  }
  const previous=document.activeElement
  if(previous instanceof HTMLElement&&!panel.contains(previous))dialogReturnFocus.set(panel,previous)
  activeDialog=panel
  if(panel){const target=panel.querySelector<HTMLElement>('.expense-entry-form input:not([type="file"]):not(:disabled)')??panel.querySelector<HTMLElement>('button:not(:disabled),a[href]');target?.focus()}
}
function shellModalKeys(event:KeyboardEvent){
  const panel=visibleDialog()
  if(!panel)return
  if(event.key==='Escape'){
    if(syncOpen.value){event.preventDefault();closeSyncStatus()}
    else if(inboxOpen.value){event.preventDefault();inboxOpen.value=false;inboxTrigger.value?.focus()}
    else if(bookPicker.value){event.preventDefault();bookPicker.value=false;scopeTrigger.value?.focus()}
    else {
      const close=panel.querySelector<HTMLButtonElement>('button[aria-label^="关闭"]:not(:disabled),button[aria-label^="取消"]:not(:disabled)')
      if(close){event.preventDefault();close.click()}
    }
  }else if(event.key==='Tab'){
    const controls=[...panel.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled):not([type="file"]),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(e=>e.getClientRects().length>0)
    const first=controls[0],last=controls.at(-1)
    if(event.shiftKey&&(document.activeElement===first||!panel.contains(document.activeElement))){event.preventDefault();last?.focus()}
    else if(!event.shiftKey&&(document.activeElement===last||!panel.contains(document.activeElement))){event.preventDefault();first?.focus()}
  }
}
watch([form,detailId,refundForm,requestForm,requestBoard,reviewOpen,categoryPanel,previewId,bookPicker,inboxOpen,syncOpen,route,financialNavigationPaused],()=>{void nextTick(focusDialog)})
watch([form,refundForm,requestForm,reviewOpen,categoryPanel,requestBoard],()=>{if(!form.value&&!refundForm.value&&!requestForm.value&&!reviewOpen.value&&!categoryPanel.value&&!requestBoard.value)financialNavigationPaused.value=false},{flush:'sync'})
function cacheError() { localWarning.value='离线页面准备失败；本机账本仍保留，请联网重开以重试' }
onMounted(async () => {
  try {
    userId.value=await store.active()
    if (userId.value) { beginOpeningScope();await refresh(); username.value=account.value?.username ?? '' }
    if (navigator.storage?.persist) await navigator.storage.persist()
    localWarning.value=store.status||localWarning.value
  } catch { localWarning.value='本机存储无法打开，请检查浏览器设置。请勿清除已有网站数据' }
  finally { ready.value=true }
  if (supported) { void sync(); void checkFamilyAndInvites() }
})
const inviteTimer=window.setInterval(()=>{void checkFamilyAndInvites()},10000)
window.addEventListener('keydown',shellModalKeys)
window.addEventListener('pointerdown',dismissFinancialMenu)
window.addEventListener('hashchange',hashChanged)
window.addEventListener('online',online)
window.addEventListener('offline',offline)
navigator.serviceWorker?.addEventListener('message',notificationMessage)
window.addEventListener('offline-cache-error',cacheError)
document.addEventListener('visibilitychange',foreground)
channel?.addEventListener('message',remoteChange)
onUnmounted(() => { window.removeEventListener('keydown',shellModalKeys);window.removeEventListener('pointerdown',dismissFinancialMenu);window.removeEventListener('hashchange',hashChanged);window.clearInterval(inviteTimer);window.removeEventListener('online',online);window.removeEventListener('offline',offline);navigator.serviceWorker?.removeEventListener('message',notificationMessage); window.removeEventListener('offline-cache-error',cacheError); document.removeEventListener('visibilitychange',foreground); channel?.close();imageUrlRevision++;imageUrlCache.clear();form.value?.added.forEach(i=>URL.revokeObjectURL(i.url)) })
</script>

<template>
  <main :inert="loggingOut" class="app-shell" :class="{private:tab==='private'}">
    <header class="life-header" :inert="shellBlocked" :class="{'life-header-auth':!account || locked}">
      <div class="life-brand-group"><a class="life-brand" href="#/home" aria-label="同在首页"><img class="life-brand-mark" src="/icon.svg" alt="" aria-hidden="true"><span class="life-brand-text"><span>同在</span><small class="life-version">v{{ appVersion }}</small></span></a><button v-if="account && !locked && syncNeedsAttention" ref="syncTrigger" class="life-sync" :class="{'is-syncing':syncing,'has-issue':!!syncProblem || syncSummary.issues.length>0 || syncSummary.frozen>0}" :aria-label="`同步状态：${syncStatus}，查看详情`" aria-haspopup="dialog" :aria-expanded="syncOpen" @click="syncOpen=true"><Icon :name="syncing?'sync':!networkOnline?'offline':syncProblem || syncSummary.issues.length || syncSummary.frozen?'warning':'sync'"/></button></div>
      <template v-if="account && !locked && !contextChanging">
        <button ref="scopeTrigger" class="life-context" @click="bookPicker=true" :aria-label="`当前使用范围：${scopeName}，切换范围`"><span><strong>{{ scopeName }}</strong><small>{{ tab==='shared'?'家庭模式':'私人模式' }}</small></span><Icon name="chevron"/></button>
        <button ref="inboxTrigger" class="life-bell" @click="inboxOpen=true" :aria-label="`消息提醒，${homeMessageCount}项待处理`"><Icon name="bell"/><span v-if="homeMessageCount" class="life-badge">{{ homeMessageCount>99?'99+':homeMessageCount }}</span></button>
        <button ref="profileTrigger" class="life-avatar" @click="navigate('/settings')" :aria-label="`${account.user.display_name}的账号与设置`"><span>{{ account.user.display_name.slice(0,1) }}</span></button>
      </template>
    </header>
    <p v-if="!supported" class="notice error" role="alert">当前浏览器暂不支持离线记账，请换用新版 Safari、Chrome 或 Edge。</p>
    <p v-if="message && !['已同步到服务端','正在同步'].includes(message)" class="notice" role="status" aria-live="polite">{{ message }}<button v-if="notificationWaiting" type="button" class="text-button" @click="processNotification">打开待处理通知</button></p>
    <div v-if="inviteNotice && account && !locked" class="invite-notice notice" role="status"><span>{{ inviteNotice }}</span><button class="text-button" @click="openFamily('received')">查看邀请</button><button class="icon-button" aria-label="关闭邀请提示" @click="inviteNotice=''"><Icon name="close"/></button></div>
    <p v-if="!ready" class="empty">正在打开本机账本…</p>
    <section v-else-if="!account || locked" class="login-card card">
      <p class="eyebrow">每一笔，都记清楚</p><h1>{{ locked ? '恢复账号' : registering?'创建账号':'欢迎来到同在' }}</h1>
      <p class="muted">{{ locked && hasPending ? '待同步内容已保留，请用原账号登录后继续。' : registering?'新账号先拥有独立私人账，之后可创建家庭或接受邀请。':'首次登录需要连接服务端，之后可离线记录支出。' }}</p>
      <form @submit.prevent="registering?register():login()" class="stack"><label>账号名<input v-model="username" name="username" autocomplete="username" :readonly="locked && hasPending" required></label><label v-if="registering">显示名<input v-model="displayName" name="display-name" autocomplete="name" maxlength="40" required></label><label>密码<input v-model="password" name="password" type="password" :autocomplete="registering?'new-password':'current-password'" required></label><p v-if="loginError" class="field-error" role="alert">{{ loginError }}</p><button class="primary" :disabled="loggingIn || !supported">{{ loggingIn ? registering?'正在注册…':'正在登录…' : registering?'创建账号':'登录' }}</button></form><button v-if="!locked" class="text-button auth-switch" @click="registering=!registering;loginError='';password=''">{{ registering?'已有账号？去登录':'没有账号？创建账号' }}</button>
    </section>
    <section v-else-if="contextChanging" class="empty">正在更新家庭状态…</section>
    <div v-else :inert="shellBlocked">
      <section v-show="route==='/home'" class="life-home" aria-label="首页">
        <h1 class="sr-only" tabindex="-1">生活工具</h1><p class="life-eyebrow" tabindex="-1">生活工具</p>
        <nav class="life-modules" aria-label="生活工具">
          <a href="#/accounting" class="life-module"><span class="life-module-icon"><Icon name="wallet"/></span><strong>共同记账</strong><small>支出与复盘</small></a>
          <a href="#/memos" class="life-module"><span class="life-module-icon"><Icon name="calendar"/></span><strong>生活备忘</strong><small>记录与日程</small></a>
          <a href="#/plans" class="life-module"><span class="life-module-icon"><Icon name="heart"/></span><strong>愿望与计划</strong><small>想法与目标</small></a>
        </nav>
      </section>
      <section v-show="route==='/settings'" class="life-settings" aria-label="账号与设置">
        <div class="page-heading"><h1 tabindex="-1">账号与设置</h1></div>
        <p class="settings-identity">{{ account.user.display_name }} · {{ account.username }}</p>
        <nav class="settings-menu" aria-label="配置项目">
          <a href="#/settings/account"><Icon name="user"/><span><strong>账号与密码</strong><small>个人资料与登录安全</small></span><Icon name="next"/></a>
          <a href="#/settings/family" @click.prevent="openFamily(pendingInvites?'received':'sent')"><Icon name="users"/><span><strong>家庭管理</strong><small>{{ account.family?.name || '创建家庭或接受邀请' }}<template v-if="pendingInvites"> · {{ pendingInvites }} 项邀请</template></small></span><Icon name="next"/></a>
          <a href="#/settings/notifications"><Icon name="bell"/><span><strong>通知与设备</strong><small>本设备的通知授权与接收</small></span><Icon name="next"/></a>
        </nav>
        <button class="subtle settings-logout" @click="logout">退出账号</button>
        <p v-if="logoutError" class="field-error" role="alert">{{ logoutError }}</p>
      </section>
      <ModuleSyncIssues v-if="route==='/plans'||route==='/memos'" :issues="currentModuleIssues.filter(issue=>issue.module===(route==='/plans'?'plan':'memo'))" :busy="moduleIssueBusy" @action="moduleIssueAction"/>
      <PlansWorkspace ref="plansWorkspaceRef" v-if="account.source && planSnapshot" v-show="route==='/plans'" :key="`${userId}:${account.restore_id}`" :source="account.source" :scope-label="scopeName" :snapshot="planSnapshot" :initial-draft="planDraft" :writable="supported && !serverUnsupported" :error="planError" :result="planResult" :focused-task-ids="memoCurrent?.local_focus?.targets.filter(t=>t.kind==='plan_task').map(t=>t.id)??[]" @focus="planFocused" @ai-adopted="planAiAdopted" @back="navigate('/home')" @command="planCommand" @draft-change="planDraftChanged" @request-attachment="planAttachmentRequested"><template #markdown="{markdown}"><MemoMarkdown :markdown="markdown"/></template></PlansWorkspace>
      <MemosWorkspace ref="memosWorkspaceRef" v-if="account.source" v-show="route==='/memos'" :key="`${userId}:${account.restore_id}`" :source="account.source" :scope-label="scopeName" :categories="memoCategories" :records="memoViews" :initial-draft="memoDraft" :writable="supported && !serverUnsupported" :error="memoError" :result="memoResult" :members="memoMembers" :deletion-requests="memoCurrent?.local_deletion_requests??[]" :focused-tasks="focusedPlanTasks" :focus-enabled="!!memoCurrent?.local_focus" :calendar-loading="calendarLoading" :calendar-error="calendarError" @calendar-month="memoCalendarMonth" schedule-enabled @focus="memoFocused" @operation="memoOperation" @open-plan-task="openPlanTask" @complete-plan-task="completePlanTask" @reorder-focus="memoReorderFocus" @reorder-categories="memoReorderCategories" @back="navigate('/home')" @save="memoSaved" @complete="memoCompleted" @create-category="memoCategoryCreated" @draft-change="memoDraftChanged" @request-attachment="memoAttachmentRequested"><template #markdown="{markdown}"><MemoMarkdown :markdown="markdown"/></template></MemosWorkspace>
      <AccountPanel v-if="route==='/settings/account'" :key="userId" :account="account" embedded @close="returnFromConfig" @restore-changed="sync" @unauthorized="locked=true"/>
      <FamilyPanel ref="familyPanelRef" :open-sequence="familyOpenSequence" v-show="route==='/settings/family'" :key="userId" :account="account" embedded :received="receivedInvites" :sent="sentInvites" :loading="invitesLoading" :initial-tab="familyInitialTab" @close="returnFromConfig" @changed="familyChanged" @metadata-changed="familyMetadataChanged" @restore-changed="sync" @refresh="checkFamilyAndInvites" @unauthorized="locked=true"/>
      <DeviceNotifications v-if="route==='/settings/notifications'" :key="userId" :account="account" embedded @close="returnFromConfig" @restore-changed="sync" @unauthorized="locked=true"/>
      <section v-show="route==='/accounting'" class="financial-workspace" aria-label="共同记账工作区">
      <div v-if="financialNavigationPaused" class="draft-resume" role="status"><div><strong>有未完成的记账输入</strong><small>仍保留原范围与记录，尚未保存</small></div><button class="text-button" @click="resumeFinancialNavigation">继续编辑</button></div>
      <div class="accounting-heading"><h1 tabindex="-1">共同记账</h1><details ref="accountTools" class="account-tools" @keydown="financialMenuKeys"><summary>更多<Icon name="chevron"/></summary><div class="account-tools-menu"><button @click="closeAccountTools();openCategoryManager()">分类管理</button><button @click="closeAccountTools();openFinancialRequests()">更正待办<span v-if="awaitingApproval" class="tool-pending">{{ awaitingApproval }} 项待我审批</span></button><button @click="closeAccountTools();openFinancialReview()">财务复盘与导出</button><button @click="closeAccountTools();sync()" :disabled="syncing || !supported">{{ syncing?'正在同步…':'立即同步' }}</button></div></details></div>
      <section v-if="frozenPending" class="notice frozen-notice" role="status"><strong>{{ frozenPending }} 项旧共同待办已冻结</strong><p>不会转入新家庭或私人账，也不会自动同步。旧共同账内容已不可查看；仅本机副本保留供你决定放弃。</p><button class="subtle" @click="discardFrozen">放弃冻结待办</button></section>
      <section class="bookline"><div><p class="book-caption">{{ tab==='shared'?'共同账 · 当前成员可见':'私人账 · 仅本人可见' }}</p></div><label class="month-label"><span aria-hidden="true" class="month-display">{{ month ? `${Number(month.slice(0,4))}年${Number(month.slice(5,7))}月` : '选择月份' }}</span><input type="month" v-model="month" aria-label="统计月份"></label></section>
      <section class="summary"><p class="muted">本月{{ tab==='shared'?'共同':'私人' }}净支出</p><p class="total" :class="{'total-long':money(total).length>11}"><span>¥</span>{{ money(total) }}</p><p class="confirmed"><Icon name="check"/>服务端已确认 · {{ official.length }} 笔</p><p v-if="scopePending" class="summary-note">待同步内容另列，不计入以上正式统计。</p></section>
      <section v-if="failures.length || imageFailures.length || categoryFailures.length" class="issues card" aria-label="同步问题"><h2>需要核对的内容 · {{ issueCount }} 项</h2>
        <article v-for="item in visibleFailures" :key="item.operation.operation_id" class="issue"><p class="field-error">{{ item.error }}</p><template v-if="item.error_code==='category_deleted' && (item.operation.kind==='create'||item.operation.kind==='update') && !item.restore_review"><p>{{ account.local.find(e=>e.id===item.operation.expense_id)?.purpose }} · ¥{{ money(account.local.find(e=>e.id===item.operation.expense_id)?.amount_cents??0) }}</p><p class="form-note">草稿、后续退款／评价等操作和图片仍保留。仅替换待办链中相同的已删分类，其他字段不变。</p><label>修正为当前分类<select :value="categoryRepairTargets[item.operation.operation_id]??''" @change="categoryRepairTargets[item.operation.operation_id]=($event.target as HTMLSelectElement).value" :disabled="categoryRecoveryBusy"><option value="">未分类</option><option v-for="name in repairCategories(item)" :key="name">{{ name }}</option></select></label><div class="action-row"><button class="primary" :disabled="categoryRecoveryBusy" @click="repairPendingCategory(item,'change')">修正分类并重试</button><button v-if="repairCategories(item).includes(pendingCategoryName(item))" class="subtle" :disabled="categoryRecoveryBusy" @click="repairPendingCategory(item,'retry')">按原分类重试</button><button v-else class="subtle" :disabled="categoryRecoveryBusy" @click="repairPendingCategory(item,'restore')">重新新增同名分类并重试</button><button class="subtle" :disabled="categoryRecoveryBusy" @click="resolve(item.operation.expense_id,false)">放弃此笔本地变更</button></div></template><template v-else-if="item.conflict"><div class="comparison"><div><span class="muted">服务端当前版本 {{ item.conflict.version }}</span><strong>¥{{ money(item.conflict.amount_cents) }}</strong><p>{{ item.conflict.purpose }}</p><ExpenseFacts :expense="item.conflict"/></div><div><span class="muted">本人保留草稿</span><strong>¥{{ money(account.local.find(e=>e.id===item.operation.expense_id)?.amount_cents??0) }}</strong><p>{{ account.local.find(e=>e.id===item.operation.expense_id)?.purpose }}</p><ExpenseFacts v-if="account.local.find(e=>e.id===item.operation.expense_id)" :expense="account.local.find(e=>e.id===item.operation.expense_id)!"/></div></div><div class="action-row"><button class="subtle" @click="resolve(item.operation.expense_id,false)">采用服务端版本</button><button class="primary" @click="resolve(item.operation.expense_id,true)">核对后提交本地内容</button></div></template><template v-else><p v-if="item.restore_review" class="form-note">恢复后的服务器中没有这笔记录。父不存在的新建可核对后重新创建，其余动作需保留草稿另行核对。</p><p>{{ account.local.find(e=>e.id===item.operation.expense_id)?.purpose }} · ¥{{ money(account.local.find(e=>e.id===item.operation.expense_id)?.amount_cents??0) }}</p><div class="action-row"><button v-if="!item.error_code && !item.restore_review && item.operation.kind==='create'" class="primary" :disabled="categoryRecoveryBusy" @click="repairPendingCategory(item,'legacy')">按原内容重试一次</button><button v-if="item.restore_review && item.operation.kind==='create'" class="primary" @click="resolve(item.operation.expense_id,true)">核对后重新创建</button><button class="subtle" @click="resolve(item.operation.expense_id,false)">放弃此笔本地变更</button></div></template></article>
        <article v-for="image in visibleImageFailures" :key="image.id" class="issue"><p>{{ account.local.find(e=>e.id===image.expense_id)?.purpose||'恢复前附件' }} · 图片{{ image.pending==='delete'?'移除':'上传' }}失败</p><p class="field-error">{{ image.error }}</p><div class="action-row"><button v-if="image.blob" class="subtle" @click="previewId=image.id">查看本机图片</button><button class="subtle" @click="imageAction(image.id)">重试图片</button><button class="subtle" @click="imageAction(image.id,true)">放弃附件变更</button></div></article>
        <article v-for="category in visibleCategoryFailures" :key="category.name" class="issue"><p>{{ category.name }} · {{ category.error }}</p><div class="action-row"><button v-if="category.error?.includes('已删除')" class="subtle" @click="restoreCategory(category)">重新新增同名分类</button><button v-else class="subtle" @click="categoryAction(category)">重试分类</button><button class="subtle" @click="categoryAction(category,true)">放弃分类同步</button></div></article>
        <div v-if="issueCount>issuePageSize" class="pagination" aria-label="同步问题分页"><button class="subtle" @click="issuePage--" :disabled="issuePage===1">上一组问题</button><span>第 {{ issuePage }} / {{ issuePageCount }} 组</span><button class="subtle" @click="issuePage++" :disabled="issuePage===issuePageCount">下一组问题</button></div>
      </section>
      <section class="ledger"><nav class="tabs" role="tablist" aria-label="账本视图"><button role="tab" :aria-selected="view==='stats'" :class="{active:view==='stats'}" @click="view='stats'">统计</button><button role="tab" :aria-selected="view==='list'" :class="{active:view==='list'}" @click="view='list'">明细</button><span>人民币 · CNY</span></nav>
        <template v-if="view==='list'">
          <div class="list-heading"><label class="search-label"><Icon name="search"/><input v-model="keyword" type="search" placeholder="搜索用途" aria-label="查找用途"></label><button class="subtle" aria-controls="expense-record-filters" :aria-expanded="filtersOpen" @click="filtersOpen=!filtersOpen;datePickerOpen=false">筛选<span v-if="categoryFilter || ratingFilter || dateFilter" class="filter-dot" aria-label="已设置筛选"></span><Icon name="chevron"/></button></div><div class="record-list-tools"><button class="text-button" @click="selecting?clearSelection():(selecting=true)" :disabled="recordFilter==='voided'">{{ selecting?'取消选择':'批量改分类' }}</button><button class="text-button voided-records-toggle" :aria-pressed="recordFilter==='voided'" @click="recordFilter=recordFilter==='voided'?'active':'voided'">{{ recordFilter==='voided'?'返回正常支出':'查看我作废的支出' }}</button><button v-if="keyword || categoryFilter || ratingFilter || dateFilter" class="text-button" @click="keyword='';categoryFilter='';ratingFilter='';dateFilter='';datePickerOpen=false">清空筛选</button></div>
          <div v-show="filtersOpen" id="expense-record-filters" class="record-filters">
            <label>分类<select v-model="categoryFilter" aria-label="筛选分类"><option value="">全部分类</option><option v-for="name in categories(tab)" :key="name">{{ name }}</option><option>未分类</option></select></label>
            <label>满意度<select v-model="ratingFilter" aria-label="筛选满意度"><option value="">全部满意度</option><option v-for="(label,n) in ratingLabels" :key="n" :value="String(n+1)">{{ label }}</option></select></label>
            <div class="date-filter" @keydown.esc.prevent="datePickerOpen=false;dateToggle?.focus()"><span class="date-filter-label">支出日期</span><button ref="dateToggle" class="subtle date-filter-toggle" :aria-label="`日期筛选 · ${dateFilter || '全部日期'}`" :aria-expanded="datePickerOpen" aria-controls="record-date-options" @click="toggleDates">{{ dateFilter?`${Number(dateFilter.slice(5,7))}/${Number(dateFilter.slice(8))}`:'全部日期' }}</button></div>
            <section v-if="datePickerOpen" id="record-date-options" class="date-options calendar-panel" aria-label="支出日期月历" @keydown.esc.prevent="datePickerOpen=false;dateToggle?.focus()"><h3>{{ month.replace('-', '年') }}月</h3><div class="calendar-week" aria-hidden="true"><span v-for="day in ['一','二','三','四','五','六','日']" :key="day">{{ day }}</span></div><div class="calendar-days"><button v-for="day in dateOptions" :key="day.value" :disabled="day.disabled" :aria-pressed="day.selected" :aria-label="`${day.value}，${!day.inMonth?'相邻月份':day.future?'未来日期':day.hasRecords?'有记录':'无记录'}`" :class="{'has-records':day.hasRecords,'adjacent':!day.inMonth}" @click="chooseDate(day.value)">{{ day.day }}</button></div><button class="subtle calendar-all" :aria-pressed="!dateFilter" @click="chooseDate('')">全部日期</button><p>仅可选择有记录的日期</p></section>
          </div>
          <p v-if="recordFilter==='voided'" class="voided-view-note">正在查看我作废的支出</p>
          <p class="filter-summary">{{ records.length }} 笔 · {{ keyword || categoryFilter || ratingFilter || dateFilter ? `筛选合计 ¥${money(filterTotal)}（含本机待同步内容）` : '每页20笔 · 最新在前' }}</p>
          <div v-if="selecting" class="batch-selection-bar"><p>已选 {{ selectedIds.length }} 笔 · 可跨页选择，切换账本、月份或筛选会清空选择。待同步及作废记录不可选。</p><div class="action-row"><button class="subtle" @click="pageRecords.filter(e=>!e.voided&&!pendingIds.has(e.id)).forEach(e=>selectRecord(e.id,true))">选择本页可选记录</button><button class="primary" @click="openBatch" :disabled="!selectedIds.length">修改所选分类</button></div></div><div v-if="pendingRecords.length" class="pending-box"><h3>当前账本有 {{ pendingRecords.length }} 笔本机记录待同步</h3><p>具体记录在下方按月份、分类和关键词筛选及分页显示；每笔分别标明同步状态。</p></div>
          <div v-if="!records.length" class="empty"><Icon name="receipt"/><p>{{ keyword || categoryFilter || ratingFilter || dateFilter?'没有匹配的记录':'这个月还没有支出' }}</p><small>{{ keyword || categoryFilter || ratingFilter || dateFilter?'试试其他分类或关键词':'从下方记一笔，慢慢积累日常。' }}</small></div>
          <ul v-else class="records"><li v-for="(record,index) in pageRecords" :key="record.id"><div v-if="index===0 || dayLabel(record.occurred_at)!==dayLabel(pageRecords[index-1]!.occurred_at)" class="day-heading">{{ dayLabel(record.occurred_at) }}</div><div class="record-main"><label v-if="selecting" class="record-selector"><input type="checkbox" :checked="selectedIds.includes(record.id)" :disabled="!!record.voided || pendingIds.has(record.id)" @change="selectRecord(record.id,($event.target as HTMLInputElement).checked)" :aria-label="`选择${record.purpose}`"></label><span class="category-icon" :data-category="record.category"><Icon :name="record.category"/></span><div class="record-body"><h3><button class="record-detail-link" @click="openDetail(record)" :aria-label="`查看${record.purpose}详情`">{{ record.purpose }}</button></h3><span v-if="record.voided" class="record-status">已作废</span><span v-else-if="record.refunds?.some(r=>!r.voided)" class="record-status">已退款 · 净支出</span><span class="record-status">{{ reviewText(record.rating,record.rating_reason) }}</span><p><time :datetime="record.occurred_at" aria-label="上海消费时间">{{ shanghaiClock(record.occurred_at) }}</time> · {{ record.category || '未分类' }} · {{ payer(record.owner_id) }}<span v-if="record.location"> · {{ record.location }}</span></p><span v-if="pendingIds.has(record.id) || failure(record.id)" class="record-status pending">{{ failure(record.id)?'需要处理 · 本机草稿仍在':'已保存到手机，待同步' }}</span><span v-if="imageState(record.id)" class="record-status pending">{{ imageState(record.id) }}</span></div><div class="record-right"><strong>¥{{ money(netAmount(record)) }}</strong><button v-if="record.owner_id===userId && !record.voided" class="text-button" @click="openForm(record.scope,record)" :disabled="!!failure(record.id)" :aria-label="`修改${record.purpose}`">修改</button></div></div><div v-if="imagesFor(record.id).length" class="record-images"><button v-for="(image,n) in imagesFor(record.id)" :key="image.id" @click="showImage(image)" :aria-label="`查看图片${n+1}`" :disabled="loadingImage===image.id"><img v-if="imageUrls[image.id]" :src="imageUrls[image.id]" alt="图片凭证缩略图"><template v-else><Icon name="photo"/><span>{{ loadingImage===image.id?'读取中':'查看图片' }}</span></template></button></div></li></ul>
          <div class="pagination"><button class="subtle" @click="changePage(-1)" :disabled="page===1">上一页</button><span>第 {{ page }} / {{ pageCount }} 页</span><button class="subtle" @click="changePage(1)" :disabled="page===pageCount">下一页</button></div>
        </template>
        <section v-else class="statistics"><h2>钱花在了哪里<small>按分类</small></h2><div v-for="item in categoryStats" :key="item.name" class="category-stat"><div><span>{{ item.name }}</span><span>¥{{ money(item.total) }} · {{ total?Math.round(item.total/total*100):0 }}%</span></div><div class="bar"><i :style="{width:`${total?item.total/total*100:0}%`}"></i></div></div><p v-if="!categoryStats.length" class="empty">本月还没有已同步的支出</p><h2>{{ tab==='shared'?'谁支付了这些支出':'我的支付' }}<small>当前账本 · 本月</small></h2><div class="payer-grid"><div v-for="user in byPayer.filter(u=>tab==='shared'||u.id===userId)" :key="user.id"><span>{{ user.display_name }}支付</span><strong>¥{{ money(user.total) }}</strong></div></div></section>
      </section>
      <div v-show="view!=='list' || !datePickerOpen" class="entry-dock"><button class="primary" @click="openForm(tab)" :disabled="!supported"><Icon name="plus"/>{{ tab==='shared'?'记共同支出':'记私人支出' }}</button></div>
      </section>
    </div>
    <div v-show="route==='/accounting' && !financialNavigationPaused">
    <CategoryPanel ref="categoryPanelRef" v-if="categoryPanel && account && !locked" :account="account" :store="store" :scope="categoryPanel.scope" :mode="categoryPanel.mode" :selected-ids="categoryPanel.ids" @close="categoryPanel=undefined" @changed="categoriesChanged" @refresh="sync" @unauthorized="locked=true" @submitted="clearSelection"/>
    <ReviewPanel :store="store" ref="reviewPanelRef" v-if="reviewOpen && account && !locked" :account="account" :scope="tab" @close="reviewOpen=false" @restore-changed="sync" @unauthorized="locked=true" @changed="reviewChanged"/>
    </div>
    <footer v-if="localWarning" role="status">{{ localWarning }}</footer>
    <div v-if="syncOpen && account && !locked" class="modal-backdrop sync-modal" @click.self="closeSyncStatus"><section class="form-card sync-status-card" role="dialog" aria-modal="true" aria-labelledby="sync-status-title"><div class="form-title"><h2 id="sync-status-title">同步状态</h2><button class="icon-button" @click="closeSyncStatus" aria-label="关闭同步状态"><Icon name="close"/></button></div><p class="sync-status-heading" role="status">{{ syncStatus }}</p><p v-if="syncProblem" class="field-error">{{ syncProblem }}</p><p class="form-note">{{ syncSummary.lastSync?`最近同步：${displayTime(syncSummary.lastSync)}（上海时间）`:'尚未完成首次同步' }}</p><p class="form-note">以下统计覆盖此账号的私人及所有家庭范围，包含记录、分类与附件；尚未保存的编辑不在其中。</p><dl class="sync-counts"><div><dt>待同步</dt><dd>{{ syncSummary.active }} 项</dd></div><div><dt>共同记账</dt><dd>{{ syncSummary.financial }} 项</dd></div><div><dt>生活备忘</dt><dd>{{ syncSummary.memo }} 项</dd></div><div><dt>愿望与计划</dt><dd>{{ syncSummary.plan }} 项</dd></div></dl><p v-if="syncSummary.active" class="form-note">待同步内容仍保存在本机，尚未全部上传。联网后会继续同步；需要核对的项目请在对应模块处理。</p><p v-if="syncSummary.frozen" class="notice">{{ syncSummary.frozen }} 项旧内容已冻结，不会自动上传或转入其他范围。请在共同记账中查看冻结待办说明。</p><ul v-if="syncSummary.issues.length" class="sync-issue-list"><li v-for="issue in syncSummary.issues" :key="issue">{{ issue }}</li></ul><button class="primary" @click="sync" :disabled="syncing || !supported || !networkOnline">{{ syncing?'同步中…':'立即同步' }}</button><p v-if="!networkOnline" class="form-note">当前离线，本机内容保留，联网后可重试。</p></section></div>
    <div v-if="bookPicker && account && !locked" class="modal-backdrop" @click.self="bookPicker=false;scopeTrigger?.focus()"><section class="form-card book-picker" role="dialog" aria-modal="true" aria-label="选择使用范围"><div class="form-title"><h2>选择使用范围</h2><button class="icon-button" @click="bookPicker=false;scopeTrigger?.focus()" aria-label="关闭范围选择"><Icon name="close"/></button></div><button v-for="family in account.context?.families??[]" :key="family.id" class="book-option" @click="switchBook('shared',family.id)" :aria-label="`家庭范围：${family.name}`"><span>{{ family.name }}<small>家庭模式 · 当前成员可见</small></span><Icon v-if="tab==='shared'&&currentFamilyId===family.id" name="check"/></button><button class="book-option" @click="switchBook('private')" aria-label="私人账"><span>私人<small>仅自己可见的记录</small></span><Icon v-if="tab==='private'" name="check"/></button></section></div>
    <div v-show="route==='/accounting' && !financialNavigationPaused">
    <div v-if="form && !locked" class="modal-backdrop" @click.self="!saving && !preparingPhotos && (form=undefined)"><section class="form-card" role="dialog" aria-modal="true" :aria-label="form.id?'修改支出':form.scope==='shared'?'记共同支出':'记私人支出'"><div class="handle"></div><div class="draft-navigation"><button class="text-button" @click="navigate('/home')"><Icon name="back"/>首页，稍后继续</button><small>未保存 · 关闭浏览器不保证保留</small></div><div class="form-title"><div><h2>{{ form.id?'修改支出':form.scope==='shared'?'记共同支出':'记私人支出' }}</h2><p class="form-scope" :class="{private:form.scope==='private'}">{{ form.scope==='shared'?'共同账 · 家庭成员可见':'私人账 · 仅本人可见' }}</p></div><button class="icon-button" @click="form=undefined" :disabled="saving || preparingPhotos" aria-label="取消"><Icon name="close"/></button></div>
      <form @submit.prevent="save" class="stack expense-entry-form"><div class="expense-sheet"><label class="amount-field">金额（元）<div><span>¥</span><input v-model="form.amount" inputmode="decimal" type="text" placeholder="0.00" required aria-label="金额（元）"></div></label><label>详细用途<textarea v-model="form.purpose" placeholder="例如：周末一起买菜" maxlength="500" rows="2" required></textarea></label></div>
        <div class="expense-settings"><div><div class="category-label"><label for="category">分类 · 选填</label><button type="button" class="text-button" aria-label="新增分类" @click="categoryEditor=!categoryEditor">＋ 新增分类</button></div><select id="category" v-model="form.category" aria-label="分类"><option value="">未分类</option><option v-for="name in categories(form.scope)" :key="name">{{ name }}</option></select><div v-if="categoryEditor" class="category-editor"><label>新分类名称<input v-model="newCategory" maxlength="24" placeholder="例如：宠物" @keydown.enter.prevent="addCategory"></label><button type="button" class="subtle" @click="addCategory" :disabled="addingCategory">{{ addingCategory?'正在保存…':'保存分类' }}</button><small>保存后可反复使用。{{ form.scope==='shared'?'共同分类家庭成员可见。':'私人分类仅本人可见。' }}</small></div></div>
        <div class="entry-time-location"><label>消费时间（本机时区）<input v-model="form.time" type="datetime-local" required></label>
          <label>满意度<select v-model="form.rating" aria-label="满意度" @change="form.ratingTouched=true"><option v-for="(label,n) in ratingLabels" :key="n" :value="String(n+1)">{{ label }}</option></select></label>
          <label v-if="ratingReasonVisible(form)">评价原因（选填）<textarea v-model="form.rating_reason" maxlength="500" rows="2" placeholder="可以记下感受，方便以后回顾"></textarea></label>
          <p v-else-if="form.rating_reason.trim()" class="form-note">原因框已收起，已填写的原因会保留；选择非“无感”满意度可查看或修改。</p>
        </div>
        <details class="expense-options settings-disclosure" :open="!!form.location || !!formImages.length || !!form.added.length"><summary><span>地点与图片 · 选填</span><small>{{ form.location || '未填写地点' }} · {{ formImages.length+form.added.length }} 张图片</small></summary><div class="stack"><label>地点（选填）<input v-model="form.location" maxlength="200" placeholder="添加地点"></label>        <div><div class="category-label"><span>图片凭证 · 选填</span><small>最多3张</small></div><div class="photo-grid"><div v-for="(image,n) in formImages" :key="image.id" class="photo-tile"><button type="button" @click="showImage(image)" :aria-label="`预览已有图片${n+1}`"><img v-if="imageUrls[image.id]" :src="imageUrls[image.id]" alt="已有凭证"><Icon v-else name="photo"/></button><button type="button" class="remove-photo" @click="form.removed.push(image.id)" :aria-label="`移除已有图片${n+1}`">×</button></div><div v-for="(image,n) in form.added" :key="image.id" class="photo-tile"><img :src="image.url" :alt="`新图片${n+1}`"><button type="button" class="remove-photo" @click="removeAdded(image.id)" :aria-label="`移除新图片${n+1}`">×</button></div><button v-if="formImages.length+form.added.length<3" type="button" class="add-photo" @click="photoInput?.click()" :disabled="preparingPhotos" aria-label="添加图片"><Icon name="photo"/><small>{{ preparingPhotos?'处理中':'添加图片' }}</small></button></div><input ref="photoInput" class="sr-only" type="file" accept="image/*" multiple @change="choosePhotos" aria-label="选择图片"><p class="form-note">可拍照或从相册选图。图片压缩后保存在本机，联网后再上传。</p></div></div></details></div>
        <p class="form-note">付款人：{{ account?.user.display_name }}。{{ form.id?'修改保留原有共同／私人归属。':'先保存到本机，再尝试同步到服务端。' }}</p><p v-if="formError" class="field-error" role="alert">{{ formError }}</p><button class="primary" :disabled="saving || preparingPhotos || addingCategory || !supported">{{ saving?'正在保存到本机…':form.scope==='shared'?'保存到共同账':'保存到私人账' }}</button>
      </form>
    </section></div>
    <div v-if="detail && !locked && !form" class="modal-backdrop" @click.self="detailId=undefined;history=undefined"><section class="form-card detail-card" role="dialog" aria-modal="true" aria-label="支出详情"><div class="draft-navigation"><button class="text-button" @click="navigate('/home')"><Icon name="back"/>首页</button></div><div class="form-title"><div><h2>{{ detail.purpose }}</h2><p class="form-scope">{{ detail.scope==='shared'?'共同账 · 家庭成员可见':'私人账 · 仅本人可见' }} · {{ payer(detail.owner_id) }}</p></div><button class="icon-button" @click="detailId=undefined;history=undefined" aria-label="关闭详情"><Icon name="close"/></button></div>
      <p class="detail-amount">¥{{ money(netAmount(detail)) }}<span v-if="detail.voided" class="muted"> · 已作废</span></p><dl class="expense-details"><div><dt>原付款</dt><dd>¥{{ money(detail.amount_cents) }}</dd></div><div><dt>净支出</dt><dd>¥{{ money(netAmount(detail)) }}{{ detail.voided?' · 已作废':'' }}</dd></div><div><dt>支出日期</dt><dd>{{ displayTime(detail.occurred_at) }}（上海时间）</dd></div><div><dt>分类 / 地点</dt><dd>{{ detail.category||'未分类' }} / {{ detail.location||'未填写' }}</dd></div><div><dt>评价</dt><dd>{{ reviewText(detail.rating, detail.rating_reason) }}</dd></div></dl>
      <h3>退款明细</h3><p v-if="!detail.refunds?.length" class="form-note">暂无退款。退款净额归属原消费月份。</p><article v-for="refund in detail.refunds" :key="refund.id" class="refund-item"><strong>¥{{ money(refund.amount_cents) }}{{ refund.voided?' · 已作废':'' }}</strong><p>{{ displayTime(refund.occurred_at) }} · {{ refund.reason||'未填写原因' }}</p><div v-if="detail.owner_id===userId && !detail.voided && !refund.voided" class="action-row"><button class="subtle" :aria-label="`更正退款${money(refund.amount_cents)}元`" @click="openRefund(refund.id)">更正退款</button><button class="subtle" :aria-label="`作废退款${money(refund.amount_cents)}元`" @click="maintain({kind:'refund_void',data:{refund_id:refund.id}})" :disabled="maintenanceBusy">作废退款</button></div></article>
      <div v-if="imagesFor(detail.id).length" class="record-images detail-images"><button v-for="(image,n) in imagesFor(detail.id)" :key="image.id" @click="showImage(image)" :aria-label="`查看详情图片${n+1}`"><img v-if="imageUrls[image.id]" :src="imageUrls[image.id]" alt="图片凭证"><span v-else>查看图片</span></button></div>
      <p v-if="detailError" class="field-error" role="alert">{{ detailError }}</p><template v-if="detail.owner_id===userId"><div v-if="!detail.voided" class="action-row"><button class="subtle" @click="openForm(detail.scope,detail)" :disabled="!!failure(detail.id)">修改支出与评价</button><button class="subtle" @click="openRefund()" :disabled="!!failure(detail.id)">登记退款</button></div><details v-if="!detail.voided" class="maintenance-menu"><summary>归属与作废</summary><p class="form-note">归属或作废的权限变化需同步成功后生效；离线副本无法即时撤回。</p><label v-if="detail.scope==='private'&&(account?.context?.families.length??0)>0">转共同的目标家庭<select v-model="transferFamilyId" aria-label="转共同的目标家庭"><option value="">选择目标家庭</option><option v-for="family in account?.context?.families??[]" :key="family.id" :value="family.id">{{ family.name }}</option></select></label><div class="action-row"><button v-if="detail.scope==='shared'||(account?.context?.families.length??0)>0" class="subtle" @click="maintain({kind:'scope',data:{scope:detail.scope==='shared'?'private':'shared'}})" :disabled="maintenanceBusy || !!failure(detail.id)">{{ detail.scope==='shared'?'转为私人账':'转为共同账' }}</button><button class="subtle danger" @click="maintain({kind:'void',data:{}})" :disabled="maintenanceBusy || !!failure(detail.id)">作废支出</button></div></details><button class="subtle history-button" @click="loadHistory" :disabled="historyLoading">{{ historyLoading?'读取历史中…':'查看本人历史' }}</button></template>
      <button v-else-if="!detail.voided && detail.scope==='shared'" class="primary" @click="openRequest">申请更正</button>
      <section v-if="history" class="history-list" aria-label="本人修改历史"><h3>本人修改历史</h3><article v-for="revision in history" :key="revision.version"><strong>版本 {{ revision.version }}</strong><details><summary>查看修改前后</summary><div class="comparison"><div v-if="revision.before"><span class="muted">修改前</span><ExpenseFacts :expense="revision.before"/></div><div><span class="muted">修改后</span><ExpenseFacts :expense="revision.after"/></div></div></details><p>{{ revision.after.purpose }} · 原付款 ¥{{ money(revision.after.amount_cents) }} · 净额 ¥{{ money(netAmount(revision.after)) }}</p><p>{{ revision.after.scope==='shared'?'共同':'私人' }} · {{ revision.after.voided?'已作废':'有效' }} · {{ displayTime(revision.after.occurred_at) }}</p><p>{{ reviewText(revision.after.rating, revision.after.rating_reason) }}</p><p v-for="refund in revision.after.refunds" :key="refund.id">退款 ¥{{ money(refund.amount_cents) }} · {{ refund.reason }} · {{ displayTime(refund.occurred_at) }}{{ refund.voided?' · 作废':'' }}</p></article></section>
    </section></div>
    <div v-if="refundForm && !locked && detail" class="modal-backdrop sub-modal" @click.self="!maintenanceBusy && (refundForm=undefined)"><section class="form-card" role="dialog" aria-modal="true" :aria-label="refundForm.editing?'更正退款':'登记退款'"><div class="form-title"><h2>{{ refundForm.editing?'更正退款':'登记退款' }}</h2><button class="icon-button" @click="refundForm=undefined" :disabled="maintenanceBusy" aria-label="取消退款"><Icon name="close"/></button></div><form class="stack" @submit.prevent="saveRefund"><label>退款金额（元）<input v-model="refundForm.amount" inputmode="decimal" required></label><label>退款时间（本机时区）<input v-model="refundForm.time" type="datetime-local" required></label><label>退款原因（选填）<textarea v-model="refundForm.reason" maxlength="500" rows="2"></textarea></label><p class="form-note">退款保留原付款，净额计入原消费月份。可离线保存，联网后同步。</p><p v-if="detailError" class="field-error" role="alert">{{ detailError }}</p><button class="primary" :disabled="maintenanceBusy">保存退款到手机</button></form></section></div>
    <div v-if="requestBoard && !locked && account" class="modal-backdrop sub-modal" @click.self="requestBoard=false"><section class="form-card request-board-card" role="dialog" aria-modal="true" aria-label="更正待办"><div class="form-title"><h2>更正待办</h2><button class="icon-button" @click="requestBoard=false" aria-label="关闭更正待办"><Icon name="close"/></button></div><p class="form-note">申请和审批需要联网；原账变化后需重新提出申请。只展示当前有权查看的内容。</p><RequestBoardToolbar :busy="syncing || requestBusy" :history-open="requestHistoryOpen" :history-count="requestHistoryCount" @refresh="refreshRequestBoard" @toggle-history="requestHistoryOpen=!requestHistoryOpen;requestPage=1"/><p v-if="requestMessage" class="notice" role="status">{{ requestMessage }}</p><p v-if="!boardRequests.length && !boardCategoryCount" class="empty">暂无更正申请</p><CategoryChangeCards ref="requestCardsRef" :history-open="requestHistoryOpen" :reset-key="requestResetKey" :acknowledged="requestAcknowledged" :decisions="categoryDecisions" :context-key="`${categoryContext(account)}:${tab}`" :changes="account.category_changes??[]" :expenses="account.local" :user-id="account.user.id" :busy="requestBusy" @decision="decideCategory"/><article v-for="item in boardRequests.slice((requestPage-1)*20,requestPage*20)" :key="item.id" :id="`request-${item.id}`" class="request-item"><p class="eyebrow">{{ item.owner_id===userId?'收到的申请':'发出的申请' }} · {{ statusName(item.status) }}</p><h3>{{ item.before.purpose }} · {{ actionName(item.kind) }}</h3><p class="request-reason">申请原因：{{ item.reason || '未填写' }}</p><p v-if="item.status==='rejected'" class="rejection-reason">拒绝原因：{{ item.rejection_reason || '未填写拒绝原因' }}</p><div class="comparison"><div><span class="muted">申请时 · 版本 {{ item.base_version }}</span><strong>¥{{ money(netAmount(item.before)) }}</strong><p>{{ item.before.purpose }}</p><small>原款 ¥{{ money(item.before.amount_cents) }} · {{ displayTime(item.before.occurred_at) }} · {{ item.before.category||'未分类' }} · {{ item.before.location||'未填写地点' }}</small><p v-for="refund in item.before.refunds" :key="refund.id">退款 ¥{{ money(refund.amount_cents) }} · {{ refund.reason }} · {{ displayTime(refund.occurred_at) }}</p></div><div v-if="requestAfter(item)"><span class="muted">建议修改后</span><strong>¥{{ money(netAmount(requestAfter(item)!)) }}</strong><p>{{ requestAfter(item)!.purpose }}{{ requestAfter(item)!.voided?' · 已作废':'' }}</p><small>原款 ¥{{ money(requestAfter(item)!.amount_cents) }} · {{ displayTime(requestAfter(item)!.occurred_at) }} · {{ requestAfter(item)!.category||'未分类' }} · {{ requestAfter(item)!.location||'未填写地点' }}</small><p v-for="refund in requestAfter(item)!.refunds" :key="refund.id">退款 ¥{{ money(refund.amount_cents) }} · {{ refund.reason }} · {{ displayTime(refund.occurred_at) }}{{ refund.voided?' · 作废':'' }}</p></div></div><DecisionButtons ref="singleDecisionRefs" v-if="item.status==='pending' && item.owner_id===userId" :busy="requestBusy" :frozen="decisions[item.id]" :context-key="requestContext" accept-label="接受申请" reject-label="拒绝申请" @decision="(decision,reason)=>decide(item,decision,reason)"/><p v-if="item.status==='stale'" class="form-note">原记录已变化，不能再接受此申请。请依据当前记录重新申请。</p></article><div v-if="requestPages>1" class="pagination"><button class="subtle" @click="requestPage--" :disabled="requestPage===1">上一页申请</button><span>{{ requestPage }} / {{ requestPages }}</span><button class="subtle" @click="requestPage++" :disabled="requestPage===requestPages">下一页申请</button></div></section></div>
    <div v-if="requestForm && !locked && detail" class="modal-backdrop sub-modal"><section class="form-card" role="dialog" aria-modal="true" aria-label="申请更正"><div class="form-title"><h2>申请更正</h2><button class="icon-button" @click="requestForm=undefined" :disabled="requestBusy" aria-label="取消申请"><Icon name="close"/></button></div><form class="stack" @submit.prevent="submitRequest"><fieldset class="request-fields" :disabled="requestBusy || !!requestForm.submitted"><label>建议动作<select v-model="requestForm.kind" @change="requestForm.refund_id=''" :disabled="requestBusy"><option value="update">修改支出</option><option value="refund_create">新增退款</option><option value="refund_update">更正退款</option><option value="refund_void">作废退款</option><option value="void">作废支出</option></select></label><template v-if="requestForm.kind==='update'"><label>建议金额（元）<input v-model="requestForm.amount" inputmode="decimal" required></label><label>建议用途<textarea v-model="requestForm.purpose" maxlength="500" required></textarea></label><label>建议消费时间<input v-model="requestForm.time" type="datetime-local" required></label><label>建议地点<input v-model="requestForm.location" maxlength="200"></label><label>建议分类<select v-model="requestForm.category"><option value="">未分类</option><option v-for="name in categories('shared')" :key="name">{{ name }}</option></select></label></template><label v-if="requestForm.kind==='refund_update'||requestForm.kind==='refund_void'">选择退款<select v-model="requestForm.refund_id" required @change="chooseRequestRefund"><option value="">请选择</option><option v-for="refund in requestForm.original.refunds?.filter(r=>!r.voided)" :key="refund.id" :value="refund.id">¥{{ money(refund.amount_cents) }} · {{ refund.reason||displayTime(refund.occurred_at) }}</option></select></label><template v-if="requestForm.kind==='refund_create'||requestForm.kind==='refund_update'"><label>建议退款金额（元）<input v-model="requestForm.refund_amount" inputmode="decimal" required></label><label>建议退款时间<input v-model="requestForm.refund_time" type="datetime-local" required></label><label>建议退款原因<textarea v-model="requestForm.refund_reason" maxlength="500"></textarea></label></template><p v-if="requestForm.kind==='void'" class="form-note">接受后整笔支出退出正常列表和统计，保留记录者本人历史。</p><label>申请原因（选填）<textarea v-model="requestForm.reason" maxlength="500"></textarea></label></fieldset><p v-if="requestMessage" class="field-error" role="alert">{{ requestMessage }}</p><p v-if="requestForm.submitted" class="form-note">已提交过此内容，重试会沿用原编号和内容。若需修改，请先取消并同步确认上次结果。</p><p class="form-note">仅在线提交，记录者接受后才修改原账。网络失败会保留原编号供重试。</p><button class="primary" :disabled="requestBusy">{{ requestBusy?'正在提交…':'提交更正申请' }}</button></form></section></div>
    <div v-if="previewId && imageUrls[previewId] && !locked" class="image-backdrop" @click.self="previewId=undefined"><section role="dialog" aria-modal="true" aria-label="图片凭证"><button class="preview-close" @click="previewId=undefined" aria-label="关闭图片"><Icon name="close"/></button><img :src="imageUrls[previewId]" alt="图片凭证大图"></section></div>
    </div>
    <MessageInbox :scope-label="scopeName" v-if="inboxOpen && account && !locked" :messages="inboxMessages" :ignored="inboxIgnored" :loading="inboxLoading" :busy="inboxBusy" :error="inboxError" @close="inboxOpen=false;inboxTrigger?.focus()" @toggle="changeInboxIgnored" @open="openMessage" @ignore="ignoreMessage" @restore="item=>ignoreMessage(item,true)"/>
  </main>
  <div v-if="loggingOut" class="modal-backdrop" role="status" aria-live="polite"><p class="form-card">正在退出账号，尚未清除本机输入…</p></div>
</template>
