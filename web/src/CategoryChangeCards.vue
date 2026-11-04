<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import DecisionButtons from './DecisionButtons.vue'
import { needsAttention, type DecisionAttempt } from './requestBoard'
import { categoryAction, categoryStatus, categoryChangePage } from './categoryChanges'
import type { CategoryChange, Expense } from './types'
const props=defineProps<{changes:CategoryChange[];userId:string;busy:boolean;expenses:Expense[];contextKey:string;historyOpen:boolean;acknowledged:string[];decisions:Record<string,DecisionAttempt>;resetKey:number}>()
const emit=defineEmits<{decision:[change:CategoryChange,decision:'accept'|'reject',reason:string]}>()
const decisionDrafts=ref<{hasUnsavedChanges:boolean}[]>([])
const hasUnsavedChanges=computed(()=>decisionDrafts.value.some(item=>item.hasUnsavedChanges))
defineExpose({hasUnsavedChanges})
const pendingPage=ref(1),historyPage=ref(1),rejectedPage=ref(1)
const pending=computed(()=>categoryChangePage(props.changes,true,pendingPage.value))
const rejected=computed(()=>categoryChangePage(props.changes.filter(c=>c.status==='rejected'&&needsAttention(c,props.userId,props.acknowledged,'category')),false,rejectedPage.value))
const history=computed(()=>categoryChangePage(props.changes,false,historyPage.value))
const groups=computed(()=>[{name:'待处理分类申请',data:pending.value,page:pendingPage,visible:true},{name:'被拒绝的分类申请',data:rejected.value,page:rejectedPage,visible:!props.historyOpen},{name:'历史分类更正记录',data:history.value,page:historyPage,visible:props.historyOpen}])
watch([()=>props.contextKey,()=>props.resetKey],()=>{pendingPage.value=1;historyPage.value=1;rejectedPage.value=1},{flush:'sync'})
watch(()=>pending.value.pages,n=>{pendingPage.value=Math.min(pendingPage.value,n)})
watch(()=>rejected.value.pages,n=>{rejectedPage.value=Math.min(rejectedPage.value,n)})
watch(()=>history.value.pages,n=>{historyPage.value=Math.min(historyPage.value,n)})
function canDecide(change:CategoryChange){return change.status==='pending'&&change.approvals.some(a=>a.owner_id===props.userId&&a.status==='pending')}
</script>
<template>
  <template v-for="group in groups" :key="group.name"><section v-if="group.visible && group.data.total" :aria-label="group.name"><h3>{{ group.name }}（{{ group.data.total }}）</h3>
  <article v-for="change in group.data.items" :key="change.id??change.preview_id" class="request-item category-change-card">
    <p class="eyebrow">{{ change.requester_id===userId?'发出的分类申请':'收到的分类申请' }} · {{ change.scope==='shared'?'共同账':'私人账' }} · {{ categoryStatus(change.status) }}</p>
    <h3>{{ categoryAction(change) }}</h3>
    <p class="category-batch-summary">共 {{ change.total_count }} 笔 <span>· 发起人 {{ change.own_count }} 笔自动同意</span></p>
    <div class="category-approval-row" v-for="owner in change.other_owners" :key="owner.owner_id"><span>{{ owner.display_name }} · {{ owner.count }} 笔</span><strong>{{ {pending:'待审批',accepted:'已同意',rejected:'已拒绝'}[change.approvals.find(a=>a.owner_id===owner.owner_id)?.status??'pending'] }}</strong></div>
    <p v-if="change.reason">申请原因：{{ change.reason }}</p><p v-if="change.status==='rejected'" class="rejection-reason">拒绝原因：{{ change.rejection_reason || '未填写拒绝原因' }}</p>
    <details v-if="change.items.length"><summary>查看{{ change.requester_id===userId?'本批次':'本人' }}记录（{{ change.items.length }} 笔）</summary><ul class="category-item-list"><li v-for="item in change.items" :key="item.id"><strong>{{ expenses.find(e=>e.id===item.id)?.purpose??'记录已不可见' }}</strong><small>申请时分类：{{ item.category||'未分类' }} · 版本 {{ item.base_version }}</small></li></ul></details>
    <p v-if="change.status==='pending'" class="form-note">全部相关记录者同意、原记录未变化后统一执行。任一拒绝或变化，整批不改。</p>
    <DecisionButtons ref="decisionDrafts" v-if="canDecide(change)" :busy="busy" :frozen="decisions[change.id!]" :context-key="contextKey" :accept-label="`同意本人 ${change.approvals.find(a=>a.owner_id===userId)?.count} 笔`" reject-label="拒绝整批" @decision="(decision,reason)=>emit('decision',change,decision,reason)"/>
  </article>
  <div v-if="group.data.pages>1" class="pagination"><button class="subtle" :disabled="group.data.page===1" @click="group.page.value--">上一页</button><span>{{ group.data.page }} / {{ group.data.pages }}</span><button class="subtle" :disabled="group.data.page===group.data.pages" @click="group.page.value++">下一页</button></div>
  </section></template>
</template>
