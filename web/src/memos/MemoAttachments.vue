<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import type { MemoAttachmentView } from './types'
import { trapDialogTab } from './dialog'

const props = withDefaults(defineProps<{ files: MemoAttachmentView[]; editable?: boolean; disabled?: boolean; embedded?: boolean }>(), { editable: false, disabled: false, embedded: false })
const emit = defineEmits<{ add: [files: MemoAttachmentView[]]; remove: [id: string]; request: [file: MemoAttachmentView] }>()
const photos = ref<HTMLInputElement>()
const documents = ref<HTMLInputElement>()
const urls = ref<Record<string, string>>({})
const readErrors=ref<Record<string,string>>({})
const previewId=ref(''),previewClose=ref<HTMLButtonElement>()
const previewFile=computed(()=>props.files.find(file=>file.id===previewId.value))
let previewOpener:HTMLElement|null=null
function showPhoto(file:MemoAttachmentView,event:Event){previewId.value=file.id;previewOpener=event.currentTarget as HTMLElement;void nextTick(()=>previewClose.value?.focus())}
function closePhoto(){previewId.value='';void nextTick(()=>{if(previewOpener?.isConnected)previewOpener.focus();previewOpener=null})}
let urlGeneration=0
function clearUrls() { Object.values(urls.value).forEach(url => URL.revokeObjectURL(url)); urls.value = {} }
watch(() => props.files.map(file => [file.id, file.blob] as const), async (entries, previous) => {
  if(previous && entries.length===previous.length && entries.every((entry,index)=>entry[0]===previous[index]?.[0] && entry[1]===previous[index]?.[1]))return
  const generation=++urlGeneration,files=[...props.files]
  if(previewId.value&&!files.some(file=>file.id===previewId.value&&file.blob))closePhoto()
  clearUrls();readErrors.value={}
  for (const file of files) if (file.blob) try {
    const bytes=await file.blob.arrayBuffer()
    if(generation!==urlGeneration)return
    urls.value[file.id]=URL.createObjectURL(new Blob([bytes],{type:file.mime_type}))
  }catch{if(generation===urlGeneration)readErrors.value[file.id]='本机文件暂时无法读取，请联网后加载或重试。'}
}, { immediate: true })
onUnmounted(()=>{urlGeneration++;clearUrls()})
function choose(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? []).map(file => ({ id: crypto.randomUUID(), kind: input.accept === 'image/*' ? 'photo' as const : 'file' as const, file_name: file.name, mime_type: file.type || 'application/octet-stream', size_bytes: file.size, blob: file }))
  if (files.length) emit('add', files)
  input.value = ''
}
function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB` }
function previewable(file: MemoAttachmentView) { return /^image\/(png|jpeg|gif|webp|avif)$/.test(file.mime_type) }
</script>

<template>
  <section class="memo-assets" :class="{ 'memo-assets-embedded': embedded }" aria-label="照片与附件">
    <h3 v-if="!embedded">照片与附件</h3>
    <div v-if="editable" class="memo-file-controls">
      <button type="button" class="memo-secondary" :disabled="disabled" @click="photos?.click()">{{ embedded ? '＋ 添加图片' : '＋ 照片' }}</button>
      <button type="button" class="memo-secondary" :disabled="disabled" @click="documents?.click()">{{ embedded ? '＋ 添加文件' : '＋ 附件' }}</button>
      <input ref="photos" type="file" accept="image/*" multiple hidden aria-label="选择照片" @change="choose">
      <input ref="documents" type="file" multiple hidden aria-label="选择附件" @change="choose">
    </div>
    <ul v-if="files.length" class="memo-file-list">
      <li v-for="file in files" :key="file.id" class="memo-file">
        <button v-if="urls[file.id] && previewable(file)" type="button" class="memo-file-open" :aria-label="`预览照片 ${file.file_name}`" @click="showPhoto(file,$event)"><img :src="urls[file.id]" alt="" class="memo-file-thumb"><span class="memo-file-copy"><strong>{{ file.file_name }}</strong><small>{{ size(file.size_bytes) }}<template v-if="file.pending"> · 待同步</template></small></span></button>
        <a v-else-if="urls[file.id]" :href="urls[file.id]" :download="file.file_name" class="memo-file-open" :aria-label="`下载 ${file.file_name}`">
          <img v-if="previewable(file)" :src="urls[file.id]" alt="" class="memo-file-thumb">
          <span v-else class="memo-file-symbol" aria-hidden="true">↧</span>
          <span class="memo-file-copy"><strong>{{ file.file_name }}</strong><small>{{ size(file.size_bytes) }}<template v-if="file.pending"> · 待同步</template></small></span>
        </a>
        <button v-else type="button" class="memo-file-open" @click="emit('request', file)"><span class="memo-file-symbol" aria-hidden="true">↧</span><span class="memo-file-copy"><strong>{{ file.file_name }}</strong><small>{{ size(file.size_bytes) }} · 点此加载</small></span></button>
        <button v-if="editable" type="button" class="memo-text-button" :disabled="disabled" :aria-label="`移除 ${file.file_name}`" @click="emit('remove', file.id)">移除</button>
        <p v-if="file.error||readErrors[file.id]" class="memo-error" role="alert">{{ file.error||readErrors[file.id] }}</p>
      </li>
    </ul>
    <p v-else-if="!editable" class="memo-muted">没有附件。</p>
    <div v-if="previewFile && urls[previewFile.id]" class="memo-overlay memo-photo-overlay" @click.self="closePhoto" @keydown.esc.stop="!$event.isComposing && closePhoto()"><section class="memo-photo-dialog" role="dialog" aria-modal="true" :aria-label="`照片预览：${previewFile.file_name}`" @keydown="trapDialogTab"><header><strong>{{ previewFile.file_name }}</strong><button ref="previewClose" type="button" class="memo-text-button" aria-label="关闭照片预览" @click="closePhoto">关闭</button></header><img :src="urls[previewFile.id]" :alt="previewFile.file_name"><a :href="urls[previewFile.id]" :download="previewFile.file_name" class="memo-photo-download">下载照片</a></section></div>
  </section>
</template>
