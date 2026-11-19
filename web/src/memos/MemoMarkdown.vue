<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import type { MarkdownTask } from './markdownChecklist'
const props=defineProps<{markdown:string;tasks?:MarkdownTask[];writable?:boolean}>()
const emit=defineEmits<{check:[itemId:string,checked:boolean]}>()
const parser=new MarkdownIt({html:false,linkify:false,typographer:false})
parser.core.ruler.after('inline','memo_tasks',(state:any)=>{
 if(!props.tasks)return
 const byLine=new Map(props.tasks.map(task=>[props.markdown.slice(0,task.from).split('\n').length-1,task]))
 for(let index=0;index<state.tokens.length;index++){
  const token=state.tokens[index],task=token.type==='list_item_open'&&token.map?byLine.get(token.map[0]):undefined
  if(!task)continue
  const inline=state.tokens[index+2]
  if(inline?.type!=='inline'||!inline.children?.length||!/^\[[ xX]\]\s+/.test(inline.content))continue
  inline.children[0].content=inline.children[0].content.replace(/^\[[ xX]\]\s+/,'')
  token.attrJoin('class','md-task-item'+(task.checked?' is-checked':''))
  const open=new state.Token('memo_task_open','',0),close=new state.Token('memo_task_close','',0);open.meta=task
  inline.children.unshift(open);inline.children.push(close)
 }
})
parser.renderer.rules.memo_task_open=(tokens:any,index:number)=>{const task=tokens[index].meta;return '<label class="md-task-label"><input type="checkbox" data-memo-item="'+parser.utils.escapeHtml(task.id)+'" aria-label="'+parser.utils.escapeHtml(task.text)+'"'+(task.checked?' checked':'')+(props.writable?'':' disabled')+'><span>'}
parser.renderer.rules.memo_task_close=()=>'</span></label>'
// Source HTML and remote images stay inert; only our known task IDs can emit mutations.
const html=computed(()=>DOMPurify.sanitize(parser.render(props.markdown),{USE_PROFILES:{html:true},FORBID_TAGS:['img','style'],FORBID_ATTR:['style','id','name']}))
function changed(event:Event){const input=event.target as HTMLInputElement,id=input.dataset.memoItem;if(input.type==='checkbox'&&props.writable&&id&&props.tasks?.some(task=>task.id===id))emit('check',id,input.checked)}
</script>
<template><div class="memo-markdown" v-html="html" @change="changed" /></template>
<style scoped>
:deep(.md-task-item){list-style:none}:deep(.md-task-label){display:flex;gap:10px;align-items:flex-start;min-height:44px;cursor:pointer}:deep(.md-task-label>input){appearance:none;box-sizing:border-box;flex:none;width:22px;height:22px;padding:0;line-height:1;margin:3px 0 0;border:1.5px solid var(--sl-brand,#007f69);border-radius:50%;display:grid;place-items:center}:deep(.md-task-label>input:checked){background:var(--sl-brand,#007f69)}:deep(.md-task-label>input:checked:after){content:'✓';color:#fff;font-size:15px;line-height:1}:deep(.md-task-label>input:focus-visible){outline:2px solid var(--sl-brand,#007f69);outline-offset:3px}:deep(.md-task-label>span){min-width:0;overflow-wrap:anywhere}:deep(.md-task-item.is-checked>.md-task-label>span),:deep(.md-task-item.is-checked>p>.md-task-label>span){color:var(--sl-muted,#63766f);text-decoration:line-through}
</style>
