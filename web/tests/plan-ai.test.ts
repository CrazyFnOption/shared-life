import { afterEach, expect, test, vi } from 'vitest'
import { acknowledgedAiInput, aiError, aiBudgetNotice, aiUsd, requestTurn, AI_READ_TIMEOUT_MS, AI_SEND_TIMEOUT_MS, planAiApi, selectionWithStages, sourceHref, supportedAiMaterial, validateAiInput } from '../src/plans/ai'
import type { AiPlanDraft, AiConversation } from '../src/plans/ai'
import { ApiError } from '../src/api'
import { apiResponse, sharedSource as source } from './sourceFixture'
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers()})
const draft:AiPlanDraft={id:'draft',turn_id:'turn',base_version:3,items:[{id:'stage',kind:'stage',title:'准备',body_markdown:'',stage_key:null,assignee_ids:[],due_date:null,adopted:false},{id:'task',kind:'task',title:'任务',body_markdown:'完成标准',stage_key:'stage',assignee_ids:[],due_date:null,adopted:false},{id:'used',kind:'task',title:'已采纳',body_markdown:'',stage_key:'stage',assignee_ids:[],due_date:null,adopted:true}]}
test('逐项采纳自动带入明确预览的阶段，已采纳项不能再次加入',()=>{
 expect(selectionWithStages(draft,['task','task','used']).map(x=>x.id)).toEqual(['stage','task'])
 expect(selectionWithStages({...draft,items:draft.items.map(x=>x.id==='stage'?{...x,adopted:true}:x)},['task']).map(x=>x.id)).toEqual(['task'])
 expect(selectionWithStages(draft,[])).toEqual([])
})
test('AI资料只接受规定的当前附件类型；来源URL排除脚本协议',()=>{
 const base={id:'photo',kind:'photo' as const,name:'虚构照片',mime_type:'image/jpeg',size_bytes:1,sha256:'x'}
 expect(supportedAiMaterial(base)).toBe(true);expect(supportedAiMaterial({...base,kind:'file',mime_type:'application/pdf'})).toBe(true)
 expect(supportedAiMaterial({...base,kind:'file',mime_type:'text/plain'})).toBe(true);expect(supportedAiMaterial({...base,mime_type:'image/svg+xml'})).toBe(false)
 expect(sourceHref('javascript:alert(1)')).toBeUndefined();expect(sourceHref('https://example.invalid/source')).toBe('https://example.invalid/source')
})
test('AI HTTP链沿用显式Source且只发送主动选择的资料、保留操作ID',async()=>{
 const calls:{url:string;options:RequestInit}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{calls.push({url,options});return apiResponse(JSON.stringify({source,record_id:'record'}),options)}))
 await planAiApi.read(source,'record')
 const input={op_id:'same-retry',content:'虚构目标',attachment_ids:['chosen-photo'],allow_web:true,base_version:3}
 await planAiApi.send(source,'record',input);await planAiApi.adopt(source,'record',{op_id:'adopt',draft_id:'draft',item_ids:['stage','task'],base_version:3,target_plan_id:null})
 expect(calls.map(c=>c.url)).toEqual(['/api/v2/plans/records/record/ai','/api/v2/plans/records/record/ai/messages','/api/v2/plans/records/record/ai/adopt'])
 expect(JSON.parse(calls[1]!.options.body as string)).toEqual(input)
 expect((calls[1]!.options.headers as Record<string,string>)['X-Family-Id']).toBe(source.family_id)
})
test('即使响应头正确，AI正文来源不一致也拒收；费用未知有明确预留说明',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(_url:string,options:RequestInit)=>apiResponse(JSON.stringify({source:{...source,family_id:'other-family'},record_id:'record'}),options)))
 await expect(planAiApi.read(source,'record')).rejects.toMatchObject({code:'response_source_mismatch'})
 expect(aiError(new ApiError(503,'ai_budget_unknown'))).toContain('不代表已扣费')
 expect(aiError(new ApiError(429,'ai_budget_exceeded'))).toContain('额度不足')
})

