<script setup lang="ts">
import { computed } from 'vue'

const props=withDefaults(defineProps<{modelValue:string;disabled?:boolean;label?:string}>(),{disabled:false,label:'提醒时间'})
const emit=defineEmits<{'update:modelValue':[value:string];change:[value:string]}>()
const hours=Array.from({length:24},(_,hour)=>String(hour).padStart(2,'0'))
const minutes=Array.from({length:60},(_,minute)=>String(minute).padStart(2,'0'))
const hour=computed(()=>props.modelValue.slice(0,2))
const minute=computed(()=>props.modelValue.slice(3,5))
function change(part:'hour'|'minute',event:Event){
 const value=(event.target as HTMLSelectElement).value
 const clock=part==='hour'?`${value}:${minute.value}`:`${hour.value}:${value}`
 emit('update:modelValue',clock)
 emit('change',clock)
}
</script>

<template>
 <fieldset class="memo-time-input" :disabled="disabled">
  <legend class="memo-sr">{{ label }}</legend>
  <label><span class="memo-sr">{{ label }}小时</span><select :aria-label="`${label}小时`" :value="hour" @change="change('hour',$event)"><option v-for="value in hours" :key="value" :value="value">{{ value }} 时</option></select></label>
  <label><span class="memo-sr">{{ label }}分钟</span><select :aria-label="`${label}分钟`" :value="minute" @change="change('minute',$event)"><option v-for="value in minutes" :key="value" :value="value">{{ value }} 分</option></select></label>
 </fieldset>
</template>
