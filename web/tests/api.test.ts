import { afterEach,expect,test,vi } from 'vitest'
import { api,apiRequest,ApiError,setFamilyContext,setRestoreId } from '../src/api'
import { privateSource as p,sharedSource as s,apiResponse } from './sourceFixture'
afterEach(()=>vi.unstubAllGlobals())
test('业务GET/PUT/DELETE及Blob一律v2硬路径和完整显式来源；账号订阅无假家庭',async()=>{
 const calls:{url:string;options:RequestInit}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{calls.push({url,options});return apiResponse('{}',options)}));setRestoreId('a','wrong-current')
 await api.saveSettings('a','private',{weekly:{enabled:true,weekday:6,time:'19:00'}},p)
 await api.subscribe(p,{endpoint:'https://example.invalid/push',expirationTime:null,keys:{p256dh:'public',auth:'protected'}});await api.unsubscribe(p,'https://example.invalid/push');await api.logout('a','restore-a')
 await api.uploadImage('a','e','i',new Blob(['fiction'],{type:'image/jpeg'}),'restore-a',s);await api.deleteImage('a','e','i','restore-a',s);await api.getImage('a','e','i',s);await api.export('a','json','all',s)
 expect(calls.every(c=>(c.options.headers as Record<string,string>)['X-Restore-Id']==='restore-a')).toBe(true)
 expect(calls.map(c=>c.options.method)).toEqual(['PUT','POST','DELETE','POST','PUT','DELETE','GET',undefined])
 expect(calls.filter(c=>!c.url.includes('/auth/')).every(c=>c.url.startsWith('/api/v2/'))).toBe(true)
 expect(calls.slice(4).every(c=>(c.options.headers as Record<string,string>)['X-Relation-Epoch']==='3')).toBe(true)
 expect((calls[1]!.options.headers as Record<string,string>)['X-Family-Id']).toBeUndefined()
 expect(JSON.parse(calls[1]!.options.body as string)).toEqual({endpoint:'https://example.invalid/push',keys:{p256dh:'public',auth:'protected'}})
})
test('来源固定发出时restore，缺来源/空restore拒绝且绝不借用当前缓存',async()=>{
 const fetch=vi.fn(async(_url:string,options:RequestInit)=>apiResponse('{}',options));vi.stubGlobal('fetch',fetch);setRestoreId('a','new');setFamilyContext('a',{family:null,membership_version:999})
 await apiRequest('/operations','a',{},undefined,'ignored',{...p,restore_id:'old'})
 expect((fetch.mock.calls[0]![1]!.headers as Record<string,string>)['X-Restore-Id']).toBe('old')
 await expect(apiRequest('/operations','a',{},undefined,'old')).rejects.toThrow('source_context_required')
 await expect(api.send('a',{} as any,'new',{...p,restore_id:''})).rejects.toThrow('source_context_required');expect(fetch).toHaveBeenCalledTimes(1)
})
test('restore_changed保留机器码和新恢复ID，不当普通冲突',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:{code:'restore_changed',restore_id:'new'}}),{status:409})))
 await expect(apiRequest('/operations','a',{},undefined,'restore-a',p)).rejects.toMatchObject({code:'restore_changed',restore_id:'new',status:409})
})
test('家庭管理携带目标revision/epoch，账号默认与建家仍无家庭头',async()=>{
 const calls:{url:string;options:RequestInit}[]=[];vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{calls.push({url,options});return apiResponse('{}',options)}))
 await api.removeMember('a','family-a','b',4,9,s);await api.transferOwner('a','family-a','b',4,9,s);await api.transferAndLeave('a','family-a','b',4,9,s);await api.dissolveFamily('a','family-a',4,s)
 await api.defaultScope(p,{request_id:'default',base_version:7,scope:'shared',family_id:'family-a'});await api.createFamily(p,'丙','create')
 expect(calls.slice(0,4).map(c=>JSON.parse(c.options.body as string))).toEqual([{expected_family_revision:4,target_relation_epoch:9},{user_id:'b',expected_family_revision:4,target_relation_epoch:9},{user_id:'b',expected_family_revision:4,target_relation_epoch:9},{expected_family_revision:4}])
 expect(calls.slice(4).every(c=>!(c.options.headers as Record<string,string>)['X-Family-Id'])).toBe(true)
 expect(calls[4]!.url).toBe('/api/account/default-scope')
})
test('错误响应来源、缺响应头及旧协议不能交回正文或Blob',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('{"secret":"other-family"}')))
 await expect(api.snapshot('a',s)).rejects.toMatchObject({code:'response_source_mismatch'})
 await expect(api.getImage('a','e','i',s)).rejects.toMatchObject({code:'response_source_mismatch'})
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('{"protocol_version":1}')))
 await expect(api.context('a')).rejects.toMatchObject({code:'server_upgrade_required'})
})
test.each([false,true])('foreground写计数等待响应正文真正settle，失败=%s也不提前归零',async fail=>{
 const {apiWrites}=await import('../src/api');let release!:(value:unknown)=>void,reject!:(error:Error)=>void,entered!:()=>void
 const parsed=new Promise<void>(resolve=>{entered=resolve}),body=new Promise<unknown>((resolve,no)=>{release=resolve;reject=no})
 vi.stubGlobal('fetch',vi.fn(async(_url:string,options:RequestInit)=>{const response=apiResponse('{}',options);response.json=()=>{entered();return body};return response}))
 const pending=api.uploadImage('a','expense','image',new Blob(['kept']),'restore-a',s)
 await parsed;expect(apiWrites.value).toBe(1)
 if(fail){reject(new Error('decode failed'));await expect(pending).rejects.toThrow('decode failed')}else{release({id:'image'});await expect(pending).resolves.toEqual({id:'image'})}
 expect(apiWrites.value).toBe(0)
})
