<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { api } from './api'
import Icon from './Icon.vue'
import type { AccountSource } from './types'

const props = defineProps<{origin:AccountSource;open:boolean}>()
const emit = defineEmits<{count:[value:number|undefined];close:[]}>()
const items = ref<Awaited<ReturnType<typeof api.feedback>>>([])
const archived = ref(false)
const editing = ref(false)
const body = ref('')
let requestId = ''
let active = true
const busy = ref(false)
const error = ref('')
const visible = computed(()=>items.value.filter(item=>Boolean(item.completed_at)===archived.value))
async function load(){
  try {
    const result = await api.feedback(props.origin)
    if (!active) return
    items.value = result
    emit('count',result.filter(item=>!item.completed_at).length)
    error.value = ''
  } catch { if(active){error.value='反馈加载失败，请重试';emit('count',undefined)} }
}
function start(){
  requestId = crypto.randomUUID()
  body.value = ''
  editing.value = true
  archived.value = false
}
async function submit(id?:string){
  if(busy.value || (!id && !body.value.trim())) return
  busy.value = true
  try {
    if(id) await api.completeFeedback(props.origin,id)
    else {
      await api.addFeedback(props.origin,requestId,body.value.trim())
      editing.value = false
    }
    await load()
  } catch { error.value='操作未确认，请重试' }
  finally { busy.value = false }
}
onMounted(load)
onBeforeUnmount(()=>{active=false})
watch(()=>props.open,()=>{if(!busy.value) void load()})
</script>
<template>
  <section v-if="open" class="config-page config-panel feedback-panel" aria-labelledby="feedback-title">
    <header class="form-title config-page-head feedback-heading">
      <button type="button" class="config-back" @click="emit('close')"><Icon name="chevron"/>返回</button>
      <p class="config-kicker">配置</p>
      <h2 id="feedback-title" tabindex="-1">反馈</h2>
      <button v-if="!editing" type="button" class="primary" @click="start">＋ 新建</button>
    </header>
    <form v-if="editing" class="stack" @submit.prevent="submit()">
      <label>需要改进什么？<textarea v-model="body" required maxlength="2000" rows="4"/></label>
      <div class="feedback-form-actions">
        <button class="primary" :disabled="busy || !body.trim()">保存</button>
        <button class="subtle" type="button" :disabled="busy" @click="editing=false">取消</button>
      </div>
    </form>
    <nav class="invite-tabs feedback-filters" aria-label="反馈状态">
      <button v-for="option in [false,true]" :key="String(option)" type="button"
        :class="{active:archived===option}" :aria-pressed="archived===option" @click="archived=option">
        {{ option?'已归档':'未完成' }}
      </button>
    </nav>
    <p v-if="error" class="field-error" role="alert">{{ error }} <button class="text-button" @click="load">重新加载</button></p>
    <ul v-else-if="visible.length" class="feedback-list">
      <li v-for="item in visible" :key="item.id">
        <span>{{ item.body }}</span>
        <button v-if="!archived" type="button" class="subtle" :disabled="busy" @click="submit(item.id)">完成</button>
      </li>
    </ul>
    <p v-else class="empty">{{ archived?'暂无已归档反馈':'暂无未完成的改进' }}</p>
  </section>
</template>
<style scoped>
.feedback-heading { grid-template-columns: auto minmax(0, 1fr) auto; }
.feedback-heading h2 { grid-column: 1 / 3; }
.feedback-heading .primary { grid-column: 3; grid-row: 2; width: auto; white-space: nowrap; }
.feedback-filters { margin-top: 0; }
.feedback-panel form { margin-bottom: 24px; }
.feedback-form-actions { display: flex; gap: 12px; }
.feedback-form-actions .primary { width: auto; }
.feedback-list { list-style: none; margin: 0; padding: 0; }
.feedback-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--sl-line);
}
.feedback-list span { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.feedback-list button { flex: none; }
</style>
