<script setup lang="ts">
import { scheduleLabel } from './dates'
import type { MemoRecordView } from './types'
defineProps<{ record: MemoRecordView; category?: string; day?: string; writable: boolean; busy?: boolean }>()
const emit = defineEmits<{ open: []; complete: [] }>()
</script>
<template>
  <div class="memo-row" :class="{ 'is-complete': record.completed }">
    <button type="button" class="memo-row-open" :aria-label="`${record.title}${category ? '，' + category : ''}，${record.creator_name}记录`" @click="emit('open')">
      <span v-if="record.kind === 'schedule' && (day || record.calendar_projection)" class="memo-time">{{ scheduleLabel(record, day) }}</span>
      <span class="memo-row-left"><strong :title="record.title">{{ record.title }}</strong><span v-if="category" class="memo-category-tag" :title="category">{{ category }}</span></span>
      <span v-if="record.archived" class="memo-category-tag">{{ record.completed?'已完成':'已归档' }}</span><small class="memo-creator" :title="record.creator_name">{{ record.creator_name }}</small>
    </button>
    <button v-if="!record.completed && !record.archived && writable" type="button" class="memo-complete" :disabled="busy" :aria-label="`完成 ${record.title}`" @click="emit('complete')">已完成</button>
  </div>
</template>
