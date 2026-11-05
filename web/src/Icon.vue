<script setup lang="ts">
withDefaults(defineProps<{name?:string}>(),{name:'receipt'})
const paths:Record<string,string>={
  bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',wallet:'M3 5h17v15H3V5Zm0 0 14-3v3M15 11h6v5h-6z',calendar:'M4 5h16v16H4V5ZM8 3v4m8-4v4M4 10h16m-9 6 2 2 4-4',heart:'M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-4 4 2 10 8 15 6-5 12-11 8-15Z',user:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2',users:'M15 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM3 21v-2a8 8 0 0 1 16 0v2M18 3a4 4 0 0 1 0 8m2 4a6 6 0 0 1 2 5',back:'m14 6-6 6 6 6',next:'m9 6 6 6-6 6',
  sync:'M20 7a8 8 0 0 0-13-3L3 8m0-5v5h5M4 17a8 8 0 0 0 13 3l4-4m0 5v-5h-5',offline:'m3 3 18 18M4 9a13 13 0 0 1 3-2m4-1a13 13 0 0 1 9 3M7 13a8 8 0 0 1 4-2m5 1 1 1M10 17a3 3 0 0 1 4 0M12 21h.01',warning:'m12 3 10 18H2L12 3Zm0 6v5m0 3h.01',
  chevron:'m6 9 6 6 6-6', check:'m5 12 4 4L19 6',search:'M21 21l-5-5 M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14',
  photo:'M3 3h18v18H3z M3 16l5-5 5 5 3-3 5 5 M15 7h.01',close:'m6 6 12 12M6 18 18 6',plus:'M12 5v14M5 12h14',
  餐饮:'M5 3v7m3-7v7M4 7h5m-2 3v11M17 3c-3 4-3 8 0 8h2V3h-2v18',购物:'M5 7h14l1 14H4L5 7Zm4 0V5a3 3 0 0 1 6 0v2',
  房租:'m3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7',水电网:'m13 2-8 12h6l-1 8 9-13h-6l1-7',交通:'M5 17H3V9l2-5h14l2 5v8h-2M3 10h18M6 14h2m8 0h2M5 17v3m14-3v3',receipt:'M5 3h14v18l-3-2-4 2-4-2-3 2V3M8 8h8M8 12h6',lock:'M6 10h12v11H6z M8 10V6a4 4 0 0 1 8 0v4'}
</script>
<template><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] ?? paths.receipt"/></svg></template>
