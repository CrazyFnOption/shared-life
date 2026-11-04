import { onUnmounted, ref } from 'vue'
export function useMemoSort(commit:(group:string,from:string,to:string)=>void,menu:(id:string)=>void){
 const held=ref(''),over=ref('');let timer:ReturnType<typeof setTimeout>|undefined,start:{id:string;group:string;x:number;y:number;element:HTMLElement;pointer:number}|undefined,suppress=false
 function clear(){if(timer)clearTimeout(timer);timer=undefined;start=undefined;held.value='';over.value=''}
 function down(event:PointerEvent,id:string,group:string){if(event.button!==0)return;clear();start={id,group,x:event.clientX,y:event.clientY,element:event.currentTarget as HTMLElement,pointer:event.pointerId};timer=setTimeout(()=>{if(!start)return;held.value=id;start.element.setPointerCapture?.(start.pointer)},450)}
 function move(event:PointerEvent){if(!start)return;if(!held.value){if(Math.hypot(event.clientX-start.x,event.clientY-start.y)>10)clear();return}const element=document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('[data-memo-sort]');if(element?.dataset.sortGroup===start.group)over.value=element.dataset.memoSort??''}
 function up(event:PointerEvent){if(!start)return;const capture=start;if(held.value){suppress=true;if(over.value&&over.value!==held.value)commit(capture.group,capture.id,over.value);else if(capture.group==='category')menu(capture.id);event.preventDefault()}clear()}
 function click(event:MouseEvent){if(suppress){event.preventDefault();event.stopImmediatePropagation();suppress=false}}
 onUnmounted(clear)
 return{held,over,down,move,up,cancel:clear,click}
}
