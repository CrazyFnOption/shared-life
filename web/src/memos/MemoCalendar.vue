<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { adjacentMonth, dayLabel, localDay, monthGrid, scheduleOnDay } from './dates'
import { trapDialogTab } from './dialog'
import type { ScheduleView } from './types'
const props = defineProps<{ selected: string; schedules: ScheduleView[]; loading?:boolean; error?:string }>()
const emit = defineEmits<{ select: [day: string]; close: []; month:[month:string] }>()
const month = ref(props.selected.slice(0, 7))
const close = ref<HTMLButtonElement>()
watch(month,value=>emit('month',value),{immediate:true})
const days = computed(() => monthGrid(month.value))
const today = localDay()
function enabled(day: string) { return day === today || day > today && props.schedules.some(record => scheduleOnDay(record, day)) }
onMounted(() => close.value?.focus())
</script>
<template>
  <div class="memo-overlay" @click.self="emit('close')" @keydown.esc.stop="emit('close')">
    <section class="memo-modal" role="dialog" aria-modal="true" aria-label="选择日程日期" @keydown="trapDialogTab">
      <header class="memo-modal-head"><h3>日程月历</h3><button ref="close" type="button" class="memo-text-button" @click="emit('close')">关闭</button></header>
      <div class="memo-month"><button type="button" class="memo-icon-button" aria-label="上个月" @click="month = adjacentMonth(month, -1)">‹</button><strong>{{ month.replace('-', ' 年 ') }} 月</strong><button type="button" class="memo-icon-button" aria-label="下个月" @click="month = adjacentMonth(month, 1)">›</button></div>
      <p v-if="loading" class="memo-muted" role="status">正在核对这个月的日程…</p><p v-if="error" class="memo-error" role="alert">{{ error }}</p>
      <div class="memo-week" aria-hidden="true"><span v-for="day in ['一','二','三','四','五','六','日']" :key="day">{{ day }}</span></div>
      <div class="memo-days"><template v-for="(day, index) in days" :key="day ?? `blank-${index}`"><button v-if="day" type="button" class="memo-day" :class="{ has: schedules.some(record => scheduleOnDay(record, day)) }" :aria-label="dayLabel(day)" :aria-pressed="selected === day" :aria-current="day===today?'date':undefined" :disabled="!enabled(day)" @click="emit('select', day)">{{ Number(day.slice(-2)) }}</button><span v-else /></template></div>
      <button type="button" class="memo-text-button" @click="emit('select', today)">回到今天</button>
    </section>
  </div>
</template>
