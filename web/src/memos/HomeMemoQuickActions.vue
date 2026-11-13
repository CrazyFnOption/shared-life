<script setup lang="ts">
import { computed } from 'vue'
import { occurrenceLabel } from './reminderSlots'
import MemoRecordRow from './MemoRecordRow.vue'
import { homeFocusedRecords } from './view'
import type { MemoCategoryView, MemoRecordView } from './types'
const props=defineProps<{records:MemoRecordView[];categories:MemoCategoryView[];writable:boolean;busy:boolean;scopeLabel:string}>()
const emit=defineEmits<{create:[];open:[record:MemoRecordView];complete:[record:MemoRecordView];expense:[]}>()
const focused=computed(()=>homeFocusedRecords(props.records))
const key=(item:MemoRecordView)=>`${item.kind==='memo'&&item.origin==='plan_task'?'plan_task':'memo'}:${item.id}`
function category(item:MemoRecordView){return item.kind==='memo'&&item.origin==='plan_task'?'计划任务':(props.categories.find(c=>c.id===item.category_id)?.name??'未分类')+(item.recurrence&&item.current_occurrence_at?' · '+occurrenceLabel(item.current_occurrence_at):'')}
</script>
<template>
  <section class="home-quick" aria-label="首页快捷操作">
    <div class="home-focus-heading"><h2>待办事项</h2><button type="button" class="memo-text-button" :disabled="!writable||busy" @click="emit('create')">＋ 新建备忘</button></div>
    <div v-if="focused.length" class="home-focus-list" aria-label="待办事项，上下滑动查看更多" tabindex="0">
      <div v-for="item in focused" :key="key(item)" class="home-focus-item"><MemoRecordRow :record="item" :category="category(item)" :writable="writable" :busy="busy" @open="emit('open',item)" @complete="emit('complete',item)" /></div>
    </div>
    <p v-else class="home-focus-empty">把想跟进的事记在这里。</p>
    <button type="button" class="home-quick-expense" :disabled="!writable||busy" @click="emit('expense')"><span class="home-quick-plus" aria-hidden="true">＋</span><span><strong>快速记账</strong><small>{{ scopeLabel }}</small></span><span aria-hidden="true">›</span></button>
  </section>
</template>
<style scoped>
.home-quick{--memo-ink:var(--sl-ink);--memo-muted:var(--sl-muted);--memo-accent:var(--sl-brand);--memo-line:var(--sl-line);margin:0 0 32px;min-width:0}.home-focus-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}.home-focus-heading h2{font-size:18px;margin:0;letter-spacing:-.02em}.home-focus-heading .memo-text-button{padding:8px 0 8px 12px;min-height:44px}.home-focus-list{max-height:208px;overflow-y:auto;overscroll-behavior-y:contain;touch-action:pan-y;scrollbar-width:thin;scrollbar-color:var(--sl-line) transparent;border:1px solid var(--sl-line);border-radius:16px;background:var(--sl-paper);outline-offset:4px}.home-focus-list .home-focus-item+.home-focus-item{border-top:1px solid var(--sl-line)}.home-focus-list :deep(.memo-row){display:flex;align-items:center;gap:8px;padding:0 12px;min-width:0}.home-focus-list :deep(.memo-row-open){display:flex;align-items:center;gap:8px;flex:1;min-width:0;min-height:68px;padding:12px 0;background:none;border:0;text-align:left;color:var(--sl-ink)}.home-focus-list :deep(.memo-row-left){display:flex;min-width:0;flex:1;flex-direction:column;align-items:flex-start;gap:4px}.home-focus-list :deep(.memo-row-left strong){font-size:14px;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}.home-focus-list :deep(.memo-category-tag){font-size:11px;color:var(--sl-muted)}.home-focus-list :deep(.memo-creator){font-size:11px;max-width:20%;color:var(--sl-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.home-focus-list :deep(.memo-complete){font-size:12px;border:1px solid var(--sl-line);background:transparent;border-radius:10px;padding:0 8px;min-height:44px;white-space:nowrap;color:var(--sl-brand)}.home-focus-list :deep(.memo-time){display:none}.home-focus-empty{color:var(--sl-muted);font-size:14px;margin:0;padding:16px 0 20px}.home-quick-expense{width:100%;display:flex;align-items:center;gap:12px;padding:16px;margin-top:12px;border:1px solid var(--sl-line);border-radius:16px;background:var(--sl-paper);text-align:left;color:var(--sl-ink)}.home-quick-expense>span:nth-child(2){display:grid;gap:4px;flex:1}.home-quick-expense strong{font-size:15px;font-weight:600}.home-quick-expense small{font-size:12px;color:var(--sl-muted)}.home-quick-plus{display:grid;place-items:center;color:var(--sl-brand);font-size:24px;width:36px;height:36px;background:var(--sl-brand-soft);border-radius:12px}.home-quick-expense:hover{border-color:var(--sl-brand)}.home-quick-expense:active{transform:scale(.99)}
</style>
