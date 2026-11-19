/** Task syntax comes from the installed GFM parser; IDs and occurrence state stay in schema13. */
import { markdownLanguage } from '@codemirror/lang-markdown'
import { MapMode } from '@codemirror/state'
import type { ChangeDesc } from '@codemirror/state'
import type { ChecklistItem } from './domain'

export interface MarkdownTask extends ChecklistItem {from:number;to:number;marker:number;checked:boolean}
export function taskLines(body:string):Omit<MarkdownTask,'id'>[]{
 const result:Omit<MarkdownTask,'id'>[]=[]
 markdownLanguage.parser.parse(body).iterate({enter(node){
  if(node.name!=='TaskMarker')return
  const from=body.lastIndexOf('\n',node.from-1)+1,end=body.indexOf('\n',node.to),to=end<0?body.length:end,text=body.slice(node.to,to).trim()
  if(text)result.push({from,to,marker:node.from,text,checked:/[xX]/.test(body.slice(node.from,node.to))})
 }})
 return result
}
export function bindTasks(body:string,items:ChecklistItem[]):MarkdownTask[]{
 const available=[...items]
 return taskLines(body).map(task=>{const index=available.findIndex(item=>item.text===task.text),item=index<0?undefined:available.splice(index,1)[0];return{...task,id:item?.id??crypto.randomUUID()}})
}
export function mapTaskEdits(before:MarkdownTask[],body:string,changes:ChangeDesc,known:ChecklistItem[]=before):MarkdownTask[]{
 const tasks=taskLines(body),assigned=new Map<number,string>(),used=new Set<string>()
 for(const prior of before){
  const marker=changes.mapPos(prior.marker,1,MapMode.TrackDel)
  const index=marker===null?-1:tasks.findIndex(task=>task.from<=marker&&marker<=task.to)
  if(index>=0&&!assigned.has(index)){assigned.set(index,prior.id);used.add(prior.id)}
 }
 return tasks.map((task,index)=>{const prior=known.find(item=>!used.has(item.id)&&item.text===task.text),id=assigned.get(index)??prior?.id??crypto.randomUUID();used.add(id);return{...task,id}})
}
/** Older independent items enter the same body once; opening a record never writes it. */
export function bodyWithItems(body:string,items:ChecklistItem[]):string{
 let lines=body.split('\n'),tasks=taskLines(body),used=new Set<number>(),ordinary=new Set<number>()
 markdownLanguage.parser.parse(body).iterate({enter(node){if(node.name==='ListItem'){const line=body.slice(node.from,body.indexOf('\n',node.from)<0?body.length:body.indexOf('\n',node.from));if(/^[-+*]\s+/.test(line)&&!/^[-+*]\s+\[[ xX]\]/.test(line))ordinary.add(body.slice(0,node.from).split('\n').length-1)}}})
 const missing:string[]=[]
 for(const item of items){
  const task=tasks.findIndex((task,index)=>!used.has(index)&&task.text===item.text)
  if(task>=0){used.add(task);continue}
  const line=lines.findIndex((line,index)=>ordinary.has(index)&&line.replace(/^\s*[-+*]\s+/,'').trim()===item.text)
  if(line>=0){lines[line]=lines[line]!.replace(/^(\s*[-+*]\s+)/,'$1[ ] ');ordinary.delete(line)}else missing.push('- [ ] '+item.text)
 }
 const content=lines.join('\n');return missing.length?content+(content&&!content.endsWith('\n')?'\n\n':'')+missing.join('\n'):content
}
export function taskBody(body:string,template:ChecklistItem[],items:ChecklistItem[],checked:Set<string>,historical=false):{body:string;tasks:MarkdownTask[]}{
 const compatible=bodyWithItems(body,template),bound=bindTasks(compatible,template),byId=new Map(items.map(item=>[item.id,item])),lines=compatible.split('\n'),seen=new Set<string>()
 for(const task of [...bound].reverse()){
  const index=compatible.slice(0,task.from).split('\n').length-1,item=byId.get(task.id)
  if(!item){if(historical)lines.splice(index,1);continue}
  seen.add(item.id);lines[index]=lines[index]!.slice(0,task.marker-task.from)+`[${checked.has(item.id)?'x':' '}] `+item.text
 }
 let projected=lines.join('\n')
 const absent=items.filter(item=>!seen.has(item.id));if(absent.length)projected+=(projected&&!projected.endsWith('\n')?'\n\n':'')+absent.map(item=>`- [${checked.has(item.id)?'x':' '}] ${item.text}`).join('\n')
 if(historical&&items.length){
  // Frozen names/order belong to the archived occurrence, even after template edits.
  const current=taskLines(projected),source=projected.split('\n'),indices=current.map(task=>projected.slice(0,task.from).split('\n').length-1),first=indices[0]??source.length
  for(const index of [...indices].reverse())source.splice(index,1)
  source.splice(first,0,...items.map(item=>`- [${checked.has(item.id)?'x':' '}] ${item.text}`));projected=source.join('\n')
 }
 return{body:projected,tasks:bindTasks(projected,items).filter(task=>byId.has(task.id))}
}
