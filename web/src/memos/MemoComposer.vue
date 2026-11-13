<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import MemoAttachments from './MemoAttachments.vue'
import MemoTimeInput from './MemoTimeInput.vue'
const MarkdownEditor = defineAsyncComponent(() => import('../MarkdownEditor.vue'))
import type { User } from '../types'
import { localDay, simpleRepeatAnchor } from './dates'
import { copyDraft } from './types'
import type { MemoAttachmentView, MemoCategoryView, MemoDraft } from './types'

const props = withDefaults(defineProps<{ initial: MemoDraft; lockedKind?: 'memo'|'schedule'; categories: MemoCategoryView[]; saving?: boolean; error?: string; scheduleEnabled?: boolean; focusEnabled?: boolean; members?: User[]; currentUserId?: string }>(), { saving: false, error: '', scheduleEnabled: false, focusEnabled: false, members: () => [], currentUserId: '' })
const emit = defineEmits<{ save: [draft: MemoDraft]; cancel: []; change: [draft: MemoDraft]; 'create-category': []; 'request-attachment': [file: MemoAttachmentView] }>()
const draft = ref(copyDraft(props.initial))
draft.value.reminders??=[]
draft.value.recurrence??=null
const remindersOpen = ref(false)

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
const initialWeekDay=new Date(`${referenceDay}T12:00:00+08:00`).getDay()||7
const weekDays=ref(draft.value.recurrence?.weekdays?[...draft.value.recurrence.weekdays]:[initialWeekDay])
const monthDays=ref(draft.value.recurrence?.month_days?[...draft.value.recurrence.month_days]:[Number((draft.value.recurrence?.start_date??referenceDay).slice(-2))])
const clockRows=ref(draft.value.reminders.length?draft.value.reminders.map(reminder=>({id:reminder.id,clock:reminder.mode==='relative'?localTime(new Date(Date.parse(`${draft.value.start_date}T${draft.value.all_day?'00:00':draft.value.start_time}:00+08:00`)-reminder.offset_minutes*60000).toISOString()).slice(11,16):localTime(reminder.at).slice(11,16)})):[{id:crypto.randomUUID(),clock:notifyClock.value}])
const yearMonth=ref(Number(referenceDay.slice(5,7)))
const yearDay=ref(Number(referenceDay.slice(-2)))
const yearCalendar=ref<'solar'|'lunar'>(draft.value.recurrence?.calendar??'solar')
const lunarMonth=ref(draft.value.recurrence?.lunar_month??1),lunarDay=ref(draft.value.recurrence?.lunar_day??1),lunarLeap=ref(draft.value.recurrence?.lunar_leap??false)
const settingsTouched=ref(false)
const weekdays=['周一','周二','周三','周四','周五','周六','周日']
const recipientDifference=computed(()=>draft.value.reminders!.some(reminder=>reminder.all_members!==draft.value.reminders![0]?.all_members||JSON.stringify(reminder.recipient_ids)!==JSON.stringify(draft.value.reminders![0]?.recipient_ids)))
function toggleDay(values:number[],day:number){const index=values.indexOf(day);if(index>=0)values.splice(index,1);else values.push(day);configureReminder()}
function addClock(){if(clockRows.value.length>=20)return;clockRows.value.push({id:crypto.randomUUID(),clock:'09:00'});configureReminder()}
function removeClock(id:string){if(clockRows.value.length<=1)return;clockRows.value=clockRows.value.filter(row=>row.id!==id);configureReminder()}
function recipientsChanged(){const first=draft.value.reminders?.[0];if(first)for(const reminder of draft.value.reminders??[]){reminder.all_members=first.all_members;reminder.recipient_ids=[...first.recipient_ids]}}
const reminderSummary=computed(()=>({none:'不提醒',once:'仅一次',daily:'每天',weekly:'每周',monthly:'每月',yearly:'每年'})[reminderMode.value])
function localTime(value:string|null){if(!value)return '';const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';return new Date(date.getTime()+8*3600000).toISOString().slice(0,16)}
function onceTime(day:string,clock=notifyClock.value){configureReminder();if(draft.value.reminders?.[0])draft.value.reminders[0].at=day?new Date(`${day}T${clock}:00+08:00`).toISOString():null}
function onceClock(clock:string){notifyClock.value=clock;onceTime(localTime(draft.value.reminders?.[0]?.mode==='relative'?initialAt??null:draft.value.reminders?.[0]?.at??null).slice(0,10),clock)}
function configureReminder(){
 localError.value=''
 settingsTouched.value=true
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
  if((settingsTouched.value||!draft.value.id)&&reminderMode.value!=='none'&&reminderMode.value!=='once'){configureReminder();if(localError.value)return}
  if (!draft.value.title.trim()) { localError.value = '请填写标题。'; return }
  if (draft.value.category_id && !props.categories.some(category => category.id === draft.value.category_id)) { localError.value = '请选择备忘分类，或先新建分类。'; return }
  if (isSchedule.value) {
    if (!draft.value.start_date) { localError.value = '请选择开始日期。'; return }
    if (!draft.value.all_day && !times.includes(draft.value.start_time)) { localError.value = '请选择半小时精度的开始时间。'; return }
    if (draft.value.has_end && (!draft.value.end_date || draft.value.end_date < draft.value.start_date || !draft.value.all_day && (draft.value.end_date === draft.value.start_date && draft.value.end_time <= draft.value.start_time || !times.includes(draft.value.end_time)))) { localError.value = '结束时间须晚于开始时间。全天日程可在同一天结束。'; return }
  }
  if(draft.value.reminders?.some(r=>r.mode==='absolute'&&!r.at||!r.all_members&&!r.recipient_ids.length||r.mode==='relative'&&!isSchedule.value)){localError.value='请填写提醒时间并选择接收人。';return}
  if(draft.value.recurrence&&(draft.value.recurrence.interval<1||draft.value.recurrence.interval>99||!draft.value.recurrence.start_date||draft.value.recurrence.until&&draft.value.recurrence.until<draft.value.recurrence.start_date)){localError.value='请核对重复的间隔、开始和结束日期。';return}
  if(settingsTouched.value&&draft.value.reminders){const unique=new Map<string|null,typeof draft.value.reminders[number]>();for(const reminder of draft.value.reminders)if(!unique.has(reminder.at))unique.set(reminder.at,reminder);draft.value.reminders=[...unique.values()].sort((a,b)=>(a.at??'').localeCompare(b.at??''))}
  emit('save', { ...copyDraft(draft.value), title: draft.value.title.trim() })
}
function addFiles(files: MemoAttachmentView[]) { draft.value.attachments.push(...files) }
function titleEnter(event: KeyboardEvent) { if (!event.isComposing && event.keyCode !== 229) event.preventDefault() }
</script>

<template>
  <form class="memo-composer" :aria-busy="saving" @submit.prevent="save" @compositionstart="nativeComposing = true" @compositionend="nativeComposing = false">
    <section class="memo-compose-sheet" aria-label="记录正文">
      <input v-model="draft.title" class="memo-title-input" aria-label="标题" placeholder="标题" :disabled="saving" @keydown.enter="titleEnter">
      <details class="memo-body-disclosure" :open="bodyOpen" @toggle="bodyOpen=($event.target as HTMLDetailsElement).open">
        <summary><span>{{ bodyOpen || draft.body.trim() || draft.attachments.length ? '正文与文件' : '添加正文、图片或附件' }}</span><small>{{ draft.attachments.length ? `${draft.attachments.length} 个文件` : draft.body.trim() ? '已填写' : '选填' }}</small></summary>
        <div class="memo-body-content">
          <MarkdownEditor v-model="draft.body" :disabled="saving" @composition-state="editorComposing=$event" />
          <MemoAttachments :files="draft.attachments" editable embedded :disabled="saving" @add="addFiles" @remove="id => draft.attachments = draft.attachments.filter(file => file.id !== id)" @request="emit('request-attachment', $event)" />
        </div>
      </details>
    </section>
    <section class="memo-settings memo-compose-settings" aria-label="记录设置">
      <div class="memo-setting-group memo-core-settings">
          <div class="memo-field"><div class="memo-field-head"><label for="memo-category">分类</label><button type="button" class="memo-text-button" :disabled="saving" @click="emit('create-category')">＋ 新建分类</button></div><select id="memo-category" v-model="draft.category_id" :disabled="saving"><option value="">未分类</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></div>
          <label v-if="focusEnabled" class="memo-option">在首页关注<input v-model="draft.focused" type="checkbox" :disabled="saving"></label>
        <details v-if="isSchedule" class="memo-disclosure memo-legacy-schedule"><summary><span>原记录时间</span><small>已保留</small></summary>
          <label class="memo-option">全天日程<input v-model="draft.all_day" type="checkbox" :disabled="saving"></label>
          <div class="memo-date-time"><label class="memo-field">开始日期<input v-model="draft.start_date" type="date" max="2099-12-31" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">开始时间<select aria-label="开始时间" v-model="draft.start_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div>
          <button type="button" class="memo-text-button memo-end-toggle" :aria-expanded="draft.has_end" :disabled="saving" @click="draft.has_end = !draft.has_end">{{ draft.has_end ? '移除结束' : '＋ 添加结束（选填）' }}</button>
          <div v-if="draft.has_end" class="memo-date-time"><label class="memo-field">结束日期<input v-model="draft.end_date" type="date" max="2099-12-31" :min="draft.start_date" :disabled="saving"></label><label v-if="!draft.all_day" class="memo-field">结束时间<select aria-label="结束时间" v-model="draft.end_time" :disabled="saving"><option v-for="time in times" :key="time">{{ time }}</option></select></label></div>
        </details>
      </div>
      <details class="memo-disclosure" :open="remindersOpen" @toggle="remindersOpen=($event.target as HTMLDetailsElement).open">
        <summary><span>提醒与重复</span><small>{{ reminderSummary }}</small></summary>
        <section aria-label="提醒与重复设置">
          <label class="memo-field">重复<select v-model="reminderMode" :disabled="saving" @change="configureReminder"><option value="none">不提醒</option><option value="once">仅一次</option><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option><option value="yearly">每年</option></select></label>
          <template v-if="reminderMode!=='none'">
            <div v-if="reminderMode==='once' && draft.reminders?.[0]" class="memo-once-time"><label class="memo-field">提醒日期<input type="date" :value="localTime(draft.reminders[0].mode==='relative'?initialAt??null:draft.reminders[0].at).slice(0,10)" :disabled="saving" @input="onceTime(($event.target as HTMLInputElement).value)"></label><div class="memo-field"><span>提醒时间</span><MemoTimeInput :model-value="notifyClock" :disabled="saving" @update:model-value="onceClock" /></div></div>
            <template v-else>
              <fieldset v-if="reminderMode==='weekly'" class="memo-day-picker" :disabled="saving"><legend>星期 <small>可多选</small></legend><div class="memo-day-options"><button v-for="(name,index) in weekdays" :key="name" type="button" :aria-label="name" :aria-pressed="weekDays.includes(index+1)" @click="toggleDay(weekDays,index+1)">{{ name.slice(1) }}</button></div></fieldset>
              <fieldset v-if="reminderMode==='monthly'" class="memo-day-picker" :disabled="saving"><legend>每月几号 <small>可多选</small></legend><div class="memo-day-options"><button v-for="day in 31" :key="day" type="button" :aria-label="`${day}号`" :aria-pressed="monthDays.includes(day)" @click="toggleDay(monthDays,day)">{{ day }}</button></div><p class="memo-muted">当月没有所选日期时，在月底提醒。</p></fieldset>
              <template v-if="reminderMode==='yearly'">
                <label class="memo-field">纪念日历法<select v-model="yearCalendar" :disabled="saving" @change="configureReminder"><option value="solar">公历</option><option value="lunar">农历</option></select></label>
                <div v-if="yearCalendar==='solar'" class="memo-date-time"><label class="memo-field">月份<select v-model.number="yearMonth" :disabled="saving" @change="configureReminder"><option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option></select></label><label class="memo-field">日期<select v-model.number="yearDay" :disabled="saving" @change="configureReminder"><option v-for="day in 31" :key="day" :value="day">{{ day }} 日</option></select></label></div>
                <template v-else><div class="memo-date-time"><label class="memo-field">农历月<select v-model.number="lunarMonth" :disabled="saving" @change="configureReminder"><option v-for="month in 12" :key="month" :value="month">{{ month }} 月</option></select></label><label class="memo-field">农历日<select v-model.number="lunarDay" :disabled="saving" @change="configureReminder"><option v-for="day in 30" :key="day" :value="day">{{ day }} 日</option></select></label></div><label class="memo-option">闰月<input v-model="lunarLeap" type="checkbox" :disabled="saving" @change="configureReminder"></label></template>
              </template>
              <section class="memo-clock-list" aria-label="提醒时间"><div class="memo-field-head"><span>提醒时间</span><button type="button" class="memo-text-button" :disabled="saving||clockRows.length>=20" @click="addClock">＋ 添加时间</button></div><div v-for="(row,index) in clockRows" :key="row.id" class="memo-clock-row"><div class="memo-field"><MemoTimeInput v-model="row.clock" :label="`提醒时间${index+1}`" :disabled="saving" @change="configureReminder" /></div><button v-if="clockRows.length>1" type="button" class="memo-text-button" :aria-label="`移除第${index+1}个提醒时间`" :disabled="saving" @click="removeClock(row.id)">移除</button></div><p class="memo-muted">每个时间分别完成，其他时间继续提醒。</p></section>
            </template>
            <fieldset v-if="draft.reminders?.[0]" class="memo-setting-group memo-reminder" :disabled="saving" @change="recipientsChanged"><legend class="memo-sr">提醒对象</legend><label v-if="members.length>1" class="memo-option">家庭全员<input v-model="draft.reminders[0].all_members" type="checkbox"></label><div v-if="!draft.reminders[0].all_members" class="memo-recipients"><label v-for="member in members" :key="member.id" class="memo-option"><input v-model="draft.reminders[0].recipient_ids" type="checkbox" :value="member.id">{{ member.display_name }}</label></div></fieldset>
            <p v-if="recipientDifference" class="memo-muted">各时间原有提醒对象已保留；修改提醒对象将统一应用。</p>
            <p v-if="draft.id&&!settingsTouched" class="memo-muted">未修改提醒设置时，原规则会完整保留。</p>
            <p v-else-if="reminderMode!=='once'" class="memo-muted">按固定日期提醒，完成时间不影响下一期。</p>
          </template>
        </section>
      </details>
    </section>
    <p v-if="localError || error" class="memo-error" role="alert">{{ localError || error }}</p>
    <div class="memo-form-actions"><button type="button" class="memo-text-button" :disabled="saving" @click="emit('cancel')">取消</button><button type="submit" class="memo-primary" :disabled="saving || composing">{{ saving ? '正在保存…' : draft.id ? '保存修改' : '保存记录' }}</button></div>
  </form>
</template>
