<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import MemoAttachments from './MemoAttachments.vue'
const MarkdownEditor = defineAsyncComponent(() => import('../MarkdownEditor.vue'))
import type { User } from '../types'
import { localDay } from './dates'
import { copyDraft } from './types'
import type { MemoAttachmentView, MemoCategoryView, MemoDraft } from './types'

const props = withDefaults(defineProps<{ initial: MemoDraft; lockedKind?: 'memo'|'schedule'; categories: MemoCategoryView[]; saving?: boolean; error?: string; scheduleEnabled?: boolean; focusEnabled?: boolean; members?: User[]; currentUserId?: string }>(), { saving: false, error: '', scheduleEnabled: false, focusEnabled: false, members: () => [], currentUserId: '' })
const emit = defineEmits<{ save: [draft: MemoDraft]; cancel: []; change: [draft: MemoDraft]; 'create-category': []; 'request-attachment': [file: MemoAttachmentView] }>()
const draft = ref(copyDraft(props.initial))
draft.value.reminders??=[]
draft.value.recurrence??=null
const remindersOpen = ref(false)
const repeatOpen = ref(false)
const localError = ref('')
const composing = ref(false)
const times = Array.from({ length: 48 }, (_, index) => `${String(Math.floor(index / 2)).padStart(2, '0')}:${index % 2 ? '30' : '00'}`)
const isSchedule = computed(() => draft.value.kind === 'schedule')
watch(draft, value => { localError.value = ''; emit('change', copyDraft(value)) }, { deep: true })
watch(()=>draft.value.start_date,day=>{if(draft.value.kind==='schedule'&&draft.value.recurrence)draft.value.recurrence.start_date=day})
watch(() => props.categories, categories => {
  if (!draft.value.category_id && categories[0]) draft.value.category_id = categories[0].id
})
function addReminder(){draft.value.reminders!.push({id:crypto.randomUUID(),mode:isSchedule.value?'relative':'absolute',at:null,offset_minutes:0,recipient_ids:props.currentUserId?[props.currentUserId]:[],all_members:false})}
function localTime(value:string|null){if(!value)return '';const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';return `${localDay(date)}T${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`}
function absoluteTime(event:Event){const value=(event.target as HTMLInputElement).value;return value?new Date(value).toISOString():null}
function toggleRepeat(){draft.value.recurrence=draft.value.recurrence?null:{frequency:'yearly',interval:1,calendar:'solar',start_date:draft.value.start_date||localDay(),until:null,lunar_month:1,lunar_day:1,lunar_leap:false}}
function save() {
  if (props.saving || composing.value) return
  if (!draft.value.title.trim()) { localError.value = '请填写标题。'; return }
  if (!isSchedule.value && !props.categories.some(category => category.id === draft.value.category_id)) { localError.value = '请选择备忘分类，或先新建分类。'; return }
  if (isSchedule.value) {
    if (!draft.value.start_date) { localError.value = '请选择开始日期。'; return }
    if (!draft.value.all_day && !times.includes(draft.value.start_time)) { localError.value = '请选择半小时精度的开始时间。'; return }
    if (draft.value.has_end && (!draft.value.end_date || draft.value.end_date < draft.value.start_date || !draft.value.all_day && (draft.value.end_date === draft.value.start_date && draft.value.end_time <= draft.value.start_time || !times.includes(draft.value.end_time)))) { localError.value = '结束时间须晚于开始时间。全天日程可在同一天结束。'; return }
  }
  if(draft.value.reminders?.some(r=>r.mode==='absolute'&&!r.at||!r.all_members&&!r.recipient_ids.length||r.mode==='relative'&&!isSchedule.value)){localError.value='请填写提醒时间并选择接收人；相对提醒仅适用于日程。';return}
  if(draft.value.recurrence&&(draft.value.recurrence.interval<1||draft.value.recurrence.interval>99||!draft.value.recurrence.start_date||draft.value.recurrence.until&&draft.value.recurrence.until<draft.value.recurrence.start_date)){localError.value='请核对重复的间隔、开始和结束日期。';return}
  emit('save', { ...copyDraft(draft.value), title: draft.value.title.trim() })
}
function addFiles(files: MemoAttachmentView[]) { draft.value.attachments.push(...files) }
</script>

<template>
  <form class="memo-composer" :aria-busy="saving" @submit.prevent="save" @compositionstart="composing = true" @compositionend="composing = false">
    <fieldset v-if="!draft.id && scheduleEnabled && !lockedKind" class="memo-type-tabs" :disabled="saving"><legend>用途</legend><label><input v-model="draft.kind" type="radio" value="memo">备忘</label><label><input v-model="draft.kind" type="radio" value="schedule">日程</label></fieldset>
    <section class="memo-compose-sheet" aria-label="记录正文">
      <input v-model="draft.title" class="memo-title-input" aria-label="标题" placeholder="标题" :disabled="saving" @keydown.enter.prevent>
      <div class="memo-body-head"><span>正文</span><span>Markdown</span></div>
      <MarkdownEditor v-model="draft.body" :disabled="saving" @composition-state="composing=$event" />
    </section>
    <section class="memo-settings" aria-label="记录设置">
      <template v-if="!isSchedule"><h3>备忘设置</h3><div class="memo-setting-group"><div class="memo-field"><div class="memo-field-head"><label for="memo-category">分类</label><button type="button" class="memo-text-button" :disabled="saving" @click="emit('create-category')">＋ 新建分类</button></div><select id="memo-category" v-model="draft.category_id" :disabled="saving"><option v-if="!categories.length" value="">请先新建分类</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></div><label v-if="focusEnabled" class="memo-option">加入待办事项<input v-model="draft.focused" type="checkbox" :disabled="saving"></label></div></template>
      <template v-else><h3>日程时间</h3><div class="memo-setting-group"><label class="memo-option">全天<input v-model="draft.all_day" type="checkbox" :disabled="saving"></label><div class="memo-date-time"><label class="memo-field">开始日期<input v-model="draft.start_date" type="date" max="2099-12-31" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">开始时间<select aria-label="开始时间" v-model="draft.start_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div><button type="button" class="memo-text-button" :aria-expanded="draft.has_end" :disabled="saving" @click="draft.has_end = !draft.has_end">{{ draft.has_end ? '移除结束' : '添加结束（选填）' }}</button><div v-if="draft.has_end" class="memo-date-time"><label class="memo-field">结束日期<input v-model="draft.end_date" type="date" max="2099-12-31" :min="draft.start_date" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">结束时间<select aria-label="结束时间" v-model="draft.end_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div></div></template>
    </section>
    <details class="memo-settings memo-disclosure" :open="remindersOpen" @toggle="remindersOpen=($event.target as HTMLDetailsElement).open"><summary><span>提醒</span><small>{{ draft.reminders?.length ? `${draft.reminders.length} 个提醒` : '不提醒' }}</small></summary><section aria-label="提醒设置"><div class="memo-section-head"><h3>提醒</h3><button type="button" class="memo-text-button" :disabled="saving" @click="addReminder">＋ 添加提醒</button></div><p v-if="!draft.reminders?.length" class="memo-muted">不提醒；需要时可添加多个提醒。</p><fieldset v-for="(reminder,index) in draft.reminders" :key="reminder.id" class="memo-setting-group memo-reminder" :disabled="saving"><legend>提醒 {{ index+1 }}</legend><div class="memo-section-head"><span class="memo-muted">提醒 {{ index+1 }}</span><button type="button" class="memo-text-button" @click="draft.reminders = draft.reminders?.filter(r=>r.id!==reminder.id)">移除</button></div><label v-if="isSchedule" class="memo-field">提醒方式<select aria-label="提醒方式" v-model="reminder.mode" @change="reminder.mode==='relative' && (reminder.at=null)"><option value="relative">按日程开始时间</option><option value="absolute">指定时间</option></select></label><label v-if="reminder.mode==='absolute'" class="memo-field">提醒时间<input type="datetime-local" :value="localTime(reminder.at)" @input="reminder.at=absoluteTime($event)"></label><label v-else class="memo-field">提前多久<select aria-label="提前多久" v-model.number="reminder.offset_minutes"><option :value="0">开始时</option><option :value="30">提前半小时</option><option :value="60">提前1小时</option><option :value="1440">提前1天</option><option :value="10080">提前1周</option></select></label><label v-if="members.length>1" class="memo-option">家庭全员<input v-model="reminder.all_members" type="checkbox"></label><div v-if="!reminder.all_members" class="memo-recipients"><label v-for="member in members" :key="member.id" class="memo-option"><input v-model="reminder.recipient_ids" type="checkbox" :value="member.id">{{ member.display_name }}</label></div></fieldset></section></details>
    <details class="memo-settings memo-disclosure" :open="repeatOpen" @toggle="repeatOpen=($event.target as HTMLDetailsElement).open"><summary><span>重复</span><small>{{ draft.recurrence ? `${draft.recurrence.calendar==='lunar'?'农历':'公历'} · 每 ${draft.recurrence.interval} ${({daily:'天',weekly:'周',monthly:'月',yearly:'年'})[draft.recurrence.frequency]}` : '不重复' }}</small></summary><section aria-label="重复设置"><h3>重复</h3><div class="memo-setting-group"><label class="memo-option">固定日期重复<input type="checkbox" :checked="!!draft.recurrence" :disabled="saving" @change="toggleRepeat"></label><template v-if="draft.recurrence"><label class="memo-field">历法<select aria-label="历法" v-model="draft.recurrence.calendar" :disabled="saving" @change="draft.recurrence.calendar==='lunar' && (draft.recurrence.frequency='yearly')"><option value="solar">公历</option><option value="lunar">农历 · 每年</option></select></label><label class="memo-field">重复周期<select aria-label="重复周期" v-model="draft.recurrence.frequency" :disabled="saving||draft.recurrence.calendar==='lunar'"><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option><option value="yearly">每年</option></select></label><label class="memo-field">间隔周期数<input v-model.number="draft.recurrence.interval" type="number" min="1" max="99" :disabled="saving"></label><div class="memo-date-time"><label class="memo-field">开始日期<input v-model="draft.recurrence.start_date" type="date" max="2099-12-31" :disabled="saving"></label><label class="memo-field">结束日期（选填）<input v-model="draft.recurrence.until" type="date" max="2099-12-31" :min="draft.recurrence.start_date" :disabled="saving"></label></div><template v-if="draft.recurrence.calendar==='lunar'"><div class="memo-date-time"><label class="memo-field">农历月<select aria-label="农历月" v-model.number="draft.recurrence.lunar_month" :disabled="saving"><option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option></select></label><label class="memo-field">农历日<select aria-label="农历日" v-model.number="draft.recurrence.lunar_day" :disabled="saving"><option v-for="day in 30" :key="day" :value="day">{{ day }} 日</option></select></label></div><label class="memo-option">闰月<input v-model="draft.recurrence.lunar_leap" type="checkbox" :disabled="saving"></label></template><p class="memo-muted">按固定日期重复；晚完成不会顺延。缺日期取月末，无对应闰月按普通同名月。</p></template></div></section></details>
    <MemoAttachments :files="draft.attachments" editable :disabled="saving" @add="addFiles" @remove="id => draft.attachments = draft.attachments.filter(file => file.id !== id)" @request="emit('request-attachment', $event)" />
    <p v-if="localError || error" class="memo-error" role="alert">{{ localError || error }}</p>
    <div class="memo-form-actions"><button type="button" class="memo-text-button" :disabled="saving" @click="emit('cancel')">取消</button><button type="submit" class="memo-primary" :disabled="saving">{{ saving ? '正在保存…' : draft.id ? '保存修改' : '保存记录' }}</button></div>
  </form>
</template>
