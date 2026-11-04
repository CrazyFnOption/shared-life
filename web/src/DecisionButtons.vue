<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { DecisionAttempt } from './requestBoard'
const props=defineProps<{busy:boolean;acceptLabel:string;rejectLabel:string;frozen?:DecisionAttempt;contextKey:string}>()
const emit=defineEmits<{decision:[decision:'accept'|'reject',reason:string]}>()
const editing=ref(false),reason=ref('')
const reasonInput=ref<HTMLTextAreaElement>()
watch(editing,open=>{if(open)void nextTick(()=>reasonInput.value?.focus())})
const hasUnsavedChanges=computed(()=>!!props.frozen || !!reason.value)
defineExpose({hasUnsavedChanges})
watch(()=>props.contextKey,()=>{editing.value=false;reason.value=''})
</script>
<template>
  <div v-if="editing || frozen?.decision==='reject'" class="rejection-editor">
    <label>拒绝原因（选填）<textarea ref="reasonInput" :value="frozen?.rejection_reason??reason" @input="reason=($event.target as HTMLTextAreaElement).value" :disabled="busy || !!frozen" maxlength="500" rows="2" placeholder="可以说明拒绝原因"></textarea></label>
    <p v-if="frozen" class="form-note">上次结果未确认，重试沿用原决定编号和拒绝原因。</p>
    <div class="action-row decision-actions" :aria-busy="busy"><button v-if="!frozen" class="subtle" :disabled="busy" @click="editing=false">取消拒绝</button><button class="primary" :disabled="busy" @click="emit('decision','reject',frozen?.rejection_reason??reason)">{{ busy?'正在提交…':frozen?'重试原拒绝':'确认拒绝' }}</button></div>
  </div>
  <div v-else class="action-row decision-actions" :aria-busy="busy"><button class="subtle" :disabled="busy || !!frozen" @click="editing=true">{{ rejectLabel }}</button><button class="primary" :disabled="busy" @click="emit('decision','accept','')">{{ busy?'正在提交…':frozen?'重试原接受':acceptLabel }}</button></div>
</template>
