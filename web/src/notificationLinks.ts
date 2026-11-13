import type { Source } from './types'
export interface NotificationIntent {id:string;source:Source;kind?:'monthly_report'|'weekly_review'}
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function safeNotificationUrl(value:unknown,origin:string):string|undefined{
 if(typeof value!=='string'||value.startsWith('//'))return
 try{const url=new URL(value,origin);if(url.origin!==origin||url.pathname!=='/'||url.username||url.password)return;return url.pathname+url.search+url.hash}catch{return}
}
export function parseNotificationIntent(value:string,origin:string):NotificationIntent|undefined{
 const safe=safeNotificationUrl(value,origin);if(!safe)return
 const query=new URL(safe,origin).searchParams
 for(const key of ['notification','scope','family_id','user_id','restore_id','relation_epoch'])if(query.getAll(key).length!==1)return
 const id=query.get('notification')!,scope=query.get('scope'),family=query.get('family_id'),user=query.get('user_id')!,restore=query.get('restore_id')!,rawEpoch=query.get('relation_epoch')!
 if(!uuid.test(id)||!uuid.test(user)||!uuid.test(restore)||!/^\d+$/.test(rawEpoch))return
 const epoch=Number(rawEpoch);if(!Number.isSafeInteger(epoch))return
 const kind=query.get('kind');if(query.getAll('kind').length>1||kind!==null&&kind!=='monthly_report'&&kind!=='weekly_review')return
 const extra:Pick<NotificationIntent,'kind'>=kind?{kind}:{}
 if(scope==='private'&&family==='none'&&epoch===0)return{id,...extra,source:{protocol_version:2,user_id:user,restore_id:restore,scope:'private',family_id:null,relation_epoch:0}}
 if(scope==='shared'&&family&&uuid.test(family)&&epoch>0)return{id,...extra,source:{protocol_version:2,user_id:user,restore_id:restore,scope:'shared',family_id:family,relation_epoch:epoch}}
}
