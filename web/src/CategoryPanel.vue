<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import { AccountChanged, FamilyChanged, LedgerStore, RestoreChanged } from './store'
import { categoryAction, categoryContext, categoryNames, categoryStatus, eligibleSelection } from './categoryChanges'
import CategoryChangeCards from './CategoryChangeCards.vue'
import Icon from './Icon.vue'
import RequestBoardToolbar from './RequestBoardToolbar.vue'
import { freezeDecision, useRequestBoard, type DecisionAttempt } from './requestBoard'
import type { Account, CategoryChange, CategoryChangeInput, Scope } from './types'
const props=defineProps<{account:Account;store:LedgerStore;scope:Scope;selectedIds?:string[];mode:'manage'|'batch'|'requests'}>()
const emit=defineEmits<{close:[];changed:[];unauthorized:[];refresh:[];submitted:[]}>()
const scope=computed(()=>props.scope)
const kind=ref<'rename'|'delete'|'merge'>('rename')
const source=ref(''),target=ref(''),reason=ref(''),notice=ref(''),busy=ref(false)
const reasonOpen=ref(false)
const preview=ref<CategoryChange>()
const submission=ref<{id:string;preview_id:string;reason:string}>()
const decisions=ref<Record<string,DecisionAttempt>>({})
const changeCards=ref<{hasUnsavedChanges:boolean}>()
const hasUnsavedChanges=computed(()=>!!source.value||!!target.value||!!reason.value||kind.value!=='rename'||!!submission.value||!!changeCards.value?.hasUnsavedChanges)
const isBusy=computed(()=>busy.value)
defineExpose({hasUnsavedChanges,isBusy})
let epoch=0
const context=computed(()=>categoryContext(props.account))
const {historyOpen,resetKey,acknowledged,acknowledge}=useRequestBoard(context)
const names=computed(()=>categoryNames(props.account,scope.value))
const mergeTargets=computed(()=>[...new Set(props.account.categories.filter(c=>c.scope===scope.value&&c.name!==source.value).map(c=>c.name))])
watch(kind,()=>{if(!submission.value)target.value=''},{flush:'sync'})
watch(source,()=>{if(kind.value==='merge'&&target.value===source.value&&!submission.value)target.value=''},{flush:'sync'})
const changes=computed(()=>(props.account.category_changes??[]).filter(c=>c.scope===scope.value))
const title=computed(()=>props.mode==='batch'?'批量修改分类':props.mode==='requests'?'分类更正待办':'分类管理')
watch(context,()=>{epoch++;preview.value=undefined;submission.value=undefined;decisions.value={};notice.value='';emit('close')},{flush:'sync'})
watch(scope,()=>{source.value='';target.value='';reason.value='';reasonOpen.value=false;historyOpen.value=false;resetKey.value++})
watch([scope,kind,source,target],()=>{if(!submission.value)preview.value=undefined;notice.value=''},{flush:'sync'})
onUnmounted(()=>{epoch++})
function contextValue(){return{family:props.account.family??null,membership_version:props.account.membership_version??0,source:props.account.source}}
async function current(expected:string){const latest=await props.store.readProjection(props.account.user.id,props.account.source!);if(categoryContext(latest)!==expected)throw new FamilyChanged();return latest}
async function snapshot(expected:string,attempt:number){
  const id=props.account.user.id,latest=await current(expected),result=await api.snapshot(id,latest.source!)
  if(attempt!==epoch)throw new AccountChanged()
  await current(expected)
  await props.store.snapshot(id,result,latest.restore_id??'')
  emit('changed')
  return props.store.readProjection(id,latest.source!)
}
function errorMessage(error:unknown):string{
  if(error instanceof ApiError){
    if(error.status===401||error.code==='account_changed'){emit('unauthorized');return '登录状态已变化，请恢复原账号'}
    if(['restore_changed','family_changed'].includes(error.code)){emit('refresh');return '账本上下文已变化，请刷新后重新操作'}
    const messages:Record<string,string>={category_exists:'目标分类已存在；要迁移全部源记录并删除源分类，请选择“合并分类”',category_deleted:'此分类已删除，请刷新分类目录；新增同名分类须明确确认',category_not_found:'分类已不存在，请刷新目录',category_no_changes:'没有需要修改的记录或分类',category_change_stale:'记录或分类已变化，整批未修改；请重新预览',category_owner_unavailable:'存在已离组的记录者，不能代为审批；本次未修改',category_change_decided:'本批次已处理，请刷新核对结果',category_preview_used:'此预览已提交，请刷新核对原申请',decision_id_reused:'此决定编号已用于另一决定，请刷新核对结果'}
    if(messages[error.code])return messages[error.code]!
    if(error.status===404){emit('refresh');return '记录或申请已不可访问，请刷新'}
    return '内容未被接受，请核对当前账本后重试'
  }
  return error instanceof TypeError?'网络未确认结果；原编号与内容已保留，请重试':error instanceof Error?error.message:'操作未完成，请重试'
}
async function run(body:(expected:string,attempt:number)=>Promise<void>){
  if(busy.value)return
  if(!navigator.onLine){notice.value='分类管理与审批需要联网，尚未提交';return}
  busy.value=true;notice.value=''
  const expected=context.value,attempt=epoch,id=props.account.user.id
  try{await props.store.lock(`${props.store.name}:sync:${id}`,async()=>{await current(expected);if(attempt!==epoch)throw new AccountChanged();await body(expected,attempt)})}
  catch(error){if(attempt===epoch){notice.value=errorMessage(error);if(error instanceof ApiError&&['category_change_stale','category_deleted'].includes(error.code)){preview.value=undefined;submission.value=undefined;emit('refresh')}}}
  finally{if(attempt===epoch)busy.value=false}
}
async function getPreview(){
  await run(async(expected,attempt)=>{
    const latest=await current(expected)
    if(latest.queue.some(p=>!p.frozen)){throw new Error('请先同步或处理本机账目变更，再预览分类操作')}
    let input:CategoryChangeInput
    if(props.mode==='batch'){
      const ids=props.selectedIds??[]
      if(!ids.length||eligibleSelection(latest,scope.value,ids).length!==ids.length)throw new Error('所选记录已变化或尚未同步，请返回明细重新选择')
      input={kind:'reclassify',scope:scope.value,target_name:target.value,expense_ids:ids}
    }else{
      if(!source.value)throw new Error('请选择要管理的分类')
      const name=target.value.trim()
      if(kind.value==='rename'&&(!name||[...name].length>24))throw new Error('新名称应为1～24个字')
      if(kind.value==='merge'&&(!mergeTargets.value.includes(name)||name===source.value))throw new Error('请选择不同于源分类的已有目标分类')
      input={kind:kind.value==='merge'?'rename':kind.value,scope:scope.value,source_name:source.value,target_name:kind.value==='delete'?'':name,...(kind.value==='merge'?{merge:true}:{})}
    }
    const result=await api.previewCategories(latest.user.id,input,latest.restore_id??'',contextValue())
    await current(expected);if(attempt!==epoch)return
    preview.value=result;submission.value=undefined;reasonOpen.value=false
  })
}
async function submit(){
  const change=preview.value;if(!change)return
  await run(async(expected,attempt)=>{
    if([...reason.value.trim()].length>500)throw new Error('申请原因最多500字')
    const latest=await current(expected)
    if(latest.queue.some(p=>!p.frozen))throw new Error('请先同步本机账目变更，再重新预览')
    const input=submission.value??{id:crypto.randomUUID(),preview_id:change.preview_id,reason:reason.value.trim()}
    submission.value=input
    await api.submitCategories(latest.user.id,input,latest.restore_id??'',contextValue())
    const next=await snapshot(expected,attempt);if(attempt!==epoch)return
    const result=next.category_changes?.find(c=>c.id===input.id)
    preview.value=undefined;submission.value=undefined;reason.value='';reasonOpen.value=false
    if(change.kind==='rename' && (result?.status==='applied'||result?.status==='pending') && target.value.trim()===change.target_name)target.value=''
    emit('submitted')
    notice.value=result?categoryStatus(result.status):'账本或记录权限已变化，申请内容已撤回'
  })
}
async function decide(change:CategoryChange,decision:'accept'|'reject',rejectionReason=''){
  if(!change.id)return
  const previous=decisions.value[change.id]
  if(previous&&previous.decision!==decision){notice.value='上次决定结果未知，请先重试原决定或刷新确认';return}
  await run(async(expected,attempt)=>{
    const latest=await current(expected),id=change.id!
    const live=latest.category_changes?.find(c=>c.id===id)
    if(!live||live.status!=='pending'||!live.approvals.some(a=>a.owner_id===latest.user.id&&a.status==='pending'))throw new Error('申请或审批权限已变化，请刷新更正待办')
    const input=freezeDecision(previous,decision,rejectionReason);decisions.value[id]=input
    await api.decideCategories(latest.user.id,id,input.id,input.decision,latest.restore_id??'',contextValue(),input.rejection_reason)
    const next=await snapshot(expected,attempt);if(attempt!==epoch)return
    const result=next.category_changes?.find(c=>c.id===id)
    notice.value=result?categoryStatus(result.status):'申请已不可访问，内容已撤回'
    delete decisions.value[id]
  })
}
async function refreshBoard(){
  await run(async(expected,attempt)=>{
    const next=await snapshot(expected,attempt);if(attempt!==epoch||categoryContext(next)!==expected)return
    const ids=(next.category_changes??[]).filter(c=>c.scope===scope.value&&c.status==='rejected'&&c.requester_id===next.user.id).map(c=>`category:${c.id}`)
    const saved=acknowledge(ids)
    notice.value=saved?'待办已刷新，已处理项已收起':'待办已刷新；本页已收起，浏览器未能保存此确认'
  })
}
</script>
<template>
  <div class="modal-backdrop sub-modal" @click.self="!busy && emit('close')"><section class="form-card category-panel" role="dialog" aria-modal="true" :aria-label="title">
    <div class="form-title"><div><h2>{{ title }}</h2><p class="form-scope">{{ scope==='shared'?`${account.family?.name??''} · 共同账`:'私人账 · 仅本人可见' }}</p></div><button class="icon-button" aria-label="关闭分类管理" :disabled="busy" @click="emit('close')"><Icon name="close"/></button></div>
    <p class="form-note">分类操作只改分类，保留金额及原始支出。{{ mode==='batch'?'仅修改所选记录，不包含未选中的支出。':'管理范围包含当前账本所有月份，不受明细筛选限制。' }}操作与审批需要联网。</p>
    <form v-if="mode!=='requests'" class="stack category-manage-form" @submit.prevent="getPreview">
      <p class="step-caption">01 · 选择分类调整</p><fieldset class="request-fields" :disabled="busy || !!submission">
        <template v-if="mode!=='batch'"><label>操作<select v-model="kind"><option value="rename">改名分类</option><option value="merge">合并分类</option><option value="delete">删除分类</option></select></label><label>原分类<select v-model="source"><option value="">请选择</option><option v-for="name in names" :key="name">{{ name }}</option></select></label></template>
        <p v-else class="form-note">当前{{ scope==='shared'?'共同账':'私人账' }} · 已选 {{ selectedIds?.length??0 }} 笔，仅修改选中的记录。</p>
        <label v-if="mode==='batch'">改为分类<select v-model="target"><option value="">未分类</option><option v-for="name in names" :key="name">{{ name }}</option></select></label>
        <label v-else-if="kind==='merge'">合并到已有分类<select v-model="target" aria-label="合并到已有分类"><option value="">请选择</option><option v-for="name in mergeTargets" :key="name">{{ name }}</option></select></label><label v-else-if="kind==='rename'">新分类名称<input v-model="target" maxlength="24" placeholder="输入新名称"></label>
        <p v-if="kind==='merge' && mode!=='batch'" class="form-note">全部源分类记录迁移到已有目标后删除源分类，目标原记录保持。下一步先显示影响笔数。</p><p v-if="kind==='delete' && mode!=='batch'" class="form-note">删除后原记录统一改为未分类，支出保留。下一步会先显示影响笔数。</p>
      </fieldset>
      <button v-if="!submission" class="subtle" :disabled="busy">{{ busy?'读取影响中…':'预览影响范围' }}</button>
    </form>
    <section v-if="preview" class="category-preview" aria-label="分类影响预览"><p class="step-caption">02 · 核对影响并提交</p><h3>{{ categoryAction(preview) }}</h3><p class="preview-count">影响总数 {{ preview.total_count }} 笔</p><div class="category-impact"><div><span>本人记录</span><strong>{{ preview.own_count }} 笔</strong><small>直接变更，无需申请</small></div><div v-for="owner in preview.other_owners" :key="owner.owner_id"><span>{{ owner.display_name }}的记录</span><strong>{{ owner.count }} 笔</strong><small>合并为一张申请，等待本人审批</small></div></div><p class="form-note">{{ preview.merge?'全部相关记录者批准且原账未变化后，统一迁移并删除源分类；任一拒绝或变化，整批不执行。':preview.other_owners.length?'全部相关记录者批准且原记录未变化后，统一执行；任一拒绝或变化，全部不改。':'确认后统一执行。' }}</p><button class="text-button" :aria-expanded="reasonOpen" @click="reasonOpen=!reasonOpen">{{ reasonOpen?'收起原因':reason.trim()?'查看/修改原因（已保留）':'添加原因' }}</button><label v-if="reasonOpen">原因（选填）<textarea v-model="reason" rows="2" maxlength="500" :disabled="busy || !!submission"></textarea></label><p v-if="submission" class="form-note">已提交过此内容。结果未确认时，重试沿用原编号；请先刷新核对再修改方案。</p><button class="primary" :disabled="busy" @click="submit">{{ busy?'正在提交…':submission?'重试原提交':preview.merge?`申请合并并删除${preview.source_name}`:preview.kind==='delete'?`确认删除分类 · ${preview.total_count} 笔改为未分类`:'确认并提交分类更正' }}</button></section>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <details class="category-history settings-disclosure"><summary>分类更正记录 <small>{{ changes.length }} 项</small></summary><RequestBoardToolbar :busy="busy" :history-open="historyOpen" :history-count="changes.filter(c=>c.status!=='pending').length" @refresh="refreshBoard" @toggle-history="historyOpen=!historyOpen"/>
<p v-if="!changes.length" class="form-note">暂无分类更正申请</p>
    <CategoryChangeCards ref="changeCards" :history-open="historyOpen" :reset-key="resetKey" :acknowledged="acknowledged" :decisions="decisions" :context-key="`${context}:${scope}`" :changes="changes" :expenses="account.local" :user-id="account.user.id" :busy="busy" @decision="decide"/></details>
  </section></div>
</template>