test('发送边界与HTTP契约一致，超长发言或第六项资料在本地说明',()=>{const input={content:'字'.repeat(8000),attachment_ids:['a','b','c','d','e'],allow_web:true};expect(validateAiInput(input)).toBe('');expect(validateAiInput({...input,content:input.content+'字'})).toContain('8,000');expect(validateAiInput({...input,attachment_ids:[...input.attachment_ids,'f']})).toContain('5 项');})

test('未知预留不再全局封禁，实际余额不足仍明确拦截',()=>{expect(aiBudgetNotice({blocked:true})).toContain('剩余额度不足');expect(aiBudgetNotice({blocked:false})).toBe('');expect(aiError(new ApiError(503,'ai_budget_unknown'))).toContain('剩余额度内可主动');expect(aiError(new ApiError(503,'ai_paused'))).toContain('恢复后 AI 已暂停');expect(aiUsd(5450000)).toBe('$5.45');expect(aiUsd(122872)).toBe('$0.122872');})

test('只以本人原op匹配GET，不把旧请求或其他成员状态当成本次回执',()=>{const value={messages:[{id:'old',op_id:'old-op',role:'user',author_id:source.user_id,status:'ready'},{id:'other',op_id:'requested',role:'user',author_id:'other-user',status:'ready'},{id:'mine',op_id:'requested',role:'user',author_id:source.user_id,status:'running'}]} as AiConversation;expect(requestTurn(value,'requested',source.user_id)?.id).toBe('mine');expect(requestTurn({...value,messages:value.messages.slice(0,2)},'requested',source.user_id)).toBeUndefined();})

test.each([['GET',AI_READ_TIMEOUT_MS],['POST',AI_SEND_TIMEOUT_MS]] as const)('AI %s等待有界，超时只中断本机等待且不重发',async(method,milliseconds)=>{vi.useFakeTimers();const {apiWrites}=await import('../src/api');const calls:string[]=[];vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{calls.push(url);return new Promise<Response>((_resolve,reject)=>options.signal!.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))}));const request=method==='GET'?planAiApi.read(source,'record'):planAiApi.send(source,'record',{op_id:'kept-operation',content:'虚构',attachment_ids:[],allow_web:false,base_version:1});const observed=request.catch(error=>error);await vi.advanceTimersByTimeAsync(milliseconds);expect(await observed).toMatchObject({code:'ai_request_pending'});expect(calls).toHaveLength(1);expect(apiWrites.value).toBe(0);})

test('有界等待之前的正常慢响应仍接纳原请求，不额外调用',async()=>{vi.useFakeTimers();let finish!:(response:Response)=>void;let options!:RequestInit;const fetch=vi.fn(async(_url:string,input:RequestInit)=>{options=input;return new Promise<Response>(resolve=>{finish=resolve})});vi.stubGlobal('fetch',fetch);const request=planAiApi.send(source,'record',{op_id:'slow-same-op',content:'虚构慢响应',attachment_ids:[],allow_web:false,base_version:1});await vi.advanceTimersByTimeAsync(300000);finish(apiResponse(JSON.stringify({source,record_id:'record'}),options));await expect(request).resolves.toMatchObject({record_id:'record'});expect(fetch).toHaveBeenCalledTimes(1);})


test('本人发言已持久化后清除未编辑的原始文本与资料，包含首尾空白',()=>{const current={content:'  虚构发言\n',attachment_ids:['photo','pdf']},sent={...current,attachment_ids:[...current.attachment_ids],text_edited:false,materials_edited:false,acknowledged:false};expect(acknowledgedAiInput(current,sent)).toEqual({content:'',attachment_ids:[]});expect(current.attachment_ids).toEqual(['photo','pdf']);})

test('回执只确认一次，不擦除后续编辑、重新输入的同文或更改过的资料',()=>{const sent={content:'虚构发言',attachment_ids:['photo'],text_edited:false,materials_edited:false,acknowledged:false};const newer={content:'新条件',attachment_ids:['pdf']};expect(acknowledgedAiInput(newer,sent)).toEqual(newer);const original={content:sent.content,attachment_ids:sent.attachment_ids};expect(acknowledgedAiInput(original,{...sent,text_edited:true,materials_edited:true})).toEqual(original);expect(acknowledgedAiInput(original,{...sent,acknowledged:true})).toEqual(original);})
