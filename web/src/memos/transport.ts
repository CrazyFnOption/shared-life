import { calendarBounds } from './calendarCache'
import type { MemoCalendarSnapshot } from './calendarCache'
import { apiRequest, apiWrites, ApiError } from '../api'
import type { Source } from '../types'
import type { MemoAttachment, MemoOperation, MemoReceipt, MemoSnapshot, MemoMessage } from './domain'
function headers(source:Source,write=false){return{'X-App-Protocol':'2','X-Expected-User':source.user_id,'X-Restore-Id':source.restore_id,'X-Scope':source.scope,'X-Family-Id':source.family_id??'none','X-Relation-Epoch':String(source.relation_epoch),...(write?{'X-App-Request':'shared-expense'}:{})}}
function checkSource(response:Response,source:Source){for(const [name,value] of Object.entries({'X-App-Protocol':'2','X-Source-User':source.user_id,'X-Restore-Id':source.restore_id,'X-Scope':source.scope,'X-Family-Id':source.family_id??'none','X-Relation-Epoch':String(source.relation_epoch)}))if(response.headers.get(name)!==value)throw new ApiError(409,'response_source_mismatch')}
async function attachment(source:Source,memoId:string,id:string,blob?:Blob):Promise<Response>{
 if(blob)apiWrites.value++
 try{const response=await fetch(`/api/v2/memos/records/${encodeURIComponent(memoId)}/attachments/${encodeURIComponent(id)}`,{method:blob?'PUT':'GET',headers:{...headers(source,!!blob),...(blob?{'Content-Type':blob.type||'application/octet-stream'}:{})},credentials:'same-origin',cache:'no-store',body:blob})
 if(!response.ok){const result=await response.json().catch(()=>({})),detail=result.detail??result;throw new ApiError(response.status,typeof detail==='object'?detail.code??'invalid_request':detail)}checkSource(response,source);return response
 }finally{if(blob)apiWrites.value--}
}
export const memoApi={
 notification:(userId:string,source:Source,id:string)=>apiRequest<{source:Source;message:MemoMessage|null;notice?:string;choose_scope?:boolean}>(`/memos/notifications/${encodeURIComponent(id)}`,userId,undefined,undefined,undefined,source),
 calendar:(userId:string,source:Source,month:string)=>{const range=calendarBounds(month);return apiRequest<MemoCalendarSnapshot>(`/memos/calendar?from_date=${range.from}&to_date=${range.to}`,userId,undefined,undefined,undefined,source)},
 messages:(userId:string,source:Source,ignored=false)=>apiRequest<{source:Source;messages:MemoMessage[]}>(`/memos/messages${ignored?'?include_ignored=true':''}`,userId,undefined,undefined,undefined,source),
 ignore:(userId:string,source:Source,id:string,restore=false)=>apiRequest<{source:Source;id:string;ignored:boolean}>(`/memos/messages/${encodeURIComponent(id)}/${restore?'restore':'ignore'}`,userId,{},undefined,undefined,source),
 snapshot:(userId:string,source:Source)=>apiRequest<MemoSnapshot>('/memos/sync',userId,undefined,undefined,undefined,source),
 send:(userId:string,operation:MemoOperation,source:Source)=>apiRequest<{source:Source;receipt:MemoReceipt}>('/memos/operations',userId,operation,undefined,undefined,source),
 upload:async(source:Source,memoId:string,id:string,blob:Blob)=>(await attachment(source,memoId,id,blob)).json() as Promise<{source:Source;attachment:MemoAttachment}>,
 download:async(source:Source,memoId:string,id:string)=>(await attachment(source,memoId,id)).blob(),
}
