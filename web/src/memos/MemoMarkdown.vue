<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
const props=defineProps<{markdown:string}>()
const parser=new MarkdownIt({html:false,linkify:false,typographer:false})
// Markdown-linked images must not contact external servers during a private read.
// Photos belong to the record's authenticated attachments and are rendered there.
const html=computed(()=>DOMPurify.sanitize(parser.render(props.markdown),{USE_PROFILES:{html:true},FORBID_TAGS:['img','style'],FORBID_ATTR:['style','id','name']}))
</script>
<template><div class="memo-markdown" v-html="html" /></template>
