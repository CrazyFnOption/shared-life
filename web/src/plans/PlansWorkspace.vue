<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'
import type { Source } from '../types'
import { sameSource, sourceKey } from '../sources'
import MemoAttachments from '../memos/MemoAttachments.vue'
const MarkdownEditor = defineAsyncComponent(() => import('../MarkdownEditor.vue'))
import { trapDialogTab } from '../memos/dialog'
import type { MemoAttachmentView } from '../memos/types'
import { planProgress, taskCompleteOperation, taskReference as readTaskReference } from './domain'
import { copyPlanDraft } from './types'
import type { PlanRecord, PlanTask, PlanStage, PlanAttachment, PlanAttachmentCommand, PlanCommand, PlanCommandResult, PlanDraftChange, PlanFocusCommand, PlanOperation, PlanSnapshot, PlanEditorDraft } from './types'
import '../memos/memos.css'
import './plans.css'
import './modern-plans.css'
const props=withDefaults(defineProps<{source:Source;scopeLabel:string;snapshot:PlanSnapshot;writable?:boolean;loading?:boolean;error?:string;result?:PlanCommandResult;focusedTaskIds?:string[];initialDraft?:PlanEditorDraft}>(),{writable:false,loading:false,error:'',focusedTaskIds:()=>[]})
const emit=defineEmits<{back:[];command:[command:PlanCommand];focus:[command:PlanFocusCommand];'request-attachment':[command:PlanAttachmentCommand];'draft-change':[value:PlanDraftChange]}>()
type Editor=PlanEditorDraft
const tab=ref<'wish'|'plan'>('wish'),selected=ref(''),taskId=ref(''),creator=ref('all'),archived=ref(false),editor=ref<Editor>(),editorSource=ref<Source>(),pending=ref(''),operationError=ref(''),notice=ref(''),nativeComposing=ref(false),editorComposing=ref(false),heading=ref<HTMLElement>(),deleteReason=ref(''),deleteDialog=ref(false)
const composing=computed(()=>nativeComposing.value||editorComposing.value)
let submitted:PlanOperation|undefined
let navigationEpoch=0
let saveContext:{request_id:string;source:Source;draft:Editor;navigation:number}|undefined
let deleteOpener:HTMLElement|null=null
const deleteTitle=ref<HTMLElement>()
const records=computed(()=>props.snapshot.records.filter(r=>!r.deleted)),record=computed(()=>records.value.find(r=>r.id===selected.value)),task=computed(()=>props.snapshot.tasks.find(t=>t.id===taskId.value&&!t.deleted))
const visible=computed(()=>records.value.filter(r=>r.kind===tab.value&&r.archived===archived.value&&(tab.value!=='wish'||creator.value==='all'||r.author_id===creator.value)))
const stages=computed(()=>props.snapshot.stages.filter(s=>s.plan_id===record.value?.id&&!s.deleted).sort((a,b)=>a.position-b.position||a.id.localeCompare(b.id)))
const current=computed(()=>task.value??record.value)
const requests=computed(()=>props.snapshot.deletion_requests.filter(r=>r.target_id===current.value?.id&&r.status==='pending'))
const headingText=computed(()=>editor.value?`${editor.value.base?'编辑':'新建'}${entityLabel(editor.value.entity)}`:task.value?'计划任务':record.value?entityLabel(record.value.kind):'愿望与计划')
function entityLabel(entity:string){return entity==='wish'?'愿望':entity==='plan'?'计划':entity==='stage'?'阶段':'任务'}
function name(id:string){return props.snapshot.users.find(u=>u.id===id)?.display_name??'原成员'}
function stepTasks(id:string){return props.snapshot.tasks.filter(t=>t.stage_id===id&&!t.deleted).sort((a,b)=>a.position-b.position||a.id.localeCompare(b.id))}
function focusHeading(){void nextTick(()=>heading.value?.focus())}
function open(item:PlanRecord){selected.value=item.id;taskId.value='';operationError.value='';focusHeading()}
function back(){if(pending.value)return;if(editor.value){cancel();return}if(taskId.value){taskId.value='';focusHeading();return}if(selected.value){selected.value='';focusHeading();return}emit('back')}
function start(entity:Editor['entity'],item?:PlanRecord|PlanTask|PlanStage,fromWish?:PlanRecord){
 if(!props.writable||pending.value)return
 editorSource.value={...props.source};operationError.value=''
 const r=item&&'kind' in item?item:undefined,t=item&&'assignee_ids' in item?item:undefined,s=item&&'position' in item?item:undefined
 editor.value={source:{...props.source},id:item?.id??crypto.randomUUID(),entity,base:item?.version??0,title:item?.title??fromWish?.title??'',body_markdown:item&&'body_markdown'in item?item.body_markdown:'',wish_kind:r?.wish_kind??(entity==='wish'?'生活愿景':''),target_date:r?.target_date??'',plan_id:s?.plan_id??record.value?.id??'',stage_id:t?.stage_id??stages.value[0]?.id??'',assignee_ids:t?[...t.assignee_ids]:[],due_date:t?.due_date??'',position:s?.position??(entity==='stage'?stages.value.length:props.snapshot.tasks.length),source_wish_id:fromWish?.id??'',attachments:item&&'attachments'in item?item.attachments.map(a=>({...a})):[]}
 focusHeading()
}
function restoreEditor(){
 const draft=props.initialDraft
 if(!draft || !sameSource(draft.source,props.source))return
 editorSource.value={...draft.source};editor.value=copyPlanDraft(draft)
 selected.value=draft.entity==='task'||draft.entity==='stage'?draft.plan_id:draft.base?draft.id:''
 taskId.value=draft.entity==='task'&&draft.base?draft.id:''
}
function openTarget(id:string):boolean{
 if(editor.value || pending.value || !sameSource(props.snapshot.source,props.source))return false
 const found=records.value.find(item=>item.id===id)
 if(found){deleteDialog.value=false;open(found);return true}
 const foundTask=readTaskReference(props.snapshot,id)
 if(!foundTask)return false
 selected.value=foundTask.plan_id;taskId.value=foundTask.id;deleteDialog.value=false;operationError.value='';focusHeading();return true
}
function resetNavigation(){navigationEpoch++;tab.value='wish';selected.value='';taskId.value='';editor.value=undefined;editorSource.value=undefined;deleteDialog.value=false;deleteOpener=null;creator.value='all';archived.value=false;operationError.value='';notice.value=''}
const restorableDraft=computed(()=>props.writable && props.initialDraft && sameSource(props.initialDraft.source,props.source)?props.initialDraft:undefined)
defineExpose({openTarget,resetNavigation})
function cancel(){if(pending.value)return;if(editorSource.value)emit('draft-change',{source:{...editorSource.value},dirty:false,draft:null});editor.value=undefined;editorSource.value=undefined;operationError.value='';focusHeading()}
function send(operation:PlanOperation,blobs?:PlanCommand['blobs']){if(!props.writable||pending.value)return;pending.value=operation.op_id;submitted=operation;operationError.value='';emit('command',{request_id:operation.op_id,source:{...props.source},operation,blobs})}
function mutate(entity:PlanOperation['entity_type'],item:{id:string;version:number},kind:string,data:Record<string,unknown>){send({op_id:crypto.randomUUID(),entity_type:entity,entity_id:item.id,base_version:item.version,kind,data})}
async function save(){
 if(!editor.value||!editorSource.value||pending.value||composing.value)return
 const draft=copyPlanDraft(editor.value),captured={...editorSource.value},title=draft.title.trim()
 if(!title){operationError.value='请填写名称。';return}
 if(draft.entity==='task'&&!draft.stage_id){operationError.value='请先新建阶段，再添加任务。';return}
 if(new TextEncoder().encode(draft.body_markdown).length>200*1024){operationError.value='正文不能超过200 KiB。';return}
 const opId=crypto.randomUUID();pending.value=opId;saveContext={request_id:opId,source:captured,draft,navigation:navigationEpoch}
 try{
  const files:PlanAttachment[]=[],blobs:NonNullable<PlanCommand['blobs']>=[]
  for(const file of draft.attachments){
   if(file.size_bytes>20*1024*1024||file.kind==='photo'&&(file.size_bytes>2*1024*1024||!['image/jpeg','image/png','image/webp'].includes(file.mime_type)))throw new Error('照片请使用2 MiB以内的JPEG／PNG／WebP；文件不超过20 MiB。')
   const sha256=file.sha256||(file.blob?Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.blob.arrayBuffer()))).map(v=>v.toString(16).padStart(2,'0')).join(''):'')
   if(!sha256)throw new Error('附件内容尚未就绪，请先加载附件。')
   files.push({id:file.id,kind:file.kind,name:file.name,mime_type:file.mime_type,size_bytes:file.size_bytes,sha256})
   if(file.blob&&(!file.state||file.state==='pending'))blobs.push({id:file.id,parent_id:draft.id,blob:file.blob})
  }
  if(!sameSource(captured,props.source)||saveContext?.request_id!==opId||pending.value!==opId)return
  let data:Record<string,unknown>
  if(draft.entity==='stage')data={title,plan_id:draft.plan_id,position:draft.position}
  else if(draft.entity==='task')data={title,body_markdown:draft.body_markdown,attachments:files,plan_id:draft.plan_id,stage_id:draft.stage_id,assignee_ids:[...draft.assignee_ids],due_date:draft.due_date||null,position:draft.position}
  else data={title,body_markdown:draft.body_markdown,attachments:files,wish_kind:draft.entity==='wish'?draft.wish_kind:'',target_date:draft.entity==='plan'?draft.target_date||null:null,...(draft.source_wish_id?{source_wish_id:draft.source_wish_id}:{})}
  const operation:PlanOperation={op_id:opId,entity_type:draft.entity,entity_id:draft.id,base_version:draft.base,kind:draft.base?'update':draft.source_wish_id?'from_wish':'create',data}
  submitted=operation;emit('command',{request_id:opId,source:captured,operation,blobs})
 }catch(error){if(pending.value===opId){pending.value='';saveContext=undefined;operationError.value=error instanceof Error?error.message:'保存未完成，输入已保留。'}}
}
function complete(item:PlanTask){const operation=taskCompleteOperation(item,!item.completed);send(operation)}
function focusTask(item:PlanTask){if(!props.writable||pending.value)return;emit('focus',{source:{...props.source},request_id:crypto.randomUUID(),task_id:item.id,focused:!props.focusedTaskIds.includes(item.id)})}
function showDeleteDialog(event:Event){deleteOpener=event.currentTarget as HTMLElement;deleteReason.value='';deleteDialog.value=true;void nextTick(()=>deleteTitle.value?.closest('form')?.querySelector<HTMLElement>('textarea,button')?.focus())}
function closeDeleteDialog(){deleteDialog.value=false;void nextTick(()=>{if(deleteOpener?.isConnected)deleteOpener.focus();deleteOpener=null})}
function deleteItem(){if(composing.value)return;const item=current.value;if(!item)return;const entity=task.value?'task':record.value!.kind;if(item.author_id===props.source.user_id)mutate(entity,item,'delete',{});else send({op_id:crypto.randomUUID(),entity_type:'deletion_request',entity_id:crypto.randomUUID(),kind:'create',base_version:0,data:{target_type:entity,target_id:item.id,target_version:item.version,reason:deleteReason.value}});closeDeleteDialog()}
function addFiles(files:MemoAttachmentView[]){if(!editor.value)return;editor.value.attachments.push(...files.map(file=>({id:file.id,kind:file.kind??(file.mime_type.startsWith('image/')?'photo':'file'),name:file.file_name,mime_type:file.mime_type,size_bytes:file.size_bytes,sha256:'',blob:file.blob})))}
function attachmentViews(files:PlanAttachment[]):MemoAttachmentView[]{return files.map(f=>({id:f.id,kind:f.kind,file_name:f.name,mime_type:f.mime_type,size_bytes:f.size_bytes,blob:f.blob,pending:f.state==='pending'}))}
function requestAttachment(file:MemoAttachmentView){const attachment=(editor.value?.attachments??current.value?.attachments??[]).find(a=>a.id===file.id);if(attachment)emit('request-attachment',{source:{...props.source},parent_id:editor.value?.id??current.value!.id,attachment})}
watch(()=>props.result,result=>{
 if(!result||!pending.value||result.request_id!==pending.value||!sameSource(result.source,props.source))return
 pending.value=''
 if(result.status==='error'){saveContext=undefined;operationError.value=result.error||'保存未完成，输入已保留。';return}
 notice.value='已保存到本机。'
 const saved=saveContext;saveContext=undefined
 if(saved&&saved.request_id===result.request_id&&sameSource(saved.source,result.source)){
  emit('draft-change',{source:{...saved.source},dirty:false,draft:null})
  if(saved.navigation===navigationEpoch&&editor.value&&sameSource(editorSource.value,saved.source)){
   const entity=saved.draft.entity;if(entity==='wish'||entity==='plan'){selected.value=result.record_id??saved.draft.id;taskId.value=''}else if(entity==='task')taskId.value=saved.draft.id
   editor.value=undefined;editorSource.value=undefined;focusHeading()
  }
 }
 if(submitted?.kind==='delete'){if(submitted.entity_type==='task')taskId.value='';else selected.value=''}
 submitted=undefined
})
watch(()=>sourceKey(props.source),()=>{navigationEpoch++;saveContext=undefined;selected.value='';taskId.value='';editor.value=undefined;editorSource.value=undefined;pending.value='';operationError.value='';notice.value='';deleteDialog.value=false;deleteOpener=null;submitted=undefined;creator.value='all';archived.value=false})
watch(editor,value=>{if(value && editorSource.value && sameSource(value.source,editorSource.value) && sameSource(editorSource.value,props.source))emit('draft-change',{source:{...editorSource.value},dirty:true,draft:copyPlanDraft(value)})},{deep:true})

