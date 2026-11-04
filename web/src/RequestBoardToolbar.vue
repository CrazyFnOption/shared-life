<script setup lang="ts">
defineProps<{busy:boolean;historyOpen:boolean;historyCount:number;refreshLabel?:string}>()
const emit=defineEmits<{refresh:[];toggleHistory:[]}>()
</script>
<template><div class="request-board-toolbar"><button class="subtle" :disabled="busy" @click="emit('refresh')">{{ refreshLabel||'刷新更正待办' }}</button><button class="subtle" :disabled="busy || !historyCount" :aria-expanded="historyOpen" @click="emit('toggleHistory')">{{ historyOpen?'收起历史更正记录':'查看历史更正记录' }}（{{ historyCount }}）</button></div></template>
