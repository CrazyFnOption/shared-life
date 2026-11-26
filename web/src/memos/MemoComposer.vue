<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import MemoAttachments from './MemoAttachments.vue'
import MemoTimeInput from './MemoTimeInput.vue'
const MarkdownEditor = defineAsyncComponent(() => import('../MarkdownEditor.vue'))
import type { User } from '../types'
import { localDay, simpleRepeatAnchor } from './dates'
import { bindTasks, mapTaskEdits } from './markdownChecklist'
import { copyDraft } from './types'
import { freezeQuickReminder, quickReminderAt, quickReminderLabel } from './quickReminders'
import type { MemoAttachmentView, MemoCategoryView, MemoDraft, MemoQuickReminder } from './types'

const props = withDefaults(defineProps<{ initial: MemoDraft; lockedKind?: 'memo'|'schedule'; categories: MemoCategoryView[]; saving?: boolean; error?: string; scheduleEnabled?: boolean; focusEnabled?: boolean; members?: User[]; currentUserId?: string }>(), { saving: false, error: '', scheduleEnabled: false, focusEnabled: false, members: () => [], currentUserId: '' })
const emit = defineEmits<{ save: [draft: MemoDraft]; cancel: []; change: [draft: MemoDraft]; 'create-category': []; 'request-attachment': [file: MemoAttachmentView] }>()
const draft = ref(copyDraft(props.initial))
const bodyTasks=ref(bindTasks(draft.value.body,draft.value.checklist_items??[]))
const knownTasks=[...bodyTasks.value]
const originalChecks=new Set(draft.value.checklist_checked_ids??[])
function bodyChanged(value:{body:string;changes:import('@codemirror/state').ChangeDesc}){bodyTasks.value=mapTaskEdits(bodyTasks.value,value.body,value.changes,knownTasks);for(const task of bodyTasks.value){const known=knownTasks.find(item=>item.id===task.id);if(known)Object.assign(known,task);else knownTasks.push(task)};draft.value.checklist_items=bodyTasks.value.map(({id,text})=>({id,text}))}
draft.value.reminders??=[]
draft.value.recurrence??=null
const remindersOpen = ref(Boolean(draft.value.quick_reminder))

const bodyOpen = ref(Boolean(draft.value.body.trim() || draft.value.attachments.length))
const localError = ref('')
const nativeComposing = ref(false)
const editorComposing = ref(false)
const composing = computed(() => nativeComposing.value || editorComposing.value)
const times = Array.from({ length: 48 }, (_, index) => `${String(Math.floor(index / 2)).padStart(2, '0')}:${index % 2 ? '30' : '00'}`)
const isSchedule = computed(() => draft.value.kind === 'schedule')
watch(draft, value => { localError.value = ''; emit('change', copyDraft(value)) }, { deep: true })
watch(()=>draft.value.start_date,day=>{if(draft.value.kind==='schedule'&&draft.value.recurrence)draft.value.recurrence.start_date=day})
const initialReminder=draft.value.reminders[0]
const reminderMode=ref<'none'|'once'|'daily'|'weekly'|'monthly'|'yearly'>(draft.value.recurrence?.frequency??(initialReminder?'once':'none'))
const initialAt=initialReminder?.mode==='relative'?new Date(Date.parse(`${draft.value.start_date}T${draft.value.all_day?'00:00':draft.value.start_time}:00+08:00`)-initialReminder.offset_minutes*60000).toISOString():initialReminder?.at
const referenceDay=initialAt?localTime(initialAt).slice(0,10):draft.value.recurrence?.start_date||localDay()
const notifyClock=ref(initialAt?localTime(initialAt).slice(11,16):draft.value.start_time||'09:00')
const initialWeekDay=new Date(`${referenceDay}T12:00:00Z`).getUTCDay()||7
const weekDays=ref(draft.value.recurrence?.weekdays?[...draft.value.recurrence.weekdays]:[initialWeekDay])
const monthDays=ref(draft.value.recurrence?.month_days?[...draft.value.recurrence.month_days]:[Number((draft.value.recurrence?.start_date??referenceDay).slice(-2))])
const clockRows=ref(draft.value.reminders.length?draft.value.reminders.map(reminder=>({id:reminder.id,clock:reminder.mode==='relative'?localTime(new Date(Date.parse(`${draft.value.start_date}T${draft.value.all_day?'00:00':draft.value.start_time}:00+08:00`)-reminder.offset_minutes*60000).toISOString()).slice(11,16):localTime(reminder.at).slice(11,16)})):[{id:crypto.randomUUID(),clock:notifyClock.value}])
const yearMonth=ref(Number(referenceDay.slice(5,7)))
const yearDay=ref(Number(referenceDay.slice(-2)))
const yearCalendar=ref<'solar'|'lunar'>(draft.value.recurrence?.calendar??'solar')
const lunarMonth=ref(draft.value.recurrence?.lunar_month??1),lunarDay=ref(draft.value.recurrence?.lunar_day??1),lunarLeap=ref(draft.value.recurrence?.lunar_leap??false)
const settingsTouched=ref(false)
const weekdays=['周一','周二','周三','周四','周五','周六','周日']
const today = ref(localTime(new Date().toISOString()).slice(0, 10))
const tomorrow = computed(() => localTime(new Date(Date.parse(`${today.value}T12:00:00+08:00`) + 86400000).toISOString()).slice(0, 10))
const delayMode = ref(Boolean(draft.value.quick_reminder))
const delayAmount = ref(draft.value.quick_reminder?.amount.toString() ?? '')
const delayUnit = ref<MemoQuickReminder['unit']>(draft.value.quick_reminder?.unit ?? 'minute')
const onceAt = computed(() => {
  const reminder = draft.value.reminders?.[0]
  return reminder?.mode === 'relative' ? initialAt ?? null : reminder?.at ?? null
})
const onceDay = computed(() => localTime(onceAt.value).slice(0, 10) || today.value)
const relativeDay = computed(() => onceDay.value === today.value ? '今天' : onceDay.value === tomorrow.value ? '明天' : '')

