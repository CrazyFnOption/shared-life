import { computed, ref, watch, type Ref } from 'vue'
export interface DecisionAttempt { id:string; decision:'accept'|'reject'; rejection_reason:string }
export function freezeDecision(previous:DecisionAttempt|undefined,decision:'accept'|'reject',reason=''):DecisionAttempt {
  if(previous){if(previous.decision!==decision)throw new Error('上次决定结果未知，请先重试原决定或刷新确认');return previous}
  const rejection_reason=reason.trim()
  if([...rejection_reason].length>500)throw new Error('拒绝原因最多500字')
  if(decision==='accept'&&rejection_reason)throw new Error('接受决定不能附拒绝原因')
  return {id:crypto.randomUUID(),decision,rejection_reason}
}
export function decisionBody(id:string,decision:'accept'|'reject',reason='') {
  return {decision_id:id,decision,...(reason?{rejection_reason:reason}:{})}
}
export function needsAttention(item:{id:string|null;status:string;requester_id:string},userId:string,acknowledged:string[],kind:'single'|'category'):boolean {
  return item.status==='pending'||item.status==='rejected'&&item.requester_id===userId&&!acknowledged.includes(`${kind}:${item.id}`)
}
const confirmed=ref<Record<string,string[]>>({})
const prefix='shared-expense:request-confirmed:'
export function useRequestBoard(context:Ref<string>){
  const historyOpen=ref(false),resetKey=ref(0)
  watch(context,key=>{
    historyOpen.value=false;resetKey.value++
    if(!key||confirmed.value[key])return
    try{const value=JSON.parse(localStorage.getItem(prefix+key)??'[]');confirmed.value[key]=Array.isArray(value)?value.filter(v=>typeof v==='string'):[]}
    catch{confirmed.value[key]=[]}
  },{immediate:true,flush:'sync'})
  const acknowledged=computed(()=>confirmed.value[context.value]??[])
  function acknowledge(ids:string[]):boolean {
    const key=context.value;if(!key)return false
    const values=[...new Set([...acknowledged.value,...ids])]
    let saved=true;try{localStorage.setItem(prefix+key,JSON.stringify(values))}catch{saved=false}
    confirmed.value[key]=values;historyOpen.value=false;resetKey.value++
    return saved
  }
  return {historyOpen,resetKey,acknowledged,acknowledge}
}
