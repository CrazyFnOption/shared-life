<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { sameSource, sourceKey } from '../sources'
import type { MemoDeletionRequest } from './domain'
import type { Source, User } from '../types'
import MemoAttachments from './MemoAttachments.vue'
import MemoCalendar from './MemoCalendar.vue'
import MemoComposer from './MemoComposer.vue'
import MemoRecordRow from './MemoRecordRow.vue'
import { compareSchedules, dayLabel, localDay, scheduleOnDay, schedulesForDay } from './dates'
import { useMemoSort } from './sortGesture'
import { trapDialogTab } from './dialog'
import { copyDraft } from './types'
import type { MemoAttachmentView, MemoCategoryCommand, MemoCategoryView, MemoCommandOrigin, MemoCommandResult, MemoCompleteCommand, MemoDraft, MemoDraftChange, MemoFocusCommand, MemoRecordView, MemoSaveCommand, MemoOperationCommand, MemoView, ScheduleView } from './types'
import './memos.css'
import './modern-memos.css'

const props = withDefaults(defineProps<{ source: Source; scopeLabel: string; categories: MemoCategoryView[]; records: MemoRecordView[]; writable?: boolean; loading?: boolean; error?: string; result?: MemoCommandResult; scheduleEnabled?: boolean; focusEnabled?: boolean; initialDraft?: MemoDraft; members?: User[]; deletionRequests?: MemoDeletionRequest[]; focusedTasks?: MemoView[]; openTargetId?: string; calendarLoading?:boolean; calendarError?:string }>(), { writable: false, loading: false, error: '', scheduleEnabled: false, focusEnabled: false, members: () => [], deletionRequests: () => [], focusedTasks: () => [] })
const emit = defineEmits<{ back: []; save: [command: MemoSaveCommand]; complete: [command: MemoCompleteCommand]; focus: [command: MemoFocusCommand]; 'create-category': [command: MemoCategoryCommand]; 'calendar-month':[month:string]; operation: [command: MemoOperationCommand]; 'open-plan-task': [id:string]; 'complete-plan-task': [id:string]; 'reorder-focus': [ids:string[]]; 'reorder-categories': [ids:string[]]; 'draft-change': [value: MemoDraftChange]; 'request-attachment': [value: MemoCommandOrigin & { record_id: string; attachment: MemoAttachmentView }] }>()
type Screen = 'overview' | 'directory' | 'category' | 'schedules' | 'detail' | 'editor'
const screen = ref<Screen>('overview')
const previous = ref<Screen>('overview')
const categoryId = ref('')
const recordId = ref('')
const editor = ref<MemoDraft>()
const editorSource = ref<Source>()
const lockedKind = ref<'memo'|'schedule'>()
const restorableDraft = computed(() => props.writable && props.initialDraft?.source && sameSource(props.initialDraft.source,props.source) ? props.initialDraft : undefined)
const selectedDay = ref(localDay())
const calendarOpen = ref(false)
const calendarTrigger = ref<HTMLButtonElement>()
const focusExpanded = ref(false)
const scheduleExpanded = ref(false)
const pending = ref<{ id: string; action: 'save' | 'category' | 'record' }>()
let navigationEpoch=0
let saveContext:{request_id:string;source:Source;draft:MemoDraft;navigation:number}|undefined
const operationError = ref('')
const notice = ref('')
const categoryDialog = ref(false)
const categoryName = ref('')
const categoryMode=ref<'create'|'rename'|'delete'>('create')
const managedCategory=ref<MemoCategoryView>()
const mergeCategoryId=ref('')
const showingArchive=ref(false)
const deletingRecord=ref(false)
const deletionReason=ref('')
const categoryInput = ref<HTMLInputElement>()
let categoryOpener: HTMLElement | null = null
const heading = ref<HTMLElement>()
const categories = computed(() => [...props.categories].sort((a, b) => a.position - b.position))
const category = computed(() => categories.value.find(item => item.id === categoryId.value))
const record = computed(() => props.records.find(item => item.id === recordId.value))
const schedules = computed(() => props.records.filter((item): item is ScheduleView => item.kind === 'schedule'))
const daySchedulesKnown=computed(()=>schedules.value.filter(item=>item.recurrence&&!item.archived).every(item=>item.occurrence_months?.includes(selectedDay.value.slice(0,7))))
const daySchedules = computed(() => schedulesForDay(schedules.value,selectedDay.value))
const focused = computed(() => [...props.records,...props.focusedTasks].filter(item => item.kind === 'memo' && item.focused && !item.completed && !item.archived).sort((a, b) => a.kind === 'memo' && b.kind === 'memo' ? a.focus_position - b.focus_position : 0))
const categoryRecords = computed(() => props.records.filter(item => item.kind === 'memo' && item.category_id === categoryId.value && !!item.archived === showingArchive.value))
const allSchedules = computed(() => [...schedules.value].filter(item=>!!item.archived===showingArchive.value).sort((a, b) => a.start_date.localeCompare(b.start_date) || compareSchedules(a, b)))
const pageTitle = computed(() => screen.value === 'overview' ? '生活备忘' : screen.value === 'directory' ? '日程与备忘' : screen.value === 'category' ? category.value?.name ?? '备忘分类' : screen.value === 'schedules' ? '日程安排' : screen.value === 'editor' ? `${editor.value?.id ? '编辑' : '新建'}${editor.value?.kind === 'schedule' ? '日程' : '备忘'}` : record.value?.kind === 'schedule' ? '日程' : '备忘')
function sortKey(item:MemoRecordView){return `${item.kind==='memo'&&item.origin==='plan_task'?'plan_task':'memo'}:${item.id}`}
function reorder(group:string,from:string,to:string){if(!props.writable||pending.value)return;const ids=group==='category'?categories.value.map(c=>c.id):focused.value.map(sortKey),fromIndex=ids.indexOf(from),toIndex=ids.indexOf(to);if(fromIndex<0||toIndex<0||fromIndex===toIndex)return;ids.splice(toIndex,0,ids.splice(fromIndex,1)[0]!);if(group==='category')emit('reorder-categories',ids);else emit('reorder-focus',ids)}
const sort=useMemoSort(reorder,id=>{const item=categories.value.find(c=>c.id===id);if(item)manageCategory(item,'rename')})
function keyboardSort(event:KeyboardEvent,group:string,id:string){if(!event.altKey||!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const ids=group==='category'?categories.value.map(c=>c.id):focused.value.map(sortKey),index=ids.indexOf(id),target=ids[index+(['ArrowUp','ArrowLeft'].includes(event.key)?-1:1)];if(target)reorder(group,id,target)}
function categoryLabel(item: MemoRecordView) { if(item.kind==='memo'&&item.origin==='plan_task')return '计划任务';return item.kind === 'memo' ? categories.value.find(category => category.id === item.category_id)?.name ?? '原分类' : '日程安排' }
function origin(source: Source = props.source): MemoCommandOrigin { return { request_id: crypto.randomUUID(), source: { ...source } } }
function go(next: Screen) { screen.value = next; operationError.value = ''; void nextTick(() => heading.value?.focus()) }
function back() {
  if (screen.value === 'editor') { cancelEditor(); return }
  if (screen.value === 'overview') { emit('back'); return }
  go(screen.value === 'detail' ? previous.value : screen.value === 'directory' ? 'overview' : 'directory')
}
function open(item: MemoRecordView) { if(item.kind==='memo'&&item.origin==='plan_task'){emit('open-plan-task',item.id);return}previous.value = screen.value; recordId.value = item.id; go('detail') }
function startEditor(item?: MemoRecordView) {
  if (!props.writable || pending.value) return
  lockedKind.value = item?.kind ?? (screen.value==='schedules'?'schedule':screen.value==='category'?'memo':undefined)
  editorSource.value = { ...props.source }
  const schedule = item?.kind === 'schedule' ? item : undefined
  editor.value = { source:{...props.source}, id: item?.id, base_version: item?.version ?? 0, kind: item?.kind ?? (screen.value === 'schedules' ? 'schedule' : 'memo'), title: item?.title ?? '', body: item?.body ?? '', category_id: item?.kind === 'memo' ? item.category_id : categoryId.value || categories.value[0]?.id || '', focused: item?.kind === 'memo' ? item.focused : false, start_date: schedule?.start_date ?? selectedDay.value, end_date: schedule?.end_date ?? selectedDay.value, start_time: schedule?.start_time ?? '09:00', end_time: schedule?.end_time ?? '09:30', all_day: !schedule?.start_time, has_end: !!schedule?.end_date, attachments: item?.attachments.map(file => ({ ...file })) ?? [],reminders:item?.reminders?.map(r=>({...r,recipient_ids:[...r.recipient_ids]}))??[],recurrence:item?.recurrence?{...item.recurrence}:null }
  emit('draft-change', { source: { ...editorSource.value }, draft: copyDraft(editor.value) })
  go('editor')
}
function restoreEditor() { if(!props.initialDraft?.source||!sameSource(props.initialDraft.source,props.source))return;editorSource.value={...props.source};editor.value=copyDraft(props.initialDraft);lockedKind.value=props.initialDraft.kind;screen.value='editor' }

function changeDraft(value: MemoDraft) { if (!editorSource.value) return; editor.value = copyDraft(value); emit('draft-change', { source: { ...editorSource.value }, draft: copyDraft(value) }) }
function cancelEditor() { if (pending.value) return; if (editorSource.value) emit('draft-change', { source: { ...editorSource.value }, draft: null }); go(editor.value?.id ? 'detail' : previous.value); editor.value = undefined; editorSource.value = undefined }
function save(value: MemoDraft) {
  if (!props.writable || pending.value || !editorSource.value || !sameSource(editorSource.value, props.source)) return
  const command = { ...origin(editorSource.value), draft: copyDraft(value) }
  saveContext={request_id:command.request_id,source:{...command.source},draft:copyDraft(command.draft),navigation:navigationEpoch}
  pending.value = { id: command.request_id, action: 'save' }; operationError.value = ''; emit('save', command)
}
function complete(item: MemoRecordView) {
  if (!props.writable || pending.value || item.kind !== 'memo') return
  if(item.origin==='plan_task'){emit('complete-plan-task',item.id);return}
  const command = { ...origin(), record_id: item.id, base_version: item.version, completed: !item.completed }
  pending.value = { id: command.request_id, action: 'record' }; operationError.value = ''; emit('complete', command)
}
function focus(item: MemoRecordView) {
  if (!props.writable || pending.value || item.kind !== 'memo') return
  const command = { ...origin(), record_id: item.id, base_version: item.version, focused: !item.focused }
  pending.value = { id: command.request_id, action: 'record' }; operationError.value = ''; emit('focus', command)
}
function showCategoryDialog() { categoryMode.value='create';managedCategory.value=undefined;categoryOpener = document.activeElement as HTMLElement | null; categoryDialog.value = true; categoryName.value = ''; operationError.value = ''; void nextTick(() => categoryInput.value?.focus()) }
function closeCategoryDialog() { categoryDialog.value = false; void nextTick(() => categoryOpener?.isConnected && categoryOpener.focus()) }
function manageCategory(item:MemoCategoryView,mode:'rename'|'delete'){if(!props.writable||pending.value)return;showCategoryDialog();categoryMode.value=mode;managedCategory.value=item;categoryName.value=item.name;mergeCategoryId.value=categories.value.find(c=>c.id!==item.id)?.id??''}
function sendOperation(entity:'memo'|'category'|'deletion_request',id:string,version:number,kind:string,data:MemoOperationCommand['operation']['data'],action:'record'|'category'='record'){if(!props.writable||pending.value)return;const captured=origin();pending.value={id:captured.request_id,action};operationError.value='';emit('operation',{...captured,operation:{op_id:captured.request_id,entity_type:entity,entity_id:id,kind,base_version:version,data}})}
function archiveRecord(){if(record.value)sendOperation('memo',record.value.id,record.value.version,'archive',{archived:!record.value.archived})}
function removeRecord(){const current=record.value;if(!current)return;if(current.can_delete){if(confirm('删除这条记录？正文与附件将不再显示。'))sendOperation('memo',current.id,current.version,'delete',{})}else{deletingRecord.value=true;deletionReason.value=''}}
function requestDeletion(){const current=record.value;if(!current)return;sendOperation('deletion_request',crypto.randomUUID(),0,'create',{memo_id:current.id,target_version:current.version,reason:deletionReason.value});deletingRecord.value=false}
function openTarget(id:string){if(editor.value)return false;const found=props.records.find(r=>r.id===id);if(!found)return false;previous.value='overview';recordId.value=id;go('detail');return true}
function resetNavigation(){
 navigationEpoch++
 screen.value='overview';previous.value='overview';recordId.value='';categoryId.value='';editor.value=undefined;editorSource.value=undefined;lockedKind.value=undefined
 categoryDialog.value=false;deletingRecord.value=false;calendarOpen.value=false;managedCategory.value=undefined;categoryName.value='';deletionReason.value='';showingArchive.value=false;focusExpanded.value=false;scheduleExpanded.value=false;selectedDay.value=localDay();operationError.value='';notice.value='';sort.cancel()
}
defineExpose({openTarget,resetNavigation})
watch(()=>props.openTargetId,id=>{if(id)openTarget(id)})
function createCategory() {
  if (!props.writable || pending.value) return
  if(categoryMode.value==='delete'&&managedCategory.value){const current=managedCategory.value,nonempty=props.records.some(r=>r.kind==='memo'&&r.category_id===current.id);if(nonempty&&!mergeCategoryId.value){operationError.value='此分类有记录，请先创建另一个备忘分类接收全部内容。';return}sendOperation('category',current.id,current.version??0,'delete',{target_category_id:nonempty?mergeCategoryId.value:null},'category');return}
  const name = categoryName.value.trim()
  if (!name) { operationError.value = '请填写分类名称。'; return }
  if (categories.value.some(category => category.name === name&&category.id!==managedCategory.value?.id)) { operationError.value = '已有同名分类，请换一个名称。'; return }
  if(categoryMode.value==='rename'&&managedCategory.value){sendOperation('category',managedCategory.value.id,managedCategory.value.version??0,'rename',{name},'category');return}
  const command = { ...origin(), name }; pending.value = { id: command.request_id, action: 'category' }; operationError.value = ''; emit('create-category', command)
}
function attachmentRequest(attachment: MemoAttachmentView) { if (recordId.value) emit('request-attachment', { ...origin(), record_id: recordId.value, attachment }) }
function closeCalendar() { calendarOpen.value = false; void nextTick(() => calendarTrigger.value?.focus()) }
function selectDay(day: string) { selectedDay.value = day; scheduleExpanded.value = false; closeCalendar() }
watch(() => props.result, result => {
  if (!result || !pending.value || result.request_id !== pending.value.id || !sameSource(result.source, props.source)) return
  const action = pending.value.action; pending.value = undefined
  if (result.status === 'error') { saveContext=undefined; operationError.value = result.error || '保存未完成，输入已保留。'; return }
  if (action === 'category') { closeCategoryDialog(); return }
  if(screen.value==='detail'&&!record.value)go(previous.value)
  if (action === 'save') {
    const saved=saveContext;saveContext=undefined
    if(!saved||saved.request_id!==result.request_id||!sameSource(saved.source,result.source))return
    emit('draft-change',{source:{...saved.source},draft:null})
    if(saved.navigation===navigationEpoch&&screen.value==='editor'&&editor.value&&sameSource(editorSource.value,saved.source)){
      recordId.value=result.record_id??saved.draft.id??'';editor.value=undefined;editorSource.value=undefined;go('detail')
    }
    notice.value='已保存到本机。'
  }
})
watch(() => sourceKey(props.source), () => {
  // The root retains eligible drafts under their original source and clears revoked ones.
  navigationEpoch++;saveContext=undefined
  screen.value = 'overview'; previous.value = 'overview'; recordId.value = ''; categoryId.value = ''; editor.value = undefined; editorSource.value = undefined; pending.value = undefined; categoryDialog.value = false;deletingRecord.value=false;deletionReason.value='';managedCategory.value=undefined;categoryName.value=''; calendarOpen.value = false; operationError.value = ''; notice.value = ''; focusExpanded.value = false; scheduleExpanded.value = false;selectedDay.value=localDay();lockedKind.value=undefined
})
</script>

<template>
  <section class="memos-workspace" aria-label="生活备忘">
    <header class="memo-header" :class="{ 'is-root': screen === 'overview' }"><div v-if="screen !== 'overview'" class="memo-header-meta"><button type="button" class="memo-back" :disabled="!!pending && screen === 'editor'" @click="back">‹ 返回</button></div><div class="memo-heading-copy"><h2 ref="heading" tabindex="-1">{{ pageTitle }}</h2></div><button v-if="writable && ['overview','category','schedules'].includes(screen) && (screen !== 'schedules' || scheduleEnabled)" type="button" class="memo-add" :disabled="!!pending" @click="previous = screen; startEditor()">＋ 新建</button><button v-else-if="screen === 'detail' && record && writable && record.can_edit !== false" type="button" class="memo-primary" :disabled="!!pending" @click="startEditor(record)">编辑</button></header>
    <p v-if="error || operationError && screen !== 'editor' && !categoryDialog" class="memo-error memo-banner" role="alert">{{ error || operationError }}</p>
    <p v-if="loading" class="memo-muted memo-banner" role="status">正在读取本机记录…</p>
    <main class="memo-body">
      <button v-if="screen==='overview' && restorableDraft" type="button" class="memo-draft-resume" :disabled="!!pending" @click="restoreEditor"><span><strong>继续未保存的{{ restorableDraft.kind==='schedule'?'日程':'备忘' }}</strong><small>{{ restorableDraft.title || '未命名草稿' }}</small></span><span aria-hidden="true">↗</span></button>
      <template v-if="screen === 'overview'">
        <section class="memo-section" aria-labelledby="memo-focus-title"><h3 id="memo-focus-title">待办事项</h3><div v-if="focused.length" class="memo-block"><div v-for="item in focusExpanded ? focused : focused.slice(0,3)" :key="sortKey(item)" class="memo-sort-item" :class="{held:sort.held.value===sortKey(item),over:sort.over.value===sortKey(item)}" :data-memo-sort="sortKey(item)" data-sort-group="focus" @pointerdown="sort.down($event,sortKey(item),'focus')" @pointermove="sort.move" @pointerup="sort.up" @pointercancel="sort.cancel" @click.capture="sort.click" @keydown="keyboardSort($event,'focus',sortKey(item))"><MemoRecordRow :record="item" :category="categoryLabel(item)" :writable="writable && !item.archived" :busy="!!pending" @open="open(item)" @complete="complete(item)" /></div><button v-if="focused.length > 3" type="button" class="memo-more" :aria-expanded="focusExpanded" @click="focusExpanded = !focusExpanded">{{ focusExpanded ? '收起' : `更多（${focused.length - 3}）` }}</button></div><p v-else class="memo-empty">还没有关注的条目。</p></section>
        <section class="memo-section" aria-label="日程安排"><button ref="calendarTrigger" type="button" class="memo-calendar-trigger" @click="calendarOpen = true"><h3>日程安排</h3><span>{{ dayLabel(selectedDay) }} <span aria-hidden="true">▦</span></span></button><div v-if="daySchedules.length" class="memo-block"><MemoRecordRow v-for="item in scheduleExpanded ? daySchedules : daySchedules.slice(0,3)" :key="item.occurrence_key??item.id" :record="item" :day="selectedDay" :writable="false" @open="open(item)" /><button v-if="daySchedules.length > 3" type="button" class="memo-more" :aria-expanded="scheduleExpanded" @click="scheduleExpanded = !scheduleExpanded">{{ scheduleExpanded ? '收起' : `更多（${daySchedules.length - 3}）` }}</button></div><p v-else class="memo-empty">{{ daySchedulesKnown?'这一天没有日程安排。':calendarLoading?'正在核对日程…':'重复日程尚未缓存，请联网后打开月历查看。' }}</p></section>
        <button type="button" class="memo-directory-entry" @click="go('directory')"><span aria-hidden="true">▤</span><strong>日程与备忘</strong><span aria-hidden="true">›</span></button>
      </template>
      <template v-else-if="screen === 'directory'"><button type="button" class="memo-directory-entry memo-system-entry" @click="go('schedules')"><span aria-hidden="true">▦</span><span class="memo-system-copy"><strong>日程安排</strong><small>固定日程清单 · 按发生日期查看</small></span><span aria-hidden="true">›</span></button><div class="memo-section-head"><h3>备忘分类</h3><button v-if="writable" type="button" class="memo-text-button" @click="showCategoryDialog">＋ 新建分类</button></div><div v-if="categories.length" class="memo-category-grid"><div v-for="item in categories" :key="item.id" class="memo-category-tile"><button type="button" class="memo-category-card" :class="{held:sort.held.value===item.id,over:sort.over.value===item.id}" :data-memo-sort="item.id" data-sort-group="category" @pointerdown="sort.down($event,item.id,'category')" @pointermove="sort.move" @pointerup="sort.up" @pointercancel="sort.cancel" @click.capture="sort.click" @keydown="keyboardSort($event,'category',item.id)" @contextmenu.prevent="manageCategory(item,'rename')" @keydown.shift.f10.prevent="manageCategory(item,'rename')" @click="categoryId = item.id; showingArchive=false;go('category')"><span aria-hidden="true">▤</span><strong :title="item.name">{{ item.name }}</strong><small>{{ records.filter(record => record.kind === 'memo' && record.category_id === item.id).length }} 条备忘</small></button></div></div><p v-else class="memo-empty">还没有备忘分类，先建一个吧。</p></template>
      <template v-else-if="screen === 'category' || screen === 'schedules'"><div v-if="screen==='category'||screen==='schedules'" class="memo-section-head"><button type="button" class="memo-text-button" @click="showingArchive=!showingArchive">{{ showingArchive?'返回当前备忘':'查看归档' }}</button><span class="memo-muted">{{ showingArchive?'归档记录':'当前记录' }}</span></div><div v-if="(screen === 'category' ? categoryRecords : allSchedules).length" class="memo-block"><template v-for="item in screen === 'category' ? categoryRecords : allSchedules" :key="item.id"><p v-if="item.kind === 'schedule'" class="memo-list-date">{{ dayLabel(item.start_date) }}</p><MemoRecordRow :record="item" :writable="writable && !item.archived" :busy="!!pending" @open="open(item)" @complete="complete(item)" /></template></div><p v-else class="memo-empty">{{ screen === 'category' ? '这个分类还没有备忘。' : '还没有日程安排。' }}</p></template>
      <article v-else-if="screen === 'detail' && record" class="memo-detail"><div class="memo-title-block"><p class="memo-eyebrow">{{ categoryLabel(record) }} · {{ scopeLabel }}</p><h2 class="memo-record-title">{{ record.title }}</h2><p class="memo-muted">{{ record.creator_name }}记录</p><p v-if="record.pending" class="memo-muted">已保存到本机 · 待同步</p><p v-if="record.error" class="memo-error" role="alert">{{ record.error }}</p></div><section v-if="record.kind === 'memo'" aria-label="备忘状态"><div v-if="writable && !record.archived" class="memo-record-actions"><button type="button" class="memo-primary" :disabled="!!pending" @click="complete(record)">{{ record.completed ? '重新打开' : '标记完成' }}</button><button type="button" v-if="focusEnabled" class="memo-secondary" :disabled="!!pending" :aria-pressed="record.focused" @click="focus(record)">{{ record.focused ? '移出待办' : '加入待办事项' }}</button></div><p class="memo-muted memo-caption">{{ record.completed ? '已完成，仍保留在原分类。' : source.scope === 'shared' ? '家庭成员完成一次即可。' : '完成后仍保留在原分类。' }}</p></section><dl v-else class="memo-range"><div><dt>开始</dt><dd>{{ dayLabel(record.start_date) }} · {{ record.start_time || '全天' }}</dd></div><div v-if="record.end_date"><dt>结束</dt><dd>{{ dayLabel(record.end_date) }} · {{ record.end_time || '全天' }}</dd></div></dl><section class="memo-reading memo-markdown" aria-label="正文"><slot name="markdown" :markdown="record.body"><p class="memo-plaintext">{{ record.body || '暂无正文' }}</p></slot></section><section v-if="record.reminders?.length||record.recurrence" class="memo-config-summary"><h3>提醒与重复</h3><ul v-if="record.reminders?.length"><li v-for="reminder in record.reminders" :key="reminder.id">{{ reminder.mode==='relative'?`日程开始前 ${reminder.offset_minutes} 分钟`:reminder.at?new Date(reminder.at).toLocaleString('zh-CN'):'未指定时间' }} · {{ reminder.all_members?'家庭全员':reminder.recipient_ids.map(id=>members.find(m=>m.id===id)?.display_name??'原成员').join('、') }}</li></ul><p v-if="record.recurrence" class="memo-muted">{{ record.recurrence.calendar==='lunar'?'农历':'公历' }} · 每 {{ record.recurrence.interval }} {{ ({daily:'天',weekly:'周',monthly:'月',yearly:'年'})[record.recurrence.frequency] }}</p></section><MemoAttachments v-if="record.attachments.length" :files="record.attachments" @request="attachmentRequest" /><section v-if="writable" class="memo-record-actions"><button type="button" class="memo-secondary" :disabled="!!pending" @click="archiveRecord">{{ record.archived?'取消归档':'归档' }}</button><button type="button" class="memo-text-button" :disabled="!!pending" @click="removeRecord">{{ record.can_delete?'删除记录':'申请删除' }}</button></section><section v-for="request in deletionRequests.filter(r=>r.memo_id===record!.id&&r.status==='pending')" :key="request.id" class="memo-setting-group"><h3>删除申请</h3><p>{{ request.reason||'未填写原因' }}</p><div v-if="request.author_id===source.user_id" class="memo-record-actions"><button type="button" class="memo-secondary" :disabled="!!pending" @click="sendOperation('deletion_request',request.id,request.version,'decide',{accepted:true})">同意删除</button><button type="button" class="memo-text-button" :disabled="!!pending" @click="sendOperation('deletion_request',request.id,request.version,'decide',{accepted:false})">拒绝删除</button></div></section></article>
      <MemoComposer v-else-if="screen === 'editor' && editor" :key="editorSource ? sourceKey(editorSource) + (editor.id ?? 'new') : 'editor'" :initial="editor" :locked-kind="lockedKind" :categories="categories" :saving="pending?.action === 'save'" :error="operationError" :schedule-enabled="scheduleEnabled" :focus-enabled="focusEnabled" :members="members" :current-user-id="source.user_id" @save="save" @cancel="cancelEditor" @change="changeDraft" @create-category="showCategoryDialog" @request-attachment="attachmentRequest"><template #markdown="slotProps"><slot name="markdown" :markdown="slotProps.markdown"><p class="memo-plaintext">{{ slotProps.markdown || '还没有正文。' }}</p></slot></template></MemoComposer>
      <p v-else class="memo-empty">记录不可用，请返回后刷新。</p>
    </main>
    <p class="memo-sr" role="status" aria-live="polite">{{ notice }}</p>
    <MemoCalendar v-if="calendarOpen" :selected="selectedDay" :schedules="schedules" :loading="calendarLoading" :error="calendarError" @month="emit('calendar-month',$event)" @select="selectDay" @close="closeCalendar" />
    <div v-if="deletingRecord" class="memo-overlay" @keydown.esc.stop="deletingRecord=false" @click.self="deletingRecord=false"><form class="memo-modal" role="dialog" aria-modal="true" aria-label="申请删除记录" @keydown="trapDialogTab" @submit.prevent="requestDeletion"><h3>向原记录人申请删除</h3><label class="memo-field">申请原因<input v-model="deletionReason" autofocus></label><div class="memo-form-actions"><button type="button" class="memo-text-button" @click="deletingRecord=false">取消</button><button type="submit" class="memo-primary">提交申请</button></div></form></div>
    <div v-if="categoryDialog" class="memo-overlay" @click.self="!pending && closeCategoryDialog()" @keydown.esc.stop="!pending && closeCategoryDialog()"><form class="memo-modal" role="dialog" aria-modal="true" aria-labelledby="memo-category-dialog-title" @submit.prevent="createCategory" @keydown="trapDialogTab"><h3 id="memo-category-dialog-title">{{ categoryMode==='create'?'新建分类':categoryMode==='rename'?'修改分类':'删除分类' }}</h3><button v-if="categoryMode==='rename'" type="button" class="memo-text-button" @click="categoryMode='delete'">删除分类并整体移入其他分类</button><label v-if="categoryMode!=='delete'" class="memo-field">分类名称<input ref="categoryInput" v-model="categoryName" :disabled="!!pending" @keydown.enter.prevent="event => { if (!event.isComposing) createCategory() }"></label><template v-if="categoryMode==='delete'"><p>分类中的全部记录、附件与状态会一并保留。</p><label class="memo-field">移入分类<select v-model="mergeCategoryId" :disabled="!!pending"><option value="">请选择接收分类</option><option v-for="item in categories.filter(c=>c.id!==managedCategory?.id)" :key="item.id" :value="item.id">{{ item.name }}</option></select></label></template><p v-if="operationError" class="memo-error" role="alert">{{ operationError }}</p><div class="memo-form-actions"><button type="button" class="memo-text-button" :disabled="!!pending" @click="closeCategoryDialog">取消</button><button type="submit" class="memo-primary" :disabled="!!pending">{{ pending ? '正在保存…' : categoryMode==='create'?'创建分类':categoryMode==='rename'?'保存名称':'整体移入并删除' }}</button></div></form></div>
  </section>
</template>
