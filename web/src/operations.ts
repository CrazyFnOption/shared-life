import type { Expense, Operation, ReviewData } from './types'
export function normalizeExpense(expense:Expense):Expense {
  const result={...expense,voided:expense.voided??false,rating:expense.rating??null,rating_reason:expense.rating_reason??'',refunds:(expense.refunds??[]).map(r=>({...r}))}
  return {...result,net_amount_cents:netAmount(result)}
}
export function netAmount(expense:Expense):number { return expense.voided?0:expense.amount_cents-(expense.refunds??[]).filter(r=>!r.voided).reduce((n,r)=>n+r.amount_cents,0) }
export function sameExpense(a:Expense,b:Expense):boolean {
  const left=normalizeExpense(a),right=normalizeExpense(b)
  return (['id','owner_id','scope','version','amount_cents','purpose','occurred_at','location','voided','rating','rating_reason','net_amount_cents'] as const).every(k=>left[k]===right[k]) && (left.category??'')===(right.category??'') && JSON.stringify(left.refunds)===JSON.stringify(right.refunds)
}
export function validateReview(data:Partial<ReviewData>):void {
  if ((data.rating===undefined)!==(data.rating_reason===undefined)) throw new Error('评分与原因需一起保存')
  if(data.rating===undefined)return
  if(data.rating===null){if(data.rating_reason!=='')throw new Error('清除评价时原因需清空');return}
  if(!Number.isInteger(data.rating)||data.rating<1||data.rating>5||[...(data.rating_reason??'').trim()].length>500)throw new Error('请选择有效满意度，原因最多500字')
}
export function applyOperation(previous:Expense|undefined,operation:Operation,ownerId:string):Expense {
  if(operation.kind==='create') {
    validateReview(operation.data)
    if(previous)throw new Error('记录已存在，不能重新创建')
    return normalizeExpense({...operation.data,rating:operation.data.rating??3,rating_reason:operation.data.rating_reason??'',id:operation.expense_id,owner_id:ownerId,version:1})
  }
  if(!previous)throw new Error('记录已不可用，草稿保留，请核对服务端版本')
  const next=normalizeExpense(previous)
  if(next.voided)throw new Error('记录已作废，不能继续修改；本机草稿已保留')
  switch(operation.kind){
    case 'update': validateReview(operation.data);Object.assign(next,operation.data);break
    case 'review':validateReview(operation.data);Object.assign(next,operation.data);break
    case 'scope':next.scope=operation.data.scope;break
    case 'void':next.voided=true;break
    case 'refund_create':case 'refund_update': {
      const data=operation.data,old=next.refunds!.find(r=>r.id===data.refund_id)
      if(!Number.isSafeInteger(data.amount_cents)||data.amount_cents<=0)throw new Error('退款金额必须大于零')
      if(!data.occurred_at||Number.isNaN(Date.parse(data.occurred_at)))throw new Error('请选择有效退款日期')
      if([...data.reason].length>500)throw new Error('退款原因最多500字')
      if(operation.kind==='refund_create'&&old)throw new Error('退款已存在，请核对后保留草稿')
      if(operation.kind==='refund_update'&&(!old||old.voided))throw new Error('原退款已变化，请核对后保留草稿')
      const refund={id:data.refund_id,amount_cents:data.amount_cents,occurred_at:data.occurred_at,reason:data.reason,voided:false,version:(old?.version??0)+1}
      next.refunds=next.refunds!.filter(r=>r.id!==refund.id).concat(refund);break
    }
    case 'refund_void': {const old=next.refunds!.find(r=>r.id===operation.data.refund_id);if(!old||old.voided)throw new Error('退款不存在或已作废');old.voided=true;old.version++;break}
  }
  if(!Number.isSafeInteger(next.amount_cents)||next.amount_cents<=0)throw new Error('原付款必须大于零')
  if(next.refunds!.filter(r=>!r.voided).reduce((n,r)=>n+r.amount_cents,0)>next.amount_cents)throw new Error('有效退款合计不能超过原付款，请先核对退款')
  next.version=operation.base_version+1;next.net_amount_cents=netAmount(next)
  return next
}
