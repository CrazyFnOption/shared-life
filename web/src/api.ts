import { ref } from 'vue'
import { decisionBody } from './requestBoard'
import { requireSource } from './sources'
import type { AccountContext, AccountSource, AppSettings, Category, CategoryChange, CategoryChangeInput, ChangeRequest, FamilyContext, FamilyInvite, RequestInput, Revision, Expense, ImageMeta, Operation, Receipt, Report, Scope, SettingsInput, Snapshot, Source, User } from './types'
export class ApiError extends Error{constructor(readonly status:number,readonly code:string,readonly current?:Expense,readonly restore_id?:string){super(code)}}
export const apiWrites=ref(0)
const restoreIds=new Map<string,string>()
export function setRestoreId(userId:string,id?:string){if(id)restoreIds.set(userId,id);else restoreIds.delete(userId)}
// Kept as an inert import compatibility export. Protocol 2 never infers business source.
export function setFamilyContext(_userId:string,_context?:FamilyContext){}
function accountHeaders(origin:AccountSource,write=false){if(!origin.user_id||!origin.restore_id)throw new Error('source_context_required');return{'X-App-Protocol':'2','X-Expected-User':origin.user_id,'X-Restore-Id':origin.restore_id,...(write?{'X-App-Request':'shared-expense'}:{})}}
function headersFor(userId:string,source:Source|FamilyContext|undefined,write=false){const s=requireSource(source);if(s.user_id!==userId)throw new Error('source_context_required');return{...accountHeaders(s,write),'X-Scope':s.scope,'X-Family-Id':s.family_id??'none','X-Relation-Epoch':String(s.relation_epoch)}}
async function checked(response:Response):Promise<Response>{if(!response.ok){const result=await response.json().catch(()=>({})),detail=result.detail??result;throw new ApiError(response.status,typeof detail==='object'&&detail?detail.code??'invalid_request':response.status===401?'unauthorized':'invalid_request',detail?.current,detail?.restore_id)}return response}
function checkResponse(response:Response,source:Source){for(const [key,value] of Object.entries({'X-App-Protocol':'2','X-Source-User':source.user_id,'X-Restore-Id':source.restore_id,'X-Scope':source.scope,'X-Family-Id':source.family_id??'none','X-Relation-Epoch':String(source.relation_epoch)}))if(response.headers.get(key)!==value)throw new ApiError(409,'response_source_mismatch')}
async function fetchJson<T>(path:string,headers:Record<string,string>,body?:unknown,method?:string,source?:Source,signal?:AbortSignal):Promise<T>{
 const verb=method??(body!==undefined?'POST':'GET'),write=verb!=='GET';if(body!==undefined)headers['Content-Type']='application/json'
 if(write)apiWrites.value++
 try{const response=await checked(await fetch(`/api${path}`,{method:verb,headers,signal,credentials:'same-origin',cache:'no-store',body:body!==undefined?JSON.stringify(body):undefined}));if(source)checkResponse(response,source);return response.status===204?undefined as T:await response.json()}
 finally{if(write)apiWrites.value--}
}
export async function apiRequest<T>(path:string,userId?:string,body?:unknown,method?:string,restoreId?:string,source?:Source|FamilyContext,signal?:AbortSignal):Promise<T>{
 if(path.startsWith('/auth/')){const headers:Record<string,string>={};if(userId)headers['X-Expected-User']=userId;if(body!==undefined||path==='/auth/logout'){headers['X-App-Request']='shared-expense';const id=restoreId??(userId?restoreIds.get(userId):undefined);if(id)headers['X-Restore-Id']=id}return fetchJson(path,headers,body,method??(path==='/auth/logout'?'POST':undefined),undefined,signal)}
 const s=requireSource(source);return fetchJson(`/v2${path}`,headersFor(userId!,s,body!==undefined||method!==undefined&&method!=='GET'),body,method,s,signal)
}
function accountRequest<T>(path:string,origin:AccountSource,body?:unknown,method?:string){return fetchJson<T>(path,accountHeaders(origin,body!==undefined||method!==undefined&&method!=='GET'),body,method)}
async function imageRequest(userId:string,expenseId:string,imageId:string,method:'GET'|'PUT'|'DELETE',source:Source,blob?:Blob):Promise<Response>{
 const headers:Record<string,string>=headersFor(userId,source,method!=='GET');if(blob)headers['Content-Type']=blob.type
 if(method==='DELETE')apiWrites.value++
 try{const response=await checked(await fetch(`/api/v2/expenses/${encodeURIComponent(expenseId)}/images/${encodeURIComponent(imageId)}`,{method,headers,body:blob,credentials:'same-origin',cache:'no-store'}));checkResponse(response,source);return response}
 finally{if(method==='DELETE')apiWrites.value--}
}
const business=<T>(path:string,userId:string,source:Source|FamilyContext,body?:unknown,method?:string)=>apiRequest<T>(path,userId,body,method,undefined,source)
export const api={
 context:async(userId:string):Promise<AccountContext>=>{const result=await fetchJson<AccountContext>('/context',{'X-App-Protocol':'2','X-Expected-User':userId});if(result.protocol_version!==2)throw new ApiError(426,'server_upgrade_required');return result},
 defaultScope:(origin:AccountSource,input:{request_id:string;base_version:number;scope:Scope;family_id:string|null})=>accountRequest<{context:AccountContext}>('/account/default-scope',origin,input,'PUT'),
 settings:(userId:string,scope:Scope,source:Source)=>business<AppSettings>(`/settings?scope=${scope}`,userId,source),
 saveSettings:(userId:string,scope:Scope,input:SettingsInput,source:Source)=>business<AppSettings>(`/settings?scope=${scope}`,userId,source,input,'PUT'),
 reports:(userId:string,scope:Scope,source:Source)=>business<{reports:Report[]}>(`/reports?scope=${scope}`,userId,source),
 report:(userId:string,id:string,source:Source)=>business<Report>(`/reports/${encodeURIComponent(id)}`,userId,source),
 retryReport:(userId:string,id:string,source:Source)=>business<Report>(`/reports/${encodeURIComponent(id)}/retry`,userId,source,{}),
 subscribe:(origin:AccountSource,subscription:PushSubscriptionJSON)=>accountRequest<void>('/v2/push-subscriptions',origin,{endpoint:subscription.endpoint,keys:{p256dh:subscription.keys?.p256dh,auth:subscription.keys?.auth}}),
 unsubscribe:(origin:AccountSource,endpoint:string)=>accountRequest<void>('/v2/push-subscriptions',origin,{endpoint},'DELETE'),
 export:async(userId:string,format:'json'|'csv',scope:Scope|'all',source:Source):Promise<Blob>=>{const response=await checked(await fetch(`/api/v2/export?format=${format}&scope=${scope}`,{headers:headersFor(userId,source),credentials:'same-origin',cache:'no-store'}));checkResponse(response,source);return response.blob()},
 requestChange:(userId:string,input:RequestInput,restoreId:string,source:Source)=>business<ChangeRequest>('/change-requests',userId,source,input),
 decide:(userId:string,id:string,decision_id:string,decision:'accept'|'reject',restoreId:string,rejectionReason:string,source:Source)=>business<{request:ChangeRequest;expense:Expense}>(`/change-requests/${encodeURIComponent(id)}/decision`,userId,source,decisionBody(decision_id,decision,rejectionReason)),
 history:(userId:string,id:string,source:Source)=>business<{revisions:Revision[];source_dependencies:{family_id:string;relation_epoch:number}[]}>(`/expenses/${encodeURIComponent(id)}/history`,userId,source),
 addCategory:(userId:string,category:Category&{restore_deleted?:boolean},restoreId:string,source:Source|FamilyContext)=>business<Category>('/categories',userId,source,category),
 previewCategories:(userId:string,input:CategoryChangeInput,restoreId:string,source:Source|FamilyContext)=>business<CategoryChange>('/category-changes/preview',userId,source,input),
 submitCategories:(userId:string,input:{id:string;preview_id:string;reason?:string},restoreId:string,source:Source|FamilyContext)=>business<{change:CategoryChange}>('/category-changes',userId,source,input),
 decideCategories:(userId:string,id:string,decision_id:string,decision:'accept'|'reject',restoreId:string,source:Source|FamilyContext,rejectionReason='')=>business<{change:CategoryChange}>(`/category-changes/${encodeURIComponent(id)}/decision`,userId,source,decisionBody(decision_id,decision,rejectionReason)),
 uploadImage:async(userId:string,expenseId:string,imageId:string,blob:Blob,restoreId:string,source:Source):Promise<ImageMeta>=>{apiWrites.value++;try{return await(await imageRequest(userId,expenseId,imageId,'PUT',source,blob)).json()}finally{apiWrites.value--}},
 deleteImage:async(userId:string,expenseId:string,imageId:string,restoreId:string,source:Source):Promise<void>=>{await imageRequest(userId,expenseId,imageId,'DELETE',source)},
 getImage:async(userId:string,expenseId:string,imageId:string,source:Source):Promise<Blob>=>(await imageRequest(userId,expenseId,imageId,'GET',source)).blob(),
 me:()=>apiRequest<{user:User}>('/auth/me'),login:(username:string,password:string)=>apiRequest<{user:User}>('/auth/login',undefined,{username,password}),register:(username:string,display_name:string,password:string)=>apiRequest<{user:User}>('/auth/register',undefined,{username,display_name,password}),
 invites:(origin:AccountSource)=>accountRequest<{received:FamilyInvite[];sent:FamilyInvite[];restore_id:string;context_version:number}>('/v2/family-invites',origin),
 createFamily:(origin:AccountSource,name:string,request_id:string)=>accountRequest<{context:AccountContext}>('/v2/families',origin,{request_id,name}),
 invite:(userId:string,username:string,request_id:string,source:Source)=>business<FamilyInvite>('/family-invites',userId,source,{request_id,username}),
 decideInvite:(origin:AccountSource,id:string,action:'accept'|'reject')=>accountRequest<{context:AccountContext}|FamilyInvite>(`/v2/family-invites/${encodeURIComponent(id)}/${action}`,origin,{}),
 revokeInvite:(userId:string,id:string,source:Source)=>business<{context:AccountContext}>(`/family-invites/${encodeURIComponent(id)}/revoke`,userId,source,{}),
 leaveFamily:(userId:string,id:string,revision:number,source:Source)=>business<{context:AccountContext}>(`/families/${encodeURIComponent(id)}/leave`,userId,source,{expected_family_revision:revision}),
 removeMember:(userId:string,id:string,memberId:string,revision:number,targetEpoch:number,source:Source)=>business<{context:AccountContext}>(`/families/${encodeURIComponent(id)}/members/${encodeURIComponent(memberId)}/remove`,userId,source,{expected_family_revision:revision,target_relation_epoch:targetEpoch}),
 transferOwner:(userId:string,id:string,memberId:string,revision:number,targetEpoch:number,source:Source)=>business<{context:AccountContext}>(`/families/${encodeURIComponent(id)}/owner`,userId,source,{user_id:memberId,expected_family_revision:revision,target_relation_epoch:targetEpoch}),
 transferAndLeave:(userId:string,id:string,memberId:string,revision:number,targetEpoch:number,source:Source)=>business<{context:AccountContext}>(`/families/${encodeURIComponent(id)}/transfer-and-leave`,userId,source,{user_id:memberId,expected_family_revision:revision,target_relation_epoch:targetEpoch}),
 dissolveFamily:(userId:string,id:string,revision:number,source:Source)=>business<{context:AccountContext}>(`/families/${encodeURIComponent(id)}/dissolve`,userId,source,{expected_family_revision:revision}),
 changePassword:(userId:string,old_password:string,new_password:string)=>apiRequest<void>('/auth/password',userId,{old_password,new_password}),logout:(userId:string,restoreId?:string)=>apiRequest<void>('/auth/logout',userId,undefined,undefined,restoreId),
 snapshot:(userId:string,source:Source)=>business<Snapshot>('/sync',userId,source),send:(userId:string,operation:Operation,restoreId:string,source:Source)=>business<Receipt>('/operations',userId,source,operation)
}
