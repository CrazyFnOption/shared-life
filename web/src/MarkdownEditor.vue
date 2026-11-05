<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { Compartment, EditorState, StateEffect, StateField } from '@codemirror/state'
import type { Range } from '@codemirror/state'
import { Decoration, EditorView, keymap, placeholder as editorPlaceholder } from '@codemirror/view'
import type { DecorationSet } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, undo, redo } from '@codemirror/commands'
import { markdown, markdownKeymap } from '@codemirror/lang-markdown'
import { syntaxTree } from '@codemirror/language'
import './markdown-editor.css'

const props = withDefaults(defineProps<{modelValue:string;disabled?:boolean;label?:string;placeholder?:string}>(),{disabled:false,label:'正文，支持 Markdown',placeholder:'写下内容，也可以用 Markdown 排版'})
const emit = defineEmits<{'update:modelValue':[value:string];'composition-state':[active:boolean]}>()
const host = ref<HTMLElement>()
let view:EditorView|undefined
const editable = new Compartment()
const activity = StateEffect.define<boolean>()
let focused=false, composing=false, settling=false
let appliedDisabled=props.disabled

function decorate(state:EditorState,active:boolean):DecorationSet {
 const ranges:Range<Decoration>[]=[]
 const activeRanges=active?state.selection.ranges.map(r=>({from:state.doc.lineAt(r.from).from,to:state.doc.lineAt(r.to).to})):[]
 // Expand the editing area to complete syntax blocks; cross-line constructs stay raw.
 syntaxTree(state).iterate({enter(node){if(/^(FencedCode|CodeBlock|Blockquote|ListItem|Paragraph|ATXHeading\d|SetextHeading\d)$/.test(node.name)){for(const range of activeRanges)if(node.from<=range.to&&node.to>=range.from){range.from=Math.min(range.from,node.from);range.to=Math.max(range.to,node.to)}}}})
 const intersects=(from:number,to:number)=>activeRanges.some(r=>from<=r.to&&to>=r.from)
 function lineClasses(from:number,to:number,cls:string){for(let pos=state.doc.lineAt(from).from;pos<=to;){ranges.push(Decoration.line({class:cls}).range(pos));const line=state.doc.lineAt(pos);if(line.to===state.doc.length)break;pos=line.to+1}}
 syntaxTree(state).iterate({enter(node){
  const {name,from,to}=node
  if(name==='Image')return false // External images always remain safe visible Markdown text.
  if(intersects(from,to))return
  if(/^ATXHeading\d$|^SetextHeading\d$/.test(name))lineClasses(from,to,`md-heading md-h${name.slice(-1)}`)
  else if(name==='StrongEmphasis')ranges.push(Decoration.mark({class:'md-strong'}).range(from,to))
  else if(name==='Emphasis')ranges.push(Decoration.mark({class:'md-emphasis'}).range(from,to))
  else if(name==='InlineCode')ranges.push(Decoration.mark({class:'md-inline-code'}).range(from,to))
  else if(name==='FencedCode'||name==='CodeBlock'){
   lineClasses(from,to,'md-code-line')
   if(name==='FencedCode'){for(const mark of [...node.node.getChildren('CodeMark'),...node.node.getChildren('CodeInfo')])ranges.push(Decoration.replace({}).range(mark.from,mark.to))}
   return false
  }
  else if(name==='Blockquote')lineClasses(from,to,'md-quote-line')
  else if(name==='ListItem')lineClasses(from,to,'md-list-line')
  else if(name==='Link'){
   const url=node.node.getChild('URL')
   // Reference links and incomplete syntax remain literal rather than hiding unknown content.
   if(!url)return false
   const labelEnd=node.node.getChildren('LinkMark').find(n=>state.doc.sliceString(n.from,n.to)===']')
   if(!labelEnd)return false
   ranges.push(Decoration.mark({class:'md-link'}).range(from+1,labelEnd.from))
   ranges.push(Decoration.replace({}).range(from,from+1),Decoration.replace({}).range(labelEnd.from,to))
   return false
  }
  else if(['HeaderMark','EmphasisMark','CodeMark','QuoteMark'].includes(name)){
   let end=to
   if(name==='HeaderMark'&&state.doc.sliceString(to,to+1)===' ')end++
   ranges.push(Decoration.replace({}).range(from,end))
  }
 }})
 return Decoration.set(ranges,true)
}
const display = StateField.define<{focused:boolean;decorations:DecorationSet}>({
 create:state=>({focused:false,decorations:decorate(state,false)}),
 update(value,tr){
  let f=value.focused
  for(const effect of tr.effects)if(effect.is(activity))f=effect.value
  if(composing||settling)return{focused:f,decorations:value.decorations.map(tr.changes)}
  if(tr.docChanged||tr.selection||syntaxTree(tr.startState)!==syntaxTree(tr.state)||tr.effects.some(e=>e.is(activity)))return{focused:f,decorations:decorate(tr.state,f)}
  return value
 },provide:field=>EditorView.decorations.from(field,v=>v.decorations)
})
function signal(){if(!composing&&!settling)view?.dispatch({effects:activity.of(focused)})}
function syncModel(){if(view&&!composing&&!settling&&props.modelValue!==view.state.doc.toString())view.dispatch({changes:{from:0,to:view.state.doc.length,insert:props.modelValue}})}
function syncDisabled(){if(view&&!composing&&!settling&&props.disabled!==appliedDisabled){appliedDisabled=props.disabled;view.dispatch({effects:editable.reconfigure([EditorState.readOnly.of(props.disabled),EditorView.editable.of(!props.disabled)])})}}
function finishComposition(){
 const editor=view
 // iOS may leave the chosen text in pending DOM mutations until the next
 // frame. A dispatch before CodeMirror reads those mutations erases it and
 // redraws the previous pinyin. Its measurement phase flushes input first;
 // defer our display/prop updates until that phase has also finished.
 editor?.requestMeasure({key:activity,read:()=>null,write:()=>queueMicrotask(()=>{
  if(view!==editor||composing)return
  settling=false
  syncModel();syncDisabled();signal()
  emit('composition-state',false)
 })})
}
function format(kind:'heading'|'bold'|'list'|'link'){
 if(!view||props.disabled||composing||settling)return
 const {from,to}=view.state.selection.main,selected=view.state.sliceDoc(from,to)
 if(kind==='heading'||kind==='list'){
  const start=view.state.doc.lineAt(from).from,end=view.state.doc.lineAt(to).to
  const prefix=kind==='heading'?'## ':'- ',text=view.state.sliceDoc(start,end).split('\n').map(line=>prefix+line).join('\n')
  view.dispatch({changes:{from:start,to:end,insert:text},selection:{anchor:start+prefix.length,head:start+text.length},userEvent:'input.format'})
 }else{
  const before=kind==='bold'?'**':'[',after=kind==='bold'?'**':'](https://)',text=selected||(kind==='bold'?'文字':'链接文字')
  view.dispatch({changes:{from,to,insert:before+text+after},selection:{anchor:from+before.length,head:from+before.length+text.length},userEvent:'input.format'})
 }
 view.focus()
}
function historyAction(action:'undo'|'redo'){if(!view||props.disabled||composing||settling)return;(action==='undo'?undo:redo)(view);view.focus()}
onMounted(()=>{
 view=new EditorView({parent:host.value,state:EditorState.create({doc:props.modelValue,extensions:[
  markdown(),history(),keymap.of([...markdownKeymap,...defaultKeymap,...historyKeymap]),EditorView.lineWrapping,
  editable.of([EditorState.readOnly.of(props.disabled),EditorView.editable.of(!props.disabled)]),
  EditorView.contentAttributes.of({'aria-label':props.label,'aria-multiline':'true',role:'textbox',autocapitalize:'sentences'}),editorPlaceholder(props.placeholder),display,
  EditorView.updateListener.of(update=>{if(update.docChanged)emit('update:modelValue',update.state.doc.toString())}),
  EditorView.domEventHandlers({focus(){focused=true;signal()},blur(){focused=false;signal()},compositionstart(){composing=true;emit('composition-state',true)},compositionend(){composing=false;settling=true;finishComposition()}})
 ]})})
})
watch(()=>props.modelValue,syncModel)
watch(()=>props.disabled,syncDisabled)
onBeforeUnmount(()=>{view?.destroy();view=undefined;emit('composition-state',false)})
defineExpose({focus:()=>view?.focus()})
</script>
<template>
 <section class="markdown-editor" :class="{'is-disabled':disabled}" :aria-label="label">
  <div class="md-toolbar" role="toolbar" aria-label="正文格式" @mousedown.prevent>
   <button type="button" :disabled="disabled" aria-label="插入标题" title="标题" @click="format('heading')">H</button>
   <button type="button" :disabled="disabled" aria-label="加粗" title="加粗" @click="format('bold')"><strong>B</strong></button>
   <button type="button" :disabled="disabled" aria-label="插入列表" title="列表" @click="format('list')">☷</button>
   <button type="button" :disabled="disabled" aria-label="插入链接" title="链接" @click="format('link')">↗</button>
   <span class="md-toolbar-spacer" />
   <button type="button" :disabled="disabled" aria-label="撤销正文修改" title="撤销" @click="historyAction('undo')">↶</button>
   <button type="button" :disabled="disabled" aria-label="重做正文修改" title="重做" @click="historyAction('redo')">↷</button>
  </div>
  <div ref="host" class="md-document" />
  <p class="md-hint">支持 Markdown · 点按正文直接编辑</p>
 </section>
</template>
