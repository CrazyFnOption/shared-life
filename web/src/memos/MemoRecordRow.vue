<script setup lang="ts">
import { checklistView } from './domain'
import { scheduleLabel } from './dates'
import { occurrenceLabel } from './reminderSlots'
import type { MemoRecordView } from './types'
defineProps<{ record: MemoRecordView; home?: boolean; category?: string; day?: string; writable: boolean; busy?: boolean }>()
const emit = defineEmits<{ open: []; complete: [] }>()
</script>
<template>
  <div class="memo-row" :class="{ 'is-complete': record.completed }">
    <button type="button" class="memo-row-open" :aria-label="`${record.title}${category ? '，' + category : ''}，${record.creator_name}记录`" @click="emit('open')">
      <span v-if="record.kind === 'schedule' && (day || record.calendar_projection)" class="memo-time">{{ scheduleLabel(record, day) }}</span>
      <span class="memo-row-left"><strong :title="record.title">{{ record.title }}</strong><template v-if="!home"><small v-for="delay in record.snoozed_occurrences?.filter(delay=>!record.occurrence_at||delay.occurrence_at===record.occurrence_at)" :key="delay.occurrence_at" class="memo-slot-time">{{ occurrenceLabel(delay.occurrence_at) }} · 已推后至 {{ occurrenceLabel(delay.delivery_at) }}</small></template><span v-if="category" class="memo-category-tag" :title="category">{{ category }}</span><span v-else-if="!day && !(record.kind==='schedule'&&record.calendar_projection) && (record.occurrence_at||record.recurrence&&record.current_occurrence_at)" class="memo-slot-time">{{ occurrenceLabel(record.occurrence_at??record.current_occurrence_at!) }}</span></span>
      <small v-if="!home && checklistView(record).items.length" class="memo-checklist-progress">{{ checklistView(record).count }}/{{ checklistView(record).items.length }}</small><span v-if="record.archived" class="memo-category-tag">{{ record.completed?'已完成':'已归档' }}</span><small class="memo-creator" :title="record.creator_name">{{ record.creator_name }}</small>
    </button>
    <button v-if="!record.completed && !record.archived && writable" type="button" class="memo-complete" :disabled="busy" :aria-label="`完成 ${record.title}${record.recurrence && (record.occurrence_at||record.current_occurrence_at)?`，${occurrenceLabel(record.occurrence_at??record.current_occurrence_at!)}`:''}`" @click="emit('complete')">{{ record.recurrence&&record.reminders?.length?'完成本次':'已完成' }}</button>
  </div>
</template>