</script>

<template>
<section class="plans-workspace" aria-label="愿望与计划">
 <header class="wp-head" :class="{ 'is-root': !selected && !editor }"><div v-if="selected || editor" class="wp-head-meta"><button type="button" class="wp-back" :disabled="!!pending" @click="back">‹ 返回</button></div><div class="wp-head-copy"><h2 ref="heading" tabindex="-1">{{ headingText }}</h2></div><button v-if="!editor && !selected && writable" class="wp-add" :disabled="!!pending" @click="start(tab)">＋ 新建</button><button v-else-if="!editor && current && writable && !record?.archived" class="wp-add" :disabled="!!pending" @click="start(task ? 'task' : record!.kind,current)">编辑</button></header>
 <p v-if="error || operationError" class="wp-error" role="alert">{{ error || operationError }}</p><p v-if="loading" class="wp-muted" role="status">正在读取本机记录…</p>
 <main class="wp-body">
  <form v-if="editor" class="wp-form" @submit.prevent="save" @compositionstart="nativeComposing=true" @compositionend="nativeComposing=false">
   <p v-if="editor.source_wish_id" class="wp-note">来自愿望：{{ records.find(r=>r.id===editor!.source_wish_id)?.title }}。原愿望仍由你手动标记实现，资料跟随原记录。</p>
   <label class="wp-field wp-title-field">{{ entityLabel(editor.entity) }}名称<input v-model="editor.title" maxlength="200" required :disabled="!!pending"></label>
   <label v-if="editor.entity==='wish'" class="wp-field">类型<select aria-label="愿望类型" v-model="editor.wish_kind" :disabled="!!pending"><option>生活愿景</option><option>未来旅行</option><option>想要的东西</option></select></label>
   <template v-if="editor.entity==='task'"><label class="wp-field">阶段<select aria-label="阶段" v-model="editor.stage_id" required :disabled="!!pending"><option v-for="stage in stages" :key="stage.id" :value="stage.id">{{ stage.title }}</option></select></label><fieldset class="wp-assignees" :disabled="!!pending"><legend>负责人（可多选，留空为待分配）</legend><label v-for="user in snapshot.users.filter(u=>u.assignable===true || u.id===source.user_id || editor!.assignee_ids.includes(u.id))" :key="user.id"><input v-model="editor.assignee_ids" type="checkbox" :value="user.id" :disabled="user.assignable===false && !editor.assignee_ids.includes(user.id)">{{ user.display_name }}{{ user.assignable===false?'（已离组，可保留原负责人）':'' }}</label></fieldset><label class="wp-field">任务日期（选填）<input v-model="editor.due_date" type="date" :disabled="!!pending"></label></template>
   <label v-if="editor.entity==='plan'" class="wp-field">目标日期（选填）<input v-model="editor.target_date" type="date" :disabled="!!pending"></label>
   <template v-if="editor.entity!=='stage'"><div class="wp-body-label"><span>正文</span><span>Markdown</span></div><MarkdownEditor v-model="editor.body_markdown" :disabled="!!pending" placeholder="写下目标、想法或资料链接" @composition-state="editorComposing=$event" /><MemoAttachments :files="attachmentViews(editor.attachments)" editable :disabled="!!pending" @add="addFiles" @remove="id=>editor!.attachments=editor!.attachments.filter(f=>f.id!==id)" @request="requestAttachment" /></template>
   <div class="wp-form-actions"><button type="button" class="wp-link" :disabled="!!pending" @click="cancel">取消</button><button type="submit" class="wp-save" :disabled="!!pending||composing">{{ pending?'正在保存…':'保存' }}</button></div>
  </form>
  <template v-else-if="!selected">
   <button v-if="restorableDraft" type="button" class="wp-draft-resume" :disabled="!!pending" @click="restoreEditor"><span><strong>继续未保存的{{ entityLabel(restorableDraft.entity) }}</strong><small>{{ restorableDraft.title || '未命名草稿' }}</small></span><span aria-hidden="true">↗</span></button>
   <div class="wp-tabs" role="tablist" aria-label="愿望与计划"><button v-for="value in (['wish','plan'] as const)" :key="value" role="tab" :aria-selected="tab===value" class="wp-tab" @click="tab=value">{{ value==='wish'?'愿望清单':'行动计划' }}</button></div>
   <div class="wp-tools"><label v-if="tab==='wish'" class="wp-filter"><span>创建者</span><select v-model="creator" aria-label="按创建者查看愿望"><option value="all">所有人</option><option v-for="id in [...new Set(records.filter(r=>r.kind==='wish').map(r=>r.author_id))]" :key="id" :value="id">{{ name(id) }}{{ id===source.user_id?'（我）':'' }}</option></select></label><button class="wp-link" :aria-pressed="archived" @click="archived=!archived">{{ archived?'返回当前':'查看归档' }}</button></div>
   <div v-if="visible.length" class="wp-list"><button v-for="item in visible" :key="item.id" class="wp-item" @click="open(item)"><span class="wp-item-copy"><span class="wp-category">{{ item.kind==='wish'?item.wish_kind:item.source_wish_id?'来自愿望':'专项计划' }}</span><strong>{{ item.title }}</strong><small>{{ name(item.author_id) }} 创建 · {{ item.archived?'已归档':item.kind==='wish'?item.realized?'已实现':'期待实现':`${planProgress(snapshot,item.id).done}/${planProgress(snapshot,item.id).total} 项完成` }}{{ item.kind==='wish' && records.some(p=>p.source_wish_id===item.id)?' · 已有计划':'' }}</small></span><span class="wp-item-arrow" aria-hidden="true">↗</span></button></div><p v-else class="wp-empty">{{ archived?'还没有归档记录。':tab==='wish'?'记下一件期待的事吧。':'从一个小目标开始建立计划。' }}</p>
  </template>
  <article v-else-if="current && record">
   <p class="wp-category">{{ task?'计划任务':record.kind==='wish'?record.wish_kind:'专项计划' }}{{ record.archived?' · 已归档':'' }}</p><h3 class="wp-detail-title">{{ current.title }}</h3><div class="wp-meta"><span>{{ name(current.author_id) }} 创建</span><span v-if="task">{{ task.completed?'已完成':'进行中' }}</span><span v-else-if="record.kind==='wish'">{{ record.realized?'已实现':'期待实现' }}</span><span v-if="task?.due_date || record.target_date">{{ task ? '任务日期' : '目标日期' }} {{ task?.due_date || record.target_date }}</span></div>
   <p v-if="task" class="wp-muted">负责人：{{ task.assignee_ids.length?task.assignee_ids.map(name).join('、'):'待分配' }}</p>
   <p v-if="record.source_wish_id && !task" class="wp-source"><button v-if="records.some(r=>r.id===record!.source_wish_id)" class="wp-link" @click="open(records.find(r=>r.id===record!.source_wish_id)!)">来源愿望：{{ records.find(r=>r.id===record!.source_wish_id)?.title }}</button><span v-else class="wp-muted">来源愿望已不可用。</span></p>
   <section class="wp-section"><h3>正文与资料</h3><div class="wp-markdown"><slot name="markdown" :markdown="current.body_markdown"><p class="wp-copy">{{ current.body_markdown || '还没有补充正文。' }}</p></slot></div><MemoAttachments v-if="current.attachments.length" :files="attachmentViews(current.attachments)" @request="requestAttachment" /></section>
   <div v-if="writable" class="wp-actions">
    <template v-if="task && !record.archived"><button class="wp-save" :disabled="!!pending" @click="complete(task)">{{ task.completed?'重新打开任务':'标记完成' }}</button><button class="wp-link" :disabled="!!pending" :aria-pressed="focusedTaskIds.includes(task.id)" @click="focusTask(task)">{{ focusedTaskIds.includes(task.id)?'移出生活备忘待办':'加入生活备忘待办' }}</button><p class="wp-muted">家庭成员完成一次即可。任务日期不会自动成为日程。</p></template>
    <template v-else-if="!task && record.kind==='wish' && !record.archived"><button v-if="records.find(p=>p.source_wish_id===record!.id)" class="wp-save" @click="open(records.find(p=>p.source_wish_id===record!.id)!)">查看相关计划</button><button v-else class="wp-save" :disabled="!!pending" @click="start('plan',undefined,record)">从这个愿望建立计划</button><button class="wp-link" :disabled="!!pending" @click="mutate('wish',record,'realized',{realized:!record.realized})">{{ record.realized?'改为期待实现':'标记为已实现' }}</button></template>
   </div>
   <template v-if="!task && record.kind==='plan'">
    <section class="wp-summary"><div class="wp-progress-line"><span>计划进度</span><span>{{ planProgress(snapshot,record.id).done }} / {{ planProgress(snapshot,record.id).total }} 项 · {{ planProgress(snapshot,record.id).percent }}%</span></div><progress :value="planProgress(snapshot,record.id).done" :max="planProgress(snapshot,record.id).total || 1" aria-label="计划完成进度"></progress></section>
    <section v-for="stage in stages" :key="stage.id" class="wp-section wp-stage"><div class="wp-section-title"><h3><span class="wp-stage-number">{{ stages.indexOf(stage)+1 }}</span>{{ stage.title }}</h3><div v-if="writable && !record.archived" class="wp-stage-actions"><button class="wp-link" :disabled="!!pending" @click="start('stage',stage)">编辑</button><button v-if="!stepTasks(stage.id).length" class="wp-link" :disabled="!!pending" @click="mutate('stage',stage,'delete',{})">删除空阶段</button></div></div><div class="wp-list"><div v-for="item in stepTasks(stage.id)" :key="item.id" class="wp-task" :class="{'wp-done':item.completed}"><label class="wp-task-check"><input type="checkbox" :checked="item.completed" :disabled="!writable || !!pending || record.archived" :aria-label="`完成任务：${item.title}`" @change="complete(item)"></label><button class="wp-task-copy" @click="taskId=item.id;focusHeading()"><span>{{ item.title }}</span><small>{{ item.assignee_ids.length?item.assignee_ids.map(name).join('、'):'待分配' }}{{ item.due_date?` · ${item.due_date}`:'' }}{{ focusedTaskIds.includes(item.id)?' · 已关注':'' }}</small></button></div><p v-if="!stepTasks(stage.id).length" class="wp-empty">还没有任务。</p></div></section>
    <div v-if="writable && !record.archived" class="wp-inline-actions"><button class="wp-link" :disabled="!!pending" @click="start('stage')">＋ 添加阶段</button><button class="wp-link" :disabled="!!pending || !stages.length" @click="start('task')">＋ 添加任务</button></div>
   </template>
   <section v-if="requests.length" class="wp-section"><h3>删除申请</h3><div v-for="request in requests" :key="request.id" class="wp-note"><p>{{ name(request.requester_id) }} 申请删除{{ request.reason?`：${request.reason}`:'' }}</p><div v-if="request.author_id===source.user_id && writable" class="wp-inline-actions"><button class="wp-link" :disabled="!!pending" @click="mutate('deletion_request',request,'decide',{accepted:true})">同意删除</button><button class="wp-link" :disabled="!!pending" @click="mutate('deletion_request',request,'decide',{accepted:false})">拒绝</button></div><p v-else class="wp-muted">等待创建者处理。</p></div></section>
   <footer v-if="writable" class="wp-record-footer"><button v-if="!task" class="wp-link" :disabled="!!pending" @click="mutate(record.kind,record,'archive',{archived:!record.archived})">{{ record.archived?'取消归档':'归档' }}</button><button class="wp-link wp-danger" :disabled="!!pending || requests.some(r=>r.requester_id===source.user_id)" @click="showDeleteDialog">{{ current.author_id===source.user_id?'删除'+(task?'任务':entityLabel(record.kind)):'申请删除' }}</button></footer>
  </article><p v-else class="wp-empty">记录已不可用，请返回列表。</p>
 </main><p role="status" class="wp-notice">{{ notice }}</p>
 <div v-if="deleteDialog" class="memo-overlay" @keydown.esc.prevent.stop="!$event.isComposing && closeDeleteDialog()"><form class="memo-modal" role="dialog" aria-modal="true" aria-labelledby="wp-delete-title" @submit.prevent="deleteItem" @keydown="trapDialogTab" @compositionstart="nativeComposing=true" @compositionend="nativeComposing=false"><h3 id="wp-delete-title" ref="deleteTitle" tabindex="-1">{{ current?.author_id===source.user_id?'确认删除？':'申请创建者删除' }}</h3><p class="wp-muted">{{ current?.author_id===source.user_id?'删除后不再显示此记录及其下属内容。':'共享记录由原创建者决定是否删除。' }}</p><label v-if="current?.author_id!==source.user_id" class="wp-field">原因（选填）<textarea v-model="deleteReason" maxlength="2000"></textarea></label><div class="wp-form-actions"><button type="button" class="wp-link" @click="closeDeleteDialog">取消</button><button class="wp-save" type="submit">{{ current?.author_id===source.user_id?'确认删除':'提交申请' }}</button></div></form></div>
</section>
</template>
