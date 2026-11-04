import type { Source } from '../src/types'
export const privateSource:Source={protocol_version:2,user_id:'a',restore_id:'restore-a',scope:'private',family_id:null,relation_epoch:0}
export const sharedSource:Source={...privateSource,scope:'shared',family_id:'family-a',relation_epoch:3}
export function apiResponse(body:string,options:RequestInit,status=200){const sent=options.headers as Record<string,string>,headers=new Headers();for(const name of ['X-App-Protocol','X-Restore-Id','X-Scope','X-Family-Id','X-Relation-Epoch'])if(sent[name])headers.set(name,sent[name]!);if(sent['X-Expected-User'])headers.set('X-Source-User',sent['X-Expected-User']);return new Response(status===204?null:body,{status,headers})}