function pickCustomDay(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.value && input.value !== onceDay.value) onceTime(input.value)
  if (event.type === 'change') input.value = onceDay.value
}
function updateDelay() {
  const amount = Number(delayAmount.value)
  const choice = { amount, unit: delayUnit.value }
  try {
    quickReminderAt(choice, Date.now())
    draft.value.quick_reminder = choice
  } catch {
    delete draft.value.quick_reminder
  }
}
function changeTiming(after: boolean) {
  if (after === delayMode.value) return
  delayMode.value = after
  if (after) updateDelay()
  else {
    delete draft.value.quick_reminder
    if (!onceAt.value) startOnce()
  }
}
function startOnce() {
  const hour = 3600000
  const nextHour = localTime(new Date((Math.floor(Date.now() / hour) + 1) * hour).toISOString())
  reminderMode.value = 'once'
  delayMode.value = false
  onceTime(nextHour.slice(0, 10), nextHour.slice(11, 16))
}
function toggleReminders(event: Event) {
  remindersOpen.value = (event.target as HTMLDetailsElement).open
  if (remindersOpen.value) {
    today.value = localTime(new Date().toISOString()).slice(0, 10)
    if (reminderMode.value === 'none' && !props.saving) startOnce()
  }
}
function removeReminder() {
  reminderMode.value = 'none'
  configureReminder()
  remindersOpen.value = false
}
function changeFrequency(event: Event) {
  const frequency = (event.target as HTMLSelectElement).value as Exclude<typeof reminderMode.value, 'none'>
  if (frequency === 'once') {
    startOnce()
    return
  }
  const reminder = draft.value.reminders?.[0]
  if (reminderMode.value === 'once' && reminder) {
    clockRows.value = [{ id: reminder.id, clock: notifyClock.value }]
    if (frequency === 'weekly') weekDays.value = [new Date(`${onceDay.value}T12:00:00Z`).getUTCDay() || 7]
    if (frequency === 'monthly') monthDays.value = [Number(onceDay.value.slice(-2))]
  }
  reminderMode.value = frequency
  configureReminder()
}
const recipientDifference=computed(()=>draft.value.reminders!.some(reminder=>reminder.all_members!==draft.value.reminders![0]?.all_members||JSON.stringify(reminder.recipient_ids)!==JSON.stringify(draft.value.reminders![0]?.recipient_ids)))
function toggleDay(values:number[],day:number){const index=values.indexOf(day);if(index>=0)values.splice(index,1);else values.push(day);configureReminder()}
function addClock(){if(clockRows.value.length>=20)return;clockRows.value.push({id:crypto.randomUUID(),clock:'09:00'});configureReminder()}
function removeClock(id:string){if(clockRows.value.length<=1)return;clockRows.value=clockRows.value.filter(row=>row.id!==id);configureReminder()}
function recipientsChanged(){const first=draft.value.reminders?.[0];if(first)for(const reminder of draft.value.reminders??[]){reminder.all_members=first.all_members;reminder.recipient_ids=[...first.recipient_ids]}}
const frequencies = { once: '仅一次', daily: '每天', weekly: '每周', monthly: '每月', yearly: '每年' }
const reminderSummary = computed(() => {
  if (reminderMode.value === 'none') return ''
  if (reminderMode.value === 'once' && delayMode.value) {
    const duration = draft.value.quick_reminder
    return duration ? `保存后 ${quickReminderLabel(duration)}` : '请填写时长'
  }
  if (reminderMode.value === 'once' && draft.value.reminders?.length === 1 && onceAt.value) {
    const day = relativeDay.value || onceDay.value.slice(5).replace('-', '/')
    return `${day} ${notifyClock.value}`
  }
  const count = draft.value.reminders?.length ?? 0
  return count > 1 ? `${frequencies[reminderMode.value]} · ${count} 个时间` : frequencies[reminderMode.value]
})
const recipientSummary = computed(() => {
  if (recipientDifference.value) return '按时间设置'
  const reminder = draft.value.reminders?.[0]
  if (reminder?.all_members) return '家庭全员'
  const ids = reminder?.recipient_ids ?? []
  if (ids.length > 1) return `${ids.length} 人`
  if (ids[0] === props.currentUserId) return '仅自己'
  return props.members.find(member => member.id === ids[0])?.display_name ?? '未选择'
})
function localTime(value:string|null){if(!value)return '';const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';return new Date(date.getTime()+8*3600000).toISOString().slice(0,16)}
function onceTime(day: string, clock = notifyClock.value) {
  const time = Date.parse(`${day}T${clock}:00+08:00`)
  if (!Number.isFinite(time)) return
  configureReminder()
  draft.value.reminders![0]!.at = new Date(time).toISOString()
  notifyClock.value = clock
}
function onceClock(clock: string) { onceTime(onceDay.value, clock) }
function configureReminder(){
 localError.value=''
 settingsTouched.value=true
 delete draft.value.quick_reminder
 if(reminderMode.value==='none'){draft.value.reminders=[];draft.value.recurrence=null;return}
 const prior=draft.value.reminders??[]
 const makeReminder=(id:string)=>{const before=prior.find(reminder=>reminder.id===id)??prior[0];return{id,mode:'absolute' as const,at:before?.at??null,offset_minutes:0,recipient_ids:before?[...before.recipient_ids]:props.currentUserId?[props.currentUserId]:[],all_members:before?.all_members??false}}
 if(reminderMode.value==='once'){draft.value.reminders=[makeReminder(prior[0]?.id??clockRows.value[0]!.id)];draft.value.recurrence=null;return}
 try{
  if(clockRows.value.some(row=>!/^([01]\d|2[0-3]):[0-5]\d$/.test(row.clock)))throw new Error('请选择提醒时间。')
  if(reminderMode.value==='weekly'&&!weekDays.value.length)throw new Error('请至少选择一个星期。')
  if(reminderMode.value==='monthly'&&!monthDays.value.length)throw new Error('请至少选择一个日期。')
  const lunar=reminderMode.value==='yearly'&&yearCalendar.value==='lunar'
  const start=reminderMode.value==='yearly'?simpleRepeatAnchor({frequency:'yearly',clock:clockRows.value[0]!.clock,weekday:0,monthDay:1,yearMonth:yearMonth.value,yearDay:yearDay.value,calendar:yearCalendar.value}).slice(0,10):new Date(Date.now()+8*3600000).toISOString().slice(0,10)
  draft.value.reminders=clockRows.value.map(row=>({...makeReminder(row.id),at:new Date(`${start}T${row.clock}:00+08:00`).toISOString()}))
  draft.value.recurrence={frequency:reminderMode.value,interval:1,calendar:lunar?'lunar':'solar',start_date:start,until:null,lunar_month:lunar?lunarMonth.value:null,lunar_day:lunar?lunarDay.value:null,lunar_leap:lunar&&lunarLeap.value,...(reminderMode.value==='weekly'?{weekdays:[...weekDays.value].sort((a,b)=>a-b)}:{}),...(reminderMode.value==='monthly'?{month_days:[...monthDays.value].sort((a,b)=>a-b)}:{})}
  if(draft.value.kind==='schedule'){const shift=Date.parse(start)-Date.parse(draft.value.start_date);if(draft.value.has_end)draft.value.end_date=new Date(Date.parse(draft.value.end_date)+shift).toISOString().slice(0,10);draft.value.start_date=start}
 }catch(error){localError.value=error instanceof Error?error.message:'请核对提醒设置。'}
}
function save() {
  if (props.saving || composing.value) return
  if (!draft.value.title.trim()) { localError.value = '请填写标题。'; return }
  const categoryId = draft.value.category_id
  if ((!draft.value.id || categoryId) && !props.categories.some(category => category.id === categoryId)) {
    localError.value = '请选择备忘分类，或先新建分类。'
    return
  }
  if((settingsTouched.value||!draft.value.id)&&reminderMode.value!=='none'&&reminderMode.value!=='once'){configureReminder();if(localError.value)return}
  if (reminderMode.value === 'once' && delayMode.value && !draft.value.quick_reminder) {
    localError.value = '请填写有效的提醒时长。'
    return
  }
  if ((!draft.value.id || settingsTouched.value) && reminderMode.value === 'once' && !draft.value.quick_reminder && onceAt.value && Date.parse(onceAt.value) <= Date.now()) {
    localError.value = '提醒时间已过，请选择稍后的时间。'
    return
  }
  if (isSchedule.value) {
    if (!draft.value.start_date) { localError.value = '请选择开始日期。'; return }
    if (!draft.value.all_day && !times.includes(draft.value.start_time)) { localError.value = '请选择半小时精度的开始时间。'; return }
    if (draft.value.has_end && (!draft.value.end_date || draft.value.end_date < draft.value.start_date || !draft.value.all_day && (draft.value.end_date === draft.value.start_date && draft.value.end_time <= draft.value.start_time || !times.includes(draft.value.end_time)))) { localError.value = '结束时间须晚于开始时间。全天日程可在同一天结束。'; return }
  }
  if(draft.value.reminders?.some(r=>r.mode==='absolute'&&!r.at&&!draft.value.quick_reminder||!r.all_members&&!r.recipient_ids.length||r.mode==='relative'&&!isSchedule.value)){localError.value='请填写提醒时间并选择接收人。';return}
  if(draft.value.recurrence&&(draft.value.recurrence.interval<1||draft.value.recurrence.interval>99||!draft.value.recurrence.start_date||draft.value.recurrence.until&&draft.value.recurrence.until<draft.value.recurrence.start_date)){localError.value='请核对重复的间隔、开始和结束日期。';return}
  if(settingsTouched.value&&draft.value.reminders){const unique=new Map<string|null,typeof draft.value.reminders[number]>();for(const reminder of draft.value.reminders)if(!unique.has(reminder.at))unique.set(reminder.at,reminder);draft.value.reminders=[...unique.values()].sort((a,b)=>(a.at??'').localeCompare(b.at??''))}
  if(bodyTasks.value.length>100||bodyTasks.value.some(task=>task.text.length>500)){localError.value='清单最多100项，每项最多500字。';return}
  draft.value.checklist_items=bodyTasks.value.map(({id,text})=>({id,text}))
  draft.value.checklist_checks=bodyTasks.value.filter(task=>task.checked!==originalChecks.has(task.id)).map(task=>({item_id:task.id,checked:task.checked}))
  const saved=freezeQuickReminder(draft.value,Date.now())
  // Failed saves and session draft restoration retain this same frozen time.
  draft.value=saved
  delayMode.value = false
  notifyClock.value=localTime(saved.reminders?.[0]?.at??null).slice(11,16)||notifyClock.value
  emit('save', { ...copyDraft(saved), title: saved.title.trim() })
}
function addFiles(files: MemoAttachmentView[]) { draft.value.attachments.push(...files) }
function titleEnter(event: KeyboardEvent) { if (!event.isComposing && event.keyCode !== 229) event.preventDefault() }
</script>

<template>
  <form class="memo-composer" :aria-busy="saving" @submit.prevent="save" @compositionstart="nativeComposing = true" @compositionend="nativeComposing = false">
    <section class="memo-compose-sheet" aria-label="记录正文">
      <input v-model="draft.title" class="memo-title-input" aria-label="标题" placeholder="标题" :disabled="saving" @keydown.enter="titleEnter">

      <details class="memo-body-disclosure" :open="bodyOpen" @toggle="bodyOpen=($event.target as HTMLDetailsElement).open">
        <summary><span>{{ bodyOpen || draft.body.trim() || draft.attachments.length ? '正文与文件' : '添加正文、图片或附件' }}</span><small v-if="draft.attachments.length">{{ draft.attachments.length }} 个文件</small></summary>
        <div class="memo-body-content">
          <MarkdownEditor v-model="draft.body" checklist @document-change="bodyChanged" :disabled="saving" @composition-state="editorComposing=$event" />
          <MemoAttachments :files="draft.attachments" editable embedded :disabled="saving" @add="addFiles" @remove="id => draft.attachments = draft.attachments.filter(file => file.id !== id)" @request="emit('request-attachment', $event)" />
        </div>
      </details>
    </section>
    <section class="memo-settings memo-compose-settings" aria-label="记录设置">
      <div class="memo-setting-group memo-core-settings">
          <div class="memo-field"><div class="memo-field-head"><label for="memo-category">{{ draft.id ? '分类' : '分类（必填）' }}</label><button type="button" class="memo-text-button" :disabled="saving" @click="emit('create-category')">＋ 新建分类</button></div><select id="memo-category" v-model="draft.category_id" :required="!draft.id" :disabled="saving"><option value="" disabled>请选择分类</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></div>
          <label v-if="focusEnabled" class="memo-option">在首页关注<input v-model="draft.focused" type="checkbox" :disabled="saving"></label>
        <details v-if="isSchedule" class="memo-disclosure memo-legacy-schedule"><summary><span>原记录时间</span><small>已保留</small></summary>
          <label class="memo-option">全天日程<input v-model="draft.all_day" type="checkbox" :disabled="saving"></label>
          <div class="memo-date-time"><label class="memo-field">开始日期<input v-model="draft.start_date" type="date" max="2099-12-31" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">开始时间<select aria-label="开始时间" v-model="draft.start_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div>
          <button type="button" class="memo-text-button memo-end-toggle" :aria-expanded="draft.has_end" :disabled="saving" @click="draft.has_end = !draft.has_end">{{ draft.has_end ? '移除结束' : '＋ 添加结束（选填）' }}</button>
          <div v-if="draft.has_end" class="memo-date-time"><label class="memo-field">结束日期<input v-model="draft.end_date" type="date" max="2099-12-31" :min="draft.start_date" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">结束时间<select aria-label="结束时间" v-model="draft.end_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div>
        </details>
      </div>
      <details class="memo-disclosure" :open="remindersOpen" @toggle="toggleReminders">
        <summary><span>{{ reminderMode === 'none' ? '添加提醒' : '提醒' }}</span><small v-if="reminderSummary">{{ reminderSummary }}</small></summary>
        <section v-if="reminderMode !== 'none'" class="memo-reminder-settings" aria-label="提醒设置">
          <template v-if="reminderMode === 'once'">
            <div v-if="delayMode" class="memo-delay-input">
              <label class="memo-field">保存后<input v-model="delayAmount" type="number" inputmode="numeric" min="1" step="1" placeholder="时长" :disabled="saving" @input="updateDelay"></label>
              <label class="memo-field">单位<select v-model="delayUnit" :disabled="saving" @change="updateDelay"><option value="minute">分钟</option><option value="hour">小时</option><option value="day">天</option><option value="week">周</option></select></label>
            </div>
            <div v-else class="memo-once-time">
              <label class="memo-field"><span>日期<small v-if="relativeDay" class="memo-day-caption">{{ relativeDay }}</small></span><input type="date" :value="onceDay" :disabled="saving" @input="pickCustomDay" @change="pickCustomDay"></label>
              <div class="memo-field"><span>时间</span><MemoTimeInput :model-value="notifyClock" :disabled="saving" @update:model-value="onceClock" /></div>
            </div>
          </template>
          <div class="memo-reminder-controls">
            <label class="memo-field"><span class="memo-sr">重复</span><select :value="reminderMode" :disabled="saving" @change="changeFrequency"><option v-for="(label, frequency) in frequencies" :key="frequency" :value="frequency">{{ label }}</option></select></label>
            <button v-if="reminderMode === 'once'" type="button" class="memo-text-button" :disabled="saving" @click="changeTiming(!delayMode)">{{ delayMode ? '选日期和时间' : '按时长提醒' }}</button>
            <button type="button" class="memo-text-button memo-remove-reminder" :disabled="saving" @click="removeReminder">移除</button>
          </div>
          <template v-if="reminderMode !== 'once'">
            <fieldset v-if="reminderMode==='weekly'" class="memo-day-picker" :disabled="saving"><legend>星期 <small>可多选</small></legend><div class="memo-day-options"><button v-for="(name,index) in weekdays" :key="name" type="button" :aria-label="name" :aria-pressed="weekDays.includes(index+1)" @click="toggleDay(weekDays,index+1)">{{ name.slice(1) }}</button></div></fieldset>
            <fieldset v-if="reminderMode==='monthly'" class="memo-day-picker" :disabled="saving"><legend>每月几号 <small>可多选</small></legend><div class="memo-day-options"><button v-for="day in 31" :key="day" type="button" :aria-label="`${day}号`" :aria-pressed="monthDays.includes(day)" @click="toggleDay(monthDays,day)">{{ day }}</button></div><p class="memo-muted">当月没有所选日期时，在月底提醒。</p></fieldset>
            <template v-if="reminderMode==='yearly'">
              <label class="memo-field">纪念日历法<select v-model="yearCalendar" :disabled="saving" @change="configureReminder"><option value="solar">公历</option><option value="lunar">农历</option></select></label>
              <div v-if="yearCalendar==='solar'" class="memo-date-time"><label class="memo-field">月份<select v-model.number="yearMonth" :disabled="saving" @change="configureReminder"><option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option></select></label><label class="memo-field">日期<select v-model.number="yearDay" :disabled="saving" @change="configureReminder"><option v-for="day in 31" :key="day" :value="day">{{ day }} 日</option></select></label></div>
              <template v-else><div class="memo-date-time"><label class="memo-field">农历月<select v-model.number="lunarMonth" :disabled="saving" @change="configureReminder"><option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option></select></label><label class="memo-field">农历日<select v-model.number="lunarDay" :disabled="saving" @change="configureReminder"><option v-for="day in 30" :key="day" :value="day">{{ day }} 日</option></select></label></div><label class="memo-option">闰月<input v-model="lunarLeap" type="checkbox" :disabled="saving" @change="configureReminder"></label></template>
            </template>
            <section class="memo-clock-list" aria-label="提醒时间"><div class="memo-field-head"><span>提醒时间</span><button type="button" class="memo-text-button" :disabled="saving||clockRows.length>=20" @click="addClock">＋ 添加时间</button></div><div v-for="(row,index) in clockRows" :key="row.id" class="memo-clock-row"><div class="memo-field"><MemoTimeInput v-model="row.clock" :label="`提醒时间${index+1}`" :disabled="saving" @change="configureReminder" /></div><button v-if="clockRows.length>1" type="button" class="memo-text-button" :aria-label="`移除第${index+1}个提醒时间`" :disabled="saving" @click="removeClock(row.id)">移除</button></div><p class="memo-muted">每个时间分别完成，其他时间继续提醒。</p></section>
          </template>
          <details v-if="draft.reminders?.[0] && (members.length > 1 || draft.reminders[0].all_members || recipientDifference)" class="memo-recipient-options">
            <summary><span>提醒对象</span><small>{{ recipientSummary }}</small></summary>
            <fieldset class="memo-setting-group memo-reminder" :disabled="saving" @change="recipientsChanged">
              <legend class="memo-sr">提醒对象</legend>
              <label class="memo-option">家庭全员<input v-model="draft.reminders[0].all_members" type="checkbox"></label>
              <div v-if="!draft.reminders[0].all_members" class="memo-recipients"><label v-for="member in members" :key="member.id" class="memo-option"><input v-model="draft.reminders[0].recipient_ids" type="checkbox" :value="member.id">{{ member.display_name }}</label></div>
            </fieldset>
            <p v-if="recipientDifference" class="memo-muted">各时间原有提醒对象已保留；修改提醒对象将统一应用。</p>
          </details>
        </section>
      </details>
    </section>
    <p v-if="localError || error" class="memo-error" role="alert">{{ localError || error }}</p>
    <div class="memo-form-actions"><button type="button" class="memo-text-button" :disabled="saving" @click="emit('cancel')">取消</button><button type="submit" class="memo-primary" :disabled="saving || composing">{{ saving ? '正在保存…' : draft.id ? '保存修改' : '保存记录' }}</button></div>
  </form>
</template>
